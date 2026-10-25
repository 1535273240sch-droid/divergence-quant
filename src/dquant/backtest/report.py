"""自包含 HTML 回测报告。

报告不依赖 CDN、前端构建或外部图片：资金曲线、回撤曲线和热力图均以内联 SVG/HTML 生成，
可直接用浏览器打开，也便于在 M11/M12 中作为静态文件返回。
"""

from __future__ import annotations

import html
import json
import time
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from .comparison import COMPARISON_KEYS, ComparisonResult
from .models import (
    BacktestMetadata,
    BacktestMetrics,
    BacktestResult,
    BacktestTrade,
    EquityPoint,
    MonthlyReturn,
    RejectedSignal,
)
from .optimization import canonical_params, objective_value
from .sweep import SweepResult
from .walk_forward import WalkForwardResult

_COLORS = ("#4da3ff", "#2ecc71", "#f5a623", "#b07cff", "#ff6b6b", "#22d3ee")

_METRIC_LABELS: dict[str, str] = {
    "total_return": "总收益",
    "annualized_return": "年化收益",
    "max_drawdown": "最大回撤",
    "sharpe": "夏普",
    "sortino": "索提诺",
    "calmar": "卡玛",
    "win_rate": "胜率",
    "profit_factor": "盈亏比",
    "trade_count": "交易数",
    "signal_count": "信号数",
    "rejected_signal_count": "拒绝信号",
}

_CSS = """
:root{--bg:#0d1016;--panel:#141923;--line:#232b39;--text:#e8ecf3;--muted:#93a0b5;
--green:#2ecc71;--red:#ff5c5c;--blue:#4da3ff;--amber:#f5a623;--purple:#b07cff}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--text);
font:13px/1.6 -apple-system,BlinkMacSystemFont,"Segoe UI","Microsoft YaHei",sans-serif}
main{max-width:1180px;margin:0 auto;padding:28px 22px 56px}
header{display:flex;justify-content:space-between;gap:16px;align-items:flex-end;
border-bottom:1px solid var(--line);padding-bottom:16px;margin-bottom:22px}
h1{font-size:20px;margin:0 0 4px;font-weight:600;letter-spacing:.2px}
h2{font-size:15px;margin:30px 0 12px;font-weight:600}
h3{font-size:13px;margin:18px 0 8px;font-weight:600;color:var(--muted)}
.sub{color:var(--muted);font-size:12px}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:10px}
.card{background:var(--panel);border:1px solid var(--line);border-radius:8px;padding:12px 14px}
.card .label{color:var(--muted);font-size:11px;letter-spacing:.3px}
.card .value{font-size:17px;font-weight:600;margin-top:4px;font-variant-numeric:tabular-nums}
.pos{color:var(--green)}.neg{color:var(--red)}.muted{color:var(--muted)}
.chart{background:var(--panel);border:1px solid var(--line);border-radius:8px;padding:10px 6px}
table{width:100%;border-collapse:collapse;font-size:12px}
th,td{text-align:right;padding:7px 8px;border-bottom:1px solid var(--line);
font-variant-numeric:tabular-nums;white-space:nowrap}
th{color:var(--muted);font-weight:500;font-size:11px}
th:first-child,td:first-child{text-align:left}
tr.best td{background:rgba(46,204,113,.10)}
tr.detail td{border-bottom:1px solid var(--line);background:transparent}
details summary{cursor:pointer;color:var(--muted);font-size:11px}
.table-wrap{overflow-x:auto;border:1px solid var(--line);border-radius:8px;background:var(--panel)}
.table-wrap table{min-width:760px}
ul.warn{margin:0;padding-left:20px;color:var(--muted)}
ul.warn li{margin:4px 0}
code{background:#0a0d12;border:1px solid var(--line);border-radius:4px;padding:1px 5px;
font-size:11px;word-break:break-all}
.meta{display:grid;grid-template-columns:repeat(auto-fit,minmax(260px,1fr));gap:6px 20px;
background:var(--panel);border:1px solid var(--line);border-radius:8px;padding:14px}
.meta div{color:var(--muted);font-size:11px}
.meta b{display:block;color:var(--text);font-weight:500;font-size:12px;word-break:break-all}
.heat td{text-align:center;font-weight:600}
.legend{display:flex;gap:14px;flex-wrap:wrap;color:var(--muted);font-size:11px;margin:8px 0}
.legend span::before{content:"";display:inline-block;width:9px;height:9px;border-radius:2px;
background:var(--c);margin-right:5px;vertical-align:-1px}
footer{margin-top:36px;color:var(--muted);font-size:11px;border-top:1px solid var(--line);
padding-top:14px}
"""

