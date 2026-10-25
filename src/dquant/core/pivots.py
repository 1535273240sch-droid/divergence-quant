"""枢轴点识别（计划书 §5.6.2）。

经典枢轴法：某根 K 线的值是其左侧 ``left`` 根和右侧 ``right`` 根中的极值
即认定为枢轴点。枢轴点在右侧第 ``right`` 根 K 线收盘后才算确认，
``confirmed_index = index + right``，调用方必须按确认时刻使用信号，
否则会引入未来函数。

并列极值取最左出现者：枢轴值必须严格高于/低于左侧邻居，
且大于等于/小于等于右侧邻居。

本模块是纯函数：不访问数据库、不访问网络、不修改全局状态。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

import numpy as np
from numpy.typing import ArrayLike, NDArray

FloatArray = NDArray[np.float64]

PivotKind = Literal["high", "low"]


@dataclass(frozen=True, slots=True)
class PivotPoint:
    """一个已确认的枢轴点。

    ``index`` 与 ``confirmed_index`` 都是输入序列的整数下标；
    只有 ``confirmed_index`` 时刻之后该枢轴才可被信号逻辑使用。
    """

    index: int
    value: float
    kind: PivotKind
    confirmed_index: int


def _validate_values(values: ArrayLike) -> FloatArray:
    """校验输入是一维有限数值序列。"""

    array = np.asarray(values, dtype=np.float64)
    if array.ndim != 1:
        raise ValueError("values must be one-dimensional")
    if array.size and not np.all(np.isfinite(array)):
        raise ValueError("values contain NaN or infinite values")
    return array


def _validate_windows(left: int, right: int) -> tuple[int, int]:
    """校验窗口参数必须是正整数。"""

    for name, value in (("left", left), ("right", right)):
        if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
            raise ValueError(f"{name} must be a positive integer, got {value!r}")
    return left, right


def find_pivot_lows(values: ArrayLike, left: int = 5, right: int = 5) -> list[PivotPoint]:
    """识别枢轴低点：左侧 ``left`` 根严格更高、右侧 ``right`` 根不低于它。"""

    return _find_pivots(values, left, right, kind="low")


def find_pivot_highs(values: ArrayLike, left: int = 5, right: int = 5) -> list[PivotPoint]:
    """识别枢轴高点：左侧 ``left`` 根严格更低、右侧 ``right`` 根不高于它。"""

    return _find_pivots(values, left, right, kind="high")


def _find_pivots(
    values: ArrayLike,
    left: int,
    right: int,
    *,
    kind: PivotKind,
) -> list[PivotPoint]:
    """枢轴识别的统一实现。

    每个枢轴只依赖 ``[index-left, index+right]`` 窗口内的数据，
    因此对任意前缀 ``values[:cut]`` 的计算结果与全量计算中
    ``confirmed_index < cut`` 的子集完全一致（无未来函数）。
    """

    left, right = _validate_windows(left, right)
    array = _validate_values(values)
    total = array.size
    pivots: list[PivotPoint] = []

    start = min(left, max(total - 1, 0))
    for index in range(start, max(total - right, start)):
        window_left = array[max(index - left, 0) : index]
        window_right = array[index + 1 : index + right + 1]
        value = float(array[index])
        if kind == "low":
            left_ok = bool(np.all(window_left > value))
            right_ok = bool(np.all(window_right >= value))
        else:
            left_ok = bool(np.all(window_left < value))
            right_ok = bool(np.all(window_right <= value))
        if left_ok and right_ok:
            pivots.append(
                PivotPoint(index=index, value=value, kind=kind, confirmed_index=index + right)
            )
    return pivots


__all__ = ["PivotKind", "PivotPoint", "find_pivot_highs", "find_pivot_lows"]
