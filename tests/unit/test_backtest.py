"""M06 回测引擎单元测试。

测试只使用模拟行情和固定风控桩，不连接交易所、不读取真实密钥。
关键口径：Candle.ts_ms 为 K 线开盘时间；信号在收盘产生，下一根执行周期开盘成交；
同根 K 线同时触发止损与止盈时按止损处理。
"""

from __future__ import annotations

import math
import statistics
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, TypeAlias

import pytest

from dquant.backtest import (
    BacktestBarContext,
    BacktestConfig,
    BacktestDataError,
    BacktestExecutionError,
    BacktestResult,
    BacktestSignalError,
    EquityPoint,
    ExitReason,
    FeatureRequest,
    FundingRatePoint,
    RejectionReason,
    RiskRequest,
    StrategyFeatures,
    TradeExit,
    apply_slippage,
    build_drawdown_curve,
    build_walk_forward_windows,
    compare_results,
    compute_metrics,
    compute_monthly_returns,
    expand_parameter_grid,
    render_comparison_report,
    render_report,
    render_sweep_report,
    render_walk_forward_report,
    round_to_tick,
    run_backtest,
    run_parameter_sweep,
    run_walk_forward,
    write_report,
)
from dquant.contracts import (
    Candle,
    CandleBar,
    Fill,
    Instrument,
    OrderType,
    RegimeResult,
    RegimeState,
    RiskDecision,
    Signal,
    SignalAction,
    SignalSide,
    TakeProfit,
)

INST = "BTC-USDT-SWAP"
INST2 = "ETH-USDT-SWAP"
HOUR_MS = 3_600_000
DAY_MS = 86_400_000
D1_BASE_MS = int(datetime(2026, 1, 1, 16, 0, tzinfo=UTC).timestamp() * 1000)

Handler: TypeAlias = Callable[[BacktestBarContext], Sequence[Signal]]


def make_instrument(
    inst_id: str = INST,
    *,
    ct_val: str = "1",
    lot_sz: str = "1",
    min_sz: str = "1",
    tick_sz: str = "0.0001",
) -> Instrument:
    return Instrument(
        inst_id=inst_id,
        base=inst_id.split("-")[0],
        quote="USDT",
        ct_val=Decimal(ct_val),
        lot_sz=Decimal(lot_sz),
        min_sz=Decimal(min_sz),
        tick_sz=Decimal(tick_sz),
    )


def make_candle(
    ts_ms: int,
    open_: str,
    high: str,
    low: str,
    close: str,
    *,
    inst_id: str = INST,
    bar: CandleBar = CandleBar.ONE_HOUR,
    confirmed: bool = True,
) -> Candle:
    return Candle(
        inst_id=inst_id,
        bar=bar,
        ts_ms=ts_ms,
        open=Decimal(open_),
        high=Decimal(high),
        low=Decimal(low),
        close=Decimal(close),
        volume=Decimal("10"),
        quote_volume=Decimal("1000"),
        confirmed=confirmed,
    )


def single_stream(
    candles: Sequence[Candle],
    inst_id: str = INST,
) -> dict[str, dict[CandleBar, list[Candle]]]:
    return {inst_id: {CandleBar.ONE_HOUR: list(candles)}}


def open_once(ctx: BacktestBarContext) -> list[Signal]:
    """在 1H 收盘第一次产生开仓信号，便于复用测试场景。"""

    if ctx.close_ts_ms == HOUR_MS:
        return [make_signal(created_ts_ms=ctx.close_ts_ms)]
    return []


def make_signal(
    *,
    created_ts_ms: int,
    inst_id: str = INST,
    side: SignalSide = SignalSide.LONG,
    action: SignalAction = SignalAction.OPEN,
    entry_type: OrderType = OrderType.MARKET,
    price: str = "100",
    stop_loss: str | None = "95",
    take_profits: Sequence[tuple[str, str]] = (("110", "1"),),
    signal_id: str = "sig-1",
    strategy_name: str = "test-strategy",
) -> Signal:
    return Signal(
        signal_id=signal_id,
        inst_id=inst_id,
        side=side,
        action=action,
        entry_type=entry_type,
        price=Decimal(price),
        stop_loss=None if stop_loss is None else Decimal(stop_loss),
        take_profits=[
            TakeProfit(price=Decimal(tp_price), fraction=Decimal(fraction))
            for tp_price, fraction in take_profits
        ],
        reason="测试信号",
        strategy_name=strategy_name,
        created_ts_ms=created_ts_ms,
    )


class ScriptedStrategy:
    """按回调脚本产生信号，并记录每次上下文与成交。"""

    def __init__(self, handler: Handler, *, name: str = "scripted", version: str = "1.0.0") -> None:
        self.name = name
        self.version = version
        self._handler = handler
        self.contexts: list[BacktestBarContext] = []
        self.fills: list[Fill] = []

    def on_bar(self, context: BacktestBarContext) -> list[Signal]:
        self.contexts.append(context)
        return list(self._handler(context))

    def on_fill(self, context: BacktestBarContext, fill: Fill) -> None:
        self.fills.append(fill)


class FixedRiskGate:
    """固定数量风控桩，用于验证回测引擎与 M08 的注入边界。"""

    def __init__(
        self,
        qty: str = "1",
        *,
        approved: bool = True,
        rule: str = "TEST_RULE",
        detail: str = "测试风控通过",
    ) -> None:
        self.qty = Decimal(qty)
        self.approved = approved
        self.rule = rule
        self.detail = detail
        self.requests: list[RiskRequest] = []

    def evaluate(self, request: RiskRequest) -> RiskDecision:
        self.requests.append(request)
        return RiskDecision(
            signal_id=request.signal.signal_id,
            approved=self.approved,
            rule=self.rule,
            detail=self.detail,
            qty=self.qty if self.approved else Decimal("0"),
            risk_fraction=Decimal("0.01"),
        )


def build_long_trade_data() -> list[Candle]:
    return [
        make_candle(0, "100", "101", "99", "100"),
        make_candle(HOUR_MS, "100", "110", "99", "109"),
        make_candle(2 * HOUR_MS, "109", "111", "108", "110"),
    ]


