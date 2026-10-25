"""M06 回测集成测试。

重点验证：回测与策略共用同一 Signal 链路、信号确认时点不会前视、多个策略分别报告、
风控接口收到公共 Position/Signal 模型，以及从回测到 HTML 报告的端到端一致性。
"""

from __future__ import annotations

from collections.abc import Sequence
from decimal import Decimal
from pathlib import Path

import pytest

from dquant.backtest import (
    BacktestBarContext,
    BacktestConfig,
    ExitReason,
    RiskRequest,
    compare_results,
    render_comparison_report,
    run_backtest,
    write_report,
)
from dquant.contracts import (
    Candle,
    CandleBar,
    Fill,
    Instrument,
    Position,
    RiskDecision,
    Signal,
    SignalAction,
    SignalSide,
    TakeProfit,
)

INST_A = "BTC-USDT-SWAP"
INST_B = "ETH-USDT-SWAP"
HOUR_MS = 3_600_000


def instrument(inst_id: str) -> Instrument:
    return Instrument(
        inst_id=inst_id,
        base=inst_id.split("-")[0],
        quote="USDT",
        ct_val=Decimal("1"),
        lot_sz=Decimal("1"),
        min_sz=Decimal("1"),
        tick_sz=Decimal("0.0001"),
    )


def candle(
    inst_id: str,
    hour: int,
    open_: str,
    high: str,
    low: str,
    close: str,
) -> Candle:
    return Candle(
        inst_id=inst_id,
        bar=CandleBar.ONE_HOUR,
        ts_ms=hour * HOUR_MS,
        open=Decimal(open_),
        high=Decimal(high),
        low=Decimal(low),
        close=Decimal(close),
        volume=Decimal("10"),
        quote_volume=Decimal("1000"),
        confirmed=True,
    )


class StaticRiskGate:
    def __init__(self, qty: str = "1") -> None:
        self.qty = Decimal(qty)
        self.requests: list[RiskRequest] = []

    def evaluate(self, request: RiskRequest) -> RiskDecision:
        self.requests.append(request)
        return RiskDecision(
            signal_id=request.signal.signal_id,
            approved=True,
            rule="INTEGRATION_TEST",
            detail="集成测试放行",
            qty=self.qty,
            risk_fraction=Decimal("0.01"),
        )


class EntryStrategy:
    """在指定收盘时刻产生一次开仓信号，其余时间不动作。"""

    def __init__(
        self,
        *,
        name: str,
        entry_close_ts_ms: int,
        side: SignalSide = SignalSide.LONG,
        stop_pct: str = "0.05",
        take_profits: Sequence[tuple[str, str]] = (("110", "1"),),
    ) -> None:
        self.name = name
        self.version = "1.0.0"
        self.entry_close_ts_ms = entry_close_ts_ms
        self.side = side
        self.stop_pct = Decimal(stop_pct)
        self.take_profits = take_profits
        self.signals: list[Signal] = []
        self.fills: list[Fill] = []
        self.contexts: list[BacktestBarContext] = []

    def on_bar(self, context: BacktestBarContext) -> list[Signal]:
        self.contexts.append(context)
        if context.close_ts_ms != self.entry_close_ts_ms:
            return []
        price = context.candle.close
        stop = (
            price * (Decimal("1") - self.stop_pct)
            if self.side is SignalSide.LONG
            else price * (Decimal("1") + self.stop_pct)
        )
        signal = Signal(
            signal_id=f"{self.name}-{context.inst_id}",
            inst_id=context.inst_id,
            side=self.side,
            action=SignalAction.OPEN,
            entry_type="market",
            price=price,
            stop_loss=stop,
            take_profits=[
                TakeProfit(price=Decimal(tp_price), fraction=Decimal(fraction))
                for tp_price, fraction in self.take_profits
            ],
            reason="集成测试信号",
            strategy_name=self.name,
            created_ts_ms=context.close_ts_ms,
        )
        self.signals.append(signal)
        return [signal]

    def on_fill(self, context: BacktestBarContext, fill: Fill) -> None:
        self.fills.append(fill)


def two_symbol_data() -> dict[str, dict[CandleBar, list[Candle]]]:
    return {
        INST_A: {
            CandleBar.ONE_HOUR: [
                candle(INST_A, 0, "100", "101", "99", "100"),
                candle(INST_A, 1, "100", "108", "99", "107"),
                candle(INST_A, 2, "107", "109", "106", "108"),
            ]
        },
        INST_B: {
            CandleBar.ONE_HOUR: [
                candle(INST_B, 0, "50", "51", "49", "50"),
                candle(INST_B, 1, "50", "54", "49", "53"),
                candle(INST_B, 2, "53", "55", "52", "54"),
            ]
        },
    }


