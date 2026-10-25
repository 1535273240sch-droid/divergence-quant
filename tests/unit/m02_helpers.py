"""M02 测试公共工具：fixture 加载、假传输、假采集源和确定性时钟。"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Mapping
from decimal import Decimal
from pathlib import Path
from typing import Any

from dquant.adapters.errors import ExchangeError
from dquant.adapters.okx import OkxCredentials, OkxTransport
from dquant.contracts import Candle, CandleBar

FIXTURE_DIR = Path(__file__).resolve().parents[1] / "fixtures" / "m02"


def load_fixture(name: str) -> Any:
    """加载录制响应 fixture。"""

    return json.loads((FIXTURE_DIR / name).read_text(encoding="utf-8"))


def make_candle(
    ts_ms: int,
    close: str = "100",
    *,
    inst_id: str = "BTC-USDT-SWAP",
    bar: CandleBar = CandleBar.ONE_HOUR,
    open_price: str = "100",
    high: str | None = None,
    low: str | None = None,
    volume: str = "10",
    quote_volume: str = "1000",
    confirmed: bool = True,
) -> Candle:
    """构造合法的公共契约 K 线，OHLC 缺省时用开收盘价填充。"""

    open_dec = Decimal(open_price)
    close_dec = Decimal(close)
    return Candle(
        inst_id=inst_id,
        bar=bar,
        ts_ms=ts_ms,
        open=open_dec,
        high=Decimal(high) if high is not None else max(open_dec, close_dec),
        low=Decimal(low) if low is not None else min(open_dec, close_dec),
        close=close_dec,
        volume=Decimal(volume),
        quote_volume=Decimal(quote_volume),
        confirmed=confirmed,
    )


class FakeClock:
    """确定性时钟：手动推进，供限流器与重试策略测试使用。"""

    def __init__(self, start: float = 0.0) -> None:
        self.value = start
        self.sleeps: list[float] = []

    def time(self) -> float:
        return self.value

    async def sleep(self, delay: float) -> None:
        self.sleeps.append(delay)
        self.value += delay


class FakeOkxTransport(OkxTransport):
    """录制响应传输替身：按 (method, path) 路由，可注入异常序列。

    与真实传输一致：私有接口在缺少凭证时抛出 ``NO_CREDENTIALS``。
    """

    def __init__(self, credentials: OkxCredentials | None = None) -> None:
        self.credentials = credentials
        self.routes: dict[tuple[str, str], Any] = {}
        self.requests: list[tuple[str, str, Mapping[str, str] | None, bool]] = []

    def route(self, method: str, path: str, value: Any) -> None:
        self.routes[(method, path)] = value

    async def request(
        self,
        method: str,
        path: str,
        params: Mapping[str, str] | None,
        *,
        auth: bool,
    ) -> Any:
        self.requests.append((method, path, dict(params or {}), auth))
        if auth and self.credentials is None:
            raise ExchangeError(
                "NO_CREDENTIALS", f"调用私有接口 {path} 需要先配置 OKX API 凭证", retriable=False
            )
        value = self.routes[(method, path)]
        if isinstance(value, ExchangeError):
            raise value
        if isinstance(value, Exception):
            raise value
        if callable(value):
            return value(dict(params or {}))
        return value


def seed_candles(session: Any, candles: list[Candle]) -> None:
    """把公共契约 K 线写入测试数据库（转换成 M01 的 ORM 行）。"""

    from dquant.db.models import CandleModel

    session.add_all(
        [
            CandleModel(
                inst_id=candle.inst_id,
                bar=candle.bar.value,
                ts_ms=candle.ts_ms,
                open=candle.open,
                high=candle.high,
                low=candle.low,
                close=candle.close,
                vol=candle.volume,
                vol_quote=candle.quote_volume,
                confirmed=candle.confirmed,
            )
            for candle in candles
        ]
    )


class FakeHistorySource:
    """同步采集替身：预置历史页序列与增量页，记录调用，供补数编排测试。"""

    def __init__(
        self,
        history_pages: list[list[Candle]],
        recent: list[Candle] | None = None,
    ) -> None:
        self._history_pages = history_pages
        self._recent = recent or []
        self.history_calls: list[Mapping[str, Any]] = []

    def fetch_candles(
        self, inst_id: str, bar: CandleBar, since_ms: int | None, limit: int
    ) -> list[Candle]:
        return list(self._recent)

    def fetch_history_candles(
        self,
        inst_id: str,
        bar: CandleBar,
        *,
        older_than_ms: int | None = None,
        newer_than_ms: int | None = None,
        limit: int,
    ) -> list[Candle]:
        self.history_calls.append(
            {"older_than_ms": older_than_ms, "newer_than_ms": newer_than_ms, "limit": limit}
        )
        if not self._history_pages:
            return []
        return self._history_pages.pop(0)


def run(coro: Any) -> Any:
    """在同步测试里运行一个协程。"""

    return asyncio.run(coro)