def test_long_trade_fills_next_open_and_uses_fee_models() -> None:
    strategy = ScriptedStrategy(open_once)
    gate = FixedRiskGate()

    result = run_backtest(
        data=single_stream(build_long_trade_data()),
        instruments={INST: make_instrument()},
        strategy=strategy,
        risk_gate=gate,
        config=BacktestConfig(initial_equity=Decimal("10000")),
    )

    assert result.metrics.trade_count == 1
    trade = result.trades[0]
    entry_price = Decimal("100.05")
    entry_fee = entry_price * Decimal("0.0005")
    exit_fee = Decimal("110") * Decimal("0.0002")
    expected_net = (Decimal("110") - entry_price) - entry_fee - exit_fee
    assert trade.entry_price == entry_price
    assert trade.average_exit_price == Decimal("110")
    assert trade.entry_ts_ms == HOUR_MS
    assert trade.exits[0].reason is ExitReason.TAKE_PROFIT
    assert trade.exits[0].ts_ms == 2 * HOUR_MS
    assert trade.net_pnl == expected_net
    assert trade.fees == entry_fee + exit_fee
    assert trade.holding_bars == 1
    assert result.metrics.final_equity == Decimal("10000") + expected_net
    assert result.metrics.signal_count == 1
    assert len(strategy.fills) == 2


def test_stop_loss_wins_when_stop_and_take_profit_touch_same_bar() -> None:
    data = [
        make_candle(0, "100", "101", "99", "100"),
        make_candle(HOUR_MS, "100", "110", "94", "100"),
        make_candle(2 * HOUR_MS, "100", "101", "99", "100"),
    ]
    strategy = ScriptedStrategy(open_once)

    result = run_backtest(
        data=single_stream(data),
        instruments={INST: make_instrument()},
        strategy=strategy,
        risk_gate=FixedRiskGate(),
        config=BacktestConfig(initial_equity=Decimal("10000")),
    )

    trade = result.trades[0]
    assert len(trade.exits) == 1
    assert trade.exits[0].reason is ExitReason.STOP_LOSS
    # 止损触发价 95，经不利滑点后为 94.9525。
    expected_exit = Decimal("94.9525")
    assert trade.average_exit_price == expected_exit
    expected_net = (
        (expected_exit - Decimal("100.05"))
        - Decimal("100.05") * Decimal("0.0005")
        - expected_exit * Decimal("0.0005")
    )
    assert trade.net_pnl == expected_net


def test_gap_through_stop_fills_at_worse_open_price() -> None:
    data = [
        make_candle(0, "100", "101", "99", "100"),
        make_candle(HOUR_MS, "90", "92", "88", "91"),
        make_candle(2 * HOUR_MS, "91", "92", "90", "91"),
    ]
    strategy = ScriptedStrategy(open_once)

    result = run_backtest(
        data=single_stream(data),
        instruments={INST: make_instrument()},
        strategy=strategy,
        risk_gate=FixedRiskGate(),
        config=BacktestConfig(initial_equity=Decimal("10000")),
    )

    assert result.trades[0].average_exit_price == Decimal("89.955")
    assert result.trades[0].exits[0].reason is ExitReason.STOP_LOSS


def test_short_trade_mirrors_matching_and_costs() -> None:
    data = [
        make_candle(0, "100", "101", "99", "100"),
        make_candle(HOUR_MS, "100", "101", "89", "90"),
        make_candle(2 * HOUR_MS, "90", "91", "89", "90"),
    ]
    strategy = ScriptedStrategy(
        lambda ctx: (
            [
                make_signal(
                    created_ts_ms=ctx.close_ts_ms,
                    side=SignalSide.SHORT,
                    stop_loss="105",
                    take_profits=(("90", "1"),),
                )
            ]
            if ctx.close_ts_ms == HOUR_MS
            else []
        )
    )

    result = run_backtest(
        data=single_stream(data),
        instruments={INST: make_instrument()},
        strategy=strategy,
        risk_gate=FixedRiskGate(),
        config=BacktestConfig(initial_equity=Decimal("10000")),
    )

    trade = result.trades[0]
    entry_price = Decimal("99.95")
    expected_net = (
        (entry_price - Decimal("90"))
        - entry_price * Decimal("0.0005")
        - Decimal("90") * Decimal("0.0002")
    )
    assert trade.entry_price == entry_price
    assert trade.exits[0].reason is ExitReason.TAKE_PROFIT
    assert trade.net_pnl == expected_net


def test_limit_entry_fills_maker_and_expires_after_ttl() -> None:
    data = [
        make_candle(0, "100", "101", "99", "100"),
        make_candle(HOUR_MS, "100", "101", "98", "99.5"),
        make_candle(2 * HOUR_MS, "99.5", "101", "99", "100"),
    ]

    filled = run_backtest(
        data=single_stream(data),
        instruments={INST: make_instrument()},
        strategy=ScriptedStrategy(
            lambda ctx: (
                [
                    make_signal(
                        created_ts_ms=ctx.close_ts_ms,
                        entry_type=OrderType.LIMIT,
                        price="99",
                        take_profits=(("120", "1"),),
                    )
                ]
                if ctx.close_ts_ms == HOUR_MS
                else []
            )
        ),
        risk_gate=FixedRiskGate(),
        config=BacktestConfig(initial_equity=Decimal("10000")),
    )
    assert filled.trades[0].entry_price == Decimal("99")

    expired = run_backtest(
        data=single_stream(data),
        instruments={INST: make_instrument()},
        strategy=ScriptedStrategy(
            lambda ctx: (
                [
                    make_signal(
                        created_ts_ms=ctx.close_ts_ms,
                        entry_type=OrderType.LIMIT,
                        price="90",
                        stop_loss="80",
                        take_profits=(("120", "1"),),
                    )
                ]
                if ctx.close_ts_ms == HOUR_MS
                else []
            )
        ),
        risk_gate=FixedRiskGate(),
        config=BacktestConfig(initial_equity=Decimal("10000"), limit_order_ttl_bars=1),
    )
    assert expired.trades == ()
    assert expired.rejected_signals[0].reason is RejectionReason.LIMIT_ORDER_EXPIRED


