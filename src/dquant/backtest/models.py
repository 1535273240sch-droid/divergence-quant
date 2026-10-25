"""回测模块内部结果模型。

本文件不重新定义公共业务对象：信号、持仓、成交和风控结果全部来自
`dquant.contracts`，这里只描述回测过程与绩效报告专有的数据结构。
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from decimal import Decimal
from enum import StrEnum
from typing import Any

from dquant.contracts import (
    CandleBar,
    Position,
    Signal,
    SignalAction,
    SignalSide,
)

from .config import BacktestConfig


class ExitReason(StrEnum):
    """平仓原因。"""

    STOP_LOSS = "STOP_LOSS"
    TAKE_PROFIT = "TAKE_PROFIT"
    SIGNAL_CLOSE = "SIGNAL_CLOSE"
    SIGNAL_REDUCE = "SIGNAL_REDUCE"
    END_OF_BACKTEST = "END_OF_BACKTEST"
    LIQUIDATION = "LIQUIDATION"


class RejectionReason(StrEnum):
    """信号或挂单被拒绝的原因。"""

    RISK_VETO = "RISK_VETO"
    POSITION_CONFLICT = "POSITION_CONFLICT"
    NO_POSITION = "NO_POSITION"
    INVALID_QUANTITY = "INVALID_QUANTITY"
    INSUFFICIENT_MARGIN = "INSUFFICIENT_MARGIN"
    INVALID_STOP_UPDATE = "INVALID_STOP_UPDATE"
    SIDE_MISMATCH = "SIDE_MISMATCH"
    OUTSIDE_TRADING_WINDOW = "OUTSIDE_TRADING_WINDOW"
    LIMIT_ORDER_EXPIRED = "LIMIT_ORDER_EXPIRED"
    UNFILLED_AT_END = "UNFILLED_AT_END"
    UNSUPPORTED_ACTION = "UNSUPPORTED_ACTION"


@dataclass(frozen=True)
class FundingRatePoint:
    """某个时刻适用的永续资金费率。"""

    ts_ms: int
    rate: Decimal


@dataclass(frozen=True)
class TradeExit:
    """一笔持仓的一条平仓腿。"""

    ts_ms: int
    price: Decimal
    qty: Decimal
    fee: Decimal
    reason: ExitReason
    gross_pnl: Decimal
    net_pnl: Decimal
    slippage_cost: Decimal


@dataclass(frozen=True)
class BacktestTrade:
    """一笔已完全平仓的交易汇总。"""

    trade_id: str
    signal_id: str
    cl_ord_id: str
    inst_id: str
    strategy_name: str
    side: SignalSide
    initial_qty: Decimal
    entry_price: Decimal
    entry_ts_ms: int
    exit_ts_ms: int
    average_exit_price: Decimal
    gross_pnl: Decimal
    fees: Decimal
    funding: Decimal
    slippage_cost: Decimal
    net_pnl: Decimal
    return_pct: float
    r_multiple: float | None
    holding_bars: int
    holding_ms: int
    exits: tuple[TradeExit, ...]


@dataclass(frozen=True)
class EquityPoint:
    """某个收盘时刻的账户权益快照。"""

    ts_ms: int
    equity: Decimal
    wallet_balance: Decimal
    unrealized_pnl: Decimal
    used_margin: Decimal


@dataclass(frozen=True)
class DrawdownPoint:
    """资金曲线对应的回撤点。"""

    ts_ms: int
    drawdown: float
    peak_equity: Decimal
    equity: Decimal


@dataclass(frozen=True)
class MonthlyReturn:
    """按用户时区聚合的月度收益。"""

    year: int
    month: int
    return_pct: float
    start_equity: Decimal
    end_equity: Decimal


@dataclass(frozen=True)
class RejectedSignal:
    """被回测引擎拒绝的信号记录。"""

    ts_ms: int
    inst_id: str
    signal_id: str
    action: SignalAction
    reason: RejectionReason
    detail: str
    strategy_name: str


@dataclass(frozen=True)
class StreamQuality:
    """单个行情流的数据质量摘要。"""

    inst_id: str
    bar: CandleBar
    candle_count: int
    first_ts_ms: int
    last_ts_ms: int
    missing_bars: int


@dataclass(frozen=True)
class PendingOrderInfo:
    """传给风控的待成交订单摘要。"""

    inst_id: str
    side: SignalSide
    action: SignalAction
    qty: Decimal
    created_ts_ms: int


@dataclass(frozen=True)
class RiskRequest:
    """回测调用 M08 风控时的完整上下文。"""

    signal: Signal
    equity: Decimal
    wallet_balance: Decimal
    used_margin: Decimal
    available_equity: Decimal
    positions: tuple[Position, ...]
    pending_orders: tuple[PendingOrderInfo, ...]
    day_start_equity: Decimal
    week_start_equity: Decimal
    funding_rate: Decimal | None
    ts_ms: int


@dataclass(frozen=True)
class BacktestMetrics:
    """绩效指标。比例使用 float，金额使用 Decimal。"""

    initial_equity: Decimal
    final_equity: Decimal
    total_return: float
    annualized_return: float
    max_drawdown: float
    max_drawdown_amount: Decimal
    max_drawdown_peak_ts_ms: int | None
    max_drawdown_trough_ts_ms: int | None
    sharpe: float
    sortino: float
    calmar: float
    win_rate: float
    profit_factor: float | None
    trade_count: int
    winning_trades: int
    losing_trades: int
    gross_profit: Decimal
    gross_loss: Decimal
    average_holding_ms: int
    average_holding_bars: float
    total_fees: Decimal
    total_funding: Decimal
    total_slippage_cost: Decimal
    signal_count: int
    rejected_signal_count: int
    start_ts_ms: int | None
    end_ts_ms: int | None
    notes: tuple[str, ...]


@dataclass(frozen=True)
class BacktestMetadata:
    """可复现回测所需的元数据。"""

    run_id: str
    strategy_name: str
    strategy_version: str
    strategy_params: dict[str, Any]
    engine_version: str
    fee_model_version: str
    slippage_model_version: str
    funding_model_version: str
    config_hash: str
    data_fingerprint: str
    data_source: str | None
    code_commit: str | None
    random_seed: int | None
    execution_bar: CandleBar
    initial_equity: Decimal
    period_start_ts_ms: int | None
    period_end_ts_ms: int | None
    instruments: tuple[str, ...]


@dataclass(frozen=True)
class BacktestResult:
    """一次回测的完整结果。"""

    config: BacktestConfig
    metadata: BacktestMetadata
    metrics: BacktestMetrics
    equity_curve: tuple[EquityPoint, ...]
    drawdown_curve: tuple[DrawdownPoint, ...]
    trades: tuple[BacktestTrade, ...]
    rejected_signals: tuple[RejectedSignal, ...]
    monthly_returns: tuple[MonthlyReturn, ...]
    stream_quality: tuple[StreamQuality, ...]
    warnings: tuple[str, ...]

    def as_record(self) -> dict[str, Any]:
        """转换为 `backtest_runs` 表可直接落库的字段。"""

        start = self.metadata.period_start_ts_ms
        end = self.metadata.period_end_ts_ms
        period = f"{start}-{end}" if start is not None and end is not None else "unknown"
        return {
            "run_id": self.metadata.run_id,
            "strategy": self.metadata.strategy_name,
            "params": _json_safe(dict(self.metadata.strategy_params)),
            "period": period,
            "metrics_json": _json_safe(asdict(self.metrics)),
            "report_path": None,
            "code_commit": self.metadata.code_commit,
            "config_hash": self.metadata.config_hash,
            "data_snapshot_id": self.metadata.data_fingerprint,
            "data_source": self.metadata.data_source,
            "fee_model_version": self.metadata.fee_model_version,
            "slippage_model_version": self.metadata.slippage_model_version,
            "engine_version": self.metadata.engine_version,
            "random_seed": self.metadata.random_seed,
        }


def _json_safe(value: Any) -> Any:
    """把 dataclass 结果转换为 JSON 可序列化结构。"""

    if isinstance(value, Decimal):
        return format(value, "f")
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    if isinstance(value, StrEnum):
        return value.value
    return value


__all__ = [
    "BacktestMetadata",
    "BacktestMetrics",
    "BacktestResult",
    "BacktestTrade",
    "DrawdownPoint",
    "EquityPoint",
    "ExitReason",
    "FundingRatePoint",
    "MonthlyReturn",
    "PendingOrderInfo",
    "RejectedSignal",
    "RejectionReason",
    "RiskRequest",
    "StreamQuality",
    "TradeExit",
]