_LIMITATIONS = (
    "存在幸存者偏差：已下架或停止交易的币种不在当前历史数据中，组合收益可能被高估。",
    "未模拟订单簿深度、成交量冲击与部分成交；大额订单的真实滑点可能高于回测假设。",
    "市价单和止损单按固定比例滑点成交，止盈按限价成交；未模拟撮合延迟和拒单。",
    "同根 K 线同时触发止损与止盈时按止损优先处理，属于保守假设。",
    "资金费率仅在提供费率数据时计入；K 线内部多个资金费率点使用该 K 线收盘价近似。",
    "1D K 线按北京时间 00:00 收盘对齐，请确认输入数据采用同一时区口径。",
    "本报告只反映历史模拟结果，不构成任何收益承诺；合约交易可能损失全部保证金。",
)


def render_report(
    result: BacktestResult,
    *,
    generated_at_ts_ms: int | None = None,
) -> str:
    """渲染单次回测报告。"""

    generated_at = _generated_at(generated_at_ts_ms)
    title = f"{result.metadata.strategy_name} 回测报告"
    parts = [
        f"<header><div><h1>{_e(title)}</h1>"
        f'<div class="sub">{_e(result.metadata.period_start_ts_ms)} 至 '
        f"{_e(result.metadata.period_end_ts_ms)} · 执行周期 "
        f"{_e(result.metadata.execution_bar.value)} · "
        f"{_e(', '.join(result.metadata.instruments))}</div></div>"
        f'<div class="sub">生成于 {_e(generated_at)}</div></header>',
        _metric_cards(result.metrics),
        '<h2>资金曲线</h2><div class="chart">',
        _equity_svg(result.equity_curve),
        "</div>",
        '<h2>回撤曲线</h2><div class="chart">',
        _drawdown_svg(result.drawdown_curve),
        "</div>",
        "<h2>月度收益</h2>",
        _monthly_table(result.monthly_returns),
        "<h2>逐笔交易</h2>",
        _trades_table(result.trades),
        "<h2>被拒绝信号</h2>",
        _rejected_table(result.rejected_signals),
        "<h2>偏差警示与数据质量</h2>",
        _warning_section(result),
        "<h2>复现实验信息</h2>",
        _metadata_table(result.metadata),
        "<footer>DivergenceQuant M06 回测引擎 · 报告由后端确定性生成，无外部脚本。</footer>",
    ]
    return _page(title, "\n".join(parts), generated_at)


def write_report(
    result: BacktestResult,
    path: str | Path,
    *,
    generated_at_ts_ms: int | None = None,
) -> Path:
    """渲染并写入单次回测报告。"""

    return write_html(render_report(result, generated_at_ts_ms=generated_at_ts_ms), path)


def write_html(content: str, path: str | Path) -> Path:
    """把 HTML 写入磁盘并返回路径。"""

    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    return target


def render_comparison_report(
    comparison: ComparisonResult,
    *,
    generated_at_ts_ms: int | None = None,
) -> str:
    """渲染 S1/S2/S3 或 SMA/EMA 的逐策略对比报告。"""

    generated_at = _generated_at(generated_at_ts_ms)
    title = comparison.title
    rows = comparison.rows
    summary = [
        '<div class="table-wrap"><table><thead><tr>'
        "<th>策略</th><th>信号数</th><th>拒绝</th><th>交易数</th>"
        "<th>总收益</th><th>年化</th><th>最大回撤</th><th>夏普</th>"
        "<th>索提诺</th><th>卡玛</th><th>胜率</th><th>盈亏比</th>"
        "</tr></thead><tbody>"
    ]
    for row in rows:
        metrics = row.metrics
        summary.append(
            f'<tr data-strategy-row="{_e(row.name)}">'
            f"<td>{_e(row.name)}</td>"
            f"<td>{row.signal_count}</td>"
            f"<td>{row.rejected_signal_count}</td>"
            f"<td>{row.trade_count}</td>"
            f"<td>{_pct(metrics.total_return)}</td>"
            f"<td>{_pct(metrics.annualized_return)}</td>"
            f"<td>{_pct(metrics.max_drawdown)}</td>"
            f"<td>{_num(metrics.sharpe)}</td>"
            f"<td>{_num(metrics.sortino)}</td>"
            f"<td>{_num(metrics.calmar)}</td>"
            f"<td>{_pct(metrics.win_rate)}</td>"
            f"<td>{_ratio(metrics.profit_factor)}</td>"
            "</tr>"
        )
    summary.append("</tbody></table></div>")

    series = {row.name: row.equity_curve for row in rows}
    parts = [
        f"<header><div><h1>{_e(title)}</h1>"
        f'<div class="sub">共 {len(rows)} 个策略；每个策略使用同一回测引擎与成本模型</div>'
        f'</div><div class="sub">生成于 {_e(generated_at)}</div></header>',
        "<h2>逐策略指标</h2>",
        "\n".join(summary),
        '<h2>累计收益对比</h2><div class="chart">',
        _multi_equity_svg(series),
        "</div>",
        "<h2>逐策略报告</h2>",
        _strategy_blocks(comparison),
    ]
    if comparison.baseline is not None:
        parts.extend(["<h2>相对基准差值</h2>", _delta_table(comparison)])
    parts.append(
        "<footer>分策略展示，避免组合收益掩盖单一策略失效。"
        "SMA200 与 EMA200 变体由 M03 趋势分类和 M07 策略参数提供。</footer>"
    )
    return _page(title, "\n".join(parts), generated_at)


