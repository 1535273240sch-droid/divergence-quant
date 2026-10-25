"""M09 与交易所、持久化层之间的最小端口。

M02 的 OKX 适配器通过结构化类型实现这些方法；本文件不连接网络，也不复制交易所原始响应。
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Literal, Protocol

from dquant.contracts import (
    OrderAck,
    OrderRequest,
    OrderState,
    Position,
    PositionSide,
    TakeProfit,
)


@dataclass(frozen=True, slots=True)
class ProtectionRequest:
    """部分成交后要求适配器立即建立的交易所端保护单。"""

    source_cl_ord_id: str
    inst_id: str
    pos_side: PositionSide
    size: Decimal
    stop_loss: Decimal
    take_profits: tuple[TakeProfit, ...]
    mode: Literal["demo", "live"]


@dataclass(frozen=True, slots=True)
class RemoteOrder:
    """对账使用的交易所订单快照。"""

    request: OrderRequest
    ord_id: str
    state: OrderState


@dataclass(frozen=True, slots=True)
class BalanceSnapshot:
    """对账使用的最小账户权益快照。"""

    equity: Decimal
    available: Decimal
    upl: Decimal


class ExchangeGateway(Protocol):
    """M09 所需的交易所交易端口。

    真实实现由 M02 提供；测试使用明确标注的 Mock，不允许把这里替换成真实密钥连接。
    """

    async def place_order(self, request: OrderRequest) -> OrderAck:
        """提交订单，必须使用请求中的 clOrdId。"""

        ...

    async def cancel_order(self, inst_id: str, cl_ord_id: str) -> None:
        """撤销尚未完成的订单。"""

        ...

    async def place_protection_order(self, request: ProtectionRequest) -> str:
        """建立带 reduce-only 语义的交易所端保护单。"""

        ...

    async def fetch_open_orders(self) -> list[RemoteOrder]:
        """获取交易所当前挂单。"""

        ...

    async def fetch_order(self, cl_ord_id: str) -> RemoteOrder | None:
        """按 clOrdId 查询订单历史，用于启动恢复和挂单消失后的确认。"""

        ...

    async def fetch_positions(self) -> list[Position]:
        """获取交易所持仓快照。"""

        ...

    async def fetch_balance(self) -> BalanceSnapshot:
        """获取交易所账户权益快照。"""

        ...

    async def set_leverage(self, inst_id: str, lever: int, mgn_mode: str) -> None:
        """设置交易对杠杆和保证金模式。"""

        ...

    async def set_position_mode(self, mode: str) -> None:
        """设置双向持仓模式。"""

        ...
