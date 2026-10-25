"""M04 背离检测与枢轴点的单元测试。

覆盖：正常输入、空输入、边界输入、非法输入、外部依赖失败、重复调用幂等、
未来函数一致性与公共契约兼容性。
合成数据使用几何分段构造，不依赖外部行情。
"""

from __future__ import annotations

import importlib.util
import sys
from decimal import Decimal
from pathlib import Path
from typing import Any

import numpy as np
import pytest
from pydantic import ValidationError

from dquant.contracts import Candle, CandleBar, DivergenceKind, DivergenceMethod
from dquant.core.divergence import (
    DivergenceConfig,
    DivergenceInputError,
    ScoreFactors,
    build_segments,
    compute_signal_score,
    detect_divergences,
)
from dquant.core.pivots import find_pivot_highs, find_pivot_lows

STEP_MS = 14_400_000
START_TS = 1_704_067_200_000  # 2024-01-01T00:00:00Z


# ---------------------------------------------------------------------------
# 合成数据构造
# ---------------------------------------------------------------------------


def _linear(start: float, end: float, bars: int) -> list[float]:
    """构造 bars 根从 start 到 end 的线性价格序列。"""

    if bars <= 1:
        return [start]
    return [start + (end - start) * i / (bars - 1) for i in range(bars)]


def make_candles(
    closes: list[float],
    *,
    wick: float | list[float] = 0.8,
    vols: list[float] | None = None,
    inst_id: str = "TEST-USDT-SWAP",
    bar: CandleBar = CandleBar.FOUR_HOUR,
    confirmed: bool = True,
    start_ts: int = START_TS,
) -> list[Candle]:
    """由收盘价序列构造公共契约 Candle 列表。"""

    wicks = [wick] * len(closes) if isinstance(wick, float) else list(wick)
    assert len(wicks) == len(closes)
    if vols is None:
        vols = [80.0] * len(closes)
    candles: list[Candle] = []
    for index, close in enumerate(closes):
        ts = start_ts + index * STEP_MS
        candles.append(
            Candle(
                inst_id=inst_id,
                bar=bar,
                ts_ms=ts,
                open=Decimal(str(round(close, 6))),
                high=Decimal(str(round(close + wicks[index], 6))),
                low=Decimal(str(round(close - wicks[index], 6))),
                close=Decimal(str(round(close, 6))),
                volume=Decimal(str(vols[index])),
                quote_volume=Decimal(str(round(vols[index] * close, 6))),
                confirmed=confirmed,
            )
        )
    return candles


def _mirror(candles: list[Candle]) -> list[Candle]:
    """把底背离结构镜像为顶背离结构（价格轴翻转）。"""

    mirrored: list[Candle] = []
    for candle in candles:
        high = Decimal(200) - candle.low
        low = Decimal(200) - candle.high
        close = Decimal(200) - candle.close
        open_ = Decimal(200) - candle.open
        mirrored.append(
            candle.model_copy(
                update={
                    "open": open_,
                    "high": high,
                    "low": low,
                    "close": close,
                    "quote_volume": candle.volume * close,
                }
            )
        )
    return mirrored


def bull_two_segment_candles() -> list[Candle]:
    """两段绿柱的常规底背离数据。

    warmup 上涨 -> G1 深跌 -> 反弹 -> G2 浅跌且创新低 -> 确认反弹。
    G2 柱段先快跌后缓跌，保证段尾 MACD 柱缩短（同时用于早期提示测试）。
    """

    closes: list[float] = []
    closes += _linear(90.0, 100.0, 40)  # 预热上涨，产生红柱
    closes += _linear(100.0, 87.5, 16)  # G1 深跌
    closes += _linear(87.5, 93.0, 14)  # 中间反弹（红柱段）
    closes += _linear(93.0, 88.2, 9)  # G2 前半段快跌
    closes += _linear(88.2, 87.0, 7)  # G2 后半段缓跌，柱缩短
    closes += _linear(87.0, 93.5, 16)  # 确认反弹
    vols = [80.0] * 40 + [100.0] * 16 + [90.0] * 14 + [60.0] * 16 + [70.0] * 16
    assert len(vols) == len(closes)
    return make_candles(closes, vols=vols)


