"""多策略与 SMA/EMA 变体的对比结果。

M06 不计算 MA200。M03 的 SMA/EMA 趋势分类由 M03 输出，M07 用不同
`primary_ma` 参数生成两个策略变体；本模块只负责把两次回测放在同一口径下对比。
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from .models import BacktestMetrics, BacktestResult, EquityPoint

COMPARISON_KEYS = (
    "total_return",
    "annualized_return",
    "max_drawdown",
    "sharpe",
    "sortino",
    "calmar",
    "win_rate",
    "profit_factor",
    "trade_count",
    "signal_count",
)


@dataclass(frozen=True)
class ComparisonRow:
    """单个策略的对比行。"""

    name: str
    metrics: BacktestMetrics
    signal_count: int
    rejected_signal_count: int
    trade_count: int
    equity_curve: tuple[EquityPoint, ...]


@dataclass(frozen=True)
class ComparisonResult:
    """多策略对比结果。"""

    title: str
    rows: tuple[ComparisonRow, ...]
    baseline: str | None
    deltas: Mapping[str, Mapping[str, float | None]]


def compare_results(
    results: Mapping[str, BacktestResult],
    *,
    baseline: str | None = None,
    title: str = "策略对比",
) -> ComparisonResult:
    """对比多个回测结果；可选指定基准变体计算差值。"""

    if not results:
        raise ValueError("至少需要一个回测结果才能对比")
    for name in results:
        if not isinstance(name, str) or not name.strip():
            raise ValueError("策略名称不能为空")
    if baseline is not None and baseline not in results:
        raise ValueError(f"基准策略 {baseline} 不在待对比结果中")

    rows = tuple(
        ComparisonRow(
            name=name,
            metrics=result.metrics,
            signal_count=result.metrics.signal_count,
            rejected_signal_count=result.metrics.rejected_signal_count,
            trade_count=result.metrics.trade_count,
            equity_curve=result.equity_curve,
        )
        for name, result in results.items()
    )
    deltas: dict[str, dict[str, float | None]] = {}
    if baseline is not None:
        base_metrics = results[baseline].metrics
        for name, result in results.items():
            deltas[name] = {
                key: _delta(result.metrics, base_metrics, key) for key in COMPARISON_KEYS
            }
    return ComparisonResult(
        title=title,
        rows=rows,
        baseline=baseline,
        deltas=deltas,
    )


def _delta(metrics: BacktestMetrics, baseline: BacktestMetrics, key: str) -> float | None:
    current = getattr(metrics, key)
    base = getattr(baseline, key)
    if current is None or base is None:
        return None
    return float(current) - float(base)
