"""参数扫描与热力图数据。"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from dquant.contracts import Candle, CandleBar, Instrument

from .config import BacktestConfig
from .engine import run_backtest
from .interfaces import FeatureRequest, StrategyFeatures
from .models import BacktestResult, FundingRatePoint
from .optimization import (
    ObjectiveOptimizer,
    OptimizationCandidate,
    canonical_params,
    expand_parameter_grid,
)


@dataclass(frozen=True)
class SweepEntry:
    """单个参数组合的完整回测结果。"""

    params: dict[str, Any]
    result: BacktestResult


@dataclass(frozen=True)
class SweepResult:
    """参数扫描结果。"""

    entries: tuple[SweepEntry, ...]
    best: SweepEntry | None
    objective: str
    min_trades: int
    warnings: tuple[str, ...]


def run_parameter_sweep(
    *,
    data: Mapping[str, Mapping[CandleBar, Sequence[Candle]]],
    instruments: Mapping[str, Instrument],
    strategy_factory: Callable[[Mapping[str, Any]], Any],
    risk_gate_factory: Callable[[], Any],
    param_grid: Mapping[str, Sequence[Any]],
    config: BacktestConfig | None = None,
    objective: str = "sharpe",
    min_trades: int = 1,
    funding_rates: Mapping[str, Sequence[FundingRatePoint]] | None = None,
    feature_provider: Callable[[FeatureRequest], StrategyFeatures] | None = None,
    data_source: str | None = None,
    code_commit: str | None = None,
) -> SweepResult:
    """对参数网格逐一回测，并返回最优组合与完整结果。"""

    base_config = config or BacktestConfig()
    combinations = expand_parameter_grid(param_grid)
    optimizer = ObjectiveOptimizer(objective=objective, min_trades=min_trades)
    entries: list[SweepEntry] = []
    for params in combinations:
        strategy = strategy_factory(dict(params))
        risk_gate = risk_gate_factory()
        result = run_backtest(
            data=data,
            instruments=instruments,
            strategy=strategy,
            risk_gate=risk_gate,
            config=base_config,
            funding_rates=funding_rates,
            strategy_params=params,
            feature_provider=feature_provider,
            data_source=data_source,
            code_commit=code_commit,
        )
        entries.append(SweepEntry(params=dict(params), result=result))

    selection = optimizer.select(
        [
            OptimizationCandidate(params=entry.params, metrics=entry.result.metrics)
            for entry in entries
        ]
    )
    target = canonical_params(selection.candidate.params)
    best = next(entry for entry in entries if canonical_params(entry.params) == target)
    return SweepResult(
        entries=tuple(entries),
        best=best,
        objective=objective,
        min_trades=min_trades,
        warnings=selection.warnings,
    )
