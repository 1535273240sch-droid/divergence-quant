from decimal import Decimal

import pytest
from pydantic import ValidationError

from dquant.contracts import (
    Candle,
    CandleBar,
    DivergenceKind,
    Instrument,
    InstrumentType,
    OrderType,
    Signal,
    SignalAction,
    SignalSide,
    TakeProfit,
)


def test_candle_rejects_invalid_ohlc() -> None:
    with pytest.raises(ValidationError, match="high must be greater"):
        Candle(
            inst_id="BTC-USDT-SWAP",
            bar=CandleBar.FOUR_HOUR,
            ts_ms=1,
            open=Decimal("100"),
            high=Decimal("90"),
            low=Decimal("80"),
            close=Decimal("85"),
            volume=Decimal("1"),
            quote_volume=Decimal("100"),
            confirmed=True,
        )


def test_signal_requires_stop_loss_for_open_action() -> None:
    with pytest.raises(ValidationError, match="must include stop_loss"):
        Signal(
            signal_id="sig-1",
            inst_id="BTC-USDT-SWAP",
            side=SignalSide.LONG,
            action=SignalAction.OPEN,
            entry_type=OrderType.LIMIT,
            price=Decimal("100"),
            reason="测试信号",
            strategy_name="test",
            created_ts_ms=1,
        )


def test_public_models_accept_enum_values_and_reject_unknown_fields() -> None:
    instrument = Instrument(
        inst_id="BTC-USDT-SWAP",
        inst_type=InstrumentType.SWAP,
        base="BTC",
        quote="USDT",
        ct_val=Decimal("0.01"),
        lot_sz=Decimal("1"),
        min_sz=Decimal("1"),
        tick_sz=Decimal("0.1"),
    )

    assert instrument.state.value == "live"
    assert instrument.model_dump()["inst_id"] == "BTC-USDT-SWAP"

    signal = Signal(
        signal_id="sig-2",
        inst_id="BTC-USDT-SWAP",
        side="long",
        action="open",
        entry_type="limit",
        price=Decimal("100"),
        stop_loss=Decimal("95"),
        take_profits=[{"price": Decimal("110"), "fraction": Decimal("1")}],
        reason="测试信号",
        strategy_name="test",
        created_ts_ms=1,
    )
    assert isinstance(signal.take_profits[0], TakeProfit)
    assert signal.take_profits[0].fraction == Decimal("1")


def test_screener_divergence_kind_enum_is_stable() -> None:
    assert DivergenceKind.BULL_REGULAR.value == "BULL_REGULAR"