def bear_two_segment_candles() -> list[Candle]:
    """两段红柱的常规顶背离数据（底背离镜像）。"""

    return _mirror(bull_two_segment_candles())


def bull_three_segment_candles() -> list[Candle]:
    """三段绿柱：G0 -> G1 -> G2，每段价格创新低且柱面积递减。"""

    closes: list[float] = []
    closes += _linear(90.0, 100.0, 40)
    closes += _linear(100.0, 91.0, 16)  # G0
    closes += _linear(91.0, 95.0, 12)  # 反弹
    closes += _linear(95.0, 88.5, 16)  # G1（低点低于 G0，面积更小）
    closes += _linear(88.5, 92.0, 12)  # 反弹
    closes += _linear(92.0, 88.2, 9)  # G2 快跌
    closes += _linear(88.2, 88.0, 7)  # G2 缓跌
    closes += _linear(88.0, 93.0, 16)  # 确认反弹
    vols = (
        [80.0] * 40
        + [100.0] * 16
        + [90.0] * 12
        + [85.0] * 16
        + [90.0] * 12
        + [60.0] * 16
        + [70.0] * 16
    )
    assert len(vols) == len(closes)
    return make_candles(closes, vols=vols)


def bull_four_segment_candles() -> list[Candle]:
    """四段绿柱，跌速逐段放缓，验证 divergence_order 递增到 3。"""

    closes: list[float] = []
    closes += _linear(90.0, 100.0, 40)
    closes += _linear(100.0, 91.0, 14)  # G0 最急
    closes += _linear(91.0, 95.0, 12)
    closes += _linear(95.0, 88.5, 16)  # G1
    closes += _linear(88.5, 92.0, 12)
    closes += _linear(92.0, 86.9, 18)  # G2
    closes += _linear(86.9, 91.0, 14)
    closes += _linear(91.0, 86.6, 26)  # G3 最缓，仍创新低
    closes += _linear(86.6, 92.0, 16)  # 最终确认
    vols = (
        [80.0] * 40
        + [100.0] * 14
        + [90.0] * 12
        + [85.0] * 16
        + [90.0] * 12
        + [75.0] * 18
        + [85.0] * 14
        + [55.0] * 26
        + [70.0] * 16
    )
    assert len(vols) == len(closes)
    return make_candles(closes, vols=vols)


def no_divergence_candles() -> list[Candle]:
    """第二段绿柱动能强于第一段（急转弯深跌），不应产生背离。

    MACD 柱峰值对价格变化率敏感：G1 缓跌柱浅，G2 急跌柱深且持续，
    峰值与面积都不满足缩小条件。
    """

    closes: list[float] = []
    closes += _linear(90.0, 100.0, 40)
    closes += _linear(100.0, 94.0, 18)  # G1 缓慢下跌
    closes += _linear(94.0, 96.0, 12)  # 反弹
    closes += _linear(96.0, 82.0, 14)  # G2 急跌，动能明显更强
    closes += _linear(82.0, 90.0, 16)  # 确认反弹
    vols = [80.0] * len(closes)
    return make_candles(closes, vols=vols)


# ---------------------------------------------------------------------------
# 枢轴点（§5.6.2）
# ---------------------------------------------------------------------------


def test_find_pivot_lows_basic() -> None:
    values = [5.0, 4.0, 3.0, 2.0, 3.0, 4.0, 5.0, 6.0]
    pivots = find_pivot_lows(values, left=2, right=2)
    assert [(p.index, p.value, p.confirmed_index) for p in pivots] == [(3, 2.0, 5)]


def test_find_pivot_highs_basic() -> None:
    values = [1.0, 2.0, 3.0, 4.0, 3.0, 2.0, 1.0]
    pivots = find_pivot_highs(values, left=2, right=2)
    assert [(p.index, p.value, p.confirmed_index) for p in pivots] == [(3, 4.0, 5)]


