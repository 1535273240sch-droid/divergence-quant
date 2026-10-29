from decimal import Decimal

from dquant.contracts import (
    Instrument,
    InstrumentState,
    InstrumentType,
    Signal,
    SignalAction,
    SignalSide,
)
from dquant.risk.calculator import calculate_fixed_fraction_size
from dquant.risk.manager import RiskManager, RiskRequest


def _instrument() -> Instrument:
    return Instrument(
        inst_id="BTC-USDT-SWAP",
        inst_type=InstrumentType.SWAP,
        base="BTC",
        quote="USDT",
        ct_val=Decimal("1"),
        lot_sz=Decimal("1"),
        min_sz=Decimal("1"),
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
        created_ts_ms=1000,
    )


def test_fixed_fraction_default_50_percent() -> None:
    sizing = calculate_fixed_fraction_size(
        equity=Decimal("10000"),
        position_pct=Decimal("0.5"),
        position_multiplier=Decimal("1"),
        entry_price=Decimal("100"),
        instrument=_instrument(),
    )
    assert sizing.qty == Decimal("50")
    assert sizing.risk_amount == Decimal("5000")


def test_fixed_fraction_respects_multiplier_and_min_size() -> None:
    sizing = calculate_fixed_fraction_size(
        equity=Decimal("10000"),
        position_pct=Decimal("0.5"),
        position_multiplier=Decimal("1.5"),
        entry_price=Decimal("100"),
        instrument=_instrument(),
    )
    assert sizing.qty == Decimal("75")

    rejected = calculate_fixed_fraction_size(
        equity=Decimal("100"),
        position_pct=Decimal("0.01"),
        position_multiplier=Decimal("1"),
        entry_price=Decimal("1000"),
        instrument=_instrument(),
    )
    assert rejected.rejected is True


def test_risk_manager_uses_fixed_fraction_when_position_pct_present() -> None:
    manager = RiskManager(instruments={"BTC-USDT-SWAP": _instrument()})
    decision = manager.evaluate(
        RiskRequest(
            signal=_signal(),
            equity=Decimal("10000"),
            instrument=_instrument(),
            position_pct=Decimal("0.5"),
            position_multiplier=Decimal("1"),
            leverage=Decimal("3"),
        )
    )
    assert decision.approved is True
    assert decision.qty == Decimal("150")
    assert "fixed_fraction" in decision.detail
    assert "position_pct=0.5" in decision.detail
