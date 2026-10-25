"""参数网格展开与目标函数选择。"""

from __future__ import annotations

import json
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from .models import BacktestMetrics

OBJECTIVE_KEYS = (
    "total_return",
    "annualized_return",
    "sharpe",
    "sortino",
    "calmar",
    "win_rate",
    "profit_factor",
)


@dataclass(frozen=True)
class OptimizationCandidate:
    """一个待比较的参数组合。"""

    params: dict[str, Any]
    metrics: BacktestMetrics


@dataclass(frozen=True)
class OptimizationSelection:
    """目标函数选择结果。"""

    candidate: OptimizationCandidate
    warnings: tuple[str, ...]


@dataclass(frozen=True)
class ObjectiveOptimizer:
    """按指定指标选择最优参数；目标值越大越好。"""

    objective: str = "sharpe"
    min_trades: int = 1

    def __post_init__(self) -> None:
        if self.objective not in OBJECTIVE_KEYS:
            raise ValueError(
                f"不支持的目标函数 {self.objective}，可选: {', '.join(OBJECTIVE_KEYS)}"
            )
        if self.min_trades < 0:
            raise ValueError("min_trades 不能为负")

    def select(self, candidates: Sequence[OptimizationCandidate]) -> OptimizationSelection:
        if not candidates:
            raise ValueError("没有候选参数组合")
        eligible = [
            candidate
            for candidate in candidates
            if candidate.metrics.trade_count >= self.min_trades
        ]
        warnings: list[str] = []
        if not eligible:
            warnings.append(
                f"所有参数组合交易数均低于 min_trades={self.min_trades}，"
                f"改为直接按 {self.objective} 选择"
            )
            eligible = list(candidates)
        best = min(
            eligible,
            key=lambda candidate: (
                -objective_value(candidate.metrics, self.objective),
                canonical_params(candidate.params),
            ),
        )
        return OptimizationSelection(candidate=best, warnings=tuple(warnings))


def expand_parameter_grid(
    grid: Mapping[str, Sequence[Any]],
) -> list[dict[str, Any]]:
    """按参数名排序展开笛卡尔积，保证结果顺序确定。"""

    if not grid:
        return [{}]
    keys = sorted(grid)
    combinations: list[dict[str, Any]] = [{}]
    for key in keys:
        values = list(grid[key])
        if not values:
            raise ValueError(f"参数 {key} 的取值列表为空")
        expanded: list[dict[str, Any]] = []
        for base in combinations:
            for value in values:
                item = dict(base)
                item[key] = value
                expanded.append(item)
        combinations = expanded
    return [{key: combination[key] for key in keys} for combination in combinations]


def objective_value(metrics: BacktestMetrics, objective: str) -> float:
    """读取目标指标；无法定义时返回负无穷。"""

    if objective not in OBJECTIVE_KEYS:
        raise ValueError(f"不支持的目标函数: {objective}")
    value = getattr(metrics, objective)
    if value is None:
        return -math.inf
    numeric = float(value)
    if math.isnan(numeric):
        return -math.inf
    return numeric


def canonical_params(params: Mapping[str, Any]) -> str:
    """生成稳定的参数字符串，用于并列时的确定性排序。"""

    return json.dumps(dict(params), ensure_ascii=False, sort_keys=True, default=str)
