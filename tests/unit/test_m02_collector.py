"""补数编排测试：历史补数、增量更新、缺口修复与幂等（真实 SQLite 执行）。"""

from decimal import Decimal

import pytest
from m02_helpers import FakeHistorySource, make_candle, seed_candles
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from dquant.contracts import CandleBar
from dquant.data.collector import (
    AsyncAdapterRunner,
    BackfillIncomplete,
    backfill_history,
    load_or_ensure_history_size,
    repair_missing_windows,
    update_incremental,
)
from dquant.data.store import count_candles, load_candles_between, stored_candle_close
from dquant.db import Base

HOUR = 3_600_000


@pytest.fixture()
def session() -> Session:
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    factory = sessionmaker(engine)
    with factory() as db_session:
        yield db_session
    engine.dispose()


def hour_candles(start_index: int, count: int) -> list:
    return [
        make_candle((start_index + i) * HOUR, close=str(100 + start_index + i))
        for i in range(count)
    ]


def test_backfill_history_fills_range_and_is_idempotent(session: Session) -> None:
    # OKX 分页从最新往旧返回：第一页 100..199，第二页 0..99。
    source = FakeHistorySource([hour_candles(100, 100), hour_candles(0, 100)])

    stats = backfill_history(source, session, "BTC-USDT-SWAP", CandleBar.ONE_HOUR, 0, 200 * HOUR)
    session.commit()

    assert stats.fetched_rows == 200
    assert stats.upserted_rows == 200
    assert count_candles(session, "BTC-USDT-SWAP", CandleBar.ONE_HOUR) == 200
    assert stats.missing_windows == []
    loaded = load_candles_between(session, "BTC-USDT-SWAP", CandleBar.ONE_HOUR, 0, 200 * HOUR)
    assert [c.ts_ms for c in loaded] == [i * HOUR for i in range(200)]

    # 重复执行：同一数据再拉一遍，行数不变。
    source_again = FakeHistorySource([hour_candles(100, 100), hour_candles(0, 100)])
    stats_again = backfill_history(
        source_again, session, "BTC-USDT-SWAP", CandleBar.ONE_HOUR, 0, 200 * HOUR
    )
    session.commit()
    assert stats_again.upserted_rows == 200
    assert count_candles(session, "BTC-USDT-SWAP", CandleBar.ONE_HOUR) == 200


def test_backfill_history_stops_when_reaching_start(session: Session) -> None:
    source = FakeHistorySource([hour_candles(0, 10)])

    stats = backfill_history(source, session, "BTC-USDT-SWAP", CandleBar.ONE_HOUR, 0)

    assert stats.upserted_rows == 10
    assert source.history_calls == [{"older_than_ms": None, "newer_than_ms": None, "limit": 100}]


def test_backfill_history_walks_with_after_cursor(session: Session) -> None:
    source = FakeHistorySource([hour_candles(100, 100), hour_candles(0, 100)])

    backfill_history(source, session, "BTC-USDT-SWAP", CandleBar.ONE_HOUR, 0, 200 * HOUR)

    # 第二页游标 = 第一页最旧一根的时间戳。
    assert source.history_calls[1]["older_than_ms"] == 100 * HOUR


def test_backfill_history_raises_when_pages_exhausted(session: Session) -> None:
    # 每页都停在 500 之前（ oldest=500*HOUR > start=0 ），两页耗尽仍未到达起点。
    source = FakeHistorySource([hour_candles(500, 100), hour_candles(500, 100)])

    with pytest.raises(BackfillIncomplete, match="页内未补完"):
        backfill_history(source, session, "BTC-USDT-SWAP", CandleBar.ONE_HOUR, 0, max_pages=2)


def test_backfill_history_reports_remaining_gaps(session: Session) -> None:
    # 交易所本身缺 2 根（index 2、3），补数后缺口应被报告而不是静默填补。
    pages = hour_candles(0, 2) + hour_candles(4, 6)
    source = FakeHistorySource([pages])

    stats = backfill_history(source, session, "BTC-USDT-SWAP", CandleBar.ONE_HOUR, 0, 10 * HOUR)

    assert stats.missing_windows == [(2 * HOUR, 4 * HOUR)]
    assert len(stats.anomalies) == 0


def test_backfill_history_reports_price_anomaly(session: Session) -> None:
    pages = [
        make_candle(0 * HOUR, close="100"),
        make_candle(1 * HOUR, close="200"),  # +100% 异常
        make_candle(2 * HOUR, close="199"),
    ]
    source = FakeHistorySource([pages])

    stats = backfill_history(source, session, "BTC-USDT-SWAP", CandleBar.ONE_HOUR, 0, 3 * HOUR)

    assert len(stats.anomalies) == 1
    assert stats.anomalies[0].ts_ms == 1 * HOUR