def test_partial_take_profit_then_move_stop() -> None:
    data = [
        make_candle(0, "100", "101", "99", "100"),
        make_candle(HOUR_MS, "100", "106", "99", "105"),
        make_candle(2 * HOUR_MS, "105", "106", "99", "100"),
        make_candle(3 * HOUR_MS, "100", "101", "99", "100"),
    ]

    def handler(ctx: BacktestBarContext) -> list[Signal]:
        if ctx.close_ts_ms == HOUR_MS:
            return [
                make_signal(
                    created_ts_ms=ctx.close_ts_ms,
                    take_profits=(("105", "0.5"), ("110", "0.5")),
                )
            ]
        if ctx.close_ts_ms == 2 * HOUR_MS and ctx.position() is not None:
            return [
                make_signal(
                    created_ts_ms=ctx.close_ts_ms,
                    action=SignalAction.MOVE_STOP,
                    price="105",
                    stop_loss="100",
                    take_profits=(),
                    signal_id="move-1",
                )
            ]
        return []

    result = run_backtest(
        data=single_stream(data),
        instruments={INST: make_instrument()},
        strategy=ScriptedStrategy(handler),
        risk_gate=FixedRiskGate(qty="10"),
        config=BacktestConfig(initial_equity=Decimal("10000")),
    )

    trade = result.trades[0]
    assert trade.initial_qty == Decimal("10")
    assert [exit.reason for exit in trade.exits] == [
        ExitReason.TAKE_PROFIT,
        ExitReason.STOP_LOSS,
    ]
    assert [exit.qty for exit in trade.exits] == [Decimal("5"), Decimal("5")]
    assert trade.exits[0].price == Decimal("105")
    assert trade.exits[1].price == Decimal("99.95")
    assert trade.net_pnl == Decimal("23.394875")


def test_risk_veto_and_position_conflict_are_explicit() -> None:
    data = build_long_trade_data()
    vetoed = run_backtest(
        data=single_stream(data),
        instruments={INST: make_instrument()},
        strategy=ScriptedStrategy(
            lambda ctx: (
                [make_signal(created_ts_ms=ctx.close_ts_ms)] if ctx.close_ts_ms == HOUR_MS else []
            )
        ),
        risk_gate=FixedRiskGate(approved=False),
        config=BacktestConfig(initial_equity=Decimal("10000")),
    )
    assert vetoed.trades == ()
    assert vetoed.rejected_signals[0].reason is RejectionReason.RISK_VETO
    assert "测试风控通过" in vetoed.rejected_signals[0].detail

    conflicted = run_backtest(
        data=single_stream(data),
        instruments={INST: make_instrument()},
        strategy=ScriptedStrategy(
            lambda ctx: (
                [
                    make_signal(created_ts_ms=ctx.close_ts_ms, signal_id="a"),
                    make_signal(created_ts_ms=ctx.close_ts_ms, signal_id="b"),
                ]
                if ctx.close_ts_ms == HOUR_MS
                else []
            )
        ),
        risk_gate=FixedRiskGate(),
        config=BacktestConfig(initial_equity=Decimal("10000")),
    )
    assert conflicted.metrics.signal_count == 2
    assert any(
        item.reason is RejectionReason.POSITION_CONFLICT for item in conflicted.rejected_signals
    )


def test_insufficient_margin_and_qty_precision_are_rejected() -> None:
    data = build_long_trade_data()
    strategy = ScriptedStrategy(open_once)

    margin = run_backtest(
        data=single_stream(data),
        instruments={INST: make_instrument()},
        strategy=strategy,
        risk_gate=FixedRiskGate(qty="10"),
        config=BacktestConfig(initial_equity=Decimal("50")),
    )
    assert margin.rejected_signals[0].reason is RejectionReason.INSUFFICIENT_MARGIN

    precision = run_backtest(
        data=single_stream(data),
        instruments={INST: make_instrument(lot_sz="1")},
        strategy=strategy,
        risk_gate=FixedRiskGate(qty="1.5"),
        config=BacktestConfig(initial_equity=Decimal("10000")),
    )
    assert precision.rejected_signals[0].reason is RejectionReason.INVALID_QUANTITY


def test_signal_cannot_look_ahead() -> None:
    data = build_long_trade_data()
    strategy = ScriptedStrategy(
        lambda ctx: (
            [make_signal(created_ts_ms=ctx.close_ts_ms + 1)] if ctx.close_ts_ms == HOUR_MS else []
        )
    )

    with pytest.raises(BacktestSignalError, match="created_ts_ms"):
        run_backtest(
            data=single_stream(data),
            instruments={INST: make_instrument()},
            strategy=strategy,
            risk_gate=FixedRiskGate(),
            config=BacktestConfig(initial_equity=Decimal("10000")),
        )


def test_strategy_cannot_read_unclosed_candles() -> None:
    observed_lengths: list[int] = []

    def handler(ctx: BacktestBarContext) -> list[Signal]:
        observed_lengths.append(len(ctx.candles(limit=100)))
        if ctx.close_ts_ms == HOUR_MS:
            return [make_signal(created_ts_ms=ctx.close_ts_ms)]
        return []

    run_backtest(
        data=single_stream(build_long_trade_data()),
        instruments={INST: make_instrument()},
        strategy=ScriptedStrategy(handler),
        risk_gate=FixedRiskGate(),
        config=BacktestConfig(initial_equity=Decimal("10000")),
    )

    assert observed_lengths == [1, 2, 3]


def test_invalid_take_profit_layout_is_rejected() -> None:
    data = build_long_trade_data()
    strategy = ScriptedStrategy(
        lambda ctx: (
            [
                make_signal(
                    created_ts_ms=ctx.close_ts_ms,
                    take_profits=(("105", "0.7"), ("110", "0.5")),
                )
            ]
            if ctx.close_ts_ms == HOUR_MS
            else []
        )
    )

    with pytest.raises(BacktestSignalError, match="fraction"):
        run_backtest(
            data=single_stream(data),
            instruments={INST: make_instrument()},
            strategy=strategy,
            risk_gate=FixedRiskGate(),
            config=BacktestConfig(initial_equity=Decimal("10000")),
        )


