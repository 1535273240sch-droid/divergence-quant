"""绩效指标、回撤曲线和月度收益。

收益与风险比例使用 float，权益与盈亏金额保持 Decimal。
所有退化场景（空曲线、零波动、无亏损交易）都会在 `notes` 中明确说明，而不是返回伪造结果。
"""

from __future__ import annotations

import math
import statistics
from collections.abc import Sequence
from datetime import datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

from dquant.contracts import CandleBar

from .config import PERIODS_PER_YEAR
from .models import (
    BacktestMetrics,
    BacktestTrade,
    DrawdownPoint,
    EquityPoint,
    MonthlyReturn,
)


def compute_metrics(
    *,
    equity_curve: Sequence[EquityPoint],
    trades: Sequence[BacktestTrade],
    execution_bar: CandleBar,
    initial_equity: Decimal,
    signal_count: int = 0,
    rejected_signal_count: int = 0,
    risk_free_rate: float = 0.0,
) -> BacktestMetrics:
    """计算绩效指标。"""

    notes: list[str] = []
    if not equity_curve:
        notes.append("权益曲线为空，无法计算绩效指标")
        return _empty_metrics(
            initial_equity=initial_equity,
            signal_count=signal_count,
            rejected_signal_count=rejected_signal_count,
            notes=tuple(notes),
        )

    final_equity = equity_curve[-1].equity
    total_return = _ratio(final_equity, initial_equity) - 1.0
    start_ts_ms = equity_curve[0].ts_ms
    end_ts_ms = equity_curve[-1].ts_ms
    elapsed_ms = end_ts_ms - start_ts_ms
    annualized = _annualized_return(final_equity, initial_equity, elapsed_ms, notes)

    returns = _bar_returns(equity_curve)
    if len(returns) < 2:
        notes.append("有效收益样本不足 2 个，夏普与索提诺按 0 处理")
        sharpe = 0.0
        sortino = 0.0
    else:
        periods = PERIODS_PER_YEAR[execution_bar]
        sharpe = _sharpe(returns, periods, risk_free_rate, notes)
        sortino = _sortino(returns, periods, notes)

    max_dd, dd_amount, peak_ts, trough_ts = _max_drawdown(equity_curve)
    calmar = annualized / abs(max_dd) if max_dd > 0 else 0.0

    trade_stats = _trade_stats(trades, notes)
    if max_dd == 0:
        notes.append("最大回撤为 0，卡玛比率按 0 处理")

    return BacktestMetrics(
        initial_equity=initial_equity,
        final_equity=final_equity,
        total_return=total_return,
        annualized_return=annualized,
        max_drawdown=max_dd,
        max_drawdown_amount=dd_amount,
        max_drawdown_peak_ts_ms=peak_ts,
        max_drawdown_trough_ts_ms=trough_ts,
        sharpe=sharpe,
        sortino=sortino,
        calmar=calmar,
        win_rate=trade_stats["win_rate"],
        profit_factor=trade_stats["profit_factor"],
        trade_count=trade_stats["trade_count"],
        winning_trades=trade_stats["winning_trades"],
        losing_trades=trade_stats["losing_trades"],
        gross_profit=trade_stats["gross_profit"],
        gross_loss=trade_stats["gross_loss"],
        average_holding_ms=trade_stats["average_holding_ms"],
        average_holding_bars=trade_stats["average_holding_bars"],
        total_fees=trade_stats["total_fees"],
        total_funding=trade_stats["total_funding"],
        total_slippage_cost=trade_stats["total_slippage_cost"],
        signal_count=signal_count,
        rejected_signal_count=rejected_signal_count,
        start_ts_ms=start_ts_ms,
        end_ts_ms=end_ts_ms,
        notes=tuple(_deduplicate(notes)),
    )


def build_drawdown_curve(equity_curve: Sequence[EquityPoint]) -> tuple[DrawdownPoint, ...]:
    """根据资金曲线生成回撤曲线。"""

    if not equity_curve:
        return ()
    points: list[DrawdownPoint] = []
    peak = equity_curve[0].equity
    for point in equity_curve:
        if point.equity > peak:
            peak = point.equity
        drawdown = float((peak - point.equity) / peak) if peak > 0 else 1.0
        points.append(
            DrawdownPoint(
                ts_ms=point.ts_ms,
                drawdown=drawdown,
                peak_equity=peak,
                equity=point.equity,
            )
        )
    return tuple(points)


def compute_monthly_returns(
    equity_curve: Sequence[EquityPoint],
    initial_equity: Decimal,
    timezone: str = "Asia/Shanghai",
) -> tuple[MonthlyReturn, ...]:
    """按指定时区聚合月度收益。"""

    if not equity_curve:
        return ()
    tz = ZoneInfo(timezone)
    month_end: dict[tuple[int, int], Decimal] = {}
    order: list[tuple[int, int]] = []
    for point in equity_curve:
        moment = datetime.fromtimestamp(point.ts_ms / 1000, tz=tz)
        key = (moment.year, moment.month)
        if key not in month_end:
            order.append(key)
        month_end[key] = point.equity

    results: list[MonthlyReturn] = []
    previous = initial_equity
    for key in order:
        end_equity = month_end[key]
        return_pct = float(end_equity / previous - 1) if previous > 0 else 0.0
        results.append(
            MonthlyReturn(
                year=key[0],
                month=key[1],
                return_pct=return_pct,
                start_equity=previous,
                end_equity=end_equity,
            )
        )
        previous = end_equity
    return tuple(results)


