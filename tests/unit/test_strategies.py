from decimal import Decimal

import pytest

from dquant.contracts import (
    Candle,
    CandleBar,
    DivergenceKind,
    DivergenceMethod,
    DivergenceSignal,
    RegimeState,
    ScreenerCandidate,
    Signal,
    SignalAction,
    SignalSide,
)
from dquant.strategies import (
    MHDDivergenceStrategy,
    S1TrendPullbackStrategy,
    S2RangeReversalStrategy,
    S3CounterTrendStrategy,
    StrategyContext,
    StrategyInputError,
    StrategyRegistrationError,
    TemplateStrategy,
    create_strategy,
    generate_signals,
    list_strategies,
    register_strategy,
)


def _candle(
    ts_ms: int,
    close: str,
    *,
    high: str | None = None,
    low: str | None = None,
    confirmed: bool = True,
    bar: CandleBar = CandleBar.ONE_HOUR,
) -> Candle:
    close_decimal = Decimal(close)
    return Candle(
        inst_id="BTC-USDT-SWAP",
        bar=bar,
        ts_ms=ts_ms,
        open=close_decimal,
        high=Decimal(high or close),
        low=Decimal(low or close),
        close=close_decimal,
        volume=Decimal("10"),
        quote_volume=Decimal("1000"),
        confirmed=confirmed,
    )


def _candidate(
    pool: str, regime: RegimeState, kind: DivergenceKind, score: float = 80
) -> ScreenerCandidate:
    return ScreenerCandidate(
        inst_id="BTC-USDT-SWAP",
        pool=pool,
        regime=regime,
        divergence_id="div-1",
        divergence_kind=kind,
        score=score,
        rank=1,
        updated_ts_ms=10_000,
    )


def _divergence(
    kind: DivergenceKind,
    *,
    extreme_price: str = "90",
    height_div: bool = True,
    area_div: bool = True,
    confirmed_ts: int = 3_000,
) -> DivergenceSignal:
    return DivergenceSignal(
        inst_id="BTC-USDT-SWAP",
        bar=CandleBar.FOUR_HOUR,
        kind=kind,
        method=DivergenceMethod.MACD_SEGMENT,
        seg1_start_ts=1_000,
        seg1_end_ts=2_000,
        seg1_extreme_ts=2_000,
        seg1_extreme_price=Decimal("100"),
        seg1_peak=10,
        seg1_area=20,
        seg1_avg_vol=100,
        seg2_start_ts=3_000,
        seg2_end_ts=4_000,
        seg2_extreme_ts=4_000,
        seg2_extreme_price=Decimal(extreme_price),
        seg2_peak=5,
        seg2_area=8,
        seg2_avg_vol=60,
        height_div=height_div,
        area_div=area_div,
        volume_confirm=True,
        close_confirm=True,
        divergence_order=1,
        confirmed_ts=confirmed_ts,
        score=80,
    )


def _context(
    candidate: ScreenerCandidate,
    divergence: DivergenceSignal,
    candles: list[Candle],
    *,
    atr: str = "2",
    ma200: str | None = None,
    btc_regime: RegimeState | None = None,
) -> StrategyContext:
    return StrategyContext(
        candidate=candidate,
        divergence=divergence,
        trigger_candles=tuple(candles),
        atr=Decimal(atr),
        ma200=Decimal(ma200) if ma200 is not None else None,
        btc_regime=btc_regime,
    )


def test_registry_exposes_builtin_strategies_and_s3_is_disabled_by_default() -> None:
    assert {"MHD", "S1", "S2", "S3"}.issubset(set(list_strategies()))
    assert isinstance(create_strategy("MHD"), MHDDivergenceStrategy)
    assert isinstance(create_strategy("S1"), S1TrendPullbackStrategy)
    assert isinstance(create_strategy("S2"), S2RangeReversalStrategy)
    assert create_strategy("S3").params["enabled"] is False


def test_registry_rejects_duplicate_names_and_unknown_strategy() -> None:
    class DuplicateStrategy(S1TrendPullbackStrategy):
        name = "S1"

    with pytest.raises(StrategyRegistrationError, match="already registered"):
        register_strategy(DuplicateStrategy)

    with pytest.raises(KeyError, match="unknown strategy"):
        create_strategy("does-not-exist")


def test_s1_emits_idempotent_long_signal_after_structure_break() -> None:
    candles = [
        _candle(1_000, "95", high="96", low="94"),
        _candle(2_000, "96", high="97", low="95"),
        _candle(3_000, "97", high="98", low="96"),
        _candle(4_000, "101", high="102", low="99"),
    ]
    context = _context(
        _candidate("A", RegimeState.UPTREND, DivergenceKind.BULL_REGULAR),
        _divergence(DivergenceKind.BULL_REGULAR, extreme_price="90"),
        candles,
    )

    strategy = S1TrendPullbackStrategy()
    first = strategy.on_bar(context)
    second = strategy.on_bar(context)

    assert len(first) == 1
    assert first == second
    signal = first[0]
    assert isinstance(signal, Signal)
    assert signal.side == SignalSide.LONG
    assert signal.action == SignalAction.OPEN
    assert signal.price == Decimal("101")
    assert signal.stop_loss == Decimal("89")
    assert signal.take_profits[0].price == Decimal("119")
    assert signal.take_profits[0].fraction == Decimal("0.5")
    assert signal.strategy_name == "S1"