def test_data_validation_rejects_unconfirmed_misaligned_and_unsorted_candles() -> None:
    cases = [
        [make_candle(HOUR_MS, "100", "101", "99", "100", confirmed=False)],
        [make_candle(123, "100", "101", "99", "100")],
        [
            make_candle(2 * HOUR_MS, "100", "101", "99", "100"),
            make_candle(HOUR_MS, "100", "101", "99", "100"),
        ],
        [
            make_candle(HOUR_MS, "100", "101", "99", "100"),
            make_candle(HOUR_MS, "100", "101", "99", "100"),
        ],
    ]

    for candles in cases:
        with pytest.raises(BacktestDataError):
            run_backtest(
                data=single_stream(candles),
                instruments={INST: make_instrument()},
                strategy=ScriptedStrategy(lambda ctx: []),
                risk_gate=FixedRiskGate(),
                config=BacktestConfig(initial_equity=Decimal("10000")),
            )


def test_daily_candles_align_to_shanghai_midnight() -> None:
    valid = [
        make_candle(
            D1_BASE_MS,
            "100",
            "101",
            "99",
            "100",
            bar=CandleBar.ONE_DAY,
        )
    ]
    result = run_backtest(
        data={INST: {CandleBar.ONE_DAY: valid}},
        instruments={INST: make_instrument()},
        strategy=ScriptedStrategy(lambda ctx: []),
        risk_gate=FixedRiskGate(),
        config=BacktestConfig(initial_equity=Decimal("10000"), execution_bar=CandleBar.ONE_DAY),
    )
    assert result.equity_curve

    invalid = [
        make_candle(
            0,
            "100",
            "101",
            "99",
            "100",
            bar=CandleBar.ONE_DAY,
        )
    ]
    with pytest.raises(BacktestDataError, match="对齐"):
        run_backtest(
            data={INST: {CandleBar.ONE_DAY: invalid}},
            instruments={INST: make_instrument()},
            strategy=ScriptedStrategy(lambda ctx: []),
            risk_gate=FixedRiskGate(),
            config=BacktestConfig(initial_equity=Decimal("10000"), execution_bar=CandleBar.ONE_DAY),
        )


def test_missing_candles_produce_warning_and_stream_quality() -> None:
    data = [
        make_candle(0, "100", "101", "99", "100"),
        make_candle(2 * HOUR_MS, "100", "101", "99", "100"),
        make_candle(3 * HOUR_MS, "100", "101", "99", "100"),
    ]
    result = run_backtest(
        data=single_stream(data),
        instruments={INST: make_instrument()},
        strategy=ScriptedStrategy(lambda ctx: []),
        risk_gate=FixedRiskGate(),
        config=BacktestConfig(initial_equity=Decimal("10000")),
    )

    quality = result.stream_quality[0]
    assert quality.missing_bars == 1
    assert any("缺失" in warning for warning in result.warnings)


def test_funding_rate_is_charged_to_long_position() -> None:
    data = [make_candle(index * HOUR_MS, "100", "101", "99", "100") for index in range(8)]
    strategy = ScriptedStrategy(
        lambda ctx: (
            [make_signal(created_ts_ms=ctx.close_ts_ms, take_profits=())]
            if ctx.close_ts_ms == HOUR_MS
            else []
        )
    )

    result = run_backtest(
        data=single_stream(data),
        instruments={INST: make_instrument()},
        strategy=strategy,
        risk_gate=FixedRiskGate(),
        config=BacktestConfig(initial_equity=Decimal("10000"), close_positions_at_end=True),
        funding_rates={INST: [FundingRatePoint(ts_ms=8 * HOUR_MS, rate=Decimal("0.001"))]},
    )

    expected_funding = Decimal("100") * Decimal("0.001")
    assert result.metrics.total_funding == expected_funding
    assert result.trades[0].funding == expected_funding
    assert result.trades[0].exits[0].reason is ExitReason.END_OF_BACKTEST


def test_multi_instrument_portfolio_shares_equity_and_sees_pending_orders() -> None:
    data = {
        INST: {
            CandleBar.ONE_HOUR: [
                make_candle(0, "100", "101", "99", "100"),
                make_candle(HOUR_MS, "100", "101", "99", "100"),
            ]
        },
        INST2: {
            CandleBar.ONE_HOUR: [
                make_candle(0, "50", "51", "49", "50", inst_id=INST2),
                make_candle(HOUR_MS, "50", "51", "49", "50", inst_id=INST2),
            ]
        },
    }

    def handler(ctx: BacktestBarContext) -> list[Signal]:
        if ctx.close_ts_ms != HOUR_MS:
            return []
        return [
            make_signal(
                created_ts_ms=ctx.close_ts_ms,
                inst_id=ctx.inst_id,
                price=str(ctx.candle.close),
                stop_loss=str(ctx.candle.close * Decimal("0.9")),
                take_profits=(),
                signal_id=f"sig-{ctx.inst_id}",
            )
        ]

    gate = FixedRiskGate()
    result = run_backtest(
        data=data,
        instruments={INST: make_instrument(), INST2: make_instrument(INST2)},
        strategy=ScriptedStrategy(handler),
        risk_gate=gate,
        config=BacktestConfig(initial_equity=Decimal("10000")),
    )

    assert result.metrics.trade_count == 2
    assert len(result.equity_curve) == 2
    assert len(gate.requests) == 2
    assert gate.requests[1].pending_orders
    assert gate.requests[1].pending_orders[0].inst_id == INST
    assert result.metrics.final_equity > 0


