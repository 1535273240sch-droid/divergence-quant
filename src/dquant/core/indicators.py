"""指标计算库：纯函数、无副作用。

输入是按时间升序的价格序列（仅包含已收盘 K 线），输出与输入等长，
预热期使用 ``NaN`` 填充，调用方必须先丢弃 ``NaN`` 再使用。

初始化约定与 TA-Lib 保持一致，确保回测与实盘共用同一套数值口径：

- EMA 使用前 ``n`` 个值的 SMA 作为种子；
- RSI、ATR 使用 Wilder 平滑；
- MACD 的信号线是对 DIF 序列再做一次 EMA；
- ADX 的首个有效值位于索引 ``2n-1``。

本模块不访问数据库、不访问网络、不修改全局状态，也不读取未来数据。
"""

from __future__ import annotations

import operator

import numpy as np
from numpy.typing import ArrayLike, NDArray

FloatArray = NDArray[np.float64]


def _as_float_1d(values: ArrayLike, name: str) -> FloatArray:
    """把输入转换为 float64 一维数组，并拒绝非法数据。"""

    array = np.asarray(values, dtype=np.float64)
    if array.ndim != 1:
        raise ValueError(f"{name} must be one-dimensional")
    if array.size and not np.all(np.isfinite(array)):
        raise ValueError(f"{name} contains NaN or infinite values")
    return array


def _validate_period(period: int, name: str = "period") -> int:
    """指标周期必须是正整数。"""

    if isinstance(period, bool):
        raise TypeError(f"{name} must be an integer")
    try:
        value = operator.index(period)
    except TypeError as exc:
        raise TypeError(f"{name} must be an integer") from exc
    if value <= 0:
        raise ValueError(f"{name} must be positive, got {value}")
    return value


def _ema_contiguous(values: FloatArray, period: int) -> FloatArray:
    """对无缺失值序列计算 TA-Lib 口径的 EMA。"""

    result = np.full(values.size, np.nan, dtype=np.float64)
    if values.size < period:
        return result
    alpha = 2.0 / (period + 1.0)
    previous = float(values[:period].mean())
    result[period - 1] = previous
    for index in range(period, values.size):
        previous += (float(values[index]) - previous) * alpha
        result[index] = previous
    return result


def _true_range(high: FloatArray, low: FloatArray, close: FloatArray) -> FloatArray:
    """计算真实波幅 TR，索引 0 没有前一根收盘价，因此为 NaN。"""

    result = np.full(high.size, np.nan, dtype=np.float64)
    if high.size < 2:
        return result
    previous_close = close[:-1]
    result[1:] = np.maximum.reduce(
        [
            high[1:] - low[1:],
            np.abs(high[1:] - previous_close),
            np.abs(low[1:] - previous_close),
        ]
    )
    return result


def _validate_ohlc(
    high: ArrayLike, low: ArrayLike, close: ArrayLike
) -> tuple[FloatArray, FloatArray, FloatArray]:
    """校验 OHLC 输入的长度与高低价关系。"""

    high_values = _as_float_1d(high, "high")
    low_values = _as_float_1d(low, "low")
    close_values = _as_float_1d(close, "close")
    if not high_values.size == low_values.size == close_values.size:
        raise ValueError("high, low and close must have the same length")
    if np.any(high_values < low_values):
        raise ValueError("high must be greater than or equal to low")
    return high_values, low_values, close_values


def sma(close: ArrayLike, n: int = 200) -> FloatArray:
    """简单移动平均，预热 ``n-1`` 根为 NaN。"""

    n = _validate_period(n, "n")
    values = _as_float_1d(close, "close")
    result = np.full(values.size, np.nan, dtype=np.float64)
    if values.size < n:
        return result
    cumulative = np.cumsum(np.insert(values, 0, 0.0))
    result[n - 1 :] = (cumulative[n:] - cumulative[:-n]) / n
    return result


def ema(close: ArrayLike, n: int = 200) -> FloatArray:
    """指数移动平均，使用前 ``n`` 个值的 SMA 作为种子。"""

    n = _validate_period(n, "n")
    return _ema_contiguous(_as_float_1d(close, "close"), n)


def macd(
    close: ArrayLike,
    fast: int = 12,
    slow: int = 26,
    signal: int = 9,
) -> tuple[FloatArray, FloatArray, FloatArray]:
    """MACD 指标，返回 ``(DIF, DEA, 柱状图)``。

    柱状图定义为 ``DIF - DEA``。为与 TA-Lib 精确对齐，快线 EMA 的种子不是
    从第 0 根开始，而是右对齐到慢线种子窗口末端（``slow - fast`` 至
    ``slow - 1``），信号线则用前 ``signal`` 个 DIF 的简单平均作为种子。
    TA-Lib 的 MACD 只从索引 ``slow + signal - 2`` 开始输出，本函数保持同样
    的对齐，预热期全部为 ``NaN``。
    """

    fast = _validate_period(fast, "fast")
    slow = _validate_period(slow, "slow")
    signal = _validate_period(signal, "signal")
    if fast >= slow:
        raise ValueError("fast must be less than slow")

    values = _as_float_1d(close, "close")
    size = values.size
    dif = np.full(size, np.nan, dtype=np.float64)
    if size < slow:
        return dif, dif.copy(), dif.copy()

    alpha_fast = 2.0 / (fast + 1.0)
    alpha_slow = 2.0 / (slow + 1.0)
    fast_value = float(values[slow - fast : slow].mean())
    slow_value = float(values[:slow].mean())
    dif[slow - 1] = fast_value - slow_value
    for index in range(slow, size):
        fast_value += (float(values[index]) - fast_value) * alpha_fast
        slow_value += (float(values[index]) - slow_value) * alpha_slow
        dif[index] = fast_value - slow_value

    dea = np.full(size, np.nan, dtype=np.float64)
    first_signal_index = slow + signal - 2
    if size > first_signal_index:
        dea[first_signal_index] = float(dif[slow - 1 : slow - 1 + signal].mean())
        alpha_signal = 2.0 / (signal + 1.0)
        for index in range(first_signal_index + 1, size):
            dea[index] = dea[index - 1] + (dif[index] - dea[index - 1]) * alpha_signal

    histogram = dif - dea
    first_output = min(first_signal_index, size)
    dif[:first_output] = np.nan
    dea[:first_output] = np.nan
    histogram[:first_output] = np.nan
    return dif, dea, histogram


