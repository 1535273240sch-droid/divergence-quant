"""DivergenceQuant 回测引擎与绩效报告。

公共入口：

- `run_backtest`：单次事件驱动回测；
- `run_parameter_sweep`：参数扫描；
- `run_walk_forward`：训练/样本外滚动验证；
- `compare_results`：S1/S2/S3 或 SMA/EMA 变体对比；
- `render_report` / `render_comparison_report` / 报告渲染器。

回测模块只消费 `dquant.contracts` 的公共模型，策略和风控分别由 M07、M08 注入。
"""

from .comparison import (
    COMPARISON_KEYS,
    ComparisonResult,
    ComparisonRow,
    compare_results,
)
from .config import (
    ENGINE_VERSION,
    FEE_MODEL_VERSION,
    FUNDING_MODEL_VERSION,
    SLIPPAGE_MODEL_VERSION,
    BacktestConfig,
    bar_duration_ms,
    close_ts_ms,
    compute_config_hash,
    is_bar_aligned,
)
from .costs import FeeModel, SlippageModel, apply_slippage, is_tick_aligned, round_to_tick
from .engine import BacktestEngine, run_backtest
from .errors import (
    BacktestDataError,
    BacktestError,
    BacktestExecutionError,
    BacktestSignalError,
)
from .interfaces import (
    BacktestBarContext,
    FeatureProviderProtocol,
    FeatureRequest,
    RiskGateProtocol,
    StrategyFeatures,
    StrategyProtocol,
)
from .metrics import build_drawdown_curve, compute_metrics, compute_monthly_returns
from .models import (
    BacktestMetadata,
    BacktestMetrics,
    BacktestResult,
    BacktestTrade,
    DrawdownPoint,
    EquityPoint,
    ExitReason,
    FundingRatePoint,
    MonthlyReturn,
    PendingOrderInfo,
    RejectedSignal,
    RejectionReason,
    RiskRequest,
    StreamQuality,
    TradeExit,
)
from .optimization import (
    OBJECTIVE_KEYS,
    ObjectiveOptimizer,
    OptimizationCandidate,
    OptimizationSelection,
    canonical_params,
    expand_parameter_grid,
    objective_value,
)
from .report import (
    render_comparison_report,
    render_report,
    render_sweep_report,
    render_walk_forward_report,
    write_html,
    write_report,
)
from .sweep import SweepEntry, SweepResult, run_parameter_sweep
from .walk_forward import (
    WalkForwardResult,
    WalkForwardWindow,
    WalkForwardWindowSpec,
    build_walk_forward_windows,
    run_walk_forward,
)

__all__ = [
    "COMPARISON_KEYS",
    "ENGINE_VERSION",
    "FEE_MODEL_VERSION",
    "FUNDING_MODEL_VERSION",
    "OBJECTIVE_KEYS",
    "SLIPPAGE_MODEL_VERSION",
    "BacktestBarContext",
    "BacktestConfig",
    "BacktestDataError",
    "BacktestEngine",
    "BacktestError",
    "BacktestExecutionError",
    "BacktestMetadata",
    "BacktestMetrics",
    "BacktestResult",
    "BacktestSignalError",
    "BacktestTrade",
    "ComparisonResult",
    "ComparisonRow",
    "DrawdownPoint",
    "EquityPoint",
    "ExitReason",
    "FeeModel",
    "FeatureProviderProtocol",
    "FeatureRequest",
    "FundingRatePoint",
    "MonthlyReturn",
    "ObjectiveOptimizer",
    "OptimizationCandidate",
    "OptimizationSelection",
    "PendingOrderInfo",
    "RejectedSignal",
    "RejectionReason",
    "RiskGateProtocol",
    "RiskRequest",
    "SlippageModel",
    "StrategyFeatures",
    "StrategyProtocol",
    "StreamQuality",
    "SweepEntry",
    "SweepResult",
    "TradeExit",
    "WalkForwardResult",
    "WalkForwardWindow",
    "WalkForwardWindowSpec",
    "apply_slippage",
    "bar_duration_ms",
    "build_drawdown_curve",
    "build_walk_forward_windows",
    "canonical_params",
    "close_ts_ms",
    "compare_results",
    "compute_config_hash",
    "compute_metrics",
    "compute_monthly_returns",
    "expand_parameter_grid",
    "is_bar_aligned",
    "is_tick_aligned",
    "objective_value",
    "render_comparison_report",
    "render_report",
    "render_sweep_report",
    "render_walk_forward_report",
    "round_to_tick",
    "run_backtest",
    "run_parameter_sweep",
    "run_walk_forward",
    "write_html",
    "write_report",
]