def test_rejected_move_stop_and_reduce_without_position() -> None:
    data = build_long_trade_data()

    def handler(ctx: BacktestBarContext) -> list[Signal]:
        if ctx.close_ts_ms == HOUR_MS:
            return [
                make_signal(
                    created_ts_ms=ctx.close_ts_ms,
                    action=SignalAction.CLOSE,
                    price="100",
                    stop_loss=None,
                    take_profits=(),
                    signal_id="close-1",
                )
            ]
        return []

    result = run_backtest(
        data=single_stream(data),
        instruments={INST: make_instrument()},
        strategy=ScriptedStrategy(handler),
        risk_gate=FixedRiskGate(),
        config=BacktestConfig(initial_equity=Decimal("10000")),
    )
    assert result.rejected_signals[0].reason is RejectionReason.NO_POSITION


def test_metrics_from_known_equity_curve() -> None:
    day = DAY_MS
    start = int(datetime(2026, 1, 1, tzinfo=UTC).timestamp() * 1000)
    equity = (
        (start, Decimal("10000")),
        (start + day, Decimal("11000")),
        (start + 2 * day, Decimal("10450")),
        (start + 3 * day, Decimal("11000")),
    )

    curve = tuple(
        EquityPoint(
            ts_ms=ts,
            equity=value,
            wallet_balance=value,
            unrealized_pnl=Decimal("0"),
            used_margin=Decimal("0"),
        )
        for ts, value in equity
    )
    metrics = compute_metrics(
        equity_curve=curve,
        trades=(),
        execution_bar=CandleBar.ONE_DAY,
        initial_equity=Decimal("10000"),
    )

    returns = [0.1, -0.05, 11000 / 10450 - 1]
    mean = statistics.fmean(returns)
    stdev = statistics.stdev(returns)
    downside = math.sqrt(sum(min(value, 0.0) ** 2 for value in returns) / len(returns))
    annualized = (1.1 ** (365 / 3)) - 1

    assert metrics.total_return == pytest.approx(0.1)
    assert metrics.final_equity == Decimal("11000")
    assert metrics.max_drawdown == pytest.approx(0.05)
    assert metrics.max_drawdown_amount == Decimal("550")
    assert metrics.sharpe == pytest.approx(mean / stdev * math.sqrt(365))
    assert metrics.sortino == pytest.approx(mean / downside * math.sqrt(365))
    assert metrics.annualized_return == pytest.approx(annualized)
    assert metrics.calmar == pytest.approx(annualized / 0.05)
    assert metrics.trade_count == 0
    assert metrics.win_rate == 0.0
    assert metrics.profit_factor is None


def test_flat_equity_metrics_are_explicit_zeroes() -> None:
    start = int(datetime(2026, 1, 1, tzinfo=UTC).timestamp() * 1000)
    curve = tuple(
        EquityPoint(
            ts_ms=start + index * DAY_MS,
            equity=Decimal("10000"),
            wallet_balance=Decimal("10000"),
            unrealized_pnl=Decimal("0"),
            used_margin=Decimal("0"),
        )
        for index in range(3)
    )
    metrics = compute_metrics(
        equity_curve=curve,
        trades=(),
        execution_bar=CandleBar.ONE_DAY,
        initial_equity=Decimal("10000"),
    )

    assert metrics.total_return == 0.0
    assert metrics.sharpe == 0.0
    assert metrics.max_drawdown == 0.0
    assert any("波动" in note or "收益" in note for note in metrics.notes)


def test_monthly_returns_use_timezone_boundaries() -> None:
    points = (
        EquityPoint(
            ts_ms=int(datetime(2026, 1, 1, tzinfo=UTC).timestamp() * 1000),
            equity=Decimal("100"),
            wallet_balance=Decimal("100"),
            unrealized_pnl=Decimal("0"),
            used_margin=Decimal("0"),
        ),
        EquityPoint(
            ts_ms=int(datetime(2026, 1, 31, 23, tzinfo=UTC).timestamp() * 1000),
            equity=Decimal("110"),
            wallet_balance=Decimal("110"),
            unrealized_pnl=Decimal("0"),
            used_margin=Decimal("0"),
        ),
        EquityPoint(
            ts_ms=int(datetime(2026, 2, 28, 23, tzinfo=UTC).timestamp() * 1000),
            equity=Decimal("121"),
            wallet_balance=Decimal("121"),
            unrealized_pnl=Decimal("0"),
            used_margin=Decimal("0"),
        ),
    )

    monthly = compute_monthly_returns(points, Decimal("100"), timezone="UTC")
    assert [(item.year, item.month) for item in monthly] == [(2026, 1), (2026, 2)]
    assert monthly[0].return_pct == pytest.approx(0.1)
    assert monthly[1].return_pct == pytest.approx(0.1)


def test_drawdown_curve_tracks_peak_and_trough() -> None:
    curve = (
        EquityPoint(1, Decimal("100"), Decimal("100"), Decimal("0"), Decimal("0")),
        EquityPoint(2, Decimal("120"), Decimal("120"), Decimal("0"), Decimal("0")),
        EquityPoint(3, Decimal("90"), Decimal("90"), Decimal("0"), Decimal("0")),
        EquityPoint(4, Decimal("130"), Decimal("130"), Decimal("0"), Decimal("0")),
    )
    drawdown = build_drawdown_curve(curve)

    assert drawdown[2].drawdown == pytest.approx(0.25)
    assert drawdown[2].peak_equity == Decimal("120")
    assert drawdown[3].drawdown == 0.0


def test_report_contains_metrics_charts_trades_and_escapes_html(tmp_path: Path) -> None:
    result = run_backtest(
        data=single_stream(build_long_trade_data()),
        instruments={INST: make_instrument()},
        strategy=ScriptedStrategy(
            lambda ctx: (
                [
                    make_signal(
                        created_ts_ms=ctx.close_ts_ms,
                        strategy_name="<script>alert(1)</script>",
                    )
                ]
                if ctx.close_ts_ms == HOUR_MS
                else []
            ),
            name="<script>alert(1)</script>",
        ),
        risk_gate=FixedRiskGate(),
        config=BacktestConfig(initial_equity=Decimal("10000")),
    )

    html = render_report(result, generated_at_ts_ms=0)
    assert html.startswith("<!DOCTYPE html>")
    assert "资金曲线" in html
    assert "回撤曲线" in html
    assert "逐笔交易" in html
    assert "月度收益" in html
    assert "偏差警示" in html
    assert "<svg" in html
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in html
    assert "<script>alert(1)</script>" not in html

    path = write_report(result, tmp_path / "report.html", generated_at_ts_ms=0)
    assert path.is_file()
    assert path.read_text(encoding="utf-8") == html