def render_sweep_report(
    sweep: SweepResult,
    *,
    generated_at_ts_ms: int | None = None,
) -> str:
    """渲染参数扫描报告与二维热力图。"""

    generated_at = _generated_at(generated_at_ts_ms)
    ordered = sorted(
        sweep.entries,
        key=lambda entry: (
            -objective_value(entry.result.metrics, sweep.objective),
            canonical_params(entry.params),
        ),
    )
    best_key = canonical_params(sweep.best.params) if sweep.best is not None else None
    best_text = _params(sweep.best.params) if sweep.best is not None else "无"
    parts = [
        "<header><div><h1>参数扫描报告</h1>"
        f'<div class="sub">目标函数 {_e(sweep.objective)} · '
        f"组合数 {len(sweep.entries)} · min_trades={sweep.min_trades} · "
        f"最优参数 {_e(best_text)}</div></div>"
        f'<div class="sub">生成于 {_e(generated_at)}</div></header>',
        "<h2>参数热力图</h2>",
        _heatmap(sweep, ordered),
        "<h2>全部参数组合</h2>",
        _sweep_table(sweep, ordered, best_key),
    ]
    if sweep.warnings:
        parts.extend(["<h2>提示</h2>", _warning_list(sweep.warnings)])
    parts.append(
        "<footer>参数扫描只用于研究；任何参数变更仍需通过 Walk-Forward 和人工审批。</footer>"
    )
    return _page("参数扫描报告", "\n".join(parts), generated_at)


def render_walk_forward_report(
    walk_forward: WalkForwardResult,
    *,
    generated_at_ts_ms: int | None = None,
) -> str:
    """渲染 Walk-Forward 样本外报告。"""

    generated_at = _generated_at(generated_at_ts_ms)
    rows = [
        '<div class="table-wrap"><table><thead><tr>'
        "<th>#</th><th>训练区间</th><th>测试区间</th><th>最优参数</th>"
        "<th>训练收益</th><th>样本外收益</th><th>样本外回撤</th><th>样本外交易</th>"
        "</tr></thead><tbody>"
    ]
    for window in walk_forward.windows:
        spec = window.spec
        rows.append(
            f"<tr><td>{spec.index}</td>"
            f"<td>{_e(_ts(spec.train_start_ts_ms))} → {_e(_ts(spec.train_end_ts_ms))}</td>"
            f"<td>{_e(_ts(spec.test_start_ts_ms))} → {_e(_ts(spec.test_end_ts_ms))}</td>"
            f"<td>{_e(_params(window.best_params))}</td>"
            f"<td>{_pct(window.train_result.metrics.total_return)}</td>"
            f"<td>{_pct(window.test_result.metrics.total_return)}</td>"
            f"<td>{_pct(window.test_result.metrics.max_drawdown)}</td>"
            f"<td>{window.test_result.metrics.trade_count}</td></tr>"
        )
    rows.append("</tbody></table></div>")
    parts = [
        "<header><div><h1>Walk-Forward 样本外报告</h1>"
        f'<div class="sub">目标函数 {_e(walk_forward.objective)} · '
        f"窗口数 {len(walk_forward.windows)} · 训练窗口选参，测试窗口不再优化</div></div>"
        f'<div class="sub">生成于 {_e(generated_at)}</div></header>',
        "<h2>样本外汇总指标</h2>",
        _metric_cards(walk_forward.oos_metrics),
        '<h2>样本外资金曲线</h2><div class="chart">',
        _equity_svg(walk_forward.oos_equity_curve),
        "</div>",
        "<h2>窗口明细</h2>",
        "\n".join(rows),
        "<h2>样本外逐笔交易</h2>",
        _trades_table(walk_forward.oos_trades),
    ]
    if walk_forward.warnings:
        parts.extend(["<h2>提示与偏差警示</h2>", _warning_list(walk_forward.warnings)])
    parts.append("<footer>Walk-Forward 结果仍需人工评审后才可影响在线参数。</footer>")
    return _page("Walk-Forward 样本外报告", "\n".join(parts), generated_at)


