from decimal import Decimal

import numpy as np

from dquant.contracts import (
    Candle,
    CandleBar,
    Position,
    PositionSide,
    RegimeState,
    ScreenerCandidate,
    SignalAction,
    SignalSide,
)
from dquant.core.mhd import MHDResult, MHDSignal
from dquant.strategies import MHDDivergenceStrategy, StrategyContext
from dquant.strategies import mhd as mhd_module


def _candle(
    index: int,
    close: str,
    *,
    high: str | None = None,
    low: str | None = None,
    bar: CandleBar = CandleBar.FOUR_HOUR,
) -> Candle:
    close_d = Decimal(close)
    return Candle(
        inst_id="BTC-USDT-SWAP",
        bar=bar,
        ts_ms=index * 14_400_000,
        open=close_d,
        high=Decimal(high or close),
        low=Decimal(low or close),
        close=close_d,
        volume=Decimal("10"),
        quote_volume=Decimal("1000"),
        confirmed=True,
    )


def _context(candles: list[Candle]) -> StrategyContext:
    return StrategyContext(
        candidate=ScreenerCandidate(
            inst_id="BTC-USDT-SWAP",
            pool="F",
            regime=RegimeState.UPTREND,
            divergence_id=None,
            divergence_kind=None,
            score=80,
            rank=1,
            updated_ts_ms=1,
        ),
        divergence=None,
        trigger_candles=tuple(_candle(i, "100", bar=CandleBar.ONE_HOUR) for i in range(6)),
        atr=Decimal("2"),
        range_candles=tuple(candles),
    )


def _series(n: int = 150) -> list[Candle]:
    candles = []
    for index in range(n):
        close = 100 + index * 0.01
        candles.append(_candle(index, str(close), high=str(close + 0.5), low=str(close - 0.5)))
    return candles


def _fake_result(n: int = 150, *, signals=()) -> MHDResult:
    return MHDResult(
        hist=np.zeros(n, dtype=float),
        atr=np.full(n, 2.0, dtype=float),
        signals=tuple(signals),
    )


def test_mhd_emits_short_signal_at_confirmed_top(monkeypatch) -> None:
    candles = _series()
    signal = MHDSignal(
        kind="top",
        swing_index=148,
        confirmed_index=149,
        price=float(candles[-1].high),
        hist=5.0,
        d_pair=1.2,
        d_peak=2.3,
        d=2.3,
        reference="peak",
        reference_index=140,
        pm=1.0,
        hm=-1.3,
    )
    monkeypatch.setattr(
        mhd_module,
        "detect_mhd",
        lambda *args, **kwargs: _fake_result(signals=(signal,)),
    )

    result = MHDDivergenceStrategy({"timeframe": "4H"}).on_bar(_context(candles))

    assert len(result) == 1
    opened = result[0]
    assert opened.action is SignalAction.OPEN
    assert opened.side is SignalSide.SHORT
    assert opened.price == candles[-1].close
    assert opened.stop_loss == candles[-1].close + Decimal("5.0")
    assert opened.take_profits == []
    assert "MHD" in opened.reason


def test_mhd_position_management_moves_stop_and_closes(monkeypatch) -> None:
    candles = _series()
    candles[-1] = _candle(149, "120", high="125", low="119")
    context = _context(candles).with_position(
        Position(
            inst_id="BTC-USDT-SWAP",
            pos_side=PositionSide.LONG,
            size=Decimal("10"),
            avg_price=Decimal("100"),
            mark_price=Decimal("120"),
            upl=Decimal("20"),
            stop_loss=Decimal("90"),
            strategy="MHD",
            opened_ts_ms=0,
        )
    )
    monkeypatch.setattr(mhd_module, "detect_mhd", lambda *args, **kwargs: _fake_result())

    signals = MHDDivergenceStrategy({"timeframe": "4H"}).on_bar(context)
    assert len(signals) == 1
    assert signals[0].action is SignalAction.MOVE_STOP
    assert signals[0].stop_loss == Decimal("120.0")

    context2 = context.with_position(
        context.position.model_copy(update={"stop_loss": Decimal("130")})  # type: ignore[union-attr]
    )
    close_signals = MHDDivergenceStrategy({"timeframe": "4H"}).on_bar(context2)
    assert len(close_signals) == 1
    assert close_signals[0].action is SignalAction.CLOSE
