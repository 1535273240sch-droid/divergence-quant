"""订单状态机和客户端订单号生成。"""

from __future__ import annotations

import re
from collections.abc import Mapping

from dquant.contracts import OrderState

from .errors import InvalidOrderTransition

_ALLOWED_TRANSITIONS: Mapping[OrderState, frozenset[OrderState]] = {
    OrderState.PENDING: frozenset(
        {OrderState.SUBMITTED, OrderState.REJECTED, OrderState.FAILED, OrderState.CANCELED}
    ),
    OrderState.SUBMITTED: frozenset(
        {
            OrderState.PARTIAL,
            OrderState.FILLED,
            OrderState.CANCELED,
            OrderState.REJECTED,
            OrderState.FAILED,
        }
    ),
    OrderState.PARTIAL: frozenset(
        {OrderState.PARTIAL, OrderState.FILLED, OrderState.CANCELED, OrderState.FAILED}
    ),
    OrderState.FILLED: frozenset(),
    OrderState.CANCELED: frozenset(),
    OrderState.REJECTED: frozenset(),
    OrderState.FAILED: frozenset(),
}

TERMINAL_STATES = frozenset(
    {OrderState.FILLED, OrderState.CANCELED, OrderState.REJECTED, OrderState.FAILED}
)


def transition_order_state(current: OrderState, target: OrderState) -> OrderState:
    """校验并返回目标状态；重复回调迁移到同一状态是幂等的。"""

    if current == target:
        return current
    if target not in _ALLOWED_TRANSITIONS[current]:
        raise InvalidOrderTransition(f"invalid order transition: {current.value} -> {target.value}")
    return target


def build_client_order_id(strategy_prefix: str, timestamp_ms: int, sequence: int) -> str:
    """按计划书规则生成稳定、可审计且不含特殊字符的 clOrdId。"""

    prefix = re.sub(r"[^A-Za-z0-9]", "", strategy_prefix).upper()
    if not prefix:
        raise ValueError("strategy_prefix must contain at least one alphanumeric character")
    if len(prefix) > 8:
        prefix = prefix[:8]
    if timestamp_ms < 0:
        raise ValueError("timestamp_ms must not be negative")
    if sequence < 0 or sequence > 9999:
        raise ValueError("sequence must be between 0 and 9999")
    return f"{prefix}{timestamp_ms}{sequence:04d}"
