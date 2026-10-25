"""持仓中的反向背离出场和日线破 MA200 移动保本。"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Literal

from dquant.contracts import (
    DivergenceKind,
    DivergenceSignal,
    OrderRequest,
    OrderType,
    Position,
    PositionSide,
    SignalSide,
)


@dataclass(frozen=True, slots=True)
class PositionAction:
    """持仓管理动作；实际提交仍由 OMS 负责。"""

    kind: Literal["REVERSE_DIVERGENCE_EXIT", "MOVE_STOP_TO_BREAKEVEN"]
    order_request: OrderRequest
    reason: str
    new_stop_loss: Decimal | None = None


def build_position_actions(
    position: Position,
    *,
    reverse_divergence: DivergenceSignal | None,
    daily_close: Decimal | None,
    daily_ma200: Decimal | None,
    mode: Literal["demo", "live"],
    break_even_buffer: Decimal = Decimal("0.0012"),
) -> tuple[PositionAction, ...]:
    """根据已确认信号构建保护性平仓或移动止损请求。"""

    if break_even_buffer < 0 or break_even_buffer >= 1:
        raise ValueError("break_even_buffer must be between zero and one")

    if _is_reverse_divergence(position.pos_side, reverse_divergence):
        return (
            PositionAction(
                kind="REVERSE_DIVERGENCE_EXIT",
                order_request=_close_request(position, mode),
                reason="持仓出现已确认的 4H 反向背离，立即保护性平仓",
            ),
        )

    if daily_close is None or daily_ma200 is None or position.upl <= 0:
        return ()
    broke_ma = (position.pos_side is PositionSide.LONG and daily_close < daily_ma200) or (
        position.pos_side is PositionSide.SHORT and daily_close > daily_ma200
    )
    if not broke_ma:
        return ()

    if position.pos_side is PositionSide.LONG:
        new_stop = position.avg_price * (Decimal("1") - break_even_buffer)
        is_better = position.stop_loss is None or new_stop > position.stop_loss
    else:
        new_stop = position.avg_price * (Decimal("1") + break_even_buffer)
        is_better = position.stop_loss is None or new_stop < position.stop_loss
    if not is_better:
        return ()
    return (
        PositionAction(
            kind="MOVE_STOP_TO_BREAKEVEN",
            order_request=_stop_move_request(position, new_stop, mode),
            reason="日线突破 MA200 反向且当前浮盈，止损移动到保本缓冲价",
            new_stop_loss=new_stop,
        ),
    )


def _is_reverse_divergence(
    position_side: PositionSide, divergence: DivergenceSignal | None
) -> bool:
    if divergence is None or divergence.preliminary:
        return False
    if position_side is PositionSide.LONG:
        return divergence.kind in {DivergenceKind.BEAR_REGULAR, DivergenceKind.BEAR_HIDDEN}
    return divergence.kind in {DivergenceKind.BULL_REGULAR, DivergenceKind.BULL_HIDDEN}


def _close_request(position: Position, mode: Literal["demo", "live"]) -> OrderRequest:
    return OrderRequest(
        cl_ord_id=f"EXIT-{position.inst_id}-{position.pos_side.value}-{position.opened_ts_ms}",
        inst_id=position.inst_id,
        side=SignalSide.SHORT if position.pos_side is PositionSide.LONG else SignalSide.LONG,
        pos_side=position.pos_side,
        order_type=OrderType.MARKET,
        size=position.size,
        reduce_only=True,
        mode=mode,
    )


def _stop_move_request(
    position: Position,
    new_stop: Decimal,
    mode: Literal["demo", "live"],
) -> OrderRequest:
    return OrderRequest(
        cl_ord_id=f"BE-{position.inst_id}-{position.pos_side.value}-{position.opened_ts_ms}",
        inst_id=position.inst_id,
        side=SignalSide.SHORT if position.pos_side is PositionSide.LONG else SignalSide.LONG,
        pos_side=position.pos_side,
        order_type=OrderType.MARKET,
        size=position.size,
        stop_loss=new_stop,
        reduce_only=True,
        mode=mode,
    )
