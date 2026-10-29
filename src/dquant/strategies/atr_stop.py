"""ATR 初始止损与移动止损纯函数。

所有函数使用 ``Decimal``，不访问网络、数据库或全局状态。方向使用
``SignalSide``/``PositionSide`` 的字符串值，方便策略与执行层共享。
"""

from __future__ import annotations

from decimal import Decimal

from dquant.contracts import PositionSide, SignalSide

SideLike = SignalSide | PositionSide | str


def _side(value: SideLike) -> str:
    if isinstance(value, (SignalSide, PositionSide)):
        return value.value
    text = str(value).strip().lower()
    if text in {"long", "buy"}:
        return "long"
    if text in {"short", "sell"}:
        return "short"
    raise ValueError(f"unsupported side: {value!r}")


def _positive_decimal(value: object, name: str) -> Decimal:
    result = value if isinstance(value, Decimal) else Decimal(str(value))
    if not result.is_finite() or result <= 0:
        raise ValueError(f"{name} must be a positive decimal")
    return result


def initial_stop(side: SideLike, entry: Decimal, atr: Decimal, atr_mult: Decimal) -> Decimal:
    """按入场价 ± atr_mult × ATR 计算初始保护止损。"""

    entry_d = _positive_decimal(entry, "entry")
    atr_d = _positive_decimal(atr, "atr")
    mult_d = _positive_decimal(atr_mult, "atr_mult")
    distance = atr_d * mult_d
    stop = entry_d - distance if _side(side) == "long" else entry_d + distance
    if stop <= 0:
        raise ValueError("initial stop must be positive")
    return stop


def trailing_stop(
    side: SideLike,
    reference_price: Decimal,
    atr: Decimal,
    atr_mult: Decimal,
    current_stop: Decimal | None,
) -> Decimal:
    """计算候选移动止损；只在比当前止损更有利时返回新值。

    多头参考价取入场后最高价，空头取最低价。调用方负责用最新 K 线维护
    参考价；本函数不读取未来数据。
    """

    reference_d = _positive_decimal(reference_price, "reference_price")
    atr_d = _positive_decimal(atr, "atr")
    mult_d = _positive_decimal(atr_mult, "atr_mult")
    distance = atr_d * mult_d
    if _side(side) == "long":
        candidate = reference_d - distance
        if candidate <= 0:
            return current_stop if current_stop is not None else candidate
        if current_stop is not None and candidate <= current_stop:
            return current_stop
        return candidate
    candidate = reference_d + distance
    if current_stop is not None and candidate >= current_stop:
        return current_stop
    return candidate


__all__ = ["initial_stop", "trailing_stop"]