def test_pivot_requires_full_side_windows() -> None:
    # 左右窗口不完整的端点不能成为枢轴。
    values = [1.0, 0.5, 1.0, 2.0]
    assert find_pivot_lows(values, left=2, right=2) == []


def test_pivot_tie_takes_leftmost() -> None:
    values = [3.0, 2.0, 2.0, 3.0]
    pivots = find_pivot_lows(values, left=1, right=1)
    assert [p.index for p in pivots] == [1]


def test_pivots_prefix_consistency_no_lookahead() -> None:
    """截断重算：前缀结果必须与全量结果中已确认部分完全一致。"""

    rng = np.random.default_rng(42)
    values = np.sin(np.arange(200) / 9.0) * 10 + rng.normal(0, 0.4, 200)
    full = find_pivot_lows(values, left=5, right=5)
    assert full, "合成数据应产生枢轴"

    for cut in range(20, len(values) + 1, 7):
        prefix = find_pivot_lows(values[:cut], left=5, right=5)
        expected = [p for p in full if p.confirmed_index < cut]
        assert [(p.index, p.value, p.confirmed_index) for p in prefix] == [
            (p.index, p.value, p.confirmed_index) for p in expected
        ]


@pytest.mark.parametrize("bad_values", [np.array([[1.0, 2.0]]), np.array([1.0, np.nan])])
def test_pivots_reject_invalid_input(bad_values: np.ndarray) -> None:
    with pytest.raises(ValueError):
        find_pivot_lows(bad_values, left=2, right=2)


def test_pivots_empty_input_returns_empty() -> None:
    assert find_pivot_lows(np.array([]), left=2, right=2) == []
    assert find_pivot_highs(np.array([]), left=2, right=2) == []


def test_pivots_reject_invalid_windows() -> None:
    with pytest.raises(ValueError):
        find_pivot_lows([1.0, 2.0, 3.0], left=0, right=2)
    with pytest.raises(ValueError):
        find_pivot_lows([1.0, 2.0, 3.0], left=2, right=-1)


# ---------------------------------------------------------------------------
# 配置
# ---------------------------------------------------------------------------


def test_config_defaults_match_plan() -> None:
    config = DivergenceConfig()
    assert config.macd.fast == 12
    assert config.macd.slow == 26
    assert config.macd.signal == 9
    assert config.segment.min_segment_bars == 3
    assert config.segment.confirm_bars == 2
    assert config.segment.zero_tolerance_atr == 0.05
    assert config.gap.min == 5
    assert config.gap.max == 80
    assert config.area_ratio == 0.7
    assert config.require_height_div is False
    assert config.require_area_div is False
    assert config.volume.required is False
    assert config.volume.volume_ratio == 0.8
    assert config.early_signal.enabled is True
    assert config.early_signal.early_bars == 3
    assert config.enable_hidden is False
    assert config.close_confirm_bonus == 5
    assert config.consecutive.allow_second_divergence is True
    assert config.consecutive.second_divergence_bonus == 15


def test_config_rejects_invalid_values() -> None:
    with pytest.raises(ValidationError):
        DivergenceConfig(gap={"min": 10, "max": 5})
    with pytest.raises(ValidationError):
        DivergenceConfig(area_ratio=0)
    with pytest.raises(ValidationError):
        DivergenceConfig(segment={"min_segment_bars": 0, "confirm_bars": 2})
    with pytest.raises(ValidationError):
        DivergenceConfig(unknown_field=1)


# ---------------------------------------------------------------------------
# 柱段切分
# ---------------------------------------------------------------------------