def test_walk_forward_window_generation_and_validation() -> None:
    start = 0
    end = 10 * DAY_MS
    windows = build_walk_forward_windows(
        start_ts_ms=start,
        end_ts_ms=end,
        train_days=4,
        test_days=2,
        step_days=2,
    )
    assert len(windows) == 3
    assert windows[0].train_start_ts_ms == 0
    assert windows[0].train_end_ts_ms == 4 * DAY_MS
    assert windows[0].test_start_ts_ms == 4 * DAY_MS
    assert windows[0].test_end_ts_ms == 6 * DAY_MS
    assert windows[2].test_end_ts_ms == 10 * DAY_MS

    with pytest.raises(ValueError, match="step_days"):
        build_walk_forward_windows(
            start_ts_ms=start,
            end_ts_ms=end,
            train_days=4,
            test_days=2,
            step_days=1,
        )

    with pytest.raises(ValueError, match="窗口"):
        build_walk_forward_windows(
            start_ts_ms=start,
            end_ts_ms=DAY_MS,
            train_days=4,
            test_days=2,
            step_days=2,
        )


def test_parameter_grid_expansion_is_deterministic() -> None:
    grid = {"b": [2, 1], "a": ["x", "y"]}
    combos = expand_parameter_grid(grid)
    assert combos == [
        {"a": "x", "b": 2},
        {"a": "x", "b": 1},
        {"a": "y", "b": 2},
        {"a": "y", "b": 1},
    ]

    with pytest.raises(ValueError, match="空"):
        expand_parameter_grid({"a": []})


def test_parameter_sweep_outputs_heatmap_data() -> None:
    data = [
        make_candle(0, "100", "101", "99", "100"),
        make_candle(HOUR_MS, "100", "106", "99", "105"),
        make_candle(2 * HOUR_MS, "105", "112", "104", "110"),
        make_candle(3 * HOUR_MS, "110", "121", "109", "120"),
        make_candle(4 * HOUR_MS, "120", "121", "119", "120"),
    ]

    def factory(params: Mapping[str, Any]) -> ScriptedStrategy:
        def handler(ctx: BacktestBarContext) -> list[Signal]:
            if ctx.position() is not None or len(ctx.candles(limit=5)) != 1:
                return []
            price = ctx.candle.close
            if params["side"] == "long":
                side = SignalSide.LONG
                stop = price * Decimal("0.9")
                take_profit = price * (1 + Decimal(str(params["tp_pct"])) / 100)
            else:
                side = SignalSide.SHORT
                stop = price * Decimal("1.1")
                take_profit = price * (1 - Decimal(str(params["tp_pct"])) / 100)
            return [
                make_signal(
                    created_ts_ms=ctx.close_ts_ms,
                    side=side,
                    price=str(price),
                    stop_loss=str(stop),
                    take_profits=((str(take_profit), "1"),),
                )
            ]

        return ScriptedStrategy(handler, name=f"sweep-{params['side']}-{params['tp_pct']}")

    sweep = run_parameter_sweep(
        data=single_stream(data),
        instruments={INST: make_instrument()},
        strategy_factory=factory,
        risk_gate_factory=lambda: FixedRiskGate(),
        config=BacktestConfig(initial_equity=Decimal("10000")),
        param_grid={"side": ["long", "short"], "tp_pct": [5, 10]},
        objective="total_return",
    )

    assert len(sweep.entries) == 4
    assert sweep.best is not None
    assert sweep.best.params["side"] == "long"
    html = render_sweep_report(sweep, generated_at_ts_ms=0)
    assert "参数热力图" in html
    assert "long" in html
    assert "short" in html


def test_walk_forward_selects_trading_parameter_and_stitches_equity() -> None:
    data = [
        make_candle(
            D1_BASE_MS + index * DAY_MS,
            str(100 + index),
            str(101 + index),
            str(99 + index),
            str(100 + index),
            bar=CandleBar.ONE_DAY,
        )
        for index in range(12)
    ]

    def factory(params: Mapping[str, Any]) -> ScriptedStrategy:
        def handler(ctx: BacktestBarContext) -> list[Signal]:
            if not params["enabled"] or ctx.position() is not None:
                return []
            if len(ctx.candles(limit=100)) % 2 != 0:
                return []
            price = ctx.candle.close
            return [
                make_signal(
                    created_ts_ms=ctx.close_ts_ms,
                    price=str(price),
                    stop_loss=str(price * Decimal("0.8")),
                    take_profits=((str(price * Decimal("2")), "1"),),
                )
            ]

        return ScriptedStrategy(handler, name=f"wf-{params['enabled']}")

    walk_forward = run_walk_forward(
        data={INST: {CandleBar.ONE_DAY: data}},
        instruments={INST: make_instrument()},
        strategy_factory=factory,
        risk_gate_factory=lambda: FixedRiskGate(),
        config=BacktestConfig(initial_equity=Decimal("10000"), execution_bar=CandleBar.ONE_DAY),
        train_days=4,
        test_days=2,
        step_days=2,
        param_grid={"enabled": [True, False]},
        objective="total_return",
        min_trades=1,
    )

    assert walk_forward.windows
    assert all(window.best_params["enabled"] is True for window in walk_forward.windows)
    assert walk_forward.oos_equity_curve
    timestamps = [point.ts_ms for point in walk_forward.oos_equity_curve]
    assert timestamps == sorted(timestamps)
    assert len(timestamps) == len(set(timestamps))
    assert walk_forward.oos_metrics.trade_count > 0
    html = render_walk_forward_report(walk_forward, generated_at_ts_ms=0)
    assert "Walk-Forward" in html
    assert "样本外" in html