def test_s1_emits_short_signal_for_pool_b_and_rejects_wrong_candidate() -> None:
    candles = [
        _candle(1_000, "105", high="106", low="104"),
        _candle(2_000, "104", high="105", low="103"),
        _candle(3_000, "103", high="104", low="102"),
        _candle(4_000, "99", high="100", low="98"),
    ]
    strategy = S1TrendPullbackStrategy()
    context = _context(
        _candidate("B", RegimeState.DOWNTREND, DivergenceKind.BEAR_REGULAR),
        _divergence(DivergenceKind.BEAR_REGULAR, extreme_price="110"),
        candles,
    )

    signals = strategy.on_bar(context)

    assert len(signals) == 1
    assert signals[0].side == SignalSide.SHORT
    assert signals[0].stop_loss == Decimal("111")
    assert signals[0].take_profits[0].price == Decimal("81")

    wrong_pool = _context(
        _candidate("C", RegimeState.RANGE, DivergenceKind.BEAR_REGULAR),
        _divergence(DivergenceKind.BEAR_REGULAR, extreme_price="110"),
        candles,
    )
    assert strategy.on_bar(wrong_pool) == []


def test_s1_uses_single_take_profit_mode_when_split_exits_disabled() -> None:
    candles = [
        _candle(1_000, "95", high="96", low="94"),
        _candle(2_000, "96", high="97", low="95"),
        _candle(3_000, "97", high="98", low="96"),
        _candle(4_000, "101", high="102", low="99"),
    ]
    context = _context(
        _candidate("A", RegimeState.UPTREND, DivergenceKind.BULL_REGULAR),
        _divergence(DivergenceKind.BULL_REGULAR),
        candles,
    )

    signal = S1TrendPullbackStrategy(params={"split_exits": False}).on_bar(context)[0]

    assert [(tp.price, tp.fraction) for tp in signal.take_profits] == [
        (Decimal("125"), Decimal("1"))
    ]


def test_strategy_context_rejects_unconfirmed_candles_and_future_divergence() -> None:
    candles = [
        _candle(1_000, "95", high="96", low="94"),
        _candle(2_000, "96", high="97", low="95"),
        _candle(3_000, "97", high="98", low="96"),
        _candle(4_000, "101", high="102", low="99", confirmed=False),
    ]
    with pytest.raises(StrategyInputError, match="confirmed"):
        _context(
            _candidate("A", RegimeState.UPTREND, DivergenceKind.BULL_REGULAR),
            _divergence(DivergenceKind.BULL_REGULAR),
            candles,
        )

    future_candles = [_candle(1_000, "95"), _candle(2_000, "96"), _candle(3_000, "97")]
    context = _context(
        _candidate("A", RegimeState.UPTREND, DivergenceKind.BULL_REGULAR),
        _divergence(DivergenceKind.BULL_REGULAR, confirmed_ts=4_000),
        future_candles,
    )
    assert S1TrendPullbackStrategy().on_bar(context) == []


def test_s1_drops_signal_after_price_invalidates_p2_structure() -> None:
    candles = [
        _candle(1_000, "95", high="96", low="94"),
        _candle(2_000, "96", high="97", low="95"),
        _candle(3_000, "89", high="98", low="88"),
        _candle(4_000, "101", high="102", low="99"),
    ]
    context = _context(
        _candidate("A", RegimeState.UPTREND, DivergenceKind.BULL_REGULAR),
        _divergence(DivergenceKind.BULL_REGULAR, extreme_price="90", confirmed_ts=2_000),
        candles,
    )

    assert S1TrendPullbackStrategy().on_bar(context) == []


def test_s2_emits_range_long_with_midpoint_and_opposite_boundary_targets() -> None:
    range_candles = [
        _candle(index * 1_000, "100", high="110", low="90", bar=CandleBar.FOUR_HOUR)
        for index in range(1, 51)
    ]
    trigger_candles = [
        _candle(1_000, "95", high="96", low="94"),
        _candle(2_000, "96", high="97", low="95"),
        _candle(3_000, "97", high="98", low="96"),
        _candle(4_000, "99", high="100", low="97"),
    ]
    context = _context(
        _candidate("C", RegimeState.RANGE, DivergenceKind.BULL_REGULAR),
        _divergence(DivergenceKind.BULL_REGULAR, extreme_price="91"),
        trigger_candles,
        atr="2",
    )
    context = context.with_range_candles(tuple(range_candles))

    signals = S2RangeReversalStrategy().on_bar(context)

    assert len(signals) == 1
    signal = signals[0]
    assert signal.side == SignalSide.LONG
    assert signal.stop_loss == Decimal("88")
    assert [(tp.price, tp.fraction) for tp in signal.take_profits] == [
        (Decimal("100"), Decimal("0.5")),
        (Decimal("110"), Decimal("0.5")),
    ]


