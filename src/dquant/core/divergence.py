"""顶底背离检测器（计划书 §5.6，MACD 柱段主方法）。

检测流程：
1. 调用 M03 指标库计算 MACD 柱状图（``hist = DIF - DEA``）与 ATR；
2. 按正负号把 hist 切成红/绿柱段，短于 ``min_segment_bars`` 的噪音段并入相邻段；
3. 柱段结束（hist 翻转后持续 ``confirm_bars`` 根）才确认，正式信号的
   ``confirmed_ts`` 等于确认 K 线收盘时间，杜绝未来函数；
4. 相邻两段比较价格极值（默认含上下影线）、柱峰值、柱面积与成交量，
   判定常规/隐藏背离并按 §5.6.4 评分；
5. 相邻背离链（G0→G1→G2）标记二次背离 ``divergence_order``；
6. 数据末端柱段连续缩短且价格创新低时输出 ``preliminary=True`` 早期提示，
   只用于通知，不作为正式信号。

评分的"新鲜度"因子在确认时刻恒为满分：信号生成后所有字段不再随时间变化，
从而保证"截断数据逐根重算与全量计算结果一致"；历史信号的新鲜度衰减属于
选币器（M05）的职责。

本模块是纯函数：不访问数据库、不访问网络、不修改全局状态。
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal
from typing import Literal

import numpy as np
from pydantic import BaseModel, ConfigDict, Field, model_validator

from dquant.contracts import Candle, DivergenceSignal
from dquant.contracts.types import DivergenceKind, DivergenceMethod
from dquant.core.indicators import atr, macd, rsi
from dquant.core.pivots import find_pivot_lows

_ATR_PERIOD = 14
_PIVOT_PROXIMITY_BARS = 3


class DivergenceInputError(ValueError):
    """背离检测输入不满足公共契约或防未来函数要求。"""


class MacdParams(BaseModel):
    """MACD 参数。"""

    model_config = ConfigDict(extra="forbid")

    fast: int = Field(default=12, ge=1)
    slow: int = Field(default=26, ge=2)
    signal: int = Field(default=9, ge=1)

    @model_validator(mode="after")
    def validate_fast_slow(self) -> MacdParams:
        if self.fast >= self.slow:
            raise ValueError("fast must be less than slow")
        return self


class SegmentSettings(BaseModel):
    """柱段切分参数。"""

    model_config = ConfigDict(extra="forbid")

    min_segment_bars: int = Field(default=3, ge=1)
    confirm_bars: int = Field(default=2, ge=1)
    zero_tolerance_atr: float = Field(default=0.05, gt=0)


class GapSettings(BaseModel):
    """两段柱段之间的间隔窗口（根）。"""

    model_config = ConfigDict(extra="forbid")

    min: int = Field(default=5, ge=0)
    max: int = Field(default=80, ge=0)

    @model_validator(mode="after")
    def validate_range(self) -> GapSettings:
        if self.min > self.max:
            raise ValueError("gap.min must be less than or equal to gap.max")
        return self


class VolumeSettings(BaseModel):
    """成交量配合参数。"""

    model_config = ConfigDict(extra="forbid")

    enabled: bool = True
    required: bool = False
    volume_ratio: float = Field(default=0.8, gt=0)

    @model_validator(mode="after")
    def validate_required(self) -> VolumeSettings:
        if self.required and not self.enabled:
            raise ValueError("volume.required requires volume.enabled")
        return self


class EarlySignalSettings(BaseModel):
    """早期提示参数。"""

    model_config = ConfigDict(extra="forbid")

    enabled: bool = True
    early_bars: int = Field(default=3, ge=1)


class ConsecutiveSettings(BaseModel):
    """二次背离（背了又背）参数。"""

    model_config = ConfigDict(extra="forbid")

    allow_second_divergence: bool = True
    second_divergence_bonus: float = Field(default=15, ge=0)


class RsiContrastSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool = False
    period: int = Field(default=14, ge=1)


class DifContrastSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool = False


class PivotContrastSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool = False
    left: int = Field(default=5, ge=1)
    right: int = Field(default=5, ge=1)


class OptionalIndicators(BaseModel):
    """可选对照指标（默认关闭，仅参与共振加分）。"""

    model_config = ConfigDict(extra="forbid")

    rsi: RsiContrastSettings = Field(default_factory=RsiContrastSettings)
    dif: DifContrastSettings = Field(default_factory=DifContrastSettings)
    pivot: PivotContrastSettings = Field(default_factory=PivotContrastSettings)


class DivergenceConfig(BaseModel):
    """背离检测配置，默认值来自计划书 §8 ``divergence`` 段。

    说明：``AppSettings``（M01）尚未纳入 divergence 配置段，本模型先作为
    模块内配置使用；申请纳入公共配置的记录见 HANDOFF。
    """

    model_config = ConfigDict(extra="forbid")

    macd: MacdParams = Field(default_factory=MacdParams)
    segment: SegmentSettings = Field(default_factory=SegmentSettings)
    gap: GapSettings = Field(default_factory=GapSettings)
    area_ratio: float = Field(default=0.7, gt=0, le=2)
    require_height_div: bool = False
    require_area_div: bool = False
    extreme_price_source: Literal["high_low", "close"] = "high_low"
    close_confirm_bonus: float = Field(default=5, ge=0)
    consecutive: ConsecutiveSettings = Field(default_factory=ConsecutiveSettings)
    volume: VolumeSettings = Field(default_factory=VolumeSettings)
    early_signal: EarlySignalSettings = Field(default_factory=EarlySignalSettings)
    optional_indicators: OptionalIndicators = Field(default_factory=OptionalIndicators)
    enable_hidden: bool = False


@dataclass(frozen=True, slots=True)
class Segment:
    """一个已完成切分的 MACD 柱段。"""

    sign: int  # +1 红柱段（hist > 0），-1 绿柱段（hist < 0）
    indices: tuple[int, ...]  # 段内（含并入的噪音根）K 线下标，升序
    start_index: int
    end_index: int
    start_ts: int
    end_ts: int
    extreme_index: int  # 价格极值所在 K 线
    extreme_ts: int
    extreme_price: Decimal  # 绿段最低价 / 红段最高价（按 extreme_price_source）
    extreme_close: Decimal  # 收盘价口径极值
    peak: float  # 段内 |hist| 最大值
    area: float  # 段内 Σ|hist|
    avg_volume: float


@dataclass(frozen=True, slots=True)
class ScoreFactors:
    """背离强度评分的输入因子（§5.6.4）。"""

    move_atr: float  # 价格创新低/高幅度，ATR 倍数
    height_ratio: float  # 1 - 柱峰值比
    area_ratio: float  # 1 - 柱面积比
    volume_ratio: float  # 第二段均量 / 第一段均量
    volume_threshold: float  # 缩量确认阈值快照（volume_ratio 配置值）
    close_confirm: bool
    divergence_order: int
    resonance: bool  # 多指标共振加分


def _clamp01(value: float) -> float:
    return max(0.0, min(1.0, value))


def _price_move_score(move_atr: float) -> float:
    """价格幅度因子：幅度适中（0.5~2 ATR）最佳，过大说明趋势太强。"""

    if move_atr <= 0.0:
        return 0.0
    if move_atr < 0.5:
        return 15.0 * move_atr / 0.5
    if move_atr <= 2.0:
        return 15.0
    return max(0.0, 15.0 * (1.0 - (move_atr - 2.0) / 2.0))


def compute_signal_score(
    factors: ScoreFactors,
    *,
    close_bonus: float = 5.0,
    second_bonus: float = 15.0,
) -> float:
    """按 §5.6.4 权重表计算 0~100 分。

    权重：价格幅度 15 / 柱高度 20 / 柱面积 25 / 成交量 15 / 新鲜度 10 /
    多指标共振 15；收盘价同步创新低/高 +close_bonus；二次背离 +second_bonus。
    新鲜度在确认时刻恒为满分（见模块 docstring）。
    """

    total = (
        _price_move_score(factors.move_atr)
        + 20.0 * _clamp01(factors.height_ratio)
        + 25.0 * _clamp01(factors.area_ratio)
        + 15.0 * _clamp01(1.0 - factors.volume_ratio)
        + 10.0
    )
    if factors.resonance:
        total += 15.0
    if factors.close_confirm:
        total += close_bonus
    if factors.divergence_order == 2:
        total += second_bonus
    return round(min(100.0, max(0.0, total)), 2)


def _validate_candles(candles: Sequence[Candle]) -> None:
    """校验 K 线序列满足检测前提。"""

    if not candles:
        raise DivergenceInputError("candles must not be empty")
    first = candles[0]
    previous_ts: int | None = None
    for candle in candles:
        if not candle.confirmed:
            raise DivergenceInputError("candles must contain confirmed candles only")
        if candle.bar != first.bar:
            raise DivergenceInputError("candles must share the same bar")
        if candle.inst_id != first.inst_id:
            raise DivergenceInputError("candles must share the same inst_id")
        if previous_ts is not None and candle.ts_ms <= previous_ts:
            raise DivergenceInputError("candles must be strictly ordered by ts_ms")
        previous_ts = candle.ts_ms


def _make_segment(
    core_indices: tuple[int, ...],
    all_indices: tuple[int, ...],
    sign: int,
    hist: np.ndarray,
    candles: list[Candle],
    source: Literal["high_low", "close"],
) -> Segment:
    """由段内 K 线下标重算柱段属性。

    ``core_indices`` 是段自身符号的柱根，决定全部量化属性（边界、极值、
    峰值、面积、均量）；``all_indices`` 额外包含并入的噪音根，仅作为
    结构合并的记录。噪音合并不改变任何数值，从而保证任意前缀重算与
    全量计算的信号字段完全一致。
    """

    first_index = core_indices[0]
    last_index = core_indices[-1]
    extreme_index = first_index
    if source == "high_low":
        if sign < 0:
            extreme_index = min(core_indices, key=lambda i: (candles[i].low, i))
            extreme_price = candles[extreme_index].low
        else:
            extreme_index = max(core_indices, key=lambda i: (candles[i].high, -i))
            extreme_price = candles[extreme_index].high
    else:
        if sign < 0:
            extreme_index = min(core_indices, key=lambda i: (candles[i].close, i))
            extreme_price = candles[extreme_index].close
        else:
            extreme_index = max(core_indices, key=lambda i: (candles[i].close, -i))
            extreme_price = candles[extreme_index].close
    close_extreme_index = (
        min(core_indices, key=lambda i: (candles[i].close, i))
        if sign < 0
        else max(core_indices, key=lambda i: (candles[i].close, -i))
    )
    return Segment(
        sign=sign,
        indices=all_indices,
        start_index=first_index,
        end_index=last_index,
        start_ts=candles[first_index].ts_ms,
        end_ts=candles[last_index].ts_ms,
        extreme_index=extreme_index,
        extreme_ts=candles[extreme_index].ts_ms,
        extreme_price=extreme_price,
        extreme_close=candles[close_extreme_index].close,
        peak=float(max(abs(float(hist[i])) for i in core_indices)),
        area=float(sum(abs(float(hist[i])) for i in core_indices)),
        avg_volume=float(sum(float(candles[i].volume) for i in core_indices) / len(core_indices)),
    )


def build_segments(
    hist: np.ndarray,
    candles: list[Candle],
    config: DivergenceConfig,
) -> list[Segment]:
    """把 MACD 柱状图切成红/绿柱段，噪音段并入相邻段。

    hist==0 的根跟随前一根的符号（连续价格下几乎不出现）。MACD 预热期的
    NaN 根不参与切分。长度小于 ``min_segment_bars`` 的噪音段整体并入左侧
    最近的有效段（无左侧时并入右侧），并入后属性按并集根重算。
    """

    valid = np.flatnonzero(~np.isnan(hist))
    if valid.size == 0:
        return []

    raw: list[tuple[int, list[int]]] = []  # (sign, indices)
    current_sign = 1 if hist[valid[0]] > 0 else -1
    current: list[int] = []
    for index in valid:
        value = float(hist[index])
        if value > 0:
            sign = 1
        elif value < 0:
            sign = -1
        else:
            sign = current_sign
        if sign != current_sign and current:
            raw.append((current_sign, current))
            current = []
        current_sign = sign
        current.append(int(index))
    if current:
        raw.append((current_sign, current))

    min_bars = config.segment.min_segment_bars
    kept = [item for item in raw if len(item[1]) >= min_bars]
    if not kept:
        return []

    # 噪音段分配给最近的保留段：优先左侧，否则右侧。
    merged: list[tuple[int, list[int]]] = [(sign, list(indices)) for sign, indices in kept]
    for _sign, indices in raw:
        if len(indices) >= min_bars:
            continue
        target = None
        for position, (_, kept_indices) in enumerate(merged):
            if kept_indices[-1] < indices[0]:
                target = position
        if target is None:
            target = 0
        merged[target][1].extend(indices)

    # 噪音段抹掉后，两侧同号段连成一段。
    position = 1
    while position < len(merged):
        if merged[position][0] == merged[position - 1][0]:
            merged[position - 1][1].extend(merged[position][1])
            merged.pop(position)
        else:
            position += 1

    segments: list[Segment] = []
    for sign, all_indices in merged:
        ordered_all = tuple(sorted(all_indices))
        core = tuple(i for i in ordered_all if (hist[i] > 0) == (sign > 0) or hist[i] == 0)
        if not core:
            core = ordered_all
        segments.append(
            _make_segment(core, ordered_all, sign, hist, candles, config.extreme_price_source)
        )
    segments.sort(key=lambda segment: segment.start_index)
    return segments


def _atr_at(atr_values: np.ndarray, index: int) -> float:
    """取 index 处（或其之前最近）的有效 ATR，无有效值返回 0。"""

    position = index
    while position >= 0:
        value = float(atr_values[position])
        if not np.isnan(value) and value > 0.0:
            return value
        position -= 1
    return 0.0


def _gap_ok(seg1: Segment, seg2: Segment, config: DivergenceConfig) -> bool:
    gap = seg2.start_index - seg1.end_index - 1
    return config.gap.min <= gap <= config.gap.max


def _resonance(
    seg1: Segment,
    seg2: Segment,
    *,
    is_bull: bool,
    dif: np.ndarray,
    close_values: np.ndarray,
    config: DivergenceConfig,
) -> bool:
    """可选对照指标共振加分：任一启用的对照指标同向背离即加 15 分。"""

    optional = config.optional_indicators

    def _extreme(series: np.ndarray, indices: tuple[int, ...]) -> float | None:
        values = [float(series[i]) for i in indices if not np.isnan(series[i])]
        if not values:
            return None
        return min(values) if is_bull else max(values)

    if optional.dif.enabled:
        v1 = _extreme(dif, seg1.indices)
        v2 = _extreme(dif, seg2.indices)
        if v1 is not None and v2 is not None:
            if is_bull and v2 > v1:
                return True
            if not is_bull and v2 < v1:
                return True

    if optional.rsi.enabled:
        rsi_series = rsi(close_values, optional.rsi.period)
        v1 = _extreme(rsi_series, seg1.indices)
        v2 = _extreme(rsi_series, seg2.indices)
        if v1 is not None and v2 is not None:
            if is_bull and v2 > v1:
                return True
            if not is_bull and v2 < v1:
                return True

    if optional.pivot.enabled:
        pivots = find_pivot_lows(close_values, optional.pivot.left, optional.pivot.right)
        near1 = [p for p in pivots if abs(p.index - seg1.extreme_index) <= _PIVOT_PROXIMITY_BARS]
        near2 = [p for p in pivots if abs(p.index - seg2.extreme_index) <= _PIVOT_PROXIMITY_BARS]
        if is_bull and near1 and near2:
            p1 = min(near1, key=lambda p: abs(p.index - seg1.extreme_index))
            p2 = min(near2, key=lambda p: abs(p.index - seg2.extreme_index))
            d1 = float(dif[p1.index])
            d2 = float(dif[p2.index])
            if p2.value < p1.value and not np.isnan(d1) and not np.isnan(d2) and d2 > d1:
                return True

    return False


def _volume_ratio(seg1: Segment, seg2: Segment) -> float:
    if seg1.avg_volume <= 0.0:
        return 1.0
    return seg2.avg_volume / seg1.avg_volume


def _build_signal(
    seg1: Segment,
    seg2: Segment,
    *,
    kind: DivergenceKind,
    is_bull: bool,
    confirm_index: int,
    height_div: bool,
    area_div: bool,
    volume_confirm: bool,
    close_confirm: bool,
    order: int,
    resonance: bool,
    candles: list[Candle],
    atr_values: np.ndarray,
    config: DivergenceConfig,
    preliminary: bool,
) -> DivergenceSignal:
    """组装公共契约 DivergenceSignal。"""

    atr_value = _atr_at(atr_values, confirm_index)
    price_move = abs(float(seg2.extreme_price) - float(seg1.extreme_price))
    move_atr = price_move / atr_value if atr_value > 0.0 else 0.0
    factors = ScoreFactors(
        move_atr=move_atr,
        height_ratio=(1.0 - seg2.peak / seg1.peak) if seg1.peak > 0.0 else 0.0,
        area_ratio=(1.0 - seg2.area / seg1.area) if seg1.area > 0.0 else 0.0,
        volume_ratio=_volume_ratio(seg1, seg2),
        volume_threshold=config.volume.volume_ratio,
        close_confirm=close_confirm,
        divergence_order=order,
        resonance=resonance,
    )
    score = compute_signal_score(
        factors,
        close_bonus=config.close_confirm_bonus,
        second_bonus=config.consecutive.second_divergence_bonus,
    )
    return DivergenceSignal(
        inst_id=candles[0].inst_id,
        bar=candles[0].bar,
        kind=kind,
        method=DivergenceMethod.MACD_SEGMENT,
        seg1_start_ts=seg1.start_ts,
        seg1_end_ts=seg1.end_ts,
        seg1_extreme_ts=seg1.extreme_ts,
        seg1_extreme_price=seg1.extreme_price,
        seg1_peak=seg1.peak,
        seg1_area=seg1.area,
        seg1_avg_vol=seg1.avg_volume,
        seg2_start_ts=seg2.start_ts,
        seg2_end_ts=seg2.end_ts,
        seg2_extreme_ts=seg2.extreme_ts,
        seg2_extreme_price=seg2.extreme_price,
        seg2_peak=seg2.peak,
        seg2_area=seg2.area,
        seg2_avg_vol=seg2.avg_volume,
        height_div=height_div,
        area_div=area_div,
        volume_confirm=volume_confirm,
        close_confirm=close_confirm,
        divergence_order=order,
        confirmed_ts=candles[confirm_index].ts_ms,
        score=score,
        preliminary=preliminary,
    )


def _detect_regular(
    series: list[Segment],
    *,
    kind: DivergenceKind,
    is_bull: bool,
    candles: list[Candle],
    dif: np.ndarray,
    close_values: np.ndarray,
    atr_values: np.ndarray,
    config: DivergenceConfig,
) -> list[DivergenceSignal]:
    """常规背离（BULL_REGULAR / BEAR_REGULAR）。"""

    signals: list[DivergenceSignal] = []
    last_confirmed: Segment | None = None
    last_order = 0

    for position in range(1, len(series)):
        seg1, seg2 = series[position - 1], series[position]
        confirm_index = seg2.end_index + config.segment.confirm_bars
        if confirm_index >= len(candles):
            continue
        if not _gap_ok(seg1, seg2, config):
            continue

        if is_bull:
            price_ok = seg2.extreme_price < seg1.extreme_price
            close_confirm = seg2.extreme_close < seg1.extreme_close
        else:
            price_ok = seg2.extreme_price > seg1.extreme_price
            close_confirm = seg2.extreme_close > seg1.extreme_close
        if not price_ok:
            continue

        height_div = seg2.peak < seg1.peak
        area_div = seg2.area < seg1.area * config.area_ratio
        if config.require_height_div and not height_div:
            continue
        if config.require_area_div and not area_div:
            continue
        if not height_div and not area_div:
            continue

        volume_confirm = _volume_ratio(seg1, seg2) < config.volume.volume_ratio
        if config.volume.enabled and config.volume.required and not volume_confirm:
            continue

        order = (
            last_order + 1
            if last_confirmed is not None and last_confirmed.end_index == seg1.end_index
            else 1
        )
        if order == 2 and not config.consecutive.allow_second_divergence:
            continue

        resonance = _resonance(
            seg1, seg2, is_bull=is_bull, dif=dif, close_values=close_values, config=config
        )
        signals.append(
            _build_signal(
                seg1,
                seg2,
                kind=kind,
                is_bull=is_bull,
                confirm_index=confirm_index,
                height_div=height_div,
                area_div=area_div,
                volume_confirm=volume_confirm,
                close_confirm=close_confirm,
                order=order,
                resonance=resonance,
                candles=candles,
                atr_values=atr_values,
                config=config,
                preliminary=False,
            )
        )
        last_confirmed = seg2
        last_order = order
    return signals


def _detect_hidden(
    series: list[Segment],
    *,
    kind: DivergenceKind,
    is_bull: bool,
    candles: list[Candle],
    dif: np.ndarray,
    close_values: np.ndarray,
    atr_values: np.ndarray,
    config: DivergenceConfig,
) -> list[DivergenceSignal]:
    """隐藏背离（BULL_HIDDEN / BEAR_HIDDEN），默认关闭。

    隐藏底背离：价格低点抬高且指标（柱峰值）低点降低；隐藏顶背离镜像。
    """

    signals: list[DivergenceSignal] = []
    for position in range(1, len(series)):
        seg1, seg2 = series[position - 1], series[position]
        confirm_index = seg2.end_index + config.segment.confirm_bars
        if confirm_index >= len(candles):
            continue
        if not _gap_ok(seg1, seg2, config):
            continue

        if is_bull:
            price_ok = seg2.extreme_price > seg1.extreme_price
            close_confirm = seg2.extreme_close > seg1.extreme_close
            height_div = seg2.peak < seg1.peak
        else:
            price_ok = seg2.extreme_price < seg1.extreme_price
            close_confirm = seg2.extreme_close < seg1.extreme_close
            height_div = seg2.peak > seg1.peak
        if not price_ok or not height_div:
            continue

        volume_confirm = _volume_ratio(seg1, seg2) < config.volume.volume_ratio
        resonance = _resonance(
            seg1, seg2, is_bull=not is_bull, dif=dif, close_values=close_values, config=config
        )
        signals.append(
            _build_signal(
                seg1,
                seg2,
                kind=kind,
                is_bull=is_bull,
                confirm_index=confirm_index,
                height_div=height_div,
                area_div=True,
                volume_confirm=volume_confirm,
                close_confirm=close_confirm,
                order=1,
                resonance=resonance,
                candles=candles,
                atr_values=atr_values,
                config=config,
                preliminary=False,
            )
        )
    return signals


def _detect_early(
    series: list[Segment],
    *,
    kind: DivergenceKind,
    is_bull: bool,
    candles: list[Candle],
    hist: np.ndarray,
    dif: np.ndarray,
    close_values: np.ndarray,
    atr_values: np.ndarray,
    config: DivergenceConfig,
) -> list[DivergenceSignal]:
    """早期提示：末端柱段连续 early_bars 根缩短且价格创新低/高。

    只生成 ``preliminary=True`` 信号用于通知，不作为正式交易信号，
    也不参与二次背离计数。
    """

    signals: list[DivergenceSignal] = []
    if len(series) < 2:
        return signals
    tail = series[-1]
    if tail.end_index != len(candles) - 1:
        return signals

    early_bars = config.early_signal.early_bars
    tail_indices = tail.indices[-early_bars:]
    if len(tail_indices) < early_bars:
        return signals
    heights = [abs(float(hist[index])) for index in tail_indices]
    if any(heights[i] <= heights[i + 1] for i in range(len(heights) - 1)):
        return signals

    previous = series[-2]
    if not _gap_ok(previous, tail, config):
        return signals
    if is_bull:
        price_ok = tail.extreme_price < previous.extreme_price
        close_confirm = tail.extreme_close < previous.extreme_close
    else:
        price_ok = tail.extreme_price > previous.extreme_price
        close_confirm = tail.extreme_close > previous.extreme_close
    if not price_ok:
        return signals

    volume_confirm = _volume_ratio(previous, tail) < config.volume.volume_ratio
    resonance = _resonance(
        previous, tail, is_bull=is_bull, dif=dif, close_values=close_values, config=config
    )
    confirm_index = len(candles) - 1
    signals.append(
        _build_signal(
            previous,
            tail,
            kind=kind,
            is_bull=is_bull,
            confirm_index=confirm_index,
            height_div=tail.peak < previous.peak,
            area_div=tail.area < previous.area * config.area_ratio,
            volume_confirm=volume_confirm,
            close_confirm=close_confirm,
            order=1,
            resonance=resonance,
            candles=candles,
            atr_values=atr_values,
            config=config,
            preliminary=True,
        )
    )
    return signals


def detect_divergences(
    candles: Sequence[Candle],
    config: DivergenceConfig | None = None,
) -> list[DivergenceSignal]:
    """对一段已收盘 K 线检测全部背离信号。

    输入必须全部是已确认 K 线、同一交易对、同一周期、严格升序。
    输出按确认时间升序排列；同一确认时刻的正式信号先于早期提示。
    """

    _validate_candles(candles)
    resolved = config if config is not None else DivergenceConfig()
    candle_list = list(candles)

    close_values = np.asarray([float(c.close) for c in candle_list], dtype=np.float64)
    high_values = np.asarray([float(c.high) for c in candle_list], dtype=np.float64)
    low_values = np.asarray([float(c.low) for c in candle_list], dtype=np.float64)

    dif, _dea, hist = macd(
        close_values,
        fast=resolved.macd.fast,
        slow=resolved.macd.slow,
        signal=resolved.macd.signal,
    )
    atr_values = atr(high_values, low_values, close_values, _ATR_PERIOD)
    segments = build_segments(hist, candle_list, resolved)
    greens = [segment for segment in segments if segment.sign < 0]
    reds = [segment for segment in segments if segment.sign > 0]

    common = {
        "candles": candle_list,
        "dif": dif,
        "close_values": close_values,
        "atr_values": atr_values,
        "config": resolved,
    }

    signals: list[DivergenceSignal] = []
    signals += _detect_regular(greens, kind=DivergenceKind.BULL_REGULAR, is_bull=True, **common)
    signals += _detect_regular(reds, kind=DivergenceKind.BEAR_REGULAR, is_bull=False, **common)
    if resolved.enable_hidden:
        signals += _detect_hidden(greens, kind=DivergenceKind.BULL_HIDDEN, is_bull=True, **common)
        signals += _detect_hidden(reds, kind=DivergenceKind.BEAR_HIDDEN, is_bull=False, **common)
    if resolved.early_signal.enabled:
        signals += _detect_early(
            greens, kind=DivergenceKind.BULL_REGULAR, is_bull=True, hist=hist, **common
        )
        signals += _detect_early(
            reds, kind=DivergenceKind.BEAR_REGULAR, is_bull=False, hist=hist, **common
        )

    signals.sort(key=lambda signal: (signal.confirmed_ts, signal.preliminary, signal.kind))
    return signals


__all__ = [
    "DivergenceConfig",
    "DivergenceInputError",
    "ScoreFactors",
    "Segment",
    "build_segments",
    "compute_signal_score",
    "detect_divergences",
]
