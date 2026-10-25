"""M03 趋势状态分类器单元测试。

覆盖：
1. 上涨 / 下跌 / 震荡三态判定及指标数值。
2. 连续 3 根 K 线防抖与 bars_in_state 统计。
3. 数据校验：未收盘 K 线、乱序时间戳、交易对不一致、数据不足。
4. 因果性：追加未来 K 线不改变历史分类结果。
5. SMA200 / EMA200 双轨输出。
"""

import math
from decimal import Decimal

import numpy as np
import pytest

from dquant.contracts import Candle, CandleBar, RegimeResult, RegimeState
from dquant.core.indicators import sma
from dquant.core.regime import (
    InsufficientCandlesError,
    RegimeConfig,
    classify_market_state,
    classify_regime,
    classify_regime_all,
    debounce_states,
    regime_series,
)

BASE_TS_MS = 1_700_000_000_000
DAY_MS = 86_400_000


def _candles(
    closes,
    *,
    inst_id: str = "BTC-USDT-SWAP",
    bar: CandleBar = CandleBar.ONE_DAY,
    base_ts_ms: int = BASE_TS_MS,
) -> list[Candle]:
    """由收盘价序列构造合法的日线 K 线列表。"""

    candles: list[Candle] = []
    for index, close in enumerate(closes):
        value = Decimal(str(float(close)))
        candles.append(
            Candle(
                inst_id=inst_id,
                bar=bar,
                ts_ms=base_ts_ms + index * DAY_MS,
                open=value,
                high=value * Decimal("1.004"),
                low=value * Decimal("0.996"),
                close=value,
                volume=Decimal("1000"),
                quote_volume=Decimal("100000"),
                confirmed=True,
            )
        )
    return candles


def _uptrend(size: int = 260, growth: float = 0.006) -> list[float]:
    return [100.0 * (1.0 + growth) ** index for index in range(size)]


def _downtrend(size: int = 260, decay: float = 0.006) -> list[float]:
    return [100.0 * (1.0 - decay) ** index for index in range(size)]


def _sideways(size: int = 300) -> list[float]:
    return [100.0 + 2.0 * math.sin(index / 6.0) for index in range(size)]


# ---------------------------------------------------------------------------
# 三态判定
# ---------------------------------------------------------------------------


def test_uptrend_is_classified() -> None:
    candles = _candles(_uptrend())
    result = classify_regime("BTC-USDT-SWAP", CandleBar.ONE_DAY, candles)

    assert isinstance(result, RegimeResult)
    assert result.state is RegimeState.UPTREND
    assert result.ma_type == "SMA"
    assert result.dist > 0
    assert result.slope > 0.01
    assert result.above_ratio == pytest.approx(1.0)
    assert result.cross_count == 0
    assert result.bars_in_state > 1
    assert result.ts_ms == candles[-1].ts_ms
    assert result.inst_id == "BTC-USDT-SWAP"
    assert result.bar is CandleBar.ONE_DAY


def test_downtrend_is_classified() -> None:
    candles = _candles(_downtrend())
    result = classify_regime("BTC-USDT-SWAP", CandleBar.ONE_DAY, candles)

    assert result.state is RegimeState.DOWNTREND
    assert result.dist < 0
    assert result.slope < -0.01
    assert result.above_ratio == pytest.approx(0.0)


def test_sideways_market_is_range() -> None:
    candles = _candles(_sideways())
    result = classify_regime("BTC-USDT-SWAP", CandleBar.ONE_DAY, candles)
    assert result.state is RegimeState.RANGE


def test_ema_track_is_available() -> None:
    candles = _candles(_uptrend())
    result = classify_regime("BTC-USDT-SWAP", CandleBar.ONE_DAY, candles, ma_type="EMA")
    assert result.ma_type == "EMA"
    assert result.state is RegimeState.UPTREND


def test_classify_regime_all_returns_both_tracks() -> None:
    candles = _candles(_uptrend())
    results = classify_regime_all("BTC-USDT-SWAP", CandleBar.ONE_DAY, candles)

    assert set(results) == {"SMA", "EMA"}
    assert results["SMA"].ma_type == "SMA"
    assert results["EMA"].ma_type == "EMA"
    assert results["SMA"].state is RegimeState.UPTREND
    assert results["EMA"].state is RegimeState.UPTREND
    # 两条轨道使用不同均线，偏离度不应完全相同
    assert abs(results["EMA"].dist - results["SMA"].dist) > 1e-9