def rsi(close: ArrayLike, n: int = 14) -> FloatArray:
    """相对强弱指数，使用 Wilder 平滑，取值范围 0~100。"""

    n = _validate_period(n, "n")
    values = _as_float_1d(close, "close")
    result = np.full(values.size, np.nan, dtype=np.float64)
    if values.size <= n:
        return result

    changes = np.diff(values)
    gains = np.maximum(changes, 0.0)
    losses = np.maximum(-changes, 0.0)
    average_gain = float(gains[:n].mean())
    average_loss = float(losses[:n].mean())
    result[n] = _rsi_value(average_gain, average_loss)

    for index in range(n + 1, values.size):
        average_gain = (average_gain * (n - 1) + float(gains[index - 1])) / n
        average_loss = (average_loss * (n - 1) + float(losses[index - 1])) / n
        result[index] = _rsi_value(average_gain, average_loss)
    return result


def _rsi_value(average_gain: float, average_loss: float) -> float:
    """由平均涨跌幅计算 RSI，处理全平走势和单边走势。"""

    if average_loss == 0.0:
        return 100.0 if average_gain > 0.0 else 0.0
    ratio = average_gain / average_loss
    return 100.0 - 100.0 / (1.0 + ratio)


def atr(high: ArrayLike, low: ArrayLike, close: ArrayLike, n: int = 14) -> FloatArray:
    """平均真实波幅，使用 Wilder 平滑，首个有效值位于索引 ``n``。"""

    n = _validate_period(n, "n")
    high_values, low_values, close_values = _validate_ohlc(high, low, close)
    result = np.full(high_values.size, np.nan, dtype=np.float64)
    if high_values.size <= n:
        return result

    true_range = _true_range(high_values, low_values, close_values)
    result[n] = float(true_range[1 : n + 1].mean())
    for index in range(n + 1, high_values.size):
        result[index] = (result[index - 1] * (n - 1) + float(true_range[index])) / n
    return result


def _directional_index(
    smoothed_plus_dm: float, smoothed_minus_dm: float, smoothed_tr: float
) -> float:
    """由平滑后的 +DM、-DM、TR 计算 DX，退化情况返回 0。"""

    if smoothed_tr <= 0.0:
        return 0.0
    plus_di = 100.0 * smoothed_plus_dm / smoothed_tr
    minus_di = 100.0 * smoothed_minus_dm / smoothed_tr
    denominator = plus_di + minus_di
    if denominator == 0.0:
        return 0.0
    return 100.0 * abs(plus_di - minus_di) / denominator


def adx(high: ArrayLike, low: ArrayLike, close: ArrayLike, n: int = 14) -> FloatArray:
    """平均趋向指数，首个有效值位于索引 ``2n-1``。

    初始化遵循 TA-Lib 的 ADX 口径：先用 ``n-1`` 根原始值建立初值，再对
    后续 ``n`` 根做 Wilder 平滑并累加 DX，以免与 TA-Lib 产生系统性偏差。
    """

    n = _validate_period(n, "n")
    high_values, low_values, close_values = _validate_ohlc(high, low, close)
    result = np.full(high_values.size, np.nan, dtype=np.float64)
    if high_values.size < 2 * n:
        return result

    true_range = _true_range(high_values, low_values, close_values)
    up_move = high_values[1:] - high_values[:-1]
    down_move = low_values[:-1] - low_values[1:]
    plus_dm = np.where((up_move > down_move) & (up_move > 0.0), up_move, 0.0)
    minus_dm = np.where((down_move > up_move) & (down_move > 0.0), down_move, 0.0)
    true_range_valid = true_range[1:]

    smoothed_tr = float(true_range_valid[: n - 1].sum())
    smoothed_plus = float(plus_dm[: n - 1].sum())
    smoothed_minus = float(minus_dm[: n - 1].sum())
    smoothing = (n - 1) / n

    sum_dx = 0.0
    for index in range(n, 2 * n):
        position = index - 1
        smoothed_tr = smoothed_tr * smoothing + float(true_range_valid[position])
        smoothed_plus = smoothed_plus * smoothing + float(plus_dm[position])
        smoothed_minus = smoothed_minus * smoothing + float(minus_dm[position])
        sum_dx += _directional_index(smoothed_plus, smoothed_minus, smoothed_tr)

    average_dx = sum_dx / n
    result[2 * n - 1] = average_dx
    for index in range(2 * n, high_values.size):
        position = index - 1
        smoothed_tr = smoothed_tr * smoothing + float(true_range_valid[position])
        smoothed_plus = smoothed_plus * smoothing + float(plus_dm[position])
        smoothed_minus = smoothed_minus * smoothing + float(minus_dm[position])
        dx = _directional_index(smoothed_plus, smoothed_minus, smoothed_tr)
        average_dx += (dx - average_dx) / n
        result[index] = average_dx
    return result


__all__ = ["adx", "atr", "ema", "macd", "rsi", "sma"]