def _page(title: str, body: str, generated_at: str) -> str:
    return (
        "<!DOCTYPE html>\n"
        '<html lang="zh-CN"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        f"<title>{_e(title)}</title><style>{_CSS}</style></head>"
        f"<body><main>{body}</main></body></html>"
    )


def _metric_cards(metrics: BacktestMetrics) -> str:
    cards = [
        _card("总收益", _pct(metrics.total_return), _tone(metrics.total_return)),
        _card("年化收益", _pct(metrics.annualized_return), _tone(metrics.annualized_return)),
        _card("最大回撤", _pct(metrics.max_drawdown), "neg" if metrics.max_drawdown > 0 else ""),
        _card("夏普", _num(metrics.sharpe)),
        _card("索提诺", _num(metrics.sortino)),
        _card("卡玛", _num(metrics.calmar)),
        _card("胜率", _pct(metrics.win_rate)),
        _card("盈亏比", _ratio(metrics.profit_factor)),
        _card("交易数", str(metrics.trade_count)),
        _card("平均持仓", f"{metrics.average_holding_bars:.1f} 根"),
        _card("手续费", _money(metrics.total_fees)),
        _card("资金费率", _money(metrics.total_funding)),
    ]
    return '<div class="grid">' + "".join(cards) + "</div>"


def _card(label: str, value: str, tone: str = "") -> str:
    css = f" {tone}" if tone else ""
    return (
        f'<div class="card"><div class="label">{_e(label)}</div>'
        f'<div class="value{css}">{value}</div></div>'
    )


def _eq_series(points: Sequence[EquityPoint]) -> list[tuple[int, float]]:
    return [(point.ts_ms, float(point.equity)) for point in points]


def _equity_svg(points: Sequence[EquityPoint]) -> str:
    return _line_svg(_eq_series(points), color=_COLORS[0], y_suffix="", include_zero=False)


def _drawdown_svg(points: Sequence[Any]) -> str:
    series = [(point.ts_ms, -point.drawdown * 100.0) for point in points]
    return _line_svg(series, color=_COLORS[4], y_suffix="%", include_zero=True)


def _multi_equity_svg(series: Mapping[str, Sequence[EquityPoint]]) -> str:
    normalized: dict[str, list[tuple[int, float]]] = {}
    legend: list[str] = []
    for index, (name, points) in enumerate(series.items()):
        if not points:
            normalized[name] = []
            continue
        base = float(points[0].equity)
        if base <= 0:
            normalized[name] = []
            continue
        normalized[name] = [(point.ts_ms, float(point.equity) / base * 100.0) for point in points]
        color = _COLORS[index % len(_COLORS)]
        legend.append(f'<span style="--c:{color}">{_e(name)}</span>')
    chart = _line_svg(
        {},
        color=_COLORS[0],
        y_suffix="",
        include_zero=False,
        overlay=normalized,
    )
    return '<div class="legend">' + "".join(legend) + "</div>" + chart