# ---------------------------------------------------------------------------
# 单点判定规则
# ---------------------------------------------------------------------------


@pytest.fixture()
def rule_config() -> RegimeConfig:
    return RegimeConfig()


def _state(**overrides):
    base = {
        "close": 110.0,
        "ma": 100.0,
        "slope": 0.02,
        "above_ratio": 0.9,
        "cross_count": 0,
        "adx": 30.0,
    }
    base.update(overrides)
    return base


def test_rule_uptrend(rule_config: RegimeConfig) -> None:
    assert classify_market_state(config=rule_config, **_state()) is RegimeState.UPTREND


def test_rule_downtrend(rule_config: RegimeConfig) -> None:
    assert (
        classify_market_state(
            config=rule_config,
            **_state(close=90.0, slope=-0.02, above_ratio=0.1),
        )
        is RegimeState.DOWNTREND
    )


@pytest.mark.parametrize(
    "overrides",
    [
        {"above_ratio": 0.79},  # 上涨比例不足
        {"close": 99.0},  # 价格不在 MA 上方
        {"slope": 0.01},  # 斜率必须严格大于阈值
        {"cross_count": 3},  # 穿越次数过多，强制震荡
        {"cross_count": 5},
    ],
)
def test_rule_defaults_to_range(rule_config: RegimeConfig, overrides: dict) -> None:
    assert classify_market_state(config=rule_config, **_state(**overrides)) is RegimeState.RANGE


def test_rule_down_requires_low_above_ratio(rule_config: RegimeConfig) -> None:
    assert (
        classify_market_state(
            config=rule_config, **_state(close=90.0, slope=-0.02, above_ratio=0.2)
        )
        is RegimeState.DOWNTREND
    )
    assert (
        classify_market_state(
            config=rule_config, **_state(close=90.0, slope=-0.02, above_ratio=0.21)
        )
        is RegimeState.RANGE
    )


def test_rule_low_slope_and_low_adx_is_range(rule_config: RegimeConfig) -> None:
    assert (
        classify_market_state(config=rule_config, **_state(slope=0.005, adx=10.0))
        is RegimeState.RANGE
    )
    # 斜率低于阈值时本身就不满足趋势条件，ADX 分支只是显式震荡规则
    assert (
        classify_market_state(config=rule_config, **_state(slope=0.005, adx=40.0))
        is RegimeState.RANGE
    )
    assert (
        classify_market_state(config=rule_config, **_state(slope=0.005, adx=None))
        is RegimeState.RANGE
    )


def test_rule_rejects_invalid_metrics(rule_config: RegimeConfig) -> None:
    with pytest.raises(ValueError):
        classify_market_state(config=rule_config, **_state(above_ratio=1.2))
    with pytest.raises(ValueError):
        classify_market_state(config=rule_config, **_state(above_ratio=-0.1))
    with pytest.raises(ValueError):
        classify_market_state(config=rule_config, **_state(cross_count=-1))
    with pytest.raises(ValueError):
        classify_market_state(config=rule_config, **_state(ma=0.0))


# ---------------------------------------------------------------------------
# 防抖
# ---------------------------------------------------------------------------


def test_debounce_initial_state_is_applied_immediately() -> None:
    states, bars = debounce_states([RegimeState.UPTREND] * 2, 3)
    assert states == [RegimeState.UPTREND, RegimeState.UPTREND]
    assert bars == [1, 2]


def test_debounce_suppresses_two_bar_blip() -> None:
    raw = [
        RegimeState.UPTREND,
        RegimeState.UPTREND,
        RegimeState.RANGE,
        RegimeState.RANGE,
        RegimeState.UPTREND,
    ]
    states, bars = debounce_states(raw, 3)
    assert states == [RegimeState.UPTREND] * 5
    assert bars == [1, 2, 3, 4, 5]


def test_debounce_switches_after_three_consecutive_bars() -> None:
    raw = [RegimeState.UPTREND] * 3 + [RegimeState.DOWNTREND] * 4
    states, bars = debounce_states(raw, 3)
    assert states == [RegimeState.UPTREND] * 5 + [RegimeState.DOWNTREND] * 2
    assert bars == [1, 2, 3, 4, 5, 1, 2]


