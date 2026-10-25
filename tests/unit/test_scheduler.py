"""M05 调度器、持久化与服务组装的单元测试。

测试环境没有 PostgreSQL/aiosqlite，因此用同步 SQLite 会话包装出最小异步会话协议，
让 ScreenerRunStore 的 SQL 逻辑在真实 SQLAlchemy 语句上执行。
真实 PostgreSQL 的写入验收留给后续集成批次。
"""

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from decimal import Decimal

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool
from tests.unit.test_screener import make_div, make_regime

from dquant.contracts import CandleBar, DivergenceKind, RegimeState, ScreenerCandidate
from dquant.core.screener import ScreenerConfig, ScreenerInputs, ScreenerRunRecord
from dquant.db import Base
from dquant.db.models import ScreenerResultModel, ScreenerRunModel
from dquant.scheduler.persistence import (
    ScreenerRunStore,
    ScreenerStoreError,
    manual_run_id,
    parse_run_id,
    record_from_rows,
    result_rows_from_record,
    run_row_from_record,
    scheduled_run_id,
)
from dquant.scheduler.runner import (
    BAR_PERIOD_MS,
    ScreenerScheduler,
    last_closed_bar_ts,
    next_close_ts,
)
from dquant.scheduler.service import ScreenerService, utc_now_ms


class _SyncSessionAsAsync:
    """把同步 Session 适配成 store 使用的最小异步会话协议。"""

    def __init__(self, session: Session) -> None:
        self._session = session

    async def execute(self, statement: object) -> object:
        return self._session.execute(statement)  # type: ignore[arg-type]

    async def merge(self, instance: object) -> object:
        return self._session.merge(instance)  # type: ignore[arg-type]

    def add_all(self, instances: list[object]) -> None:
        self._session.add_all(instances)  # type: ignore[arg-type]

    async def flush(self) -> None:
        self._session.flush()

    async def commit(self) -> None:
        self._session.commit()

    async def rollback(self) -> None:
        self._session.rollback()