def test_comparison_report_shows_each_strategy_and_baseline_deltas() -> None:
    data = build_long_trade_data()

    def run(name: str, stop: str) -> BacktestResult:
        return run_backtest(
            data=single_stream(data),
            instruments={INST: make_instrument()},
            strategy=ScriptedStrategy(
                lambda ctx: (
                    [
                        make_signal(
                            created_ts_ms=ctx.close_ts_ms,
                            stop_loss=stop,
                            take_profits=(("110", "1"),),
                            strategy_name=name,
                        )
                    ]
                    if ctx.close_ts_ms == HOUR_MS
                    else []
                ),
                name=name,
            ),
            risk_gate=FixedRiskGate(),
            config=BacktestConfig(initial_equity=Decimal("10000")),
        )

    sma = run("SMA200", "95")
    ema = run("EMA200", "90")
    comparison = compare_results({"SMA200": sma, "EMA200": ema}, baseline="SMA200")

    assert comparison.rows[0].name == "SMA200"
    assert comparison.deltas["EMA200"]["total_return"] == pytest.approx(
        ema.metrics.total_return - sma.metrics.total_return
    )
    html = render_comparison_report(comparison, generated_at_ts_ms=0)
    assert "SMA200" in html
    assert "EMA200" in html
    assert "对比" in html
    assert "<svg" in html


def test_run_is_reproducible_and_record_has_reproducibility_metadata() -> None:
    data = single_stream(build_long_trade_data())

    def run_once() -> BacktestResult:
        return run_backtest(
            data=data,
            instruments={INST: make_instrument()},
            strategy=ScriptedStrategy(
                lambda ctx: (
                    [make_signal(created_ts_ms=ctx.close_ts_ms)]
                    if ctx.close_ts_ms == HOUR_MS
                    else []
                )
            ),
            risk_gate=FixedRiskGate(),
            config=BacktestConfig(initial_equity=Decimal("10000")),
            strategy_params={"volume_ratio": 0.8},
        )

    first = run_once()
    second = run_once()
    assert first == second
    assert first.metadata.run_id.startswith("bt-")
    assert first.metadata.config_hash == second.metadata.config_hash

    record = first.as_record()
    for key in (
        "run_id",
        "strategy",
        "params",
        "period",
        "metrics_json",
        "report_path",
        "code_commit",
        "config_hash",
        "data_snapshot_id",
        "data_source",
        "fee_model_version",
        "slippage_model_version",
        "engine_version",
        "random_seed",
    ):
        assert key in record


def test_cost_helpers_round_adverse_to_tick() -> None:
    assert apply_slippage(Decimal("100"), Decimal("0.0005"), is_buy=True) == Decimal("100.05")
    assert apply_slippage(Decimal("100"), Decimal("0.0005"), is_buy=False) == Decimal("99.95")
    assert round_to_tick(Decimal("100.05001"), Decimal("0.01"), round_up=True) == Decimal("100.06")
    assert round_to_tick(Decimal("100.05999"), Decimal("0.01"), round_up=False) == Decimal("100.05")

    with pytest.raises(ValueError, match="tick_sz"):
        round_to_tick(Decimal("100"), Decimal("0"), round_up=True)


def test_engine_rejects_reuse_of_stateful_instance() -> None:
    from dquant.backtest import BacktestEngine

    engine = BacktestEngine(
        data=single_stream(build_long_trade_data()),
        instruments={INST: make_instrument()},
        strategy=ScriptedStrategy(lambda ctx: []),
        risk_gate=FixedRiskGate(),
        config=BacktestConfig(initial_equity=Decimal("10000")),
    )
    engine.run()
    with pytest.raises(RuntimeError, match="只能运行一次"):
        engine.run()


def test_trade_exit_dataclass_is_exposed_and_typed() -> None:
    exit_leg = TradeExit(
        ts_ms=1,
        price=Decimal("100"),
        qty=Decimal("1"),
        fee=Decimal("0.01"),
        reason=ExitReason.SIGNAL_CLOSE,
        gross_pnl=Decimal("1"),
        net_pnl=Decimal("0.99"),
        slippage_cost=Decimal("0"),
    )
    assert exit_leg.reason.value == "SIGNAL_CLOSE"
    assert not hasattr(exit_leg, "unexpected")


def test_empty_market_data_is_rejected() -> None:
    with pytest.raises(BacktestDataError, match="为空"):
        run_backtest(
            data={},
            instruments={},
            strategy=ScriptedStrategy(lambda ctx: []),
            risk_gate=FixedRiskGate(),
            config=BacktestConfig(initial_equity=Decimal("10000")),
        )


def test_strategy_and_risk_gate_failures_are_wrapped() -> None:
    class RaisingStrategy:
        name = "raising"
        version = "1.0.0"

        def on_bar(self, context: BacktestBarContext) -> list[Signal]:
            raise RuntimeError("strategy boom")

        def on_fill(self, context: BacktestBarContext, fill: Fill) -> None:
            return None

    with pytest.raises(BacktestExecutionError, match="on_bar"):
        run_backtest(
            data=single_stream(build_long_trade_data()),
            instruments={INST: make_instrument()},
            strategy=RaisingStrategy(),
            risk_gate=FixedRiskGate(),
            config=BacktestConfig(initial_equity=Decimal("10000")),
        )

    class RaisingGate:
        def evaluate(self, request: RiskRequest) -> RiskDecision:
            raise RuntimeError("risk service down")

    with pytest.raises(BacktestExecutionError, match="风控"):
        run_backtest(
            data=single_stream(build_long_trade_data()),
            instruments={INST: make_instrument()},
            strategy=ScriptedStrategy(open_once),
            risk_gate=RaisingGate(),
            config=BacktestConfig(initial_equity=Decimal("10000")),
        )

    class WrongSignalGate:
        def evaluate(self, request: RiskRequest) -> RiskDecision:
            return RiskDecision(
                signal_id="another-signal",
                approved=True,
                rule="TEST",
                detail="signal id 不匹配",
                qty=Decimal("1"),
                risk_fraction=Decimal("0.01"),
            )

    with pytest.raises(BacktestExecutionError, match="signal_id"):
        run_backtest(
            data=single_stream(build_long_trade_data()),
            instruments={INST: make_instrument()},
            strategy=ScriptedStrategy(open_once),
            risk_gate=WrongSignalGate(),
            config=BacktestConfig(initial_equity=Decimal("10000")),
        )