def test_debounce_pending_state_resets_when_raw_state_returns() -> None:
    raw = [
        RegimeState.UPTREND,
        RegimeState.UPTREND,
        RegimeState.DOWNTREND,
        RegimeState.DOWNTREND,
        RegimeState.UPTREND,
    ]
    states, _ = debounce_states(raw, 3)
    assert states == [RegimeState.UPTREND] * 5


def test_debounce_confirm_bars_one_switches_immediately() -> None:
    raw = [RegimeState.UPTREND, RegimeState.UPTREND, RegimeState.RANGE]
    states, bars = debounce_states(raw, 1)
    assert states == raw
    assert bars == [1, 2, 1]


def test_debounce_empty_input() -> None:
    states, bars = debounce_states([], 3)
    assert states == []
    assert bars == []


def test_debounce_rejects_invalid_confirm_bars() -> None:
    with pytest.raises(ValueError):
        debounce_states([RegimeState.UPTREND], 0)


# ---------------------------------------------------------------------------
# 序列输出、预热期与因果性
# ---------------------------------------------------------------------------


def test_series_starts_after_full_warmup_and_aligns_timestamps() -> None:
    candles = _candles(_uptrend(260))
    results = regime_series("BTC-USDT-SWAP", CandleBar.ONE_DAY, candles)

    assert len(results) == 260 - 219
    assert results[0].ts_ms == candles[219].ts_ms
    assert results[-1].ts_ms == candles[-1].ts_ms
    assert all(result.state is RegimeState.UPTREND for result in results)


def test_minimum_candles_are_enforced() -> None:
    candles = _candles(_uptrend(219))
    with pytest.raises(InsufficientCandlesError) as excinfo:
        classify_regime("BTC-USDT-SWAP", CandleBar.ONE_DAY, candles)
    assert excinfo.value.required == 220
    assert excinfo.value.actual == 219

    with pytest.raises(InsufficientCandlesError):
        classify_regime("BTC-USDT-SWAP", CandleBar.ONE_DAY, [])


def test_exactly_enough_candles_produces_one_result() -> None:
    candles = _candles(_uptrend(220))
    results = regime_series("BTC-USDT-SWAP", CandleBar.ONE_DAY, candles)
    assert len(results) == 1


def test_small_config_metrics_are_correct() -> None:
    config = RegimeConfig(
        ma_period=3,
        slope_lookback=2,
        window=4,
        adx_period=2,
        confirm_bars=1,
    )
    closes = [10, 11, 12, 11, 10, 11, 12, 13, 12, 11, 10, 9, 10, 11, 12, 13, 14, 13, 12, 11]
    candles = _candles(closes)
    result = classify_regime("BTC-USDT-SWAP", CandleBar.ONE_DAY, candles, config=config)

    close = np.array(closes, dtype=float)
    ma = sma(close, 3)
    index = len(closes) - 1
    expected_dist = (close[index] - ma[index]) / ma[index]
    expected_slope = (ma[index] - ma[index - 2]) / ma[index - 2]
    expected_ratio = float(np.mean(close[index - 3 : index + 1] > ma[index - 3 : index + 1]))

    assert result.dist == pytest.approx(expected_dist, rel=1e-12)
    assert result.slope == pytest.approx(expected_slope, rel=1e-12)
    assert result.above_ratio == pytest.approx(expected_ratio, rel=1e-12)
    assert result.above_ratio == pytest.approx(0.25)
    assert result.cross_count == 1


def test_series_is_causal() -> None:
    candles = _candles(_uptrend(280))
    full = regime_series("BTC-USDT-SWAP", CandleBar.ONE_DAY, candles)
    prefix = regime_series("BTC-USDT-SWAP", CandleBar.ONE_DAY, candles[:250])

    cutoff_ts = candles[249].ts_ms
    full_prefix = [result for result in full if result.ts_ms <= cutoff_ts]
    assert len(full_prefix) == len(prefix)
    for left, right in zip(full_prefix, prefix, strict=True):
        assert left.model_dump() == right.model_dump()


