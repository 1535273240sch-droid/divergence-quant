"""交易所适配器抽象接口（计划书 §5.1.1）。

M02 负责实现行情与账户查询部分；下单、撤单、杠杆设置与 WebSocket 订阅由 M09
基于同一抽象补全。任何实现都不允许对未覆盖的方法静默返回成功。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable
from typing import TypeAlias

from dquant.adapters.models import Balance, FundingRate, OpenOrderSnapshot
from dquant.contracts import (
    Candle,
    CandleBar,
    Instrument,
    InstrumentType,
    OrderAck,
    OrderRequest,
    Position,
    Ticker,
)

CandleCallback: TypeAlias = Callable[[list[Candle]], Awaitable[None]]
OrderCallback: TypeAlias = Callable[[object], Awaitable[None]]


class ExchangeAdapter(ABC):
    """跨交易所的统一适配接口。时间戳一律为 UTC 毫秒。"""

    @abstractmethod
    async def load_instruments(self, inst_type: InstrumentType) -> list[Instrument]:
        """拉取交易对元数据。"""

    @abstractmethod
    async def fetch_tickers(self, inst_type: InstrumentType) -> list[Ticker]:
        """拉取最新行情快照。"""

    @abstractmethod
    async def fetch_candles(
        self,
        inst_id: str,
        bar: CandleBar,
        since_ms: int | None,
        limit: int,
    ) -> list[Candle]:
        """拉取近期 K 线，返回按 ``ts_ms`` 升序排列的结果。"""

    @abstractmethod
    async def fetch_balance(self) -> Balance:
        """拉取账户权益。"""

    @abstractmethod
    async def fetch_positions(self) -> list[Position]:
        """拉取当前持仓（只返回数量大于 0 的持仓）。"""

    @abstractmethod
    async def fetch_open_orders(self) -> list[OpenOrderSnapshot]:
        """拉取当前挂单。"""

    @abstractmethod
    async def fetch_funding_rate(self, inst_id: str) -> FundingRate:
        """拉取永续合约资金费率。"""

    @abstractmethod
    async def fetch_server_time_ms(self) -> int:
        """拉取交易所服务器时间（UTC 毫秒）。"""

    @abstractmethod
    async def sync_time(self) -> int:
        """校准服务器时间，返回 ``server_ts - local_ts``（毫秒）；偏差过大时告警。"""

    @abstractmethod
    async def fetch_history_candles(
        self,
        inst_id: str,
        bar: CandleBar,
        *,
        older_than_ms: int | None = None,
        newer_than_ms: int | None = None,
        limit: int,
    ) -> list[Candle]:
        """拉取历史 K 线单页，返回按 ``ts_ms`` 升序排列的结果。

        ``older_than_ms`` 对应 OKX ``after``（返回早于该时间戳的记录），
        ``newer_than_ms`` 对应 OKX ``before``（返回晚于该时间戳的记录）。
        """

    # ---- 以下方法属于 M09 的执行域，M02 只定义抽象边界 ----

    @abstractmethod
    async def place_order(self, req: OrderRequest) -> OrderAck:
        """下单（M09 实现）。"""

    @abstractmethod
    async def cancel_order(self, inst_id: str, cl_ord_id: str) -> None:
        """撤单（M09 实现）。"""

    @abstractmethod
    async def set_leverage(self, inst_id: str, lever: int, mgn_mode: str) -> None:
        """设置杠杆（M09 实现）。"""

    @abstractmethod
    async def subscribe_candles(
        self,
        inst_ids: list[str],
        bar: CandleBar,
        callback: CandleCallback,
    ) -> None:
        """订阅 K 线推送（M09/M13 集成 WebSocket 时实现）。"""

    @abstractmethod
    async def subscribe_orders(self, callback: OrderCallback) -> None:
        """订阅订单推送（M09/M13 集成 WebSocket 时实现）。"""