def test_build_segments_splits_by_sign_and_records_attributes() -> None:
    # hist: 五段交替（每段 >= min_segment_bars 避免噪音合并歧义）
    hist = np.array(
        [0.5, 0.4, 0.3, -0.3, -0.5, -0.2, 0.4, 0.3, 0.2, -0.4, -0.3, -0.2, 0.5, 0.4, 0.3]
    )
    candles = make_candles([100.0] * len(hist))
    segments = build_segments(hist, candles, DivergenceConfig())

    signs = [segment.sign for segment in segments]
    assert signs == [1, -1, 1, -1, 1]

    green1 = segments[1]
    assert green1.start_index == 3 and green1.end_index == 5
    assert green1.peak == pytest.approx(0.5)
    assert green1.area == pytest.approx(1.0)
    assert green1.avg_volume == pytest.approx(80.0)


def test_build_segments_merges_noise_segments() -> None:
    # 中间 1 根正柱（长度 < min_segment_bars=3）视为噪音：结构上两侧绿段
    # 连成一段（0..6），但量化属性（面积等）只统计段自身符号的柱根。
    hist = np.array([-0.4, -0.5, -0.3, 0.1, -0.4, -0.3, -0.2, 0.5, 0.4, 0.3])
    candles = make_candles([100.0] * len(hist))
    segments = build_segments(hist, candles, DivergenceConfig())

    assert len(segments) == 2
    merged = segments[0]
    assert merged.sign < 0
    assert merged.start_index == 0 and merged.end_index == 6
    assert merged.indices == (0, 1, 2, 3, 4, 5, 6)
    assert merged.area == pytest.approx(0.4 + 0.5 + 0.3 + 0.4 + 0.3 + 0.2)


def test_build_segments_all_warmup_returns_empty() -> None:
    hist = np.array([np.nan, np.nan, np.nan])
    candles = make_candles([100.0] * 3)
    assert build_segments(hist, candles, DivergenceConfig()) == []


# ---------------------------------------------------------------------------
# 常规背离检测
# ---------------------------------------------------------------------------


def test_detect_bull_regular_outputs_full_contract() -> None:
    candles = bull_two_segment_candles()
    signals = detect_divergences(candles)
    regular = [s for s in signals if not s.preliminary]

    assert regular, "两段绿柱结构必须产生至少一条已确认底背离"
    signal = regular[0]
    assert signal.inst_id == "TEST-USDT-SWAP"
    assert signal.bar == CandleBar.FOUR_HOUR
    assert signal.kind == DivergenceKind.BULL_REGULAR
    assert signal.method == DivergenceMethod.MACD_SEGMENT
    assert 0.0 <= signal.score <= 100.0
    assert signal.divergence_order == 1
    assert signal.preliminary is False
    assert signal.height_div is True
    assert signal.area_div is True
    assert signal.volume_confirm is True
    assert signal.close_confirm is True
    assert signal.seg1_peak > signal.seg2_peak
    assert signal.seg1_area > signal.seg2_area
    assert signal.seg1_avg_vol > signal.seg2_avg_vol
    assert signal.seg2_extreme_price < signal.seg1_extreme_price
    assert signal.seg1_start_ts < signal.seg1_end_ts <= signal.seg2_start_ts < signal.seg2_end_ts
    assert signal.confirmed_ts > signal.seg2_end_ts
    assert signal.confirmed_ts <= candles[-1].ts_ms


def test_detect_bear_regular_mirrors_bull() -> None:
    candles = bear_two_segment_candles()
    signals = [s for s in detect_divergences(candles) if not s.preliminary]
    assert signals, "镜像结构必须产生顶背离"
    signal = signals[0]
    assert signal.kind == DivergenceKind.BEAR_REGULAR
    assert signal.seg2_extreme_price > signal.seg1_extreme_price
    assert signal.confirmed_ts <= candles[-1].ts_ms


def test_detect_no_signal_when_momentum_stronger() -> None:
    signals = detect_divergences(no_divergence_candles())
    assert [s for s in signals if not s.preliminary and s.kind == DivergenceKind.BULL_REGULAR] == []


