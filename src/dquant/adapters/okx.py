"""OKX 模拟盘适配器（计划书 §5.1.2、T1.4）。

设计要点：

- M02 阶段强制 demo（模拟盘）模式，构造时直接拒绝 live/backtest 模式。
- REST 传输抽象为 :class:`OkxTransport`，生产实现 :class:`UrllibTransport` 只用
  Python 标准库（项目不允许本模块新增第三方依赖）；测试可注入录制响应。
- demo 模式所有请求都带 ``x-simulated-trading: 1`` 头。
- 每次请求经过令牌桶限流，可重试错误按指数退避重试。
- 所有解析失败都包装为 ``ExchangeError(code="PARSE_ERROR")``，不静默丢数据。
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import logging
import urllib.error
import urllib.parse
import urllib.request
from abc import ABC, abstractmethod
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from urllib.error import HTTPError, URLError

from pydantic import ValidationError

from dquant.adapters.base import ExchangeAdapter
from dquant.adapters.errors import ExchangeError, classify_okx_code, http_status_to_retriable
from dquant.adapters.models import Balance, CurrencyBalance, FundingRate, OpenOrderSnapshot
from dquant.adapters.ratelimit import TokenBucketLimiter
from dquant.adapters.retry import RetryPolicy
from dquant.contracts import (
    Candle,
    CandleBar,
    Instrument,
    InstrumentState,
    InstrumentType,
    Position,
    Ticker,
)

logger = logging.getLogger("dquant.adapters")

TIME_SYNC_WARN_OFFSET_MS = 1000

_PUBLIC_TIME_PATH = "/api/v5/public/time"
_INSTRUMENTS_PATH = "/api/v5/public/instruments"
_TICKERS_PATH = "/api/v5/market/tickers"
_CANDLES_PATH = "/api/v5/market/candles"
_HISTORY_CANDLES_PATH = "/api/v5/market/history-candles"
_FUNDING_RATE_PATH = "/api/v5/public/funding-rate"
_BALANCE_PATH = "/api/v5/account/balance"
_POSITIONS_PATH = "/api/v5/account/positions"
_ORDERS_PENDING_PATH = "/api/v5/trade/orders-pending"

MAX_CANDLES_LIMIT = 300
MAX_HISTORY_CANDLES_LIMIT = 100

_INSTRUMENT_STATE_MAP = {
    "live": InstrumentState.LIVE,
    "suspend": InstrumentState.SUSPENDED,
    "expired": InstrumentState.EXPIRED,
}

_PARSE_ERROR_TYPES = (ValidationError, KeyError, IndexError, ValueError, TypeError)


def iso_timestamp_ms(ts_ms: int) -> str:
    """把 UTC 毫秒时间戳转成 OKX 签名要求的 ``YYYY-MM-DDTHH:MM:SS.sssZ`` 格式。"""

    dt = datetime.fromtimestamp(ts_ms / 1000, tz=UTC)
    return dt.strftime("%Y-%m-%dT%H:%M:%S.") + f"{ts_ms % 1000:03d}Z"


def build_signature(secret: str, timestamp: str, method: str, request_path: str, body: str) -> str:
    """按 OKX 规则计算 ``OK-ACCESS-SIGN``：Base64(HMAC-SHA256(secret, ts+method+path+body))。"""

    message = f"{timestamp}{method.upper()}{request_path}{body}".encode()
    mac = hmac.new(secret.encode("utf-8"), message, hashlib.sha256)
    return base64.b64encode(mac.digest()).decode("ascii")


def build_request_headers(
    credentials: OkxCredentials | None,
    method: str,
    path_with_query: str,
    body: str,
    *,
    simulated_trading: bool,
    timestamp_ms: int,
) -> dict[str, str]:
    """组装请求头。私有接口需要凭证，缺凭证在这里显式报错。"""

    headers = {"Content-Type": "application/json"}
    if simulated_trading:
        headers["x-simulated-trading"] = "1"
    if credentials is None:
        return headers
    ts = iso_timestamp_ms(timestamp_ms)
    headers["OK-ACCESS-KEY"] = credentials.api_key
    headers["OK-ACCESS-SIGN"] = build_signature(
        credentials.secret, ts, method, path_with_query, body
    )
    headers["OK-ACCESS-TIMESTAMP"] = ts
    headers["OK-ACCESS-PASSPHRASE"] = credentials.passphrase
    return headers


@dataclass(frozen=True)
class OkxCredentials:
    """OKX API 凭证。只允许从环境变量等安全来源构造，禁止写进代码或配置文件。"""

    api_key: str
    secret: str
    passphrase: str


class OkxTransport(ABC):
    """OKX REST 传输抽象。返回解析后的 JSON 对象，网络/HTTP 层错误抛 ExchangeError。"""

    @abstractmethod
    async def request(
        self,
        method: str,
        path: str,
        params: Mapping[str, str] | None,
        *,
        auth: bool,
    ) -> Any:
        """发起一次请求。``params`` 的值必须是字符串。"""


class UrllibTransport(OkxTransport):
    """基于标准库 urllib 的传输实现，线程池中执行以保持异步接口。"""

    def __init__(
        self,
        base_url: str,
        credentials: OkxCredentials | None = None,
        *,
        simulated_trading: bool = True,
        timeout_sec: float = 10.0,
    ) -> None:
        if not base_url.startswith("https://"):
            raise ValueError("base_url must use https")
        self.base_url = base_url.rstrip("/")
        self.credentials = credentials
        self.simulated_trading = simulated_trading
        self.timeout_sec = timeout_sec
        # 签名时间戳偏移量（毫秒），由调用方用 fetch_server_time_ms 的结果校准。
        self.clock_offset_ms = 0

    async def request(
        self,
        method: str,
        path: str,
        params: Mapping[str, str] | None,
        *,
        auth: bool,
    ) -> Any:
        return await asyncio.to_thread(self._request_sync, method, path, params, auth)

    def _request_sync(
        self,
        method: str,
        path: str,
        params: Mapping[str, str] | None,
        auth: bool,
    ) -> Any:
        query = ""
        if params:
            query = "?" + urllib.parse.urlencode(dict(sorted(params.items())))
        path_with_query = path + query
        url = self.base_url + path_with_query
        body = ""
        credentials = self.credentials
        if auth and credentials is None:
            raise ExchangeError(
                "NO_CREDENTIALS",
                f"调用私有接口 {path} 需要先配置 OKX API 凭证",
                retriable=False,
            )
        headers = build_request_headers(
            credentials,
            method,
            path_with_query,
            body,
            simulated_trading=self.simulated_trading,
            timestamp_ms=_utc_now_ms() + self.clock_offset_ms,
        )
        request = urllib.request.Request(url, data=None, headers=headers, method=method.upper())
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_sec) as response:
                raw = response.read()
        except HTTPError as exc:
            code, msg = _extract_error_body(exc)
            raise ExchangeError(
                code or str(exc.code),
                msg or f"HTTP {exc.code} from {path}",
                retriable=http_status_to_retriable(exc.code),
                http_status=exc.code,
            ) from exc
        except (URLError, TimeoutError, OSError) as exc:
            raise ExchangeError(
                "NETWORK_ERROR", f"网络错误访问 {path}: {exc}", retriable=True
            ) from exc

        try:
            return json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ExchangeError(
                "PARSE_ERROR", f"响应不是合法 JSON: {path}: {exc}", retriable=False
            ) from exc


def _utc_now_ms() -> int:
    return int(datetime.now(tz=UTC).timestamp() * 1000)


def _extract_error_body(exc: HTTPError) -> tuple[str | None, str | None]:
    """尽量从 HTTPError 响应体里取 OKX 业务错误码，便于 429/50011 分类。"""

    try:
        payload = json.loads(exc.read().decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None, None
    if isinstance(payload, dict):
        code = payload.get("code")
        msg = payload.get("msg")
        return (
            str(code) if code is not None else None,
            str(msg) if msg is not None else None,
        )
    return None, None


class OkxAdapter(ExchangeAdapter):
    """OKX 模拟盘适配器。实现 M02 范围内的方法；交易与订阅方法显式声明未实现。"""

    def __init__(
        self,
        transport: OkxTransport,
        *,
        mode: str = "demo",
        rate_per_sec: float = 5.0,
        rate_capacity: float = 5.0,
        max_attempts: int = 3,
        base_delay_sec: float = 0.5,
        max_delay_sec: float = 8.0,
    ) -> None:
        if mode != "demo":
            raise ExchangeError(
                "MODE_FORBIDDEN",
                f"M02 适配器只允许 demo 模式，收到 mode={mode!r}；实盘接入由 M09 按安全门禁实现",
                retriable=False,
            )
        self.mode = mode
        self._transport = transport
        self._limiter = TokenBucketLimiter(rate_per_sec, rate_capacity)
        self._retry = RetryPolicy(
            max_attempts=max_attempts,
            base_delay_sec=base_delay_sec,
            max_delay_sec=max_delay_sec,
        )

    # ---- 基础请求编排 ----

    async def _call(
        self, method: str, path: str, params: Mapping[str, str] | None, auth: bool
    ) -> Any:
        """发起一次限流 + 重试的请求，返回校验后的 ``data`` 字段。"""

        async def operation() -> Any:
            await self._limiter.acquire()
            payload = await self._transport.request(method, path, params, auth=auth)
            return _validate_envelope(path, payload)["data"]

        return await self._retry.run(operation)

    # ---- 行情与公共接口 ----

    async def load_instruments(self, inst_type: InstrumentType) -> list[Instrument]:
        data = await self._call("GET", _INSTRUMENTS_PATH, {"instType": inst_type.value}, auth=False)
        instruments: list[Instrument] = []
        for item in data:
            try:
                instrument = _parse_instrument(item)
            except ExchangeError as exc:
                if exc.code == "UNSUPPORTED_STATE":
                    logger.warning("跳过不支持状态的交易对 %s: %s", item.get("instId"), exc.msg)
                    continue
                raise
            instruments.append(instrument)
        return instruments

    async def fetch_tickers(self, inst_type: InstrumentType) -> list[Ticker]:
        data = await self._call("GET", _TICKERS_PATH, {"instType": inst_type.value}, auth=False)
        tickers: list[Ticker] = []
        for item in data:
            try:
                tickers.append(_parse_ticker(item))
            except ExchangeError as exc:
                if exc.code == "EMPTY_PRICE":
                    logger.warning("跳过无最新价的行情快照 %s", item.get("instId"))
                    continue
                raise
        return tickers

    async def fetch_candles(
        self,
        inst_id: str,
        bar: CandleBar,
        since_ms: int | None,
        limit: int,
    ) -> list[Candle]:
        """返回 ``since_ms`` 之后最新的 ``limit`` 根 K 线（升序）。

        注意：若窗口内 K 线数量超过 ``limit``，早期部分不会被本接口返回，
        更深的缺口必须用 :meth:`fetch_history_candles` 分页补齐。
        """
        if not 1 <= limit <= MAX_CANDLES_LIMIT:
            raise ValueError(f"limit must be within 1..{MAX_CANDLES_LIMIT}")
        params: dict[str, str] = {"instId": inst_id, "bar": bar.value, "limit": str(limit)}
        if since_ms is not None:
            params["before"] = str(since_ms)
        data = await self._call("GET", _CANDLES_PATH, params, auth=False)
        return _parse_candle_rows(inst_id, bar, data)

    async def fetch_history_candles(
        self,
        inst_id: str,
        bar: CandleBar,
        *,
        older_than_ms: int | None = None,
        newer_than_ms: int | None = None,
        limit: int,
    ) -> list[Candle]:
        if not 1 <= limit <= MAX_HISTORY_CANDLES_LIMIT:
            raise ValueError(f"limit must be within 1..{MAX_HISTORY_CANDLES_LIMIT}")
        params: dict[str, str] = {"instId": inst_id, "bar": bar.value, "limit": str(limit)}
        if older_than_ms is not None:
            params["after"] = str(older_than_ms)
        if newer_than_ms is not None:
            params["before"] = str(newer_than_ms)
        data = await self._call("GET", _HISTORY_CANDLES_PATH, params, auth=False)
        return _parse_candle_rows(inst_id, bar, data)

    async def fetch_funding_rate(self, inst_id: str) -> FundingRate:
        data = await self._call("GET", _FUNDING_RATE_PATH, {"instId": inst_id}, auth=False)
        try:
            item = data[0]
            return FundingRate(
                inst_id=str(item["instId"]),
                funding_rate=Decimal(str(item["fundingRate"])),
                funding_time_ms=int(item["fundingTime"]),
                next_funding_time_ms=int(item["nextFundingTime"]),
            )
        except _PARSE_ERROR_TYPES as exc:
            raise _wrap_parse_error("funding-rate", inst_id, exc) from exc

    async def fetch_server_time_ms(self) -> int:
        data = await self._call("GET", _PUBLIC_TIME_PATH, None, auth=False)
        try:
            return int(data[0]["ts"])
        except _PARSE_ERROR_TYPES as exc:
            raise _wrap_parse_error("public/time", "", exc) from exc

    async def sync_time(self) -> int:
        """校准服务器时间，返回 ``server_ts - local_ts``（毫秒）。

        偏差超过 1 秒时记录告警日志（计划书 §5.1.2）。调用方应把返回值写入
        ``UrllibTransport.clock_offset_ms``，供签名时间戳使用。
        """
        server_ts = await self.fetch_server_time_ms()
        offset = server_ts - _utc_now_ms()
        if abs(offset) > TIME_SYNC_WARN_OFFSET_MS:
            logger.warning(
                "OKX 服务器时间与本地时间偏差 %d ms（> %d ms），签名时间戳需要校准",
                offset,
                TIME_SYNC_WARN_OFFSET_MS,
            )
        return offset

    # ---- 账户查询 ----

    async def fetch_balance(self) -> Balance:
        data = await self._call("GET", _BALANCE_PATH, None, auth=True)
        try:
            item = data[0]
            details: dict[str, CurrencyBalance] = {}
            for row in item.get("details", []):
                currency = str(row["ccy"])
                available = row.get("availEq") or row.get("availBal") or "0"
                details[currency] = CurrencyBalance(
                    currency=currency,
                    equity=Decimal(str(row.get("eq") or "0")),
                    available=Decimal(str(available)),
                    frozen=Decimal(str(row.get("frozenBal") or "0")),
                )
            return Balance(
                ts_ms=_utc_now_ms(),
                total_equity_usd=Decimal(str(item.get("totalEq") or "0")),
                details=details,
            )
        except _PARSE_ERROR_TYPES as exc:
            raise _wrap_parse_error("balance", "", exc) from exc

    async def fetch_positions(self) -> list[Position]:
        data = await self._call("GET", _POSITIONS_PATH, None, auth=True)
        positions: list[Position] = []
        for item in data:
            try:
                pos = Decimal(str(item["pos"]))
            except _PARSE_ERROR_TYPES as exc:
                raise _wrap_parse_error("positions", str(item.get("instId")), exc) from exc
            if pos <= 0:
                logger.warning("忽略数量为 0 的持仓记录 %s", item.get("instId"))
                continue
            positions.append(_parse_position(item))
        return positions

    async def fetch_open_orders(self) -> list[OpenOrderSnapshot]:
        data = await self._call("GET", _ORDERS_PENDING_PATH, None, auth=True)
        orders: list[OpenOrderSnapshot] = []
        for item in data:
            try:
                cl_ord_id = item.get("clOrdId")
                orders.append(
                    OpenOrderSnapshot(
                        ord_id=str(item["ordId"]),
                        cl_ord_id=str(cl_ord_id) if cl_ord_id else None,
                        inst_id=str(item["instId"]),
                        side=str(item["side"]),
                        pos_side=str(item["posSide"]),
                        order_type=str(item["ordType"]),
                        price=Decimal(str(item["px"])) if item.get("px") else None,
                        size=Decimal(str(item["sz"])),
                        state=str(item["state"]),
                    )
                )
            except _PARSE_ERROR_TYPES as exc:
                raise _wrap_parse_error("orders-pending", str(item.get("instId")), exc) from exc
        return orders

    # ---- M09 执行域：M02 阶段显式未实现 ----

    async def place_order(self, req: Any) -> Any:
        raise NotImplementedError("place_order 属于 M09 执行域，M02 未实现")

    async def cancel_order(self, inst_id: str, cl_ord_id: str) -> None:
        raise NotImplementedError("cancel_order 属于 M09 执行域，M02 未实现")

    async def set_leverage(self, inst_id: str, lever: int, mgn_mode: str) -> None:
        raise NotImplementedError("set_leverage 属于 M09 执行域，M02 未实现")

    async def subscribe_candles(self, inst_ids: list[str], bar: CandleBar, callback: Any) -> None:
        raise NotImplementedError("subscribe_candles 需要 WebSocket 通道，由 M09/M13 集成时实现")

    async def subscribe_orders(self, callback: Any) -> None:
        raise NotImplementedError("subscribe_orders 需要 WebSocket 通道，由 M09/M13 集成时实现")


def _validate_envelope(path: str, payload: Any) -> Any:
    """校验 OKX 响应信封并返回成功信封本身。"""

    if not isinstance(payload, dict) or "code" not in payload or "data" not in payload:
        raise ExchangeError(
            "PARSE_ERROR", f"响应缺少 OKX 信封字段 code/data: {path}", retriable=False
        )
    code = str(payload["code"])
    if code != "0":
        msg = str(payload.get("msg", ""))
        raise ExchangeError(
            code, msg or f"OKX 返回错误码 {code}: {path}", retriable=classify_okx_code(code)
        )
    return payload


def _wrap_parse_error(endpoint: str, context: str, exc: Exception) -> ExchangeError:
    detail = f"{context}: {exc}" if context else str(exc)
    return ExchangeError("PARSE_ERROR", f"解析 {endpoint} 响应失败: {detail}", retriable=False)


def _parse_instrument(item: dict[str, Any]) -> Instrument:
    inst_id = str(item["instId"])
    parts = inst_id.split("-")
    state = str(item["state"])
    mapped_state = _INSTRUMENT_STATE_MAP.get(state)
    if mapped_state is None:
        raise ExchangeError("UNSUPPORTED_STATE", f"未收录的交易对状态 {state!r}", retriable=False)
    try:
        return Instrument(
            inst_id=inst_id,
            inst_type=InstrumentType(str(item["instType"])),
            base=parts[0],
            quote=parts[1],
            ct_val=Decimal(str(item["ctVal"])),
            lot_sz=Decimal(str(item["lotSz"])),
            min_sz=Decimal(str(item["minSz"])),
            tick_sz=Decimal(str(item["tickSz"])),
            state=mapped_state,
            list_time_ms=int(item["listTime"]) if item.get("listTime") else None,
        )
    except _PARSE_ERROR_TYPES as exc:
        raise _wrap_parse_error("instruments", inst_id, exc) from exc


def _parse_ticker(item: dict[str, Any]) -> Ticker:
    inst_id = str(item["instId"])
    try:
        last = Decimal(str(item["last"]))
        if last <= 0:
            raise ExchangeError("EMPTY_PRICE", "最新价缺失或非正数", retriable=False)
        # 对 SWAP，OKX volCcy24h 是基础币成交量，USDT 计价成交量 = volCcy24h × last。
        quote_volume = Decimal(str(item["volCcy24h"])) * last
        bid_px = item.get("bidPx")
        ask_px = item.get("askPx")
        return Ticker(
            inst_id=inst_id,
            last=last,
            bid=Decimal(str(bid_px)) if bid_px else None,
            ask=Decimal(str(ask_px)) if ask_px else None,
            quote_volume_24h=quote_volume,
            ts_ms=int(item["ts"]),
        )
    except _PARSE_ERROR_TYPES as exc:
        raise _wrap_parse_error("tickers", inst_id, exc) from exc


def _parse_candle_rows(inst_id: str, bar: CandleBar, rows: Any) -> list[Candle]:
    if not isinstance(rows, list):
        raise _wrap_parse_error("candles", inst_id, TypeError("data 不是列表"))
    candles: list[Candle] = []
    for row in rows:
        try:
            if not isinstance(row, list) or len(row) < 6:
                raise IndexError(f"K 线行字段数不足: {row!r}")
            # OKX 行格式: [ts, o, h, l, c, vol(张), volCcy(币), volCcyQuote(USDT), confirm]
            ts_ms = int(row[0])
            # history-candles 的历史行可能省略 confirm 字段；该接口语义为只返回已收盘数据。
            confirm = row[8] == "1" if len(row) > 8 else True
            candles.append(
                Candle(
                    inst_id=inst_id,
                    bar=bar,
                    ts_ms=ts_ms,
                    open=Decimal(str(row[1])),
                    high=Decimal(str(row[2])),
                    low=Decimal(str(row[3])),
                    close=Decimal(str(row[4])),
                    volume=Decimal(str(row[5])),
                    quote_volume=Decimal(str(row[7])) if len(row) > 7 and row[7] else Decimal("0"),
                    confirmed=confirm,
                )
            )
        except _PARSE_ERROR_TYPES as exc:
            raise _wrap_parse_error("candles", f"{inst_id} row={row!r}", exc) from exc
    candles.sort(key=lambda item: item.ts_ms)
    return candles


def _parse_position(item: dict[str, Any]) -> Position:
    inst_id = str(item["instId"])
    try:
        pos_side = str(item["posSide"])
        if pos_side not in ("long", "short"):
            raise ValueError(
                f"持仓方向 {pos_side!r} 不是双向持仓模式（long/short），请检查账户持仓模式配置"
            )
        for field in ("avgPx", "markPx", "upl", "cTime"):
            if item.get(field) in (None, ""):
                raise KeyError(field)
        return Position(
            inst_id=inst_id,
            pos_side=pos_side,
            size=Decimal(str(item["pos"])),
            avg_price=Decimal(str(item["avgPx"])),
            mark_price=Decimal(str(item["markPx"])),
            upl=Decimal(str(item["upl"])),
            stop_loss=None,
            # 该持仓由外部（交易所端或人工）创建，策略归属标记为 external。
            strategy="external",
            opened_ts_ms=int(item["cTime"]),
        )
    except _PARSE_ERROR_TYPES as exc:
        raise _wrap_parse_error("positions", inst_id, exc) from exc
