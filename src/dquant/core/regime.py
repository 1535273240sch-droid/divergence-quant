"""MA200 趋势状态分类器（SMA200 / EMA200 双轨）。

本模块是纯函数实现：不访问数据库、不访问网络、不修改全局状态。
所有计算只使用已收盘 K 线（``Candle.confirmed`` 必须为 ``True``），
任一时刻的分类结果只依赖当前及历史 K 线，不读取未来数据。

默认参数对应计划书 §5.5 与 §8：

- 上涨：``close > ma``、``slope > +1%``、``above_ratio >= 0.8``；
- 下跌：``close < ma``、``slope < -1%``、``above_ratio <= 0.2``；
- 震荡：其余情况，或最近窗口内穿越 MA 的次数 ``>= 3``；
- 状态切换需连续 ``confirm_bars`` 根 K 线满足新状态条件才生效。
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np
from numpy.typing import ArrayLike, NDArray

from dquant.contracts import Candle, CandleBar, RegimeResult, RegimeState

from .indicators import adx as adx_indicator
from .indicators import ema as ema_indicator
from .indicators import sma as sma_indicator

_MA_TYPES = ("SMA", "EMA")
IntArray = NDArray[np.int64]


class InsufficientCandlesError(ValueError):
    """K 线数量不足以计算完整趋势指标。"""

    def __init__(self, required: int, actual: int) -> None:
        self.required = required
        self.actual = actual
        super().__init__(
            f"regime classification requires at least {required} candles, got {actual}"
        )


@dataclass(frozen=True)
class RegimeConfig:
    """趋势分类参数，默认值与计划书一致。"""

    ma_types: tuple[str, ...] = ("SMA", "EMA")
    primary_ma: str = "SMA"
    ma_period: int = 200
    slope_lookback: int = 20
    slope_threshold: float = 0.01
    window: int = 20
    above_ratio_up: float = 0.8
    above_ratio_down: float = 0.2
    max_cross_count: int = 3
    adx_period: int = 14
    adx_range_threshold: float = 20.0
    confirm_bars: int = 3

    def __post_init__(self) -> None:
        for name in (
            "ma_period",
            "slope_lookback",
            "window",
            "max_cross_count",
            "adx_period",
            "confirm_bars",
        ):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                raise ValueError(f"{name} must be a positive integer")
        if self.ma_period < 2:
            raise ValueError("ma_period must be at least 2")

        ratio_fields = ("above_ratio_up", "above_ratio_down")
        for name in ratio_fields:
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ValueError(f"{name} must be a number between 0 and 1")
            if not 0.0 <= float(value) <= 1.0:
                raise ValueError(f"{name} must be between 0 and 1")
        if float(self.above_ratio_up) <= float(self.above_ratio_down):
            raise ValueError("above_ratio_up must be greater than above_ratio_down")

        if not 0.0 < float(self.slope_threshold) < 1.0:
            raise ValueError("slope_threshold must be between 0 and 1")
        if float(self.adx_range_threshold) <= 0.0:
            raise ValueError("adx_range_threshold must be positive")

        ma_types = tuple(self.ma_types)
        if not ma_types:
            raise ValueError("ma_types must not be empty")
        if len(set(ma_types)) != len(ma_types):
            raise ValueError("ma_types must not contain duplicates")
        for ma_type in ma_types:
            if ma_type not in _MA_TYPES:
                raise ValueError(f"unsupported ma_type: {ma_type}")
        if self.primary_ma not in ma_types:
            raise ValueError("primary_ma must be one of ma_types")
        object.__setattr__(self, "ma_types", ma_types)


def classify_market_state(
    *,
    close: float,
    ma: float,
    slope: float,
    above_ratio: float,
    cross_count: int,
    adx: float | None,
    config: RegimeConfig,
) -> RegimeState:
    """根据单根 K 线的指标快照判定趋势状态。

    该函数是规则层的单一入口，便于回测、诊断和单元测试复用。
    ``adx`` 为 ``None`` 时跳过 ADX 辅助条件。
    """

    for name, value in (("close", close), ("ma", ma), ("slope", slope)):
        if not math.isfinite(value):
            raise ValueError(f"{name} must be finite")
    if close <= 0.0 or ma <= 0.0:
        raise ValueError("close and ma must be positive")
    if not 0.0 <= above_ratio <= 1.0:
        raise ValueError("above_ratio must be between 0 and 1")
    if isinstance(cross_count, bool) or not isinstance(cross_count, int) or cross_count < 0:
        raise ValueError("cross_count must be a non-negative integer")
    if adx is not None:
        if not math.isfinite(adx):
            raise ValueError("adx must be finite or None")
        if adx < 0.0:
            raise ValueError("adx must not be negative")

    return _classify_metrics(
        close=close,
        ma=ma,
        slope=slope,
        above_ratio=above_ratio,
        cross_count=cross_count,
        adx=adx,
        config=config,
    )


def _classify_metrics(
    *,
    close: float,
    ma: float,
    slope: float,
    above_ratio: float,
    cross_count: int,
    adx: float | None,
    config: RegimeConfig,
) -> RegimeState:
    """规则判定核心，调用方已完成输入校验。"""

    if cross_count >= config.max_cross_count:
        return RegimeState.RANGE
    if adx is not None and abs(slope) < config.slope_threshold and adx < config.adx_range_threshold:
        return RegimeState.RANGE
    if close > ma and slope > config.slope_threshold and above_ratio >= config.above_ratio_up:
        return RegimeState.UPTREND
    if close < ma and slope < -config.slope_threshold and above_ratio <= config.above_ratio_down:
        return RegimeState.DOWNTREND
    return RegimeState.RANGE


def debounce_states(
    raw_states: Sequence[RegimeState], confirm_bars: int
) -> tuple[list[RegimeState], list[int]]:
    """对逐根原始状态做滞后确认。

    首个原始状态直接生效（此前没有可切换的旧状态），之后新状态必须连续
    ``confirm_bars`` 根 K 线保持一致才切换。返回确认后的状态序列和每根
    K 线在生效状态中已持续的根数。
    """

    if isinstance(confirm_bars, bool) or not isinstance(confirm_bars, int):
        raise TypeError("confirm_bars must be an integer")
    if confirm_bars <= 0:
        raise ValueError("confirm_bars must be positive")

    effective_states: list[RegimeState] = []
    bars_in_state: list[int] = []
    effective: RegimeState | None = None
    pending: RegimeState | None = None
    pending_count = 0
    count = 0

    for raw in raw_states:
        if not isinstance(raw, RegimeState):
            raise TypeError("raw_states must contain RegimeState values")
        if effective is None:
            effective = raw
            count = 1
            pending = None
            pending_count = 0
        elif raw == effective:
            count += 1
            pending = None
            pending_count = 0
        else:
            if raw == pending:
                pending_count += 1
            else:
                pending = raw
                pending_count = 1
            if pending_count >= confirm_bars:
                effective = raw
                count = 1
                pending = None
                pending_count = 0
            else:
                count += 1
        effective_states.append(effective)
        bars_in_state.append(count)

    return effective_states, bars_in_state


@dataclass(frozen=True)
class _RegimeSeries:
    """逐根 K 线的分类结果，内部使用，避免重复构造公共模型。"""

    inst_id: str
    bar: CandleBar
    ma_type: str
    timestamps: list[int]
    states: list[RegimeState]
    dists: list[float]
    slopes: list[float]
    above_ratios: list[float]
    cross_counts: list[int]
    bars_in_states: list[int]

    def to_result(self, index: int) -> RegimeResult:
        return RegimeResult(
            inst_id=self.inst_id,
            bar=self.bar,
            ts_ms=self.timestamps[index],
            state=self.states[index],
            ma_type=self.ma_type,
            dist=self.dists[index],
            slope=self.slopes[index],
            above_ratio=self.above_ratios[index],
            cross_count=self.cross_counts[index],
            bars_in_state=self.bars_in_states[index],
        )

    def to_results(self) -> list[RegimeResult]:
        return [self.to_result(index) for index in range(len(self.timestamps))]


def _first_evaluable_index(config: RegimeConfig) -> int:
    """所有趋势指标都可用时的第一根 K 线下标。"""

    return max(
        config.ma_period - 1 + config.slope_lookback,
        config.ma_period - 1 + config.window - 1,
        config.ma_period + config.window - 1,
    )


def _validate_candles(
    inst_id: str,
    bar: CandleBar,
    candles: Sequence[Candle],
    config: RegimeConfig,
) -> tuple[NDArray[np.float64], NDArray[np.float64], NDArray[np.float64], list[int]]:
    """校验 K 线序列并转换为 numpy 数组。"""

    if not isinstance(bar, CandleBar):
        raise ValueError("bar must be a CandleBar value")
    if isinstance(candles, (str, bytes)) or not isinstance(candles, Sequence):
        raise TypeError("candles must be a sequence of Candle objects")

    required = _first_evaluable_index(config) + 1
    if len(candles) < required:
        raise InsufficientCandlesError(required, len(candles))

    closes = np.empty(len(candles), dtype=np.float64)
    highs = np.empty(len(candles), dtype=np.float64)
    lows = np.empty(len(candles), dtype=np.float64)
    timestamps: list[int] = []
    previous_ts = -1
    for index, candle in enumerate(candles):
        if not isinstance(candle, Candle):
            raise TypeError("candles must contain Candle objects")
        if candle.inst_id != inst_id:
            raise ValueError(
                f"candle inst_id mismatch at index {index}: {candle.inst_id} != {inst_id}"
            )
        if candle.bar is not bar:
            raise ValueError(f"candle bar mismatch at index {index}: {candle.bar} != {bar}")
        if not candle.confirmed:
            raise ValueError(f"regime classification requires confirmed candles only: {index}")
        if candle.ts_ms <= previous_ts:
            raise ValueError(f"candle ts_ms must be strictly increasing at index {index}")
        previous_ts = candle.ts_ms
        closes[index] = float(candle.close)
        highs[index] = float(candle.high)
        lows[index] = float(candle.low)
        timestamps.append(candle.ts_ms)
    return closes, highs, lows, timestamps


def _resolve_adx(
    adx_values: ArrayLike | None,
    size: int,
    high: NDArray[np.float64],
    low: NDArray[np.float64],
    close: NDArray[np.float64],
    config: RegimeConfig,
) -> NDArray[np.float64]:
    """使用调用方提供的 ADX，或由 OHLC 内部计算。"""

    if adx_values is None:
        return adx_indicator(high, low, close, config.adx_period)
    array = np.asarray(adx_values, dtype=np.float64)
    if array.ndim != 1:
        raise ValueError("adx must be one-dimensional")
    if array.size != size:
        raise ValueError(f"adx length must match candles length: {array.size} != {size}")
    if np.any(np.isinf(array)):
        raise ValueError("adx must not contain infinite values")
    return array


def _cross_prefix(differences: NDArray[np.float64]) -> IntArray:
    """统计每一根 K 线是否发生 MA 穿越，并返回前缀和。

    穿越定义为有效符号发生改变；价格恰好等于 MA（差值为 0）时沿用上一个
    非零符号，避免把一次穿越重复计数。
    """

    valid = np.isfinite(differences)
    signs = np.where(valid, np.sign(differences), 0.0)
    filled = np.zeros(signs.size, dtype=np.float64)
    last_sign = 0.0
    for index in range(signs.size):
        value = float(signs[index])
        if value != 0.0:
            last_sign = value
        filled[index] = last_sign

    changes = np.zeros(signs.size, dtype=np.int64)
    if signs.size > 1:
        crossed = (filled[1:] != 0.0) & (filled[:-1] != 0.0) & (filled[1:] != filled[:-1])
        changes[1:] = crossed.astype(np.int64)
    return np.concatenate((np.zeros(1, dtype=np.int64), np.cumsum(changes)))


def _compute_series(
    inst_id: str,
    bar: CandleBar,
    closes: NDArray[np.float64],
    highs: NDArray[np.float64],
    lows: NDArray[np.float64],
    timestamps: list[int],
    *,
    ma_type: str,
    config: RegimeConfig,
    adx_values: ArrayLike | None,
) -> _RegimeSeries:
    """计算逐根 K 线的趋势指标、原始状态和防抖后的状态。"""

    size = closes.size
    ma = (
        sma_indicator(closes, config.ma_period)
        if ma_type == "SMA"
        else ema_indicator(closes, config.ma_period)
    )

    dist = (closes - ma) / ma
    slope = np.full(size, np.nan, dtype=np.float64)
    lookback = config.slope_lookback
    slope[lookback:] = (ma[lookback:] - ma[:-lookback]) / ma[:-lookback]

    above = (closes > ma).astype(np.int64)
    above_prefix = np.concatenate((np.zeros(1, dtype=np.int64), np.cumsum(above)))
    cross_prefix = _cross_prefix(closes - ma)

    adx_result = _resolve_adx(adx_values, size, highs, lows, closes, config)
    first = _first_evaluable_index(config)

    raw_states: list[RegimeState] = []
    dists: list[float] = []
    slopes: list[float] = []
    above_ratios: list[float] = []
    cross_counts: list[int] = []
    for index in range(first, size):
        window_start = index - config.window + 1
        above_ratio = float(above_prefix[index + 1] - above_prefix[window_start]) / config.window
        cross_count = int(cross_prefix[index + 1] - cross_prefix[window_start])
        slope_value = float(slope[index])
        adx_value = float(adx_result[index])
        raw_states.append(
            _classify_metrics(
                close=float(closes[index]),
                ma=float(ma[index]),
                slope=slope_value,
                above_ratio=above_ratio,
                cross_count=cross_count,
                adx=None if not math.isfinite(adx_value) else adx_value,
                config=config,
            )
        )
        dists.append(float(dist[index]))
        slopes.append(slope_value)
        above_ratios.append(above_ratio)
        cross_counts.append(cross_count)

    states, bars_in_states = debounce_states(raw_states, config.confirm_bars)
    return _RegimeSeries(
        inst_id=inst_id,
        bar=bar,
        ma_type=ma_type,
        timestamps=timestamps[first:],
        states=states,
        dists=dists,
        slopes=slopes,
        above_ratios=above_ratios,
        cross_counts=cross_counts,
        bars_in_states=bars_in_states,
    )


def regime_series(
    inst_id: str,
    bar: CandleBar,
    candles: Sequence[Candle],
    *,
    ma_type: str = "SMA",
    config: RegimeConfig | None = None,
    adx: ArrayLike | None = None,
) -> list[RegimeResult]:
    """返回从第一根可计算 K 线开始的逐根趋势分类结果。"""

    resolved = config or RegimeConfig()
    if ma_type not in _MA_TYPES:
        raise ValueError(f"unsupported ma_type: {ma_type}")
    closes, highs, lows, timestamps = _validate_candles(inst_id, bar, candles, resolved)
    series = _compute_series(
        inst_id,
        bar,
        closes,
        highs,
        lows,
        timestamps,
        ma_type=ma_type,
        config=resolved,
        adx_values=adx,
    )
    return series.to_results()


def classify_regime(
    inst_id: str,
    bar: CandleBar,
    candles: Sequence[Candle],
    *,
    ma_type: str = "SMA",
    config: RegimeConfig | None = None,
    adx: ArrayLike | None = None,
) -> RegimeResult:
    """返回最新一根已收盘 K 线的趋势分类结果。"""

    resolved = config or RegimeConfig()
    if ma_type not in _MA_TYPES:
        raise ValueError(f"unsupported ma_type: {ma_type}")
    closes, highs, lows, timestamps = _validate_candles(inst_id, bar, candles, resolved)
    series = _compute_series(
        inst_id,
        bar,
        closes,
        highs,
        lows,
        timestamps,
        ma_type=ma_type,
        config=resolved,
        adx_values=adx,
    )
    return series.to_result(len(series.timestamps) - 1)


def classify_regime_all(
    inst_id: str,
    bar: CandleBar,
    candles: Sequence[Candle],
    *,
    config: RegimeConfig | None = None,
    adx: ArrayLike | None = None,
) -> dict[str, RegimeResult]:
    """同时计算配置中的全部 MA 轨道（默认 SMA200 与 EMA200）。"""

    resolved = config or RegimeConfig()
    return {
        ma_type: classify_regime(
            inst_id,
            bar,
            candles,
            ma_type=ma_type,
            config=resolved,
            adx=adx,
        )
        for ma_type in resolved.ma_types
    }


__all__ = [
    "InsufficientCandlesError",
    "RegimeConfig",
    "classify_market_state",
    "classify_regime",
    "classify_regime_all",
    "debounce_states",
    "regime_series",
]