def test_detect_gap_bounds() -> None:
    candles = bull_two_segment_candles()
    # 实际间隔约 14 根：把窗口设为 (20, 80) 排除，设为 (5, 10) 也排除。
    too_far = DivergenceConfig(gap={"min": 20, "max": 80})
    too_near = DivergenceConfig(gap={"min": 5, "max": 10})
    for config in (too_far, too_near):
        signals = [s for s in detect_divergences(candles, config) if not s.preliminary]
        assert signals == [], f"gap 窗口 {config.gap} 应排除该结构"


def test_detect_volume_required_rejects_without_shrink() -> None:
    closes: list[float] = []
    closes += _linear(90.0, 100.0, 40)
    closes += _linear(100.0, 87.5, 16)
    closes += _linear(87.5, 93.0, 14)
    closes += _linear(93.0, 88.2, 9)
    closes += _linear(88.2, 87.0, 7)
    closes += _linear(87.0, 93.5, 16)
    # 放量创新低：第二段成交量更大
    vols = [80.0] * 40 + [60.0] * 16 + [80.0] * 14 + [100.0] * 16 + [80.0] * 16
    candles = make_candles(closes, vols=vols)

    required = DivergenceConfig(volume={"enabled": True, "required": True, "volume_ratio": 0.8})
    signals = [s for s in detect_divergences(candles, required) if not s.preliminary]
    assert signals == []

    optional = DivergenceConfig(volume={"enabled": True, "required": False, "volume_ratio": 0.8})
    signals = [s for s in detect_divergences(candles, optional) if not s.preliminary]
    assert signals
    assert signals[0].volume_confirm is False


def test_detect_require_both_momentum_flags() -> None:
    candles = bull_two_segment_candles()
    config = DivergenceConfig(require_height_div=True, require_area_div=True)
    signals = [s for s in detect_divergences(candles, config) if not s.preliminary]
    assert signals and signals[0].height_div and signals[0].area_div


def test_detect_second_and_third_divergence_order() -> None:
    three = [s for s in detect_divergences(bull_three_segment_candles()) if not s.preliminary]
    orders = [s.divergence_order for s in three if s.kind == DivergenceKind.BULL_REGULAR]
    assert orders == [1, 2]
    second = [s for s in three if s.divergence_order == 2]
    assert second, "三段绿柱结构必须产生二次背离"

    four = [s for s in detect_divergences(bull_four_segment_candles()) if not s.preliminary]
    orders = [s.divergence_order for s in four if s.kind == DivergenceKind.BULL_REGULAR]
    assert orders == [1, 2, 3]


def test_detect_second_divergence_disabled() -> None:
    config = DivergenceConfig(consecutive={"allow_second_divergence": False})
    signals = [
        s for s in detect_divergences(bull_three_segment_candles(), config) if not s.preliminary
    ]
    orders = [s.divergence_order for s in signals]
    assert orders == [1]


def test_detect_extreme_price_source_close() -> None:
    # 使用收盘价口径时，影线针尖不再触发价格条件：抬高 G2 收盘价最低点。
    closes: list[float] = []
    closes += _linear(90.0, 100.0, 40)
    closes += _linear(100.0, 87.5, 16)
    closes += _linear(87.5, 93.0, 14)
    closes += _linear(93.0, 88.6, 9)
    closes += _linear(88.6, 88.2, 7)  # 收盘价不再创新低（88.2 > 87.5）
    closes += _linear(88.2, 93.5, 16)
    candles = make_candles(closes, wick=1.5, vols=[80.0] * len(closes))

    close_source = DivergenceConfig(extreme_price_source="close")
    signals = [
        s
        for s in detect_divergences(candles, close_source)
        if not s.preliminary and s.kind == DivergenceKind.BULL_REGULAR
    ]
    assert signals == [], "收盘价口径下价格未创新低不应产生背离"


def test_detect_short_data_returns_empty() -> None:
    assert detect_divergences(make_candles(_linear(90.0, 95.0, 10))) == []