def test_risk_interface_receives_public_contract_models() -> None:
    gate = StaticRiskGate()
    strategy = EntryStrategy(name="contract-check", entry_close_ts_ms=HOUR_MS)
    result = run_backtest(
        data=two_symbol_data(),
        instruments={INST_A: instrument(INST_A), INST_B: instrument(INST_B)},
        strategy=strategy,
        risk_gate=gate,
        config=BacktestConfig(initial_equity=Decimal("10000")),
    )

    assert result.metrics.signal_count == 2
    assert len(gate.requests) == 2
    first = gate.requests[0]
    assert isinstance(first.signal, Signal)
    assert first.signal.action is SignalAction.OPEN
    assert isinstance(first.positions, tuple)
    assert first.available_equity == first.equity - first.used_margin
    assert isinstance(first.pending_orders, tuple)
    assert isinstance(gate.requests[1].pending_orders[0].qty, Decimal)

    # 成交必须发生在信号确认 K 线之后的执行 K 线开盘。
    assert all(fill.ts_ms >= HOUR_MS for fill in strategy.fills)
    assert all(context.close_ts_ms >= HOUR_MS for context in strategy.contexts)


def test_same_strategy_class_produces_identical_signals_in_repeated_runs() -> None:
    data = two_symbol_data()
    instruments = {INST_A: instrument(INST_A), INST_B: instrument(INST_B)}
    first_strategy = EntryStrategy(name="shared-code", entry_close_ts_ms=HOUR_MS)
    second_strategy = EntryStrategy(name="shared-code", entry_close_ts_ms=HOUR_MS)

    first = run_backtest(
        data=data,
        instruments=instruments,
        strategy=first_strategy,
        risk_gate=StaticRiskGate(),
        config=BacktestConfig(initial_equity=Decimal("10000")),
    )
    second = run_backtest(
        data=data,
        instruments=instruments,
        strategy=second_strategy,
        risk_gate=StaticRiskGate(),
        config=BacktestConfig(initial_equity=Decimal("10000")),
    )

    assert first.trades == second.trades
    assert [signal.signal_id for signal in first_strategy.signals] == [
        signal.signal_id for signal in second_strategy.signals
    ]
    assert [fill.price for fill in first_strategy.fills] == [
        fill.price for fill in second_strategy.fills
    ]
    assert first.metadata.config_hash == second.metadata.config_hash


def test_multi_strategy_report_contains_separate_sections() -> None:
    data = {INST_A: {CandleBar.ONE_HOUR: two_symbol_data()[INST_A][CandleBar.ONE_HOUR]}}
    results = {}
    for index, name in enumerate(("S1", "S2", "S3")):
        results[name] = run_backtest(
            data=data,
            instruments={INST_A: instrument(INST_A)},
            strategy=EntryStrategy(
                name=name,
                entry_close_ts_ms=HOUR_MS,
                take_profits=(("110", "1"),) if index == 0 else (("120", "1"),),
            ),
            risk_gate=StaticRiskGate(),
            config=BacktestConfig(initial_equity=Decimal("10000")),
        )

    comparison = compare_results(results, baseline="S1")
    html = render_comparison_report(comparison, generated_at_ts_ms=0)

    for name in ("S1", "S2", "S3"):
        assert name in html
    assert "逐策略报告" in html
    assert len(comparison.rows) == 3
    assert all(row.trade_count == 1 for row in comparison.rows)
    assert html.count("data-strategy-row") == 3


def test_end_to_end_report_matches_hand_calculated_equity(tmp_path: Path) -> None:
    strategy = EntryStrategy(name="e2e", entry_close_ts_ms=HOUR_MS)
    result = run_backtest(
        data=two_symbol_data(),
        instruments={INST_A: instrument(INST_A), INST_B: instrument(INST_B)},
        strategy=strategy,
        risk_gate=StaticRiskGate(),
        config=BacktestConfig(initial_equity=Decimal("10000")),
    )

    entry_fill_a = Decimal("100.05")
    entry_fill_b = Decimal("50.025")
    exit_fill_a = Decimal("107.946")
    exit_fill_b = Decimal("53.973")
    expected_pnl_a = (
        (exit_fill_a - entry_fill_a)
        - entry_fill_a * Decimal("0.0005")
        - exit_fill_a * Decimal("0.0005")
    )
    expected_pnl_b = (
        (exit_fill_b - entry_fill_b)
        - entry_fill_b * Decimal("0.0005")
        - exit_fill_b * Decimal("0.0005")
    )
    assert [trade.net_pnl for trade in result.trades] == [expected_pnl_a, expected_pnl_b]
    assert result.metrics.final_equity == Decimal("10000") + expected_pnl_a + expected_pnl_b
    assert all(trade.exits[0].reason is ExitReason.END_OF_BACKTEST for trade in result.trades)

    path = write_report(result, tmp_path / "e2e.html", generated_at_ts_ms=0)
    html = path.read_text(encoding="utf-8")
    assert "e2e" in html
    assert "资金曲线" in html
    assert result.metadata.config_hash in html
    assert "END_OF_BACKTEST" in html


