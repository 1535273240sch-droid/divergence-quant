"""K 线存储层测试：幂等 upsert、对齐校验与读取（真实 SQLite 执行 SQL）。"""

from decimal import Decimal

import pytest
from m02_helpers import make_candle
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from dquant.contracts import CandleBar
from dquant.data.store import (
    count_candles,
    load_candles_between,
    load_latest_candle_ts,
    model_to_candle,
    stored_candle_close,
    upsert_candles,
)
from dquant.db import Base
from dquant.db.models import CandleModel


@pytest.fixture()
def session() -> Session:
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    factory = sessionmaker(engine)
    with factory() as db_session:
        yield db_session
    engine.dispose()


def test_upsert_and_roundtrip(session: Session) -> None:
    candles = [
        make_candle(
            3_600_000, close="101.5", open_price="101", high="103.5", low="100.5", volume="12.5"
        ),
        make_candle(7_200_000, close="102.5", volume="15.5"),
    ]

    written = upsert_candles(session, candles)
    session.commit()

    assert written == 2
    assert count_candles(session, "BTC-USDT-SWAP", CandleBar.ONE_HOUR) == 2

    loaded = load_candles_between(session, "BTC-USDT-SWAP", CandleBar.ONE_HOUR, 0, 99_999_999)
    assert [c.ts_ms for c in loaded] == [3_600_000, 7_200_000]
    assert loaded[0].close == Decimal("101.5")
    assert loaded[0].volume == Decimal("12.5")
    assert loaded[0].quote_volume == Decimal("1000")
    assert loaded[0].confirmed is True
    assert load_latest_candle_ts(session, "BTC-USDT-SWAP", CandleBar.ONE_HOUR) == 7_200_000
    assert stored_candle_close(session, "BTC-USDT-SWAP", CandleBar.ONE_HOUR, 7_200_000) == Decimal(
        "102.5"
    )


def test_upsert_is_idempotent_and_overwrites(session: Session) -> None:
    first = make_candle(3_600_000, close="100.5")
    assert upsert_candles(session, [first]) == 1
    session.commit()

    updated = make_candle(3_600_000, close="100.75", confirmed=False)
    assert upsert_candles(session, [first, updated]) == 1
    session.commit()

    # 同一主键只有一行，且被后传入的记录覆盖（未收盘标志保留）。
    assert count_candles(session, "BTC-USDT-SWAP", CandleBar.ONE_HOUR) == 1
    assert stored_candle_close(session, "BTC-USDT-SWAP", CandleBar.ONE_HOUR, 3_600_000) == Decimal(
        "100.75"
    )
    loaded = load_candles_between(session, "BTC-USDT-SWAP", CandleBar.ONE_HOUR, 0, 99_999_999)
    assert loaded[0].confirmed is False


def test_upsert_rejects_misaligned_daily_timestamp(session: Session) -> None:
    bad = make_candle(86_400_000, bar=CandleBar.ONE_DAY)  # UTC 00:00，不是北京时间开盘

    with pytest.raises(Exception, match="北京时间"):
        upsert_candles(session, [bad])

    session.rollback()
    assert count_candles(session, "BTC-USDT-SWAP", CandleBar.ONE_DAY) == 0


def test_upsert_empty_list_is_noop(session: Session) -> None:
    assert upsert_candles(session, []) == 0
    assert count_candles(session, "BTC-USDT-SWAP", CandleBar.ONE_HOUR) == 0


def test_upsert_rejects_unsupported_dialect() -> None:
    from dquant.data.store import build_candle_upsert

    with pytest.raises(ValueError, match="方言"):
        build_candle_upsert("mysql", [{"inst_id": "X"}])


def test_model_to_candle_roundtrip(session: Session) -> None:
    upsert_candles(session, [make_candle(3_600_000, close="101.5", confirmed=False)])
    session.commit()

    row = session.query(CandleModel).one()
    candle = model_to_candle(row)

    assert candle.inst_id == "BTC-USDT-SWAP"
    assert candle.bar is CandleBar.ONE_HOUR
    assert candle.confirmed is False
    assert candle.close == Decimal("101.5")
