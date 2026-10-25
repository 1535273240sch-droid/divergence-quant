"""执行模块的可替换存储端口和测试用内存实现。"""

from __future__ import annotations

from dataclasses import dataclass, replace
from decimal import Decimal
from typing import Protocol

from dquant.contracts import Fill, OrderRequest, OrderState, Position

from .errors import DuplicateOrderConflict, UnknownOrder
from .ports import BalanceSnapshot


@dataclass(frozen=True, slots=True)
class StoredOrder:
    """订单状态机在本地保存的最小记录。"""

    request: OrderRequest
    state: OrderState
    ord_id: str | None = None
    accepted_ts_ms: int | None = None
    message: str = ""
    filled_size: Decimal = Decimal("0")
    protected_size: Decimal = Decimal("0")


class ExecutionStore(Protocol):
    """OMS 与持久化实现之间的同步端口。"""

    def get_order(self, cl_ord_id: str) -> StoredOrder | None:
        """读取订单。"""

    def save_order(self, order: StoredOrder) -> None:
        """新增订单。"""

    def update_order(self, order: StoredOrder) -> None:
        """更新已有订单。"""

        ...

    def delete_order(self, cl_ord_id: str) -> None:
        """删除尚未重新提交的本地临时订单记录。"""

        ...

    def record_fill(self, fill: Fill) -> bool:
        """记录成交；返回 False 表示 trade_id 已经处理过。"""

        ...

    def list_orders(self) -> list[StoredOrder]:
        """列出本地订单。"""

        ...

    def replace_positions(self, positions: list[Position]) -> None:
        """以交易所快照替换本地持仓。"""

        ...

    def list_positions(self) -> list[Position]:
        """读取本地持仓。"""

        ...

    def set_balance(self, balance: BalanceSnapshot) -> None:
        """保存交易所账户快照。"""

        ...

    def get_balance(self) -> BalanceSnapshot | None:
        """读取最近账户快照。"""

        ...


class InMemoryExecutionStore:
    """测试和本地演练使用的内存存储，不代表生产数据库实现。"""

    def __init__(self) -> None:
        self._orders: dict[str, StoredOrder] = {}
        self._fills: dict[str, Fill] = {}
        self._positions: list[Position] = []
        self._balance: BalanceSnapshot | None = None

    def get_order(self, cl_ord_id: str) -> StoredOrder | None:
        return self._orders.get(cl_ord_id)

    def save_order(self, order: StoredOrder) -> None:
        if order.request.cl_ord_id in self._orders:
            raise DuplicateOrderConflict(f"order already exists: {order.request.cl_ord_id}")
        self._orders[order.request.cl_ord_id] = order

    def update_order(self, order: StoredOrder) -> None:
        if order.request.cl_ord_id not in self._orders:
            raise UnknownOrder(order.request.cl_ord_id)
        self._orders[order.request.cl_ord_id] = order

    def delete_order(self, cl_ord_id: str) -> None:
        if cl_ord_id not in self._orders:
            raise UnknownOrder(cl_ord_id)
        del self._orders[cl_ord_id]

    def record_fill(self, fill: Fill) -> bool:
        if fill.trade_id in self._fills:
            return False
        order = self.get_order(fill.cl_ord_id)
        if order is None:
            raise UnknownOrder(fill.cl_ord_id)
        self._fills[fill.trade_id] = fill
        self._orders[fill.cl_ord_id] = replace(
            order,
            filled_size=order.filled_size + fill.size,
        )
        return True

    def list_orders(self) -> list[StoredOrder]:
        return list(self._orders.values())

    def replace_positions(self, positions: list[Position]) -> None:
        self._positions = list(positions)

    def list_positions(self) -> list[Position]:
        return list(self._positions)

    def set_balance(self, balance: BalanceSnapshot) -> None:
        self._balance = balance

    def get_balance(self) -> BalanceSnapshot | None:
        return self._balance

    def add_submitted_order(
        self, request: OrderRequest, *, ord_id: str, accepted_ts_ms: int
    ) -> None:
        """为对账测试预置一个已提交订单。"""

        self.save_order(
            StoredOrder(
                request=request,
                state=OrderState.SUBMITTED,
                ord_id=ord_id,
                accepted_ts_ms=accepted_ts_ms,
            )
        )