def test_report_lists_each_strategy_metric_row() -> None:
    data = {INST_A: {CandleBar.ONE_HOUR: two_symbol_data()[INST_A][CandleBar.ONE_HOUR]}}
    results = {
        name: run_backtest(
            data=data,
            instruments={INST_A: instrument(INST_A)},
            strategy=EntryStrategy(name=name, entry_close_ts_ms=HOUR_MS),
            risk_gate=StaticRiskGate(),
            config=BacktestConfig(initial_equity=Decimal("10000")),
        )
        for name in ("SMA200", "EMA200")
    }
    html = render_comparison_report(
        compare_results(results, baseline="SMA200"), generated_at_ts_ms=0
    )
    assert html.count("data-strategy-row") == 2
    assert "SMA200" in html
    assert "EMA200" in html


def test_old_contexts_cannot_see_later_candles() -> None:
    strategy = EntryStrategy(name="snapshot", entry_close_ts_ms=HOUR_MS)
    run_backtest(
        data={INST_A: {CandleBar.ONE_HOUR: two_symbol_data()[INST_A][CandleBar.ONE_HOUR]}},
        instruments={INST_A: instrument(INST_A)},
        strategy=strategy,
        risk_gate=StaticRiskGate(),
        config=BacktestConfig(initial_equity=Decimal("10000")),
    )

    lengths = [len(context.candles(limit=500)) for context in strategy.contexts]
    assert lengths == [1, 2, 3]
    assert strategy.contexts[0].history is not None


def test_report_handles_no_trade_result_without_crashing() -> None:
    class NoopStrategy:
        name = "noop"
        version = "1.0.0"

        def on_bar(self, context: BacktestBarContext) -> list[Signal]:
            return []

        def on_fill(self, context: BacktestBarContext, fill: Fill) -> None:
            raise AssertionError("不应产生成交")

    result = run_backtest(
        data={INST_A: {CandleBar.ONE_HOUR: two_symbol_data()[INST_A][CandleBar.ONE_HOUR]}},
        instruments={INST_A: instrument(INST_A)},
        strategy=NoopStrategy(),
        risk_gate=StaticRiskGate(),
        config=BacktestConfig(initial_equity=Decimal("10000")),
    )
    html = render_comparison_report(compare_results({"empty": result}), generated_at_ts_ms=0)
    assert "empty" in html
    assert "0" in html


def test_risk_request_positions_are_public_models() -> None:
    data = {INST_A: {CandleBar.ONE_HOUR: two_symbol_data()[INST_A][CandleBar.ONE_HOUR]}}
    gate = StaticRiskGate()

    class ReduceStrategy:
        name = "reduce"
        version = "1.0.0"

        def on_bar(self, context: BacktestBarContext) -> list[Signal]:
            if context.close_ts_ms == HOUR_MS:
                return [
                    Signal(
                        signal_id="open-1",
                        inst_id=context.inst_id,
                        side=SignalSide.LONG,
                        action=SignalAction.OPEN,
                        entry_type="market",
                        price=context.candle.close,
                        stop_loss=Decimal("95"),
                        take_profits=[TakeProfit(price=Decimal("120"), fraction=Decimal("1"))],
                        reason="开仓",
                        strategy_name=self.name,
                        created_ts_ms=context.close_ts_ms,
                    )
                ]
            if context.close_ts_ms == 2 * HOUR_MS and context.position() is not None:
                return [
                    Signal(
                        signal_id="reduce-1",
                        inst_id=context.inst_id,
                        side=SignalSide.LONG,
                        action=SignalAction.REDUCE,
                        entry_type="market",
                        price=context.candle.close,
                        stop_loss=None,
                        take_profits=[],
                        reason="减仓",
                        strategy_name=self.name,
                        created_ts_ms=context.close_ts_ms,
                    )
                ]
            return []

        def on_fill(self, context: BacktestBarContext, fill: Fill) -> None:
            return None

    result = run_backtest(
        data=data,
        instruments={INST_A: instrument(INST_A)},
        strategy=ReduceStrategy(),
        risk_gate=gate,
        config=BacktestConfig(initial_equity=Decimal("10000")),
    )

    assert len(gate.requests) == 2
    reduce_request = gate.requests[1]
    assert reduce_request.signal.action is SignalAction.REDUCE
    assert len(reduce_request.positions) == 1
    assert isinstance(reduce_request.positions[0], Position)
    assert reduce_request.positions[0].size == Decimal("1")
    assert isinstance(result.metrics.total_fees, Decimal)
    assert result.trades[0].exits[0].reason is ExitReason.SIGNAL_REDUCE
    assert result.metrics.total_return == pytest.approx(
        float(result.metrics.final_equity / Decimal("10000") - 1)
    )
