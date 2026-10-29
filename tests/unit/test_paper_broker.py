from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from dquant.contracts import (
    Instrument,
    InstrumentState,
    InstrumentType,
    PositionSide,
    Signal,
    SignalAction,
    SignalSide,
)
from dquant.db.models import Base
from dquant.simulation.paper import PaperBroker


def _session_factory():
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    return sessionmaker(engine, future=True)


def _instrument() -> Instrument:
    return Instrument(
        inst_id="BTC-USDT-SWAP",
        inst_type=InstrumentType.SWAP,
        base="BTC",
        quote="USDT",
        ct_val=Decimal("0.01"),
        lot_sz=Decimal("0.01"),
        min_sz=Decimal("0.01"),
        tick_sz=Decimal("0.1"),
        state=InstrumentState.LIVE,
    )


def _signal() -> Signal:
    return Signal(
        signal_id="MHD:test:BTC-USDT-SWAP:long:1",
        inst_id="BTC-USDT-SWAP",
        side=SignalSide.LONG,
        action=SignalAction.OPEN,
        entry_type="market",
        price=Decimal("100"),
        stop_loss=Decimal("90"),
        take_profits=[],
        reason="test",
        strategy_name="MHD",
        created_ts_ms=int(datetime(2026, 1, 1, tzinfo=UTC).timestamp() * 1000),
    )


def test_paper_broker_open_update_stop_rebalance_and_close() -> None:
    broker = PaperBroker(_session_factory(), {"BTC-USDT-SWAP": _instrument()})
    opened = broker.open_position(
        signal=_signal(), qty=Decimal("10"), price=Decimal("100"), stop_loss=Decimal("90")
    )
    assert opened is not None
    assert opened.pos_side is PositionSide.LONG
    assert opened.size == Decimal("10")

    assert len(broker.list_positions()) == 1
    assert broker.update_stop("BTC-USDT-SWAP", PositionSide.LONG, Decimal("95")) is True

    rebalanced = broker.rebalance(
        inst_id="BTC-USDT-SWAP",
        pos_side=PositionSide.LONG,
        target_size=Decimal("20"),
        price=Decimal("110"),
    )
    assert rebalanced is not None
    assert rebalanced.size == Decimal("20")
    assert rebalanced.avg_price == Decimal("105")

    pnl = broker.close_position("BTC-USDT-SWAP", PositionSide.LONG, Decimal("120"))
    assert pnl == Decimal("3.0000")
    assert broker.list_positions() == []


def test_paper_broker_target_size_uses_50_percent_default() -> None:
    broker = PaperBroker(_session_factory(), {"BTC-USDT-SWAP": _instrument()})
    qty = broker.target_size(
        equity=Decimal("10000"),
        position_pct=Decimal("0.5"),
        position_multiplier=Decimal("1"),
        price=Decimal("100"),
        inst_id="BTC-USDT-SWAP",
    )
    # notional 5000 / (100 * 0.01) = 5000 张
    assert qty == Decimal("5000")