def _make_store() -> tuple[ScreenerRunStore, Engine]:
    """基于内存 SQLite 构建可真实执行 SQL 的 store。"""

    engine = create_engine(
        "sqlite://",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    sync_factory = sessionmaker(bind=engine, expire_on_commit=False)

    @asynccontextmanager
    async def open_session() -> AsyncIterator[_SyncSessionAsAsync]:
        with sync_factory() as session:
            yield _SyncSessionAsAsync(session)

    return ScreenerRunStore(open_session), engine


def make_candidate(
    inst_id: str = "BTC-USDT-SWAP",
    pool: str = "A",
    score: float = 80.0,
    rank: int = 1,
    regime: RegimeState = RegimeState.UPTREND,
    divergence_id: str | None = "d" * 32,
    updated_ts_ms: int = 1_000,
) -> ScreenerCandidate:
    return ScreenerCandidate(
        inst_id=inst_id,
        pool=pool,
        regime=regime,
        divergence_id=divergence_id,
        divergence_kind=None if divergence_id is None else "BULL_REGULAR",
        score=score,
        rank=rank,
        updated_ts_ms=updated_ts_ms,
    )


def make_record(
    run_id: str = scheduled_run_id(14_400_000),
    candidates: tuple[ScreenerCandidate, ...] | list[ScreenerCandidate] | None = None,
    started_ts_ms: int = 1_000,
    finished_ts_ms: int = 2_000,
    universe_size: int = 2,
) -> ScreenerRunRecord:
    if candidates is None:
        candidates = (
            make_candidate("BTC-USDT-SWAP", "A", 80.0, 1),
            make_candidate("ETH-USDT-SWAP", "A", 70.0, 2),
        )
    return ScreenerRunRecord(
        run_id=run_id,
        trigger="scheduled" if run_id.startswith("sched-") else "manual",
        close_ts_ms=14_400_000 if run_id.startswith("sched-") else None,
        started_ts_ms=started_ts_ms,
        finished_ts_ms=finished_ts_ms,
        universe_size=universe_size,
        params_hash="a" * 64,
        candidates=tuple(candidates),
        warnings=(),
    )


# ---------------------------------------------------------------------------
# 行构建纯函数
# ---------------------------------------------------------------------------


def test_run_row_from_record_maps_fields() -> None:
    record = make_record()
    row = run_row_from_record(record)

    assert row.run_id == record.run_id
    assert row.universe_size == 2
    assert row.params_hash == "a" * 64
    assert row.started_at is not None and row.started_at.utcoffset() is not None
    assert row.finished_at is not None and row.finished_at.utcoffset() is not None


def test_result_rows_from_record_map_fields() -> None:
    record = make_record()
    rows = result_rows_from_record(record)

    assert len(rows) == 2
    first = rows[0]
    assert first.run_id == record.run_id
    assert first.inst_id == "BTC-USDT-SWAP"
    assert first.pool == "A"
    assert first.regime == "UPTREND"
    assert first.divergence_id == "d" * 32
    assert first.score == pytest.approx(80.0)
    assert first.rank == 1


def test_record_round_trip_through_rows() -> None:
    record = make_record(candidates=(make_candidate(),))
    row = run_row_from_record(record)
    rows = result_rows_from_record(record)

    loaded = record_from_rows(row, rows)
    assert loaded.warnings == ()
    assert loaded.trigger == "scheduled"
    assert loaded.close_ts_ms == 14_400_000
    # 表结构不含 divergence_kind，读回时置 None；其余字段必须完整还原。
    assert loaded.candidates[0].divergence_kind is None
    assert loaded.candidates[0].model_dump(exclude={"divergence_kind"}) == record.candidates[
        0
    ].model_dump(exclude={"divergence_kind"})


def test_parse_run_id_prefixes() -> None:
    assert parse_run_id(scheduled_run_id(14_400_000)) == ("scheduled", 14_400_000)
    manual_id = manual_run_id()
    trigger, close = parse_run_id(manual_id)
    assert trigger == "manual"
    assert close is None
    assert manual_id.startswith("manual-")
    assert parse_run_id("foreign-run") == ("unknown", None)
    assert parse_run_id("sched-not-a-number") == ("unknown", None)


# ---------------------------------------------------------------------------
# 持久化 store
# ---------------------------------------------------------------------------


def _run_async(coro: object) -> object:
    return asyncio.run(coro)  # type: ignore[arg-type]


def test_store_save_and_load_round_trip() -> None:
    store, engine = _make_store()
    record = make_record()
    try:
        _run_async(store.save_run(record))

        loaded = _run_async(store.load_run(record.run_id))  # type: ignore[attr-defined]
        assert loaded is not None
        assert loaded.run_id == record.run_id
        assert loaded.universe_size == record.universe_size
        assert loaded.params_hash == record.params_hash
        # divergence_kind 不在表结构中，读回为 None；其余字段完整还原。
        assert [c.model_dump(exclude={"divergence_kind"}) for c in loaded.candidates] == [
            c.model_dump(exclude={"divergence_kind"}) for c in record.candidates
        ]
    finally:
        engine.dispose()


def test_store_replaces_results_on_same_run_id() -> None:
    store, engine = _make_store()
    run_id = scheduled_run_id(28_800_000)
    first = make_record(
        run_id,
        candidates=(
            make_candidate("BTC-USDT-SWAP", "A", 80.0, 1),
            make_candidate("ETH-USDT-SWAP", "A", 70.0, 2),
        ),
    )
    second = make_record(
        run_id,
        candidates=(make_candidate("ETH-USDT-SWAP", "A", 60.0, 1),),
        universe_size=1,
    )
    try:
        _run_async(store.save_run(first))
        _run_async(store.save_run(second))

        loaded = _run_async(store.load_run(run_id))
        assert loaded is not None
        assert loaded.universe_size == 1
        assert len(loaded.candidates) == 1
        assert loaded.candidates[0].inst_id == "ETH-USDT-SWAP"

        session_factory = sessionmaker(bind=engine)
        with session_factory() as session:
            count = session.execute(
                select(func.count()).select_from(ScreenerResultModel)
            ).scalar_one()
            assert count == 1
            run_count = session.execute(
                select(func.count()).select_from(ScreenerRunModel)
            ).scalar_one()
            assert run_count == 1
    finally:
        engine.dispose()


def test_store_load_missing_run_returns_none() -> None:
    store, engine = _make_store()
    try:
        assert _run_async(store.load_run("missing-run")) is None
        assert _run_async(store.load_latest_run()) is None
        assert _run_async(store.load_latest_candidates()) == []
    finally:
        engine.dispose()


def test_store_load_latest_run_and_candidates() -> None:
    store, engine = _make_store()
    older = make_record(scheduled_run_id(14_400_000), started_ts_ms=1_000)
    newer = make_record(
        scheduled_run_id(28_800_000),
        candidates=(make_candidate("ETH-USDT-SWAP", "F", 40.0, 1, RegimeState.RANGE, None),),
        started_ts_ms=9_000,
    )
    try:
        _run_async(store.save_run(older))
        _run_async(store.save_run(newer))

        latest = _run_async(store.load_latest_run())
        assert latest is not None
        assert latest.run_id == newer.run_id

        candidates = _run_async(store.load_latest_candidates())
        assert [c.inst_id for c in candidates] == ["ETH-USDT-SWAP"]
    finally:
        engine.dispose()


def test_store_save_failure_propagates() -> None:
    store, engine = _make_store()
    engine.dispose()  # 提前释放连接，让写入真实失败。

    with pytest.raises(ScreenerStoreError):
        _run_async(store.save_run(make_record()))


# ---------------------------------------------------------------------------
# 服务组装
# ---------------------------------------------------------------------------


class _StaticProvider:
    """返回固定输入并统计调用次数的 input_provider。"""

    def __init__(self, inputs: ScreenerInputs) -> None:
        self.inputs = inputs
        self.calls = 0

    async def __call__(self) -> ScreenerInputs:
        self.calls += 1
        return self.inputs


class _FailingProvider:
    async def __call__(self) -> ScreenerInputs:
        raise RuntimeError("行情数据不可用")


class _TickClock:
    """每次调用前进 500ms 的测试时钟。"""

    def __init__(self) -> None:
        self.now = 10_000

    def __call__(self) -> int:
        self.now += 500
        return self.now


def _sample_inputs() -> ScreenerInputs:
    return ScreenerInputs(
        regimes=[make_regime("BTC-USDT-SWAP", RegimeState.UPTREND)],
        divergences=[make_div("BTC-USDT-SWAP", score=75.0)],
        liquidity={"BTC-USDT-SWAP": Decimal("25000000")},
    )


def test_service_scheduled_run_is_persisted_with_deterministic_id() -> None:
    store, engine = _make_store()
    service = ScreenerService(
        config=ScreenerConfig(),
        store=store,
        input_provider=_StaticProvider(_sample_inputs()),
        clock=_TickClock(),
    )
    try:
        record = _run_async(service.run_scheduled(14_400_000))

        assert record.run_id == scheduled_run_id(14_400_000)
        assert record.trigger == "scheduled"
        assert record.close_ts_ms == 14_400_000
        assert record.started_ts_ms == 10_500
        assert record.finished_ts_ms >= record.started_ts_ms

        persisted = _run_async(store.load_run(record.run_id))
        assert persisted is not None
        assert persisted.universe_size == 1
        assert persisted.candidates[0].pool == "A"
    finally:
        engine.dispose()


def test_service_manual_run_ids_are_unique_and_persisted() -> None:
    store, engine = _make_store()
    service = ScreenerService(
        config=ScreenerConfig(),
        store=store,
        input_provider=_StaticProvider(_sample_inputs()),
        clock=_TickClock(),
    )
    try:
        first = _run_async(service.run_manual())
        second = _run_async(service.run_manual())

        assert first.run_id != second.run_id
        assert first.trigger == "manual"
        assert first.close_ts_ms is None
        assert _run_async(store.load_run(first.run_id)) is not None
        assert _run_async(store.load_run(second.run_id)) is not None
    finally:
        engine.dispose()


def test_service_provider_failure_propagates_without_persisting() -> None:
    store, engine = _make_store()
    service = ScreenerService(
        config=ScreenerConfig(),
        store=store,
        input_provider=_FailingProvider(),
        clock=_TickClock(),
    )
    try:
        with pytest.raises(RuntimeError, match="行情数据不可用"):
            _run_async(service.run_scheduled(14_400_000))

        assert _run_async(store.load_latest_run()) is None
    finally:
        engine.dispose()


# ---------------------------------------------------------------------------
# 4H 收盘边界与调度
# ---------------------------------------------------------------------------


def test_bar_period_table_covers_contract_bars() -> None:
    assert BAR_PERIOD_MS[CandleBar.ONE_HOUR] == 3_600_000
    assert BAR_PERIOD_MS[CandleBar.FOUR_HOUR] == 14_400_000
    assert BAR_PERIOD_MS[CandleBar.ONE_DAY] == 86_400_000


@pytest.mark.parametrize(
    ("now_ms", "expected_last", "expected_next"),
    [
        (14_400_000 - 1, 0, 14_400_000),
        (14_400_000, 14_400_000, 28_800_000),
        (14_400_000 + 1, 14_400_000, 28_800_000),
        (3 * 14_400_000 + 7_200_000, 3 * 14_400_000, 4 * 14_400_000),
    ],
)
def test_bar_boundary_math(now_ms: int, expected_last: int, expected_next: int) -> None:
    assert last_closed_bar_ts(now_ms, CandleBar.FOUR_HOUR) == expected_last
    assert next_close_ts(now_ms, CandleBar.FOUR_HOUR) == expected_next


def test_scheduler_poll_runs_once_per_close() -> None:
    store, engine = _make_store()
    provider = _StaticProvider(_sample_inputs())
    service = ScreenerService(
        config=ScreenerConfig(), store=store, input_provider=provider, clock=_TickClock()
    )
    scheduler = ScreenerScheduler(service)
    try:
        first = _run_async(scheduler.poll(14_400_000 + 1))
        assert first is not None
        assert first.close_ts_ms == 14_400_000

        assert _run_async(scheduler.poll(14_400_000 + 2)) is None
        assert provider.calls == 1

        again = _run_async(scheduler.poll(28_800_000 + 1))
        assert again is not None
        assert again.close_ts_ms == 28_800_000
        assert provider.calls == 2
    finally:
        engine.dispose()


def test_scheduler_poll_at_exact_boundary_runs_that_close() -> None:
    store, engine = _make_store()
    service = ScreenerService(
        config=ScreenerConfig(),
        store=store,
        input_provider=_StaticProvider(_sample_inputs()),
        clock=_TickClock(),
    )
    scheduler = ScreenerScheduler(service)
    try:
        record = _run_async(scheduler.poll(28_800_000))
        assert record is not None
        assert record.close_ts_ms == 28_800_000
    finally:
        engine.dispose()


def test_scheduler_poll_catches_up_latest_close_only(caplog: pytest.LogCaptureFixture) -> None:
    store, engine = _make_store()
    provider = _StaticProvider(_sample_inputs())
    service = ScreenerService(
        config=ScreenerConfig(), store=store, input_provider=provider, clock=_TickClock()
    )
    scheduler = ScreenerScheduler(service)
    try:
        first = _run_async(scheduler.poll(14_400_000 + 1))
        assert first is not None
        assert first.close_ts_ms == 14_400_000

        caplog.clear()
        with caplog.at_level(logging.WARNING, logger="dquant.scheduler.runner"):
            record = _run_async(scheduler.poll(4 * 14_400_000 + 1))

        assert record is not None
        assert record.close_ts_ms == 4 * 14_400_000
        assert provider.calls == 2
        assert any("错过" in message for message in caplog.messages)
    finally:
        engine.dispose()


def test_scheduler_poll_retries_after_failure() -> None:
    store, engine = _make_store()

    class _FlakyProvider:
        def __init__(self) -> None:
            self.calls = 0

        async def __call__(self) -> ScreenerInputs:
            self.calls += 1
            if self.calls == 1:
                raise RuntimeError("第一次扫描失败")
            return _sample_inputs()

    service = ScreenerService(
        config=ScreenerConfig(),
        store=store,
        input_provider=_FlakyProvider(),
        clock=_TickClock(),
    )
    scheduler = ScreenerScheduler(service)
    try:
        with pytest.raises(RuntimeError, match="第一次扫描失败"):
            _run_async(scheduler.poll(14_400_000 + 1))

        retried = _run_async(scheduler.poll(14_400_000 + 2))
        assert retried is not None
        assert retried.close_ts_ms == 14_400_000
        assert _run_async(store.load_latest_run()) is not None
    finally:
        engine.dispose()


def test_scheduler_run_forever_stops_on_event() -> None:
    store, engine = _make_store()
    provider = _StaticProvider(_sample_inputs())
    service = ScreenerService(
        config=ScreenerConfig(), store=store, input_provider=provider, clock=_TickClock()
    )
    scheduler = ScreenerScheduler(service)
    stop_event = asyncio.Event()
    try:

        async def stop_later() -> None:
            await asyncio.sleep(0.05)
            stop_event.set()

        async def scenario() -> None:
            await asyncio.wait_for(
                asyncio.gather(
                    scheduler.run_forever(interval_seconds=0.01, stop_event=stop_event),
                    stop_later(),
                ),
                timeout=5,
            )

        asyncio.run(scenario())
        assert provider.calls >= 1
    finally:
        engine.dispose()


def test_utc_now_ms_is_millisecond_epoch() -> None:
    now = utc_now_ms()
    assert now > 1_700_000_000_000
    assert utc_now_ms() >= now


def test_full_pipeline_manual_scan_produces_ranked_results() -> None:
    """端到端：输入 -> 纯函数选币 -> 落库 -> 读回。"""

    store, engine = _make_store()
    inputs = ScreenerInputs(
        regimes=[
            make_regime("AAA-USDT-SWAP", RegimeState.UPTREND, dist=0.06, slope=0.03),
            make_regime("BBB-USDT-SWAP", RegimeState.UPTREND, dist=0.05, slope=0.02),
            make_regime("CCC-USDT-SWAP", RegimeState.DOWNTREND),
        ],
        divergences=[
            make_div("AAA-USDT-SWAP", score=90.0),
            make_div("BBB-USDT-SWAP", score=80.0),
            make_div("CCC-USDT-SWAP", DivergenceKind.BEAR_REGULAR, score=70.0),
        ],
        liquidity={
            "AAA-USDT-SWAP": Decimal("30000000"),
            "BBB-USDT-SWAP": Decimal("20000000"),
            "CCC-USDT-SWAP": Decimal("10000000"),
        },
    )
    service = ScreenerService(
        config=ScreenerConfig(top_n_per_pool=1),
        store=store,
        input_provider=_StaticProvider(inputs),
        clock=_TickClock(),
    )
    scheduler = ScreenerScheduler(service)
    try:
        record = _run_async(scheduler.run_manual())
        candidates = _run_async(store.load_latest_candidates())

        pools = {c.pool for c in record.candidates}
        assert pools == {"A", "B"}
        # TopN=1：A 池只保留评分最高的 AAA。
        assert [c.inst_id for c in candidates if c.pool == "A"] == ["AAA-USDT-SWAP"]
        assert [c.inst_id for c in candidates if c.pool == "B"] == ["CCC-USDT-SWAP"]
        assert all(c.rank == 1 for c in candidates)
    finally:
        engine.dispose()