def _line_svg(
    series: Sequence[tuple[int, float]],
    *,
    color: str,
    y_suffix: str,
    include_zero: bool,
    overlay: Mapping[str, Sequence[tuple[int, float]]] | None = None,
) -> str:
    all_points: list[tuple[int, float]] = list(series)
    if overlay is not None:
        for values in overlay.values():
            all_points.extend(values)
    if not all_points:
        return (
            '<svg viewBox="0 0 980 260" role="img" aria-label="无图表数据">'
            '<rect width="980" height="260" fill="#141923"/>'
            '<text x="490" y="132" fill="#93a0b5" text-anchor="middle" '
            'font-size="13">无数据</text></svg>'
        )
    width, height = 980, 260
    left, right, top, bottom = 74, 20, 16, 32
    plot_w = width - left - right
    plot_h = height - top - bottom
    t_min = min(item[0] for item in all_points)
    t_max = max(item[0] for item in all_points)
    if t_max == t_min:
        t_max = t_min + 1
    y_values = [item[1] for item in all_points]
    y_min, y_max = min(y_values), max(y_values)
    if include_zero:
        y_min = min(y_min, 0.0)
        y_max = max(y_max, 0.0)
    if y_max == y_min:
        delta = max(abs(y_max) * 0.01, 1.0)
        y_min -= delta
        y_max += delta

    def x_of(ts: int) -> float:
        return left + (ts - t_min) / (t_max - t_min) * plot_w

    def y_of(value: float) -> float:
        return top + (y_max - value) / (y_max - y_min) * plot_h

    grid: list[str] = []
    for step in range(5):
        value = y_min + (y_max - y_min) * step / 4
        y = y_of(value)
        grid.append(
            f'<line x1="{left}" y1="{y:.2f}" x2="{width - right}" y2="{y:.2f}" '
            'stroke="#232b39" stroke-width="1"/>'
            f'<text x="{left - 8}" y="{y + 4:.2f}" fill="#93a0b5" font-size="10" '
            f'text-anchor="end">{_axis(value)}{_e(y_suffix)}</text>'
        )
    if include_zero and y_min < 0 < y_max:
        zero_y = y_of(0.0)
        grid.append(
            f'<line x1="{left}" y1="{zero_y:.2f}" x2="{width - right}" y2="{zero_y:.2f}" '
            'stroke="#3a4558" stroke-width="1" stroke-dasharray="4 4"/>'
        )

    def polyline(values: Sequence[tuple[int, float]]) -> str:
        if not values:
            return ""
        points = " ".join(f"{x_of(ts):.2f},{y_of(value):.2f}" for ts, value in values)
        return (
            f'<polyline points="{points}" fill="none" stroke="{color}" '
            'stroke-width="2" stroke-linejoin="round" stroke-linecap="round"/>'
        )

    lines = [polyline(series)] if series else []
    if overlay is not None:
        for index, (name, values) in enumerate(overlay.items()):
            del name
            if not values:
                continue
            points = " ".join(f"{x_of(ts):.2f},{y_of(value):.2f}" for ts, value in values)
            lines.append(
                f'<polyline points="{points}" fill="none" '
                f'stroke="{_COLORS[index % len(_COLORS)]}" stroke-width="2" '
                'stroke-linejoin="round" stroke-linecap="round"/>'
            )
    x_labels = (
        f'<text x="{left}" y="{height - 9}" fill="#93a0b5" font-size="10">'
        f"{_e(_ts(t_min))}</text>"
        f'<text x="{width - right}" y="{height - 9}" fill="#93a0b5" font-size="10" '
        f'text-anchor="end">{_e(_ts(t_max))}</text>'
    )
    return (
        f'<svg viewBox="0 0 {width} {height}" role="img" aria-label="回测曲线">'
        f'<rect width="{width}" height="{height}" fill="#141923"/>'
        + "".join(grid)
        + "".join(lines)
        + x_labels
        + "</svg>"
    )


def _monthly_table(rows: Sequence[MonthlyReturn]) -> str:
    if not rows:
        return '<p class="muted">暂无月度数据。</p>'
    body = []
    for row in rows:
        intensity = min(abs(row.return_pct) / 0.1, 1.0)
        if row.return_pct >= 0:
            color = f"rgba(46,204,113,{intensity * 0.55:.3f})"
        else:
            color = f"rgba(255,92,92,{intensity * 0.55:.3f})"
        body.append(
            f"<tr><td>{row.year}-{row.month:02d}</td>"
            f'<td style="background:{color}">{_pct(row.return_pct)}</td>'
            f"<td>{_money(row.start_equity)}</td><td>{_money(row.end_equity)}</td></tr>"
        )
    return (
        '<div class="table-wrap"><table><thead><tr><th>月份</th><th>收益</th>'
        "<th>期初权益</th><th>期末权益</th></tr></thead><tbody>"
        + "".join(body)
        + "</tbody></table></div>"
    )