def test_series_is_deterministic() -> None:
    candles = _candles(_sideways())
    first = regime_series("BTC-USDT-SWAP", CandleBar.ONE_DAY, candles)
    second = regime_series("BTC-USDT-SWAP", CandleBar.ONE_DAY, candles)
    assert [item.model_dump() for item in first] == [item.model_dump() for item in second]


def test_bars_in_state_does_not_exceed_series_length() -> None:
    candles = _candles(_sideways(280))
    results = regime_series("BTC-USDT-SWAP", CandleBar.ONE_DAY, candles)
    for position, result in enumerate(results, start=1):
        assert 1 <= result.bars_in_state <= position


def test_primary_ma_default_is_sma() -> None:
    assert RegimeConfig().primary_ma == "SMA"


# ---------------------------------------------------------------------------
# 输入校验
# ---------------------------------------------------------------------------


def test_unconfirmed_candles_are_rejected() -> None:
    candles = _candles(_uptrend(220))
    candles[100] = candles[100].model_copy(update={"confirmed": False})
    with pytest.raises(ValueError, match="confirmed"):
        regime_series("BTC-USDT-SWAP", CandleBar.ONE_DAY, candles)


def test_mismatched_inst_id_is_rejected() -> None:
    candles = _candles(_uptrend(220))
    with pytest.raises(ValueError, match="inst_id"):
        regime_series("ETH-USDT-SWAP", CandleBar.ONE_DAY, candles)


def test_mismatched_bar_is_rejected() -> None:
    candles = _candles(_uptrend(220), bar=CandleBar.ONE_DAY)
    with pytest.raises(ValueError, match="bar"):
        regime_series("BTC-USDT-SWAP", CandleBar.FOUR_HOUR, candles)


def test_unsorted_timestamps_are_rejected() -> None:
    candles = _candles(_uptrend(220))
    candles[10], candles[11] = candles[11], candles[10]
    with pytest.raises(ValueError, match="ts_ms"):
        regime_series("BTC-USDT-SWAP", CandleBar.ONE_DAY, candles)


def test_duplicate_timestamps_are_rejected() -> None:
    candles = _candles(_uptrend(220))
    candles[11] = candles[11].model_copy(update={"ts_ms": candles[10].ts_ms})
    with pytest.raises(ValueError, match="ts_ms"):
        regime_series("BTC-USDT-SWAP", CandleBar.ONE_DAY, candles)


def test_non_candle_input_is_rejected() -> None:
    with pytest.raises(TypeError):
        regime_series("BTC-USDT-SWAP", CandleBar.ONE_DAY, [{"close": 1.0}] * 220)


def test_invalid_ma_type_is_rejected() -> None:
    candles = _candles(_uptrend(220))
    with pytest.raises(ValueError, match="ma_type"):
        regime_series("BTC-USDT-SWAP", CandleBar.ONE_DAY, candles, ma_type="WMA")


def test_adx_length_mismatch_is_rejected() -> None:
    candles = _candles(_uptrend(220))
    with pytest.raises(ValueError, match="adx"):
        regime_series(
            "BTC-USDT-SWAP",
            CandleBar.ONE_DAY,
            candles,
            adx=np.zeros(len(candles) - 1),
        )


def test_custom_adx_with_nan_is_accepted() -> None:
    candles = _candles(_uptrend(220))
    adx_values = np.full(len(candles), np.nan)
    result = classify_regime("BTC-USDT-SWAP", CandleBar.ONE_DAY, candles, adx=adx_values)
    assert result.state is RegimeState.UPTREND


@pytest.mark.parametrize(
    "overrides",
    [
        {"ma_period": 1},
        {"slope_lookback": 0},
        {"window": 0},
        {"slope_threshold": 0.0},
        {"above_ratio_up": 1.2},
        {"above_ratio_down": -0.1},
        {"above_ratio_up": 0.2, "above_ratio_down": 0.8},
        {"max_cross_count": 0},
        {"adx_period": 0},
        {"adx_range_threshold": 0.0},
        {"confirm_bars": 0},
        {"ma_types": ()},
        {"ma_types": ("SMA", "WMA")},
        {"primary_ma": "WMA"},
        {"ma_types": ("SMA",), "primary_ma": "EMA"},
    ],
)
def test_config_validation(overrides: dict) -> None:
    with pytest.raises(ValueError):
        RegimeConfig(**overrides)
