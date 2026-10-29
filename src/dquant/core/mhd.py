"""MHD 摆动点 MACD 柱背离因子。

定义（与用户给出的 MHD 因子一致）::

    pm = (price_b - price_ref) / ATR14_ref
    hm = (hist_b  - hist_ref) / stdev(hist, 100)_b
    D  = pm - hm

其中 ``b`` 是当前已确认的摆动高点/低点。每个摆动点同时与两个参照系比较，
取更强的那个：

* ``D_pair``：与上一个同类摆动点比较（价格更高但柱子更低 = 看跌背离）；
* ``D_peak``：与回看窗口内 MACD 柱最极端的摆动点比较（价格回到同一水平
  但动量衰减）。

本模块是纯函数实现：只读 OHLC/指标序列，不访问数据库、不访问网络、
不修改全局状态，也不使用摆动点确认之后的数据。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

import numpy as np
from numpy.typing import ArrayLike, NDArray

from .indicators import atr as atr_series
from .indicators import macd as macd_series
from .pivots import PivotPoint, find_pivot_highs, find_pivot_lows

FloatArray = NDArray[np.float64]
MHDKind = Literal["top", "bottom"]


@dataclass(frozen=True, slots=True)
class MHDConfig:
    """MHD 参数。默认值面向 4H 合约行情。"""

    swing_left: int = 3
    swing_right: int = 3
    macd_fast: int = 12
    macd_slow: int = 26
    macd_signal: int = 9
    atr_period: int = 14
    stdev_window: int = 100
    peak_lookback: int = 120
    min_d: float = 0.5
    peak_price_tolerance_atr: float = 0.5
    require_hist_sign: bool = True

    def __post_init__(self) -> None:
        for name in (
            "swing_left",
            "swing_right",
            "macd_fast",
            "macd_slow",
            "macd_signal",
            "atr_period",
            "stdev_window",
            "peak_lookback",
        ):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                raise ValueError(f"{name} must be a positive integer")
        if self.macd_fast >= self.macd_slow:
            raise ValueError("macd_fast must be less than macd_slow")
        if self.stdev_window < 2:
            raise ValueError("stdev_window must be at least 2")
        if self.min_d < 0:
            raise ValueError("min_d must not be negative")
        if self.peak_price_tolerance_atr < 0:
            raise ValueError("peak_price_tolerance_atr must not be negative")

    @property
    def macd_warmup(self) -> int:
        """MACD 柱首个有效值之前需要丢弃的 K 线数量。"""

        return self.macd_slow + self.macd_signal - 2

    @property
    def warmup_bars(self) -> int:
        """计算一个可用 MHD 信号所需的最少 K 线数量。"""

        return max(
            self.macd_warmup + self.stdev_window,
            self.atr_period + 1,
            self.swing_left + self.swing_right + 2,
        )


@dataclass(frozen=True, slots=True)
class MHDSignal:
    """一个已确认摆动点上的 MHD 背离读数。"""

    kind: MHDKind
    swing_index: int
    confirmed_index: int
    price: float
    hist: float
    d_pair: float | None
    d_peak: float | None
    d: float
    reference: Literal["pair", "peak"]
    reference_index: int
    pm: float
    hm: float

    @property
    def score(self) -> float:
        """把 D 映射到 0~100 的可展示分数，仅用于排序/说明。"""

        return float(min(100.0, max(0.0, 50.0 + self.d * 10.0)))


@dataclass(frozen=True, slots=True)
class MHDResult:
    """MHD 计算结果。"""

    hist: FloatArray
    atr: FloatArray
    signals: tuple[MHDSignal, ...]


def detect_mhd(
    highs: ArrayLike,
    lows: ArrayLike,
    closes: ArrayLike,
    config: MHDConfig | None = None,
) -> MHDResult:
    """从 OHLC 序列计算 MACD 柱、ATR14 和全部已确认 MHD 信号。"""

    cfg = config or MHDConfig()
    high_values = _as_float_1d(highs, "highs")
    low_values = _as_float_1d(lows, "lows")
    close_values = _as_float_1d(closes, "closes")
    if not high_values.size == low_values.size == close_values.size:
        raise ValueError("highs, lows and closes must have the same length")
    if high_values.size < cfg.warmup_bars:
        empty = np.full(high_values.size, np.nan, dtype=np.float64)
        return MHDResult(hist=empty, atr=empty.copy(), signals=())

    _, _, hist = macd_series(
        close_values,
        fast=cfg.macd_fast,
        slow=cfg.macd_slow,
        signal=cfg.macd_signal,
    )
    atr_values = atr_series(high_values, low_values, close_values, cfg.atr_period)
    high_pivots = find_pivot_highs(high_values, cfg.swing_left, cfg.swing_right)
    low_pivots = find_pivot_lows(low_values, cfg.swing_left, cfg.swing_right)
    signals = evaluate_mhd(
        high_values,
        low_values,
        hist,
        atr_values,
        high_pivots,
        low_pivots,
        cfg,
    )
    return MHDResult(hist=hist, atr=atr_values, signals=signals)


def evaluate_mhd(
    highs: ArrayLike,
    lows: ArrayLike,
    hist: ArrayLike,
    atr_values: ArrayLike,
    high_pivots: list[PivotPoint] | tuple[PivotPoint, ...],
    low_pivots: list[PivotPoint] | tuple[PivotPoint, ...],
    config: MHDConfig | None = None,
) -> tuple[MHDSignal, ...]:
    """在给定指标与摆动点序列上计算 MHD 信号。

    这个入口把指标计算与背离判定解耦，便于单元测试和回测复用。
    """

    cfg = config or MHDConfig()
    high_values = _as_float_1d(highs, "highs")
    low_values = _as_float_1d(lows, "lows")
    hist_values = _as_float_1d_allow_nan(hist, "hist")
    atr_array = _as_float_1d_allow_nan(atr_values, "atr")
    if not high_values.size == low_values.size == hist_values.size == atr_array.size:
        raise ValueError("highs, lows, hist and atr must have the same length")

    signals: list[MHDSignal] = []
    signals.extend(
        _evaluate_kind(
            "top",
            high_values,
            hist_values,
            atr_array,
            tuple(high_pivots),
            cfg,
        )
    )
    signals.extend(
        _evaluate_kind(
            "bottom",
            low_values,
            hist_values,
            atr_array,
            tuple(low_pivots),
            cfg,
        )
    )
    signals.sort(key=lambda item: (item.confirmed_index, item.kind, item.swing_index))
    return tuple(signals)


def _evaluate_kind(
    kind: MHDKind,
    prices: FloatArray,
    hist: FloatArray,
    atr_values: FloatArray,
    pivots: tuple[PivotPoint, ...],
    cfg: MHDConfig,
) -> list[MHDSignal]:
    signals: list[MHDSignal] = []
    for position, pivot in enumerate(pivots):
        index = pivot.index
        if index < 1 or index >= prices.size:
            continue
        if not (np.isfinite(hist[index]) and np.isfinite(atr_values[index])):
            continue
        if cfg.require_hist_sign:
            if kind == "top" and hist[index] <= 0:
                continue
            if kind == "bottom" and hist[index] >= 0:
                continue
        if not np.isfinite(prices[index]) or prices[index] <= 0:
            continue

        candidates: list[tuple[float, Literal["pair", "peak"], int, float, float]] = []
        previous = pivots[position - 1] if position > 0 else None
        if previous is not None:
            measured = _measure(
                prices[index],
                prices[previous.index],
                hist[index],
                hist[previous.index],
                atr_values[previous.index],
                hist,
                index,
                cfg,
            )
            if measured is not None:
                pm, hm = measured
                if (kind == "top" and pm >= 0 and hm < 0) or (
                    kind == "bottom" and pm <= 0 and hm > 0
                ):
                    d = pm - hm if kind == "top" else hm - pm
                    if d >= cfg.min_d:
                        candidates.append((d, "pair", previous.index, pm, hm))

        window = [
            item
            for item in pivots
            if item.index < index and item.index >= index - cfg.peak_lookback
        ]
        if cfg.require_hist_sign:
            window = [
                item
                for item in window
                if (kind == "top" and hist[item.index] > 0)
                or (kind == "bottom" and hist[item.index] < 0)
            ]
        if window:
            reference = (
                max(window, key=lambda item: hist[item.index])
                if kind == "top"
                else min(window, key=lambda item: hist[item.index])
            )
            measured = _measure(
                prices[index],
                prices[reference.index],
                hist[index],
                hist[reference.index],
                atr_values[reference.index],
                hist,
                index,
                cfg,
            )
            if measured is not None:
                pm, hm = measured
                tolerance = cfg.peak_price_tolerance_atr
                if (
                    kind == "top"
                    and pm >= -tolerance
                    and hm < 0
                ) or (
                    kind == "bottom"
                    and pm <= tolerance
                    and hm > 0
                ):
                    d = pm - hm if kind == "top" else hm - pm
                    if d >= cfg.min_d:
                        candidates.append((d, "peak", reference.index, pm, hm))

        if not candidates:
            continue
        strongest = max(candidates, key=lambda item: (item[0], item[1] == "pair"))
        d, reference_kind, reference_index, pm, hm = strongest
        pair_d = next((item[0] for item in candidates if item[1] == "pair"), None)
        peak_d = next((item[0] for item in candidates if item[1] == "peak"), None)
        signals.append(
            MHDSignal(
                kind=kind,
                swing_index=index,
                confirmed_index=pivot.confirmed_index,
                price=float(prices[index]),
                hist=float(hist[index]),
                d_pair=pair_d,
                d_peak=peak_d,
                d=float(d),
                reference=reference_kind,
                reference_index=reference_index,
                pm=float(pm),
                hm=float(hm),
            )
        )
    return signals


def _measure(
    price_b: float,
    price_ref: float,
    hist_b: float,
    hist_ref: float,
    atr_ref: float,
    hist_series: FloatArray,
    index_b: int,
    cfg: MHDConfig,
) -> tuple[float, float] | None:
    if not (np.isfinite(price_ref) and price_ref > 0):
        return None
    if not (np.isfinite(hist_ref) and np.isfinite(hist_b)):
        return None
    if not (np.isfinite(atr_ref) and atr_ref > 0):
        return None
    start = max(0, index_b - cfg.stdev_window + 1)
    window = hist_series[start : index_b + 1]
    window = window[np.isfinite(window)]
    if window.size < 2:
        return None
    stdev = float(np.std(window, ddof=0))
    if not np.isfinite(stdev) or stdev <= 0:
        return None
    pm = (price_b - price_ref) / atr_ref
    hm = (hist_b - hist_ref) / stdev
    return float(pm), float(hm)


def _as_float_1d(values: ArrayLike, name: str) -> FloatArray:
    array = np.asarray(values, dtype=np.float64)
    if array.ndim != 1:
        raise ValueError(f"{name} must be one-dimensional")
    if array.size and not np.all(np.isfinite(array)):
        raise ValueError(f"{name} contains NaN or infinite values")
    return array


def _as_float_1d_allow_nan(values: ArrayLike, name: str) -> FloatArray:
    """指标序列允许预热期 NaN，但拒绝无穷值。"""

    array = np.asarray(values, dtype=np.float64)
    if array.ndim != 1:
        raise ValueError(f"{name} must be one-dimensional")
    if array.size and np.any(np.isinf(array)):
        raise ValueError(f"{name} contains infinite values")
    return array


__all__ = [
    "MHDConfig",
    "MHDKind",
    "MHDResult",
    "MHDSignal",
    "detect_mhd",
    "evaluate_mhd",
]