def _trades_table(trades: Sequence[BacktestTrade]) -> str:
    if not trades:
        return '<p class="muted">本次回测没有已平仓交易。</p>'
    body: list[str] = []
    for trade in trades:
        reasons = ", ".join(leg.reason.value for leg in trade.exits)
        body.append(
            f'<tr data-trade-id="{_e(trade.trade_id)}">'
            f"<td>{_e(trade.trade_id)}</td>"
            f"<td>{_e(trade.strategy_name)}</td>"
            f"<td>{_e(trade.inst_id)}</td>"
            f"<td>{_e(trade.side.value)}</td>"
            f"<td>{_e(_ts(trade.entry_ts_ms))}</td>"
            f"<td>{_price(trade.entry_price)}</td>"
            f"<td>{_e(_ts(trade.exit_ts_ms))}</td>"
            f"<td>{_price(trade.average_exit_price)}</td>"
            f"<td>{_price(trade.initial_qty)}</td>"
            f"<td>{_money(trade.fees)}</td>"
            f"<td>{_money(trade.funding)}</td>"
            f'<td class="{_tone(trade.net_pnl)}">{_money(trade.net_pnl)}</td>'
            f'<td class="{_tone(trade.return_pct)}">{_pct(trade.return_pct)}</td>'
            f"<td>{'—' if trade.r_multiple is None else f'{trade.r_multiple:.2f}R'}</td>"
            f"<td>{trade.holding_bars}</td>"
            f"<td>{_e(reasons)}</td></tr>"
        )
        legs = "".join(
            "<tr><td>"
            + _e(_ts(leg.ts_ms))
            + f"</td><td>{_price(leg.price)}</td><td>{_price(leg.qty)}</td>"
            f"<td>{_money(leg.fee)}</td><td>{_e(leg.reason.value)}</td>"
            f"<td>{_money(leg.net_pnl)}</td></tr>"
            for leg in trade.exits
        )
        body.append(
            '<tr class="detail"><td colspan="16"><details><summary>平仓明细</summary>'
            "<table><thead><tr><th>时间</th><th>价格</th><th>数量</th><th>手续费</th>"
            f"<th>原因</th><th>净盈亏</th></tr></thead><tbody>{legs}</tbody></table>"
            "</details></td></tr>"
        )
    return (
        '<div class="table-wrap"><table><thead><tr><th>交易</th><th>策略</th><th>币种</th>'
        "<th>方向</th><th>开仓时间</th><th>开仓价</th><th>平仓时间</th><th>均价</th>"
        "<th>数量</th><th>手续费</th><th>资金费</th><th>净盈亏</th><th>收益率</th>"
        "<th>R</th><th>持仓K线</th><th>平仓原因</th></tr></thead><tbody>"
        + "".join(body)
        + "</tbody></table></div>"
    )


def _rejected_table(rejected: Sequence[RejectedSignal]) -> str:
    if not rejected:
        return '<p class="muted">没有被拒绝的信号。</p>'
    rows = "".join(
        f"<tr><td>{_e(_ts(item.ts_ms))}</td><td>{_e(item.inst_id)}</td>"
        f"<td>{_e(item.strategy_name)}</td><td>{_e(item.action.value)}</td>"
        f"<td>{_e(item.reason.value)}</td><td>{_e(item.detail)}</td></tr>"
        for item in rejected
    )
    return (
        '<div class="table-wrap"><table><thead><tr><th>时间</th><th>币种</th>'
        "<th>策略</th><th>动作</th><th>原因</th><th>说明</th></tr></thead><tbody>"
        + rows
        + "</tbody></table></div>"
    )


def _warning_section(result: BacktestResult) -> str:
    items = list(_LIMITATIONS) + list(result.warnings)
    quality = "".join(
        f"<tr><td>{_e(item.inst_id)}</td><td>{_e(item.bar.value)}</td>"
        f"<td>{item.candle_count}</td><td>{item.missing_bars}</td>"
        f"<td>{_e(_ts(item.first_ts_ms))}</td><td>{_e(_ts(item.last_ts_ms))}</td></tr>"
        for item in result.stream_quality
    )
    table = (
        '<div class="table-wrap"><table><thead><tr><th>币种</th><th>周期</th>'
        "<th>K线数</th><th>缺失</th><th>首个开盘时间</th><th>最后开盘时间</th>"
        f"</tr></thead><tbody>{quality}</tbody></table></div>"
    )
    return _warning_list(items) + table