def test_detect_hidden_divergence_toggle() -> None:
    # 隐藏底背离：价格低点抬高 + 指标低点降低。
    closes: list[float] = []
    closes += _linear(90.0, 100.0, 40)
    closes += _linear(100.0, 85.0, 16)  # G1 深跌
    closes += _linear(85.0, 92.0, 14)
    closes += _linear(92.0, 87.0, 14)  # G2 低点抬高（87 > 85）但柱更弱
    closes += _linear(87.0, 93.0, 16)
    candles = make_candles(closes)

    default = detect_divergences(candles)
    assert all(s.kind != DivergenceKind.BULL_HIDDEN for s in default)

    enabled = detect_divergences(candles, DivergenceConfig(enable_hidden=True))
    hidden = [s for s in enabled if s.kind == DivergenceKind.BULL_HIDDEN]
    assert hidden
    assert hidden[0].seg2_extreme_price > hidden[0].seg1_extreme_price


# ---------------------------------------------------------------------------
# 输入校验与外部依赖
# ---------------------------------------------------------------------------


def test_detect_rejects_empty_input() -> None:
    with pytest.raises(DivergenceInputError, match="empty"):
        detect_divergences([])


def test_detect_rejects_unconfirmed_candles() -> None:
    candles = make_candles(_linear(90.0, 100.0, 60))
    broken = [c.model_copy(update={"confirmed": False}) for c in candles]
    with pytest.raises(DivergenceInputError, match="confirmed"):
        detect_divergences(broken)


def test_detect_rejects_unordered_candles() -> None:
    candles = make_candles(_linear(90.0, 100.0, 60))
    shuffled = list(candles)
    shuffled[10], shuffled[11] = shuffled[11], shuffled[10]
    with pytest.raises(DivergenceInputError, match="order"):
        detect_divergences(shuffled)


def test_detect_rejects_mixed_bar() -> None:
    daily = make_candles(_linear(90.0, 100.0, 60), bar=CandleBar.ONE_DAY)
    mixed = [c.model_copy(update={"bar": CandleBar.ONE_HOUR}) for c in daily[:5]] + list(daily[5:])
    with pytest.raises(DivergenceInputError, match="bar"):
        detect_divergences(mixed)


def test_detect_rejects_mixed_instrument() -> None:
    candles = make_candles(_linear(90.0, 100.0, 60))
    mixed = [c.model_copy(update={"inst_id": "OTHER-USDT-SWAP"}) for c in candles[:5]] + list(
        candles[5:]
    )
    with pytest.raises(DivergenceInputError, match="inst_id"):
        detect_divergences(mixed)


def test_detect_propagates_indicator_failure() -> None:
    # 外部依赖（M03 指标）对 NaN 输入抛错时必须向上传播，不能静默吞掉。
    candles = make_candles(_linear(90.0, 100.0, 60))
    poisoned = [
        c.model_copy(update={"close": Decimal("NaN") if i == 30 else c.close})
        for i, c in enumerate(candles)
    ]
    with pytest.raises(ValueError):
        detect_divergences(poisoned)


# ---------------------------------------------------------------------------
# 未来函数一致性（交付标准）
# ---------------------------------------------------------------------------


def test_prefix_recalculation_matches_full_result() -> None:
    """截断数据逐根重算必须与全量计算完全一致（正式信号）。

    早期提示（``preliminary=True``）是数据末根的即时快照：只对"当前
    正在形成"的末端柱段有效，历史回看时该段已结束，天然无法在全量计算
    中复现，因此不参与一致性比较；其 ``confirmed_ts`` 恒等于截断末根。
    """

    datasets = {
        "bull": bull_two_segment_candles(),
        "three": bull_three_segment_candles(),
    }
    for name, candles in datasets.items():
        full = detect_divergences(candles)

        def key(signal: Any) -> tuple[int, str, bool]:
            return (signal.confirmed_ts, signal.kind, signal.preliminary)

        full_map = {key(s): s for s in full if not s.preliminary}
        for cut in range(1, len(candles) + 1):
            prefix_signals = detect_divergences(candles[:cut])
            last_ts = candles[cut - 1].ts_ms
            expected_keys = {k for k in full_map if k[0] <= last_ts}
            regular = [s for s in prefix_signals if not s.preliminary]
            assert set(map(key, regular)) == expected_keys, f"{name} 前缀 {cut} 与全量结果不一致"
            for signal in regular:
                twin = full_map[key(signal)]
                assert signal.model_dump() == twin.model_dump(), (
                    f"{name} 前缀 {cut} 的信号字段与全量计算不一致"
                )
            for signal in prefix_signals:
                if signal.preliminary:
                    assert signal.confirmed_ts == last_ts, (
                        f"{name} 前缀 {cut} 的早期提示必须锚定在截断末根"
                    )


