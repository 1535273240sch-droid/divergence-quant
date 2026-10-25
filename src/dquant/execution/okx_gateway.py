"""把 OKX 适配器接到 M09 OMS 所需的交易网关端口。"""

from __future__ import annotations

from decimal import Decimal

from dquant.adapters.models import OpenOrderSnapshot
from dquant.adapters.okx import OkxAdapter
from dquant.contracts import OrderAck, OrderRequest, OrderState, Position, PositionSide, SignalSide

from .ports import BalanceSnapshot, ExchangeGateway, ProtectionRequest, RemoteOrder


class OkxGateway(ExchangeGateway):
    """生产交易网关。

    该类只负责把公共契约转换为 OKX 适配器的请求/响应，不在 API 层复制交易所字段。
    """

    def __init__(self, adapter: OkxAdapter) -> None:
        self.adapter = adapter
        self._inst_by_cl_ord_id: dict[str, str] = {}

    async def place_order(self, request: OrderRequest) -> OrderAck:
        return await self.adapter.place_order(request)

    async def cancel_order(self, inst_id: str, cl_ord_id: str) -> None:
        await self.adapter.cancel_order(inst_id, cl_ord_id)

    async def place_protection_order(self, request: ProtectionRequest) -> str:
        return await self.adapter.place_protection_order(request)

    async def fetch_open_orders(self) -> list[RemoteOrder]:
        snapshots = await self.adapter.fetch_open_orders()
        result: list[RemoteOrder] = []
        for item in snapshots:
            if item.cl_ord_id:
                self._inst_by_cl_ord_id[item.cl_ord_id] = item.inst_id
                result.append(_remote_order_from_snapshot(item))
        return result

    async def fetch_order(self, cl_ord_id: str) -> RemoteOrder | None:
        inst_id = self._inst_by_cl_ord_id.get(cl_ord_id)
        if inst_id is None:
            return None
        item = await self.adapter.fetch_order_snapshot(inst_id, cl_ord_id=cl_ord_id)
        return _remote_order_from_snapshot(item) if item is not None else None

    async def fetch_positions(self) -> list[Position]:
        return await self.adapter.fetch_positions()

    async def fetch_balance(self) -> BalanceSnapshot:
        balance = await self.adapter.fetch_balance()
        usdt = balance.details.get("USDT")
        available = usdt.available if usdt else Decimal("0")
        return BalanceSnapshot(
            equity=balance.total_equity_usd,
            available=available,
            upl=Decimal("0"),
        )

    async def set_leverage(self, inst_id: str, lever: int, mgn_mode: str) -> None:
        await self.adapter.set_leverage(inst_id, lever, mgn_mode)

    async def set_position_mode(self, mode: str) -> None:
        await self.adapter.set_position_mode(mode)


def _remote_order_from_snapshot(snapshot: OpenOrderSnapshot) -> RemoteOrder:
    if not snapshot.cl_ord_id:
        raise ValueError("OKX order snapshot is missing clOrdId")
    signal_side = (
        SignalSide.LONG
        if (snapshot.side == "buy" and snapshot.pos_side == "long")
        or (snapshot.side == "sell" and snapshot.pos_side == "short")
        else SignalSide.SHORT
    )
    reduce_only = (snapshot.side == "sell" and signal_side is SignalSide.LONG) or (
        snapshot.side == "buy" and signal_side is SignalSide.SHORT
    )
    order_type = "market" if snapshot.order_type == "market" else "limit"
    from dquant.contracts import OrderRequest

    request = OrderRequest(
        cl_ord_id=snapshot.cl_ord_id,
        inst_id=snapshot.inst_id,
        side=signal_side,
        pos_side=PositionSide(snapshot.pos_side),
        order_type=order_type,
        price=snapshot.price,
        size=snapshot.size,
        reduce_only=reduce_only,
        mode="demo",
    )
    state = {
        "live": OrderState.SUBMITTED,
        "partially_filled": OrderState.PARTIAL,
        "filled": OrderState.FILLED,
        "canceled": OrderState.CANCELED,
        "cancelled": OrderState.CANCELED,
        "rejected": OrderState.REJECTED,
    }.get(snapshot.state, OrderState.SUBMITTED)
    return RemoteOrder(request=request, ord_id=snapshot.ord_id, state=state)