def test_s2_rejects_trending_regimes_even_when_pool_is_range_pool() -> None:
    candles = [
        _candle(1_000, "95", high="96", low="94"),
        _candle(2_000, "96", high="97", low="95"),
        _candle(3_000, "97", high="98", low="96"),
        _candle(4_000, "101", high="102", low="99"),
    ]
    context = _context(
        _candidate("C", RegimeState.UPTREND, DivergenceKind.BULL_REGULAR),
        _divergence(DivergenceKind.BULL_REGULAR),
        candles,
    )
    assert S2RangeReversalStrategy().on_bar(context) == []


def test_s3_is_off_by_default_and_enabled_s3_applies_filters_and_targets_ma200() -> None:
    candles = [
        _candle(1_000, "95", high="96", low="94"),
        _candle(2_000, "96", high="97", low="95"),
        _candle(3_000, "97", high="98", low="96"),
        _candle(4_000, "101", high="102", low="99"),
    ]
    context = _context(
        _candidate("E", RegimeState.DOWNTREND, DivergenceKind.BULL_REGULAR, score=75),
        _divergence(DivergenceKind.BULL_REGULAR, extreme_price="90"),
        candles,
        atr="2",
        ma200="120",
        btc_regime=RegimeState.UPTREND,
    )

    assert S3CounterTrendStrategy().on_bar(context) == []
    signals = S3CounterTrendStrategy(params={"enabled": True}).on_bar(context)

    assert len(signals) == 1
    signal = signals[0]
    assert signal.side == SignalSide.LONG
    assert signal.stop_loss == Decimal("89")
    assert [(tp.price, tp.fraction) for tp in signal.take_profits] == [
        (Decimal("120"), Decimal("0.5")),
        (Decimal("125"), Decimal("0.5")),
    ]


def test_s3_rejects_missing_strength_flags_or_opposite_btc_trend() -> None:
    candles = [
        _candle(1_000, "95", high="96", low="94"),
        _candle(2_000, "96", high="97", low="95"),
        _candle(3_000, "97", high="98", low="96"),
        _candle(4_000, "101", high="102", low="99"),
    ]
    candidate = _candidate("E", RegimeState.UPTREND, DivergenceKind.BEAR_REGULAR, score=90)
    weak_divergence = _divergence(
        DivergenceKind.BEAR_REGULAR,
        extreme_price="110",
        area_div=False,
    )
    context = _context(
        candidate,
        weak_divergence,
        candles,
        ma200="80",
        btc_regime=RegimeState.DOWNTREND,
    )
    strategy = S3CounterTrendStrategy(params={"enabled": True})

    assert strategy.on_bar(context) == []


def test_position_management_emits_s1_time_stop_close_signal() -> None:
    from dquant.contracts import Position, PositionSide

    candles = [
        _candle(1_000, "95", high="96", low="94"),
        _candle(2_000, "96", high="97", low="95"),
        _candle(3_000, "97", high="98", low="96"),
        _candle(4_000, "101", high="102", low="99"),
    ]
    context = _context(
        _candidate("A", RegimeState.UPTREND, DivergenceKind.BULL_REGULAR),
        _divergence(DivergenceKind.BULL_REGULAR),
        candles,
    ).with_position(
        Position(
            inst_id="BTC-USDT-SWAP",
            pos_side=PositionSide.LONG,
            size=Decimal("1"),
            avg_price=Decimal("100"),
            mark_price=Decimal("101"),
            upl=Decimal("1"),
            stop_loss=Decimal("90"),
            strategy="S1",
            opened_ts_ms=1,
        ),
        bars_since_entry=30,
        reached_one_r=False,
    )

    signals = S1TrendPullbackStrategy().on_bar(context)

    assert len(signals) == 1
    assert signals[0].action == SignalAction.CLOSE
    assert signals[0].side == SignalSide.LONG
    assert signals[0].stop_loss is None


def test_strategy_runner_preserves_order_and_only_returns_public_signals() -> None:
    candles = [
        _candle(1_000, "95", high="96", low="94"),
        _candle(2_000, "96", high="97", low="95"),
        _candle(3_000, "97", high="98", low="96"),
        _candle(4_000, "101", high="102", low="99"),
    ]
    context = _context(
        _candidate("A", RegimeState.UPTREND, DivergenceKind.BULL_REGULAR),
        _divergence(DivergenceKind.BULL_REGULAR),
        candles,
    )

    signals = generate_signals(S1TrendPullbackStrategy(), [context, context])

    assert len(signals) == 2
    assert all(isinstance(signal, Signal) for signal in signals)
    assert signals[0].signal_id == signals[1].signal_id


def test_template_strategy_is_safe_to_run_without_generating_fake_signals() -> None:
    candles = [_candle(1_000, "100")]
    context = _context(
        _candidate("A", RegimeState.UPTREND, DivergenceKind.BULL_REGULAR),
        _divergence(DivergenceKind.BULL_REGULAR),
        candles,
    )

    strategy = TemplateStrategy()

    assert strategy.accepts(context.candidate) is False
    assert strategy.on_bar(context) == []