# ---------------------------------------------------------------------------
# 早期提示
# ---------------------------------------------------------------------------


def test_early_signal_preliminary_notification() -> None:
    candles = bull_two_segment_candles()
    # 截断在 G2 段末尾（尚无确认反弹根），绿柱已连续缩短且价格创新低。
    cut = 40 + 16 + 14 + 16
    truncated = candles[:cut]
    signals = detect_divergences(truncated)

    preliminary = [s for s in signals if s.preliminary]
    assert preliminary
    signal = preliminary[0]
    assert signal.kind == DivergenceKind.BULL_REGULAR
    assert signal.confirmed_ts == truncated[-1].ts_ms
    assert signal.seg2_extreme_price < signal.seg1_extreme_price
    # 早期提示只用于通知：正式信号在截断数据中不存在。
    assert [s for s in signals if not s.preliminary] == []


def test_early_signal_disabled() -> None:
    candles = bull_two_segment_candles()
    cut = 40 + 16 + 14 + 12
    config = DivergenceConfig(early_signal={"enabled": False, "early_bars": 3})
    signals = detect_divergences(candles[:cut], config)
    assert [s for s in signals if s.preliminary] == []


# ---------------------------------------------------------------------------
# 评分
# ---------------------------------------------------------------------------


def _factors(**overrides: Any) -> ScoreFactors:
    base = dict(
        move_atr=1.0,
        height_ratio=0.5,
        area_ratio=0.5,
        volume_ratio=0.6,
        volume_threshold=0.8,
        close_confirm=False,
        divergence_order=1,
        resonance=False,
    )
    base.update(overrides)
    return ScoreFactors(**base)


def test_compute_score_base_factors() -> None:
    # move=1.0(满)15 + height 0.5*20=10 + area 0.5*25=12.5 + vol (1-0.6)*15=6 + 新鲜度10
    score = compute_signal_score(_factors(), close_bonus=5, second_bonus=15)
    assert score == pytest.approx(15 + 10 + 12.5 + 6 + 10)

    full = compute_signal_score(
        _factors(close_confirm=True, divergence_order=2), close_bonus=5, second_bonus=15
    )
    assert full == pytest.approx(53.5 + 5 + 15)


def test_compute_score_price_move_band() -> None:
    score_low = compute_signal_score(_factors(move_atr=0.25), close_bonus=5, second_bonus=15)
    score_mid = compute_signal_score(_factors(move_atr=1.25), close_bonus=5, second_bonus=15)
    score_high = compute_signal_score(_factors(move_atr=4.0), close_bonus=5, second_bonus=15)
    assert score_mid > score_low
    assert score_mid > score_high
    assert score_high >= 0


def test_compute_score_capped_at_100() -> None:
    score = compute_signal_score(
        _factors(
            move_atr=1.0,
            height_ratio=1.0,
            area_ratio=1.0,
            volume_ratio=0.0,
            close_confirm=True,
            divergence_order=2,
            resonance=True,
        ),
        close_bonus=5,
        second_bonus=15,
    )
    assert score == 100.0