def _empty_metrics(
    *,
    initial_equity: Decimal,
    signal_count: int,
    rejected_signal_count: int,
    notes: tuple[str, ...],
) -> BacktestMetrics:
    return BacktestMetrics(
        initial_equity=initial_equity,
        final_equity=initial_equity,
        total_return=0.0,
        annualized_return=0.0,
        max_drawdown=0.0,
        max_drawdown_amount=Decimal("0"),
        max_drawdown_peak_ts_ms=None,
        max_drawdown_trough_ts_ms=None,
        sharpe=0.0,
        sortino=0.0,
        calmar=0.0,
        win_rate=0.0,
        profit_factor=None,
        trade_count=0,
        winning_trades=0,
        losing_trades=0,
        gross_profit=Decimal("0"),
        gross_loss=Decimal("0"),
        average_holding_ms=0,
        average_holding_bars=0.0,
        total_fees=Decimal("0"),
        total_funding=Decimal("0"),
        total_slippage_cost=Decimal("0"),
        signal_count=signal_count,
        rejected_signal_count=rejected_signal_count,
        start_ts_ms=None,
        end_ts_ms=None,
        notes=notes,
    )


def _ratio(numerator: Decimal, denominator: Decimal) -> float:
    if denominator <= 0:
        raise ValueError("denominator must be positive")
    return float(numerator / denominator)


def _annualized_return(
    final_equity: Decimal,
    initial_equity: Decimal,
    elapsed_ms: int,
    notes: list[str],
) -> float:
    if final_equity <= 0:
        notes.append("期末权益小于等于 0，年化收益按 -100% 处理")
        return -1.0
    if elapsed_ms <= 0:
        notes.append("权益曲线时间跨度不足一个周期，年化收益按 0 处理")
        return 0.0
    years = elapsed_ms / (365 * 86_400_000)
    if years < 1 / 365:
        notes.append("回测区间不足一天，年化收益仅供参照")
    ratio = _ratio(final_equity, initial_equity)
    try:
        return ratio ** (1.0 / years) - 1.0
    except OverflowError:
        notes.append("年化收益数值溢出，已按无穷大记录")
        return math.inf


def _bar_returns(equity_curve: Sequence[EquityPoint]) -> list[float]:
    returns: list[float] = []
    for previous, current in zip(equity_curve, equity_curve[1:], strict=False):
        if previous.equity > 0:
            returns.append(float(current.equity / previous.equity) - 1.0)
    return returns


def _sharpe(
    returns: Sequence[float],
    periods_per_year: int,
    risk_free_rate: float,
    notes: list[str],
) -> float:
    mean = statistics.fmean(returns) - risk_free_rate / periods_per_year
    stdev = statistics.stdev(returns)
    if stdev == 0:
        notes.append("权益曲线无波动，夏普比率按 0 处理")
        return 0.0
    return mean / stdev * math.sqrt(periods_per_year)


def _sortino(returns: Sequence[float], periods_per_year: int, notes: list[str]) -> float:
    mean = statistics.fmean(returns)
    downside = math.sqrt(sum(min(value, 0.0) ** 2 for value in returns) / len(returns))
    if downside == 0:
        notes.append("没有下行波动，索提诺比率按 0 处理")
        return 0.0
    return mean / downside * math.sqrt(periods_per_year)


def _max_drawdown(
    equity_curve: Sequence[EquityPoint],
) -> tuple[float, Decimal, int | None, int | None]:
    peak = equity_curve[0].equity
    peak_ts: int | None = equity_curve[0].ts_ms
    max_dd = 0.0
    max_amount = Decimal("0")
    worst_peak_ts: int | None = None
    worst_trough_ts: int | None = None
    for point in equity_curve:
        if point.equity > peak:
            peak = point.equity
            peak_ts = point.ts_ms
        if peak <= 0:
            continue
        drawdown = float((peak - point.equity) / peak)
        if drawdown > max_dd:
            max_dd = drawdown
            max_amount = peak - point.equity
            worst_peak_ts = peak_ts
            worst_trough_ts = point.ts_ms
    return max_dd, max_amount, worst_peak_ts, worst_trough_ts


def _trade_stats(trades: Sequence[BacktestTrade], notes: list[str]) -> dict[str, object]:
    total = len(trades)
    winners = [trade for trade in trades if trade.net_pnl > 0]
    losers = [trade for trade in trades if trade.net_pnl < 0]
    gross_profit = sum((trade.net_pnl for trade in winners), Decimal("0"))
    gross_loss = sum((-trade.net_pnl for trade in losers), Decimal("0"))
    if gross_loss > 0:
        profit_factor: float | None = float(gross_profit / gross_loss)
    else:
        profit_factor = None
        if gross_profit > 0:
            notes.append("没有亏损交易，盈亏比无法定义，报告显示为 —")
        else:
            notes.append("没有已平仓交易，盈亏比无法定义")
    if total == 0:
        notes.append("没有已平仓交易，胜率按 0 处理")
    holding_ms = [trade.holding_ms for trade in trades]
    holding_bars = [trade.holding_bars for trade in trades]
    return {
        "win_rate": len(winners) / total if total else 0.0,
        "profit_factor": profit_factor,
        "trade_count": total,
        "winning_trades": len(winners),
        "losing_trades": len(losers),
        "gross_profit": gross_profit,
        "gross_loss": gross_loss,
        "average_holding_ms": int(statistics.fmean(holding_ms)) if holding_ms else 0,
        "average_holding_bars": statistics.fmean(holding_bars) if holding_bars else 0.0,
        "total_fees": sum((trade.fees for trade in trades), Decimal("0")),
        "total_funding": sum((trade.funding for trade in trades), Decimal("0")),
        "total_slippage_cost": sum((trade.slippage_cost for trade in trades), Decimal("0")),
    }


def _deduplicate(values: Sequence[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value not in seen:
            seen.add(value)
            result.append(value)
    return result
