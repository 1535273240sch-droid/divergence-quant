"""策略信号构造辅助函数。"""

from __future__ import annotations

from collections.abc import Container
from decimal import Decimal

from dquant.contracts import (
    DivergenceKind,
    PositionSide,
    Signal,
    SignalAction,
    SignalSide,
    TakeProfit,
)

from .base import StrategyContext, StrategyInputError


def as_decimal(value: object, field_name: str) -> Decimal:
    """把策略参数安全转换成 Decimal。"""

    try:
        result = value if isinstance(value, Decimal) else Decimal(str(value))
    except (ArithmeticError, ValueError) as exc:
        raise StrategyInputError(f"{field_name} must be a valid decimal") from exc
    if result <= 0:
        raise StrategyInputError(f"{field_name} must be greater than zero")
    return result


def structure_break(ctx: StrategyContext, side: SignalSide, lookback: int) -> bool:
    """判断当前收盘是否首次突破最近结构高点或低点。"""

    if lookback < 1:
        raise StrategyInputError("structure_lookback must be at least one")
    candles = ctx.trigger_candles
    if len(candles) < lookback + 1:
        return False

    current = candles[-1]
    previous = candles[-2]
    reference = candles[-lookback - 1 : -1]
    if side == SignalSide.LONG:
        reference_high = max(candle.high for candle in reference)
        return current.close > reference_high and previous.close <= reference_high

    reference_low = min(candle.low for candle in reference)
    return current.close < reference_low and previous.close >= reference_low


def divergence_is_current(
    ctx: StrategyContext,
    expected_kind: DivergenceKind | Container[DivergenceKind],
) -> bool:
    """确认背离类型匹配且已经在当前 K 线之前确认。"""

    divergence = ctx.divergence
    if divergence is None:
        return False
    expected = (
        divergence.kind in expected_kind
        if not isinstance(expected_kind, DivergenceKind)
        else divergence.kind == expected_kind
    )
    if not expected or divergence.preliminary:
        return False
    return divergence.confirmed_ts <= ctx.trigger_candles[-1].ts_ms


def divergence_structure_invalidated(ctx: StrategyContext, side: SignalSide) -> bool:
    """判断背离确认后是否已经跌破/突破 P2 极值。"""

    divergence = ctx.divergence
    if divergence is None:
        return False
    post_confirmation = [
        candle for candle in ctx.trigger_candles if candle.ts_ms > divergence.confirmed_ts
    ]
    if side == SignalSide.LONG:
        return any(candle.low < divergence.seg2_extreme_price for candle in post_confirmation)
    return any(candle.high > divergence.seg2_extreme_price for candle in post_confirmation)


def signal_identifier(
    strategy_name: str, version: str, ctx: StrategyContext, side: SignalSide
) -> str:
    """生成可重复调用且不会因重试改变的信号 ID。"""

    latest_ts = ctx.trigger_candles[-1].ts_ms
    return f"{strategy_name}:{version}:{ctx.candidate.inst_id}:{side.value}:{latest_ts}"


def build_open_signal(
    ctx: StrategyContext,
    *,
    strategy_name: str,
    version: str,
    side: SignalSide,
    stop_loss: Decimal,
    take_profits: list[TakeProfit],
    reason: str,
) -> Signal | None:
    """构造并校验开仓信号；价格关系不成立时返回无信号。"""

    price = ctx.trigger_candles[-1].close
    if side == SignalSide.LONG and stop_loss >= price:
        return None
    if side == SignalSide.SHORT and stop_loss <= price:
        return None
    for take_profit in take_profits:
        if side == SignalSide.LONG and take_profit.price <= price:
            return None
        if side == SignalSide.SHORT and take_profit.price >= price:
            return None

    return Signal(
        signal_id=signal_identifier(strategy_name, version, ctx, side),
        inst_id=ctx.candidate.inst_id,
        side=side,
        action=SignalAction.OPEN,
        entry_type="market",
        price=price,
        stop_loss=stop_loss,
        take_profits=take_profits,
        reason=reason,
        strategy_name=strategy_name,
        created_ts_ms=ctx.now_ts_ms or ctx.trigger_candles[-1].ts_ms,
    )


def build_close_signal(ctx: StrategyContext, side: SignalSide, reason: str) -> Signal:
    """构造平仓信号。"""

    strategy_name = ctx.position.strategy if ctx.position is not None else "strategy"
    return Signal(
        signal_id=signal_identifier(strategy_name, "managed", ctx, side) + ":close",
        inst_id=ctx.candidate.inst_id,
        side=side,
        action=SignalAction.CLOSE,
        entry_type="market",
        price=ctx.trigger_candles[-1].close,
        reason=reason,
        strategy_name=strategy_name,
        created_ts_ms=ctx.now_ts_ms or ctx.trigger_candles[-1].ts_ms,
    )


def build_move_stop_signal(
    ctx: StrategyContext,
    side: SignalSide,
    stop_loss: Decimal,
    reason: str,
) -> Signal:
    """构造移动止损信号。"""

    strategy_name = ctx.position.strategy if ctx.position is not None else "strategy"
    return Signal(
        signal_id=signal_identifier(strategy_name, "managed", ctx, side) + ":move-stop",
        inst_id=ctx.candidate.inst_id,
        side=side,
        action=SignalAction.MOVE_STOP,
        entry_type="market",
        price=ctx.trigger_candles[-1].close,
        stop_loss=stop_loss,
        reason=reason,
        strategy_name=strategy_name,
        created_ts_ms=ctx.now_ts_ms or ctx.trigger_candles[-1].ts_ms,
    )


def position_signal_side(position_side: PositionSide) -> SignalSide:
    """将 OKX 持仓方向映射为策略信号方向。"""

    return SignalSide.LONG if position_side == PositionSide.LONG else SignalSide.SHORT