def test_score_close_confirm_differential_end_to_end() -> None:
    base = bull_two_segment_candles()
    with_close = [s for s in detect_divergences(base) if not s.preliminary][0]

    # 构造收盘价不创新低的对照组：仅在 G2 最低那根加长下影线，使针尖仍创新低。
    closes: list[float] = []
    closes += _linear(90.0, 100.0, 40)
    closes += _linear(100.0, 87.5, 16)
    closes += _linear(87.5, 93.0, 14)
    closes += _linear(93.0, 88.6, 9)
    closes += _linear(88.6, 88.3, 7)  # 收盘最低 88.3 > 87.5，不创新低
    closes += _linear(88.3, 93.5, 16)
    default_wick = 0.8
    wicks = [default_wick] * len(closes)
    wicks[40 + 16 + 14 + 9 + 6] = 2.0  # G2 最低收盘那根：low 86.3 < G1 low 86.7
    no_close = make_candles(closes, wick=wicks, vols=[80.0] * len(closes))
    signals = [s for s in detect_divergences(no_close) if not s.preliminary]

    assert signals, "影线创新低仍应产生背离"
    assert with_close.close_confirm is True
    assert signals[0].close_confirm is False
    # close_confirm 的 +5 是确定贡献；两组数据的段切分略有差异，
    # 其余因子变化方向不定，因此只约束差值下界（精确差值由评分单测覆盖）。
    delta = with_close.score - signals[0].score
    assert delta >= 5.0 - 1e-9


# ---------------------------------------------------------------------------
# 幂等与契约兼容
# ---------------------------------------------------------------------------


def test_detect_is_deterministic() -> None:
    candles = bull_two_segment_candles()
    first = detect_divergences(candles)
    second = detect_divergences(candles)
    assert [s.model_dump() for s in first] == [s.model_dump() for s in second]


def test_signal_fields_feed_downstream_contract() -> None:
    # M05/M07 依赖的关键字段必须可直接用于组合池与策略判断。
    signals = [s for s in detect_divergences(bull_two_segment_candles()) if not s.preliminary]
    signal = signals[0]
    assert isinstance(signal.kind, DivergenceKind)
    assert isinstance(signal.confirmed_ts, int)
    assert isinstance(signal.seg2_extreme_price, Decimal)
    assert signal.bar.value == "4H"


# ---------------------------------------------------------------------------
# 脚本：样本导出与参数校准
# ---------------------------------------------------------------------------

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS_DIR = ROOT / "scripts"
FIXTURES_DIR = ROOT / "tests" / "fixtures"


def _load_script(name: str) -> Any:
    path = SCRIPTS_DIR / f"{name}.py"
    assert path.is_file(), f"missing script: {path}"
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    # 注册进 sys.modules，模块内 dataclass 才能解析自身注解。
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def test_export_samples_renders_annotated_svg() -> None:
    export = _load_script("export_samples")
    candles = bull_two_segment_candles()
    svg = export.render_sample_svg(candles, detect_divergences(candles), inst_id="TEST-USDT-SWAP")
    assert svg.startswith("<svg")
    assert "MACD" in svg
    assert svg.count("seg-highlight") >= 2  # 两段柱子高亮
    assert "extreme-line" in svg  # 价格极值连线


def test_export_samples_cli_writes_files(tmp_path: Path) -> None:
    export = _load_script("export_samples")
    source = FIXTURES_DIR / "synthetic_candles_4h.csv"
    assert source.is_file(), "fixture 合成 K 线必须存在"
    written = export.main(["--input", str(source), "--output-dir", str(tmp_path)])
    assert written >= 1
    files = list(tmp_path.glob("*.svg"))
    assert files


def test_calibrate_reports_recall_and_best_params(tmp_path: Path) -> None:
    calibrate = _load_script("calibrate_divergence")
    golden = FIXTURES_DIR / "golden_divergences.csv"
    assert golden.is_file()

    report_path = tmp_path / "tuning.md"
    report = calibrate.main(["--golden", str(golden), "--output", str(report_path)])
    assert report.best_params is not None
    assert report.golden_total >= 1
    assert report.recall >= 0.8
    assert report_path.is_file()
    assert "召回率" in report_path.read_text(encoding="utf-8")