def _warning_list(items: Sequence[str]) -> str:
    return '<ul class="warn">' + "".join(f"<li>{_e(item)}</li>" for item in items) + "</ul>"


def _metadata_table(metadata: BacktestMetadata) -> str:
    entries = [
        ("run_id", metadata.run_id),
        ("策略", metadata.strategy_name),
        ("策略版本", metadata.strategy_version),
        ("策略参数", _params(metadata.strategy_params)),
        ("执行周期", metadata.execution_bar.value),
        ("初始权益", _money(metadata.initial_equity)),
        ("引擎版本", metadata.engine_version),
        ("手续费模型", metadata.fee_model_version),
        ("滑点模型", metadata.slippage_model_version),
        ("资金费率模型", metadata.funding_model_version),
        ("配置哈希", metadata.config_hash),
        ("行情指纹", metadata.data_fingerprint),
        ("数据来源", metadata.data_source or "未提供"),
        ("代码提交", metadata.code_commit or "未提供"),
        ("随机种子", "—" if metadata.random_seed is None else str(metadata.random_seed)),
    ]
    return (
        '<div class="meta">'
        + "".join(f"<div>{_e(label)}<b>{_e(value)}</b></div>" for label, value in entries)
        + "</div>"
    )


def _strategy_blocks(comparison: ComparisonResult) -> str:
    blocks: list[str] = []
    for row in comparison.rows:
        blocks.append(
            '<div class="card" style="margin-bottom:10px">'
            f'<div class="label">{_e(row.name)}</div>'
            f'<div class="value">{_pct(row.metrics.total_return)} · '
            f"{row.metrics.trade_count} 笔 · 夏普 {_num(row.metrics.sharpe)}</div>"
            f'<div class="sub">信号 {row.signal_count}，拒绝 {row.rejected_signal_count}，'
            f"最大回撤 {_pct(row.metrics.max_drawdown)}，胜率 {_pct(row.metrics.win_rate)}</div>"
            "</div>"
        )
    return "".join(blocks)


def _delta_table(comparison: ComparisonResult) -> str:
    baseline = comparison.baseline
    assert baseline is not None
    base_row = next(row for row in comparison.rows if row.name == baseline)
    others = [row for row in comparison.rows if row.name != baseline]
    header = "".join(f"<th>{_e(row.name)} Δ</th>" for row in others)
    body: list[str] = []
    for key in COMPARISON_KEYS:
        cells = "".join(
            f"<td>{_metric_value(key, comparison.deltas[row.name][key])}</td>" for row in others
        )
        body.append(
            f"<tr><td>{_e(_METRIC_LABELS[key])}</td>"
            f"<td>{_metric_value(key, _metric_from_row(base_row, key))}</td>{cells}</tr>"
        )
    return (
        '<div class="table-wrap"><table><thead><tr><th>指标</th>'
        f"<th>{_e(baseline)}</th>{header}</tr></thead><tbody>"
        + "".join(body)
        + "</tbody></table></div>"
    )


def _metric_from_row(row: Any, key: str) -> Any:
    if key == "signal_count":
        return row.signal_count
    if key == "rejected_signal_count":
        return row.rejected_signal_count
    if key == "trade_count":
        return row.trade_count
    return getattr(row.metrics, key)


def _metric_value(key: str, value: Any) -> str:
    if value is None:
        return "—"
    if key in {"trade_count", "signal_count", "rejected_signal_count"}:
        return f"{int(value):+d}" if isinstance(value, float) else str(value)
    if key in {"profit_factor", "sharpe", "sortino", "calmar"}:
        return _num(float(value))
    return _pct(float(value))