def test_update_incremental_appends_after_latest(session: Session) -> None:
    seed_candles(session, hour_candles(0, 10))
    session.commit()
    source = FakeHistorySource([hour_candles(10, 5)], recent=[])

    stats = update_incremental(
        source, session, "BTC-USDT-SWAP", CandleBar.ONE_HOUR, lookback_ms=HOUR, now_ms=15 * HOUR
    )
    session.commit()

    assert stats.upserted_rows == 5
    assert count_candles(session, "BTC-USDT-SWAP", CandleBar.ONE_HOUR) == 15
    # 增量游标从最新一根开始拉取。
    assert source.history_calls[0]["older_than_ms"] is None


def test_update_incremental_on_empty_db_uses_lookback(session: Session) -> None:
    source = FakeHistorySource([hour_candles(10, 5)])

    stats = update_incremental(
        source, session, "BTC-USDT-SWAP", CandleBar.ONE_HOUR, lookback_ms=6 * HOUR, now_ms=15 * HOUR
    )
    session.commit()

    assert stats.upserted_rows == 5
    assert count_candles(session, "BTC-USDT-SWAP", CandleBar.ONE_HOUR) == 5


def test_update_incremental_repairs_inner_gap_once(session: Session) -> None:
    from dquant.db.models import CandleModel

    seed_candles(session, hour_candles(0, 10))
    session.commit()
    # 人为制造中途缺口：删除 index 5。
    session.query(CandleModel).filter(CandleModel.ts_ms == 5 * HOUR).delete()
    session.commit()

    source = FakeHistorySource([hour_candles(5, 1)])

    repaired = repair_missing_windows(
        source,
        session,
        "BTC-USDT-SWAP",
        CandleBar.ONE_HOUR,
        [(5 * HOUR, 6 * HOUR)],
    )
    session.commit()

    assert repaired == 1
    assert count_candles(session, "BTC-USDT-SWAP", CandleBar.ONE_HOUR) == 10


def test_update_incremental_unconfirmed_then_confirmed_overwrite(session: Session) -> None:
    forming = [make_candle(10 * HOUR, close="110", confirmed=False)]
    source = FakeHistorySource([forming], recent=[])

    update_incremental(
        source, session, "BTC-USDT-SWAP", CandleBar.ONE_HOUR, lookback_ms=HOUR, now_ms=11 * HOUR
    )
    session.commit()
    assert stored_candle_close(session, "BTC-USDT-SWAP", CandleBar.ONE_HOUR, 10 * HOUR) is not None

    confirmed = [make_candle(10 * HOUR, close="110.5", confirmed=True)]
    source_confirmed = FakeHistorySource([confirmed], recent=[])
    update_incremental(
        source_confirmed,
        session,
        "BTC-USDT-SWAP",
        CandleBar.ONE_HOUR,
        lookback_ms=HOUR,
        now_ms=11 * HOUR,
    )
    session.commit()

    assert stored_candle_close(session, "BTC-USDT-SWAP", CandleBar.ONE_HOUR, 10 * HOUR) == Decimal(
        "110.5"
    )


def test_load_or_ensure_history_size_backfills_when_short(session: Session) -> None:
    source = FakeHistorySource([hour_candles(20, 80)])

    current = load_or_ensure_history_size(
        source, session, "BTC-USDT-SWAP", CandleBar.ONE_HOUR, min_bars=80, now_ms=99 * HOUR
    )

    assert current == 80


def test_load_or_ensure_history_size_noop_when_enough(session: Session) -> None:
    seed_candles(session, hour_candles(0, 100))
    session.commit()
    source = FakeHistorySource([])

    current = load_or_ensure_history_size(
        source, session, "BTC-USDT-SWAP", CandleBar.ONE_HOUR, min_bars=80, now_ms=99 * HOUR
    )

    assert current == 100
    assert source.history_calls == []


# ---- AsyncAdapterRunner 桥接 ----


class _AsyncAdapterStub:
    """实现异步行情接口的最小替身，用于验证同步桥接。"""

    def __init__(self, history: list) -> None:
        self._history = history

    async def fetch_candles(
        self, inst_id: str, bar: CandleBar, since_ms: int | None, limit: int
    ) -> list:
        return []

    async def fetch_history_candles(
        self,
        inst_id: str,
        bar: CandleBar,
        *,
        older_than_ms: int | None = None,
        newer_than_ms: int | None = None,
        limit: int,
    ) -> list:
        return self._history.pop(0) if self._history else []

    async def sync_time(self) -> int:
        return 0


def test_async_adapter_runner_bridges_to_sync_protocol(session: Session) -> None:
    from dquant.adapters.base import ExchangeAdapter

    stub = _AsyncAdapterStub([hour_candles(0, 5)])
    # 类型上属于鸭子替换：测试专用替身，不参与生产代码。
    with AsyncAdapterRunner(stub) as source:  # type: ignore[arg-type]
        stats = backfill_history(source, session, "BTC-USDT-SWAP", CandleBar.ONE_HOUR, 0, 5 * HOUR)
    session.commit()

    assert isinstance(stub, object)
    assert not isinstance(stub, ExchangeAdapter)
    assert stats.upserted_rows == 5