def test_close_signal_fills_next_open() -> None:
    def handler(ctx: BacktestBarContext) -> list[Signal]:
        if ctx.close_ts_ms == HOUR_MS:
            return [make_signal(created_ts_ms=ctx.close_ts_ms, take_profits=())]
        if ctx.close_ts_ms == 2 * HOUR_MS and ctx.position() is not None:
            return [
                make_signal(
                    created_ts_ms=ctx.close_ts_ms,
                    action=SignalAction.CLOSE,
                    price="109",
                    stop_loss=None,
                    take_profits=(),
                    signal_id="close-1",
                )
            ]
        return []

    result = run_backtest(
        data=single_stream(build_long_trade_data()),
        instruments={INST: make_instrument()},
        strategy=ScriptedStrategy(handler),
        risk_gate=FixedRiskGate(),
        config=BacktestConfig(initial_equity=Decimal("10000")),
    )

    trade = result.trades[0]
    assert trade.exits[0].reason is ExitReason.SIGNAL_CLOSE
    assert trade.average_exit_price == Decimal("108.9455")
    assert trade.exit_ts_ms == 2 * HOUR_MS
    assert trade.holding_bars == 2


def test_short_position_receives_positive_funding() -> None:
    data = [make_candle(index * HOUR_MS, "100", "101", "99", "100") for index in range(8)]

    def handler(ctx: BacktestBarContext) -> list[Signal]:
        if ctx.close_ts_ms == HOUR_MS:
            return [
                make_signal(
                    created_ts_ms=ctx.close_ts_ms,
                    side=SignalSide.SHORT,
                    stop_loss="105",
                    take_profits=(),
                )
            ]
        return []

    result = run_backtest(
        data=single_stream(data),
        instruments={INST: make_instrument()},
        strategy=ScriptedStrategy(handler),
        risk_gate=FixedRiskGate(),
        config=BacktestConfig(initial_equity=Decimal("10000")),
        funding_rates={INST: [FundingRatePoint(ts_ms=8 * HOUR_MS, rate=Decimal("0.001"))]},
    )

    assert result.trades[0].funding == Decimal("-0.1")
    assert result.metrics.total_funding == Decimal("-0.1")


def test_open_position_at_end_is_reported_when_close_disabled() -> None:
    result = run_backtest(
        data=single_stream(build_long_trade_data()),
        instruments={INST: make_instrument()},
        strategy=ScriptedStrategy(
            lambda ctx: (
                [make_signal(created_ts_ms=ctx.close_ts_ms, take_profits=())]
                if ctx.close_ts_ms == HOUR_MS
                else []
            )
        ),
        risk_gate=FixedRiskGate(),
        config=BacktestConfig(initial_equity=Decimal("10000"), close_positions_at_end=False),
    )

    assert result.trades == ()
    assert any("未平仓" in warning for warning in result.warnings)


def test_comparison_rejects_unknown_baseline() -> None:
    result = run_backtest(
        data=single_stream(build_long_trade_data()),
        instruments={INST: make_instrument()},
        strategy=ScriptedStrategy(open_once),
        risk_gate=FixedRiskGate(),
        config=BacktestConfig(initial_equity=Decimal("10000")),
    )
    with pytest.raises(ValueError, match="基准"):
        compare_results({"only": result}, baseline="missing")


def test_feature_provider_supplies_regime_from_closed_history() -> None:
    seen_lengths: list[int] = []
    captured: list[RegimeResult | None] = []

    def provider(request: FeatureRequest) -> StrategyFeatures:
        closed = request.history.candles(request.inst_id, request.bar)
        seen_lengths.append(len(closed))
        return StrategyFeatures(
            regime=RegimeResult(
                inst_id=request.inst_id,
                bar=request.bar,
                ts_ms=request.close_ts_ms,
                state=RegimeState.UPTREND,
                ma_type="SMA",
                dist=0.01,
                slope=0.05,
                above_ratio=0.8,
                cross_count=1,
                bars_in_state=10,
            )
        )

    def handler(ctx: BacktestBarContext) -> list[Signal]:
        captured.append(ctx.features.regime)
        return []

    result = run_backtest(
        data=single_stream(build_long_trade_data()),
        instruments={INST: make_instrument()},
        strategy=ScriptedStrategy(handler),
        risk_gate=FixedRiskGate(),
        config=BacktestConfig(initial_equity=Decimal("10000")),
        feature_provider=provider,
    )

    assert seen_lengths == [1, 2, 3]
    assert len(captured) == 3
    assert all(item is not None for item in captured)
    assert all(item.state is RegimeState.UPTREND for item in captured if item is not None)
    assert result.metrics.trade_count == 0


def test_feature_provider_must_return_strategy_features() -> None:
    def bad_provider(request: FeatureRequest) -> StrategyFeatures:
        del request
        return None  # type: ignore[return-value]

    with pytest.raises(BacktestExecutionError, match="StrategyFeatures"):
        run_backtest(
            data=single_stream(build_long_trade_data()),
            instruments={INST: make_instrument()},
            strategy=ScriptedStrategy(lambda ctx: []),
            risk_gate=FixedRiskGate(),
            config=BacktestConfig(initial_equity=Decimal("10000")),
            feature_provider=bad_provider,
        )


def test_risk_request_includes_day_anchor_and_latest_funding_rate() -> None:
    gate = FixedRiskGate()
    run_backtest(
        data=single_stream(build_long_trade_data()),
        instruments={INST: make_instrument()},
        strategy=ScriptedStrategy(open_once),
        risk_gate=gate,
        config=BacktestConfig(initial_equity=Decimal("10000")),
        funding_rates={INST: [FundingRatePoint(ts_ms=0, rate=Decimal("0.0001"))]},
    )

    request = gate.requests[0]
    assert request.day_start_equity == Decimal("10000")
    assert request.week_start_equity == Decimal("10000")
    assert request.funding_rate == Decimal("0.0001")