def _heatmap(sweep: SweepResult, ordered: Sequence[Any]) -> str:
    if not ordered:
        return '<p class="muted">没有可展示的参数组合。</p>'
    keys = sorted(ordered[0].params)
    if len(keys) != 2:
        return '<p class="muted">参数热力图仅支持二维参数网格。</p>'
    first, second = keys
    row_values = sorted({str(entry.params[first]) for entry in ordered})
    col_values = sorted({str(entry.params[second]) for entry in ordered})
    lookup = {(str(entry.params[first]), str(entry.params[second])): entry for entry in ordered}
    values = [objective_value(entry.result.metrics, sweep.objective) for entry in ordered]
    finite = [value for value in values if value != float("-inf")]
    low = min(finite) if finite else 0.0
    high = max(finite) if finite else 1.0
    header = "".join(f"<th>{_e(value)}</th>" for value in col_values)
    rows: list[str] = []
    for row_value in row_values:
        cells: list[str] = []
        for col_value in col_values:
            entry = lookup[(row_value, col_value)]
            value = objective_value(entry.result.metrics, sweep.objective)
            cells.append(
                f'<td style="background:{_heat_color(value, low, high)}">{_objective(value)}</td>'
            )
        rows.append(f"<tr><td>{_e(row_value)}</td>{''.join(cells)}</tr>")
    return (
        '<div class="table-wrap"><table class="heat"><thead><tr>'
        f"<th>{_e(first)} \\ {_e(second)}</th>{header}</tr></thead><tbody>"
        + "".join(rows)
        + "</tbody></table></div>"
        + f'<p class="sub">颜色越绿表示 {_e(sweep.objective)} 越高；越红表示越低。</p>'
    )


def _heat_color(value: float, low: float, high: float) -> str:
    if value == float("-inf") or high <= low:
        return "rgba(147,160,181,.12)"
    ratio = max(0.0, min(1.0, (value - low) / (high - low)))
    if ratio >= 0.5:
        alpha = (ratio - 0.5) * 1.1
        return f"rgba(46,204,113,{alpha:.3f})"
    alpha = (0.5 - ratio) * 1.1
    return f"rgba(255,92,92,{alpha:.3f})"


def _sweep_table(sweep: SweepResult, ordered: Sequence[Any], best_key: str | None) -> str:
    rows: list[str] = []
    for entry in ordered:
        key = canonical_params(entry.params)
        css = ' class="best"' if key == best_key else ""
        metrics = entry.result.metrics
        rows.append(
            f"<tr{css}><td>{_e(_params(entry.params))}</td>"
            f"<td>{_objective(objective_value(metrics, sweep.objective))}</td>"
            f"<td>{_pct(metrics.total_return)}</td>"
            f"<td>{_pct(metrics.max_drawdown)}</td>"
            f"<td>{_num(metrics.sharpe)}</td>"
            f"<td>{metrics.trade_count}</td><td>{metrics.signal_count}</td></tr>"
        )
    return (
        '<div class="table-wrap"><table><thead><tr><th>参数</th>'
        f"<th>{_e(sweep.objective)}</th><th>总收益</th><th>最大回撤</th>"
        "<th>夏普</th><th>交易数</th><th>信号数</th></tr></thead><tbody>"
        + "".join(rows)
        + "</tbody></table></div>"
    )


def _generated_at(ts_ms: int | None) -> str:
    if ts_ms is None:
        ts_ms = time.time_ns() // 1_000_000
    return _ts(ts_ms)


def _ts(ts_ms: Any) -> str:
    if ts_ms is None:
        return "—"
    moment = datetime.fromtimestamp(int(ts_ms) / 1000, tz=UTC)
    return moment.strftime("%Y-%m-%d %H:%M UTC")


def _money(value: Decimal) -> str:
    return f"{value:,.2f}"


def _price(value: Decimal) -> str:
    text = f"{value:,.8f}".rstrip("0").rstrip(".")
    return text or "0"


def _pct(value: float | None) -> str:
    if value is None:
        return "—"
    if value == float("inf"):
        return "∞"
    if value == float("-inf"):
        return "-∞"
    return f"{value * 100:,.2f}%"


def _num(value: float | None) -> str:
    if value is None:
        return "—"
    if value == float("inf"):
        return "∞"
    if value == float("-inf"):
        return "-∞"
    return f"{value:,.2f}"


def _ratio(value: float | None) -> str:
    if value is None:
        return "—"
    if value == float("inf"):
        return "∞"
    return f"{value:.2f}"


def _objective(value: float) -> str:
    if value == float("-inf"):
        return "—"
    if value == float("inf"):
        return "∞"
    return f"{value:,.4f}"


def _axis(value: float) -> str:
    if abs(value) >= 1_000_000:
        return f"{value / 1_000_000:,.2f}M"
    if abs(value) >= 1_000:
        return f"{value:,.0f}"
    return f"{value:,.2f}"


def _tone(value: float | Decimal) -> str:
    if value > 0:
        return "pos"
    if value < 0:
        return "neg"
    return ""


def _params(params: Mapping[str, Any]) -> str:
    return json.dumps(dict(params), ensure_ascii=False, sort_keys=True, default=str)


def _e(value: Any) -> str:
    return html.escape(str(value), quote=True)
