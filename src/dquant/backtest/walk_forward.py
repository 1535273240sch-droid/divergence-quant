"""Walk-Forward 样本外验证。

训练窗口内做参数选择，测试窗口只使用训练窗口选出的参数，且不做任何再优化。
多个测试窗口的资金曲线按窗口开始资金缩放后拼接，避免把不同初始资金的权益直接相加。
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from decimal import Decimal
from typing import Any

from dquant.contracts import Candle, CandleBar, Instrument

from .config import MILLISECONDS_PER_DAY, BacktestConfig
from .data import execution_time_range, normalize_market_data
from .engine import run_backtest
from .interfaces import FeatureRequest, StrategyFeatures
from .metrics import compute_metrics, compute_monthly_returns
from .models import (
    BacktestMetrics,
    BacktestResult,
    BacktestTrade,
    EquityPoint,
    FundingRatePoint,
    MonthlyReturn,
    TradeExit,
)
from .optimization import (
    ObjectiveOptimizer,
    OptimizationCandidate,
    expand_parameter_grid,
)


@dataclass(frozen=True)
class WalkForwardWindowSpec:
    """一个 Walk-Forward 窗口的时间边界（收盘时间，毫秒 UTC）。"""

    index: int
    train_start_ts_ms: int
    train_end_ts_ms: int
    test_start_ts_ms: int
    test_end_ts_ms: int


@dataclass(frozen=True)
class WalkForwardWindow:
    """单个窗口的训练选参与样本外结果。"""

    spec: WalkForwardWindowSpec
    best_params: dict[str, Any]
    train_result: BacktestResult
    test_result: BacktestResult
    warnings: tuple[str, ...]


@dataclass(frozen=True)
class WalkForwardResult:
    """Walk-Forward 汇总结果。"""

    config: BacktestConfig
    objective: str
    min_trades: int
    windows: tuple[WalkForwardWindow, ...]
    oos_equity_curve: tuple[EquityPoint, ...]
    oos_metrics: BacktestMetrics
    oos_trades: tuple[BacktestTrade, ...]
    oos_monthly_returns: tuple[MonthlyReturn, ...]
    warnings: tuple[str, ...]


def build_walk_forward_windows(
    *,
    start_ts_ms: int,
    end_ts_ms: int,
    train_days: int,
    test_days: int,
    step_days: int | None = None,
) -> tuple[WalkForwardWindowSpec, ...]:
    """生成滚动窗口；步长必须不小于测试窗口以避免样本外重叠。"""

    if train_days <= 0 or test_days <= 0:
        raise ValueError("train_days 和 test_days 必须大于 0")
    if start_ts_ms >= end_ts_ms:
        raise ValueError("数据区间不足以生成 Walk-Forward 窗口")
    step = test_days if step_days is None else step_days
    if step <= 0:
        raise ValueError("step_days 必须大于 0")
    if step < test_days:
        raise ValueError("step_days 不能小于 test_days，否则样本外窗口会重叠")

    specs: list[WalkForwardWindowSpec] = []
    train_start = start_ts_ms
    index = 0
    while True:
        train_end = train_start + train_days * MILLISECONDS_PER_DAY
        test_start = train_end
        test_end = test_start + test_days * MILLISECONDS_PER_DAY
        if test_end > end_ts_ms:
            break
        specs.append(
            WalkForwardWindowSpec(
                index=index,
                train_start_ts_ms=train_start,
                train_end_ts_ms=train_end,
                test_start_ts_ms=test_start,
                test_end_ts_ms=test_end,
            )
        )
        index += 1
        train_start += step * MILLISECONDS_PER_DAY
    if not specs:
        raise ValueError("数据区间不足以生成任何 Walk-Forward 窗口")
    return tuple(specs)


def run_walk_forward(
    *,
    data: Mapping[str, Mapping[CandleBar, Sequence[Candle]]],
    instruments: Mapping[str, Instrument],
    strategy_factory: Callable[[Mapping[str, Any]], Any],
    risk_gate_factory: Callable[[], Any],
    config: BacktestConfig | None = None,
    train_days: int = 120,
    test_days: int = 60,
    step_days: int | None = None,
    param_grid: Mapping[str, Sequence[Any]] | None = None,
    objective: str = "sharpe",
    min_trades: int = 1,
    funding_rates: Mapping[str, Sequence[FundingRatePoint]] | None = None,
    feature_provider: Callable[[FeatureRequest], StrategyFeatures] | None = None,
    data_source: str | None = None,
    code_commit: str | None = None,
) -> WalkForwardResult:
    """执行 Walk-Forward 并拼接样本外资金曲线。"""

    base_config = config or BacktestConfig()
    normalized = normalize_market_data(data)
    data_start, data_end = execution_time_range(normalized, base_config.execution_bar)
    range_start = (
        max(data_start, base_config.start_ts_ms)
        if base_config.start_ts_ms is not None
        else data_start
    )
    range_end = (
        min(data_end, base_config.end_ts_ms) if base_config.end_ts_ms is not None else data_end
    )
    specs = build_walk_forward_windows(
        start_ts_ms=range_start,
        end_ts_ms=range_end,
        train_days=train_days,
        test_days=test_days,
        step_days=step_days,
    )
    combinations = expand_parameter_grid(param_grid or {})
    optimizer = ObjectiveOptimizer(objective=objective, min_trades=min_trades)
    windows: list[WalkForwardWindow] = []

    for spec in specs:
        train_candidates: list[tuple[dict[str, Any], BacktestResult]] = []
        for params in combinations:
            train_result = run_backtest(
                data=normalized,
                instruments=instruments,
                strategy=strategy_factory(dict(params)),
                risk_gate=risk_gate_factory(),
                config=base_config.with_window(spec.train_start_ts_ms, spec.train_end_ts_ms),
                funding_rates=funding_rates,
                strategy_params=params,
                feature_provider=feature_provider,
                data_source=data_source,
                code_commit=code_commit,
            )
            train_candidates.append((dict(params), train_result))

        selection = optimizer.select(
            [
                OptimizationCandidate(params=params, metrics=result.metrics)
                for params, result in train_candidates
            ]
        )
        best_params = dict(selection.candidate.params)
        train_result = next(result for params, result in train_candidates if params == best_params)
        test_result = run_backtest(
            data=normalized,
            instruments=instruments,
            strategy=strategy_factory(dict(best_params)),
            risk_gate=risk_gate_factory(),
            config=base_config.with_window(spec.test_start_ts_ms, spec.test_end_ts_ms),
            funding_rates=funding_rates,
            strategy_params=best_params,
            feature_provider=feature_provider,
            data_source=data_source,
            code_commit=code_commit,
        )
        windows.append(
            WalkForwardWindow(
                spec=spec,
                best_params=best_params,
                train_result=train_result,
                test_result=test_result,
                warnings=selection.warnings,
            )
        )

    oos_equity, oos_trades, stitch_warnings = _stitch_oos(windows, base_config.initial_equity)
    oos_metrics = compute_metrics(
        equity_curve=oos_equity,
        trades=oos_trades,
        execution_bar=base_config.execution_bar,
        initial_equity=base_config.initial_equity,
        signal_count=sum(window.test_result.metrics.signal_count for window in windows),
        rejected_signal_count=sum(
            window.test_result.metrics.rejected_signal_count for window in windows
        ),
    )
    warnings: list[str] = []
    for window in windows:
        warnings.extend(window.warnings)
    warnings.extend(stitch_warnings)
    return WalkForwardResult(
        config=base_config,
        objective=objective,
        min_trades=min_trades,
        windows=tuple(windows),
        oos_equity_curve=oos_equity,
        oos_metrics=oos_metrics,
        oos_trades=oos_trades,
        oos_monthly_returns=compute_monthly_returns(
            oos_equity,
            base_config.initial_equity,
            base_config.timezone,
        ),
        warnings=tuple(_deduplicate(warnings)),
    )


def _stitch_oos(
    windows: Sequence[WalkForwardWindow],
    initial_equity: Decimal,
) -> tuple[tuple[EquityPoint, ...], tuple[BacktestTrade, ...], list[str]]:
    stitched: list[EquityPoint] = []
    scaled_trades: list[BacktestTrade] = []
    warnings: list[str] = []
    capital = initial_equity
    for window in windows:
        result = window.test_result
        if not result.equity_curve:
            warnings.append(f"窗口 {window.spec.index} 没有样本外权益曲线，已跳过拼接")
            continue
        factor = capital / result.metadata.initial_equity
        added = 0
        for point in result.equity_curve:
            if stitched and point.ts_ms <= stitched[-1].ts_ms:
                continue
            stitched.append(
                EquityPoint(
                    ts_ms=point.ts_ms,
                    equity=point.equity * factor,
                    wallet_balance=point.wallet_balance * factor,
                    unrealized_pnl=point.unrealized_pnl * factor,
                    used_margin=point.used_margin * factor,
                )
            )
            added += 1
        if not added:
            warnings.append(f"窗口 {window.spec.index} 的样本外权益与上一窗口重叠，已跳过")
            continue
        capital = stitched[-1].equity
        for trade in result.trades:
            scaled_trades.append(_scale_trade(trade, factor, window.spec.index))
    return tuple(stitched), tuple(scaled_trades), warnings


def _scale_trade(trade: BacktestTrade, factor: Decimal, window_index: int) -> BacktestTrade:
    exits: tuple[TradeExit, ...] = tuple(
        replace(
            leg,
            fee=leg.fee * factor,
            gross_pnl=leg.gross_pnl * factor,
            net_pnl=leg.net_pnl * factor,
            slippage_cost=leg.slippage_cost * factor,
        )
        for leg in trade.exits
    )
    return replace(
        trade,
        trade_id=f"wf{window_index}-{trade.trade_id}",
        gross_pnl=trade.gross_pnl * factor,
        fees=trade.fees * factor,
        funding=trade.funding * factor,
        slippage_cost=trade.slippage_cost * factor,
        net_pnl=trade.net_pnl * factor,
        exits=exits,
    )


def _deduplicate(values: Sequence[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value not in seen:
            seen.add(value)
            result.append(value)
    return result
