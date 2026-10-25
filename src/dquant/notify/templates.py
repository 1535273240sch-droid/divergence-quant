"""邮件深色 HTML 模板渲染引擎。

严格贯彻顶级量化终端 Pure Black & Liquid Glass 视觉规范：
- 底色：极致纯黑 `#000000` / `#0a0a0c`
- 卡片：微透高质感容器（边框 `1px solid rgba(255, 255, 255, 0.12)`）
- 强调色：钻石白 `#FFFFFF`、翡翠霓虹绿 `#00F5A0`、霓虹绯红 `#FF4D5E`、钛金冷灰 `#CBD5E1`
- 移动端一键响应与一次性签名链接
"""

from __future__ import annotations

import html
from typing import Any


def _base_email_shell(title: str, content_html: str) -> str:
    """包装全局基础深色邮件外壳。"""
    safe_title = html.escape(title)
    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>{safe_title}</title>
  <style>
    body {{
      margin: 0;
      padding: 0;
      background-color: #000000;
      font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
      color: #CBD5E1;
      -webkit-font-smoothing: antialiased;
    }}
    .container {{
      max-width: 600px;
      margin: 0 auto;
      padding: 24px 16px;
      background-color: #000000;
    }}
    .card {{
      background-color: #0d1117;
      border: 1px solid rgba(255, 255, 255, 0.12);
      border-radius: 12px;
      padding: 24px;
      box-shadow: 0 8px 24px rgba(0, 0, 0, 0.6);
    }}
    .header {{
      display: flex;
      align-items: center;
      justify-content: space-between;
      border-bottom: 1px solid rgba(255, 255, 255, 0.08);
      padding-bottom: 16px;
      margin-bottom: 20px;
    }}
    .logo {{
      font-size: 18px;
      font-weight: 800;
      letter-spacing: 1.5px;
      color: #FFFFFF;
      text-transform: uppercase;
    }}
    .badge {{
      font-size: 11px;
      font-weight: 700;
      padding: 3px 8px;
      border-radius: 4px;
      text-transform: uppercase;
      letter-spacing: 0.5px;
    }}
    .badge-long {{
      background-color: rgba(0, 245, 160, 0.15);
      color: #00F5A0;
      border: 1px solid rgba(0, 245, 160, 0.4);
    }}
    .badge-short {{
      background-color: rgba(255, 77, 94, 0.15);
      color: #FF4D5E;
      border: 1px solid rgba(255, 77, 94, 0.4);
    }}
    .badge-warn {{
      background-color: rgba(245, 158, 11, 0.15);
      color: #F59E0B;
      border: 1px solid rgba(245, 158, 11, 0.4);
    }}
    .badge-info {{
      background-color: rgba(59, 130, 246, 0.15);
      color: #60A5FA;
      border: 1px solid rgba(59, 130, 246, 0.4);
    }}
    .data-table {{
      width: 100%;
      border-collapse: collapse;
      margin-top: 16px;
      font-size: 14px;
    }}
    .data-table td {{
      padding: 10px 0;
      border-bottom: 1px solid rgba(255, 255, 255, 0.06);
    }}
    .data-label {{
      color: #94A3B8;
      width: 35%;
    }}
    .data-value {{
      color: #FFFFFF;
      font-weight: 600;
      font-family: 'SF Mono', Consolas, Monaco, monospace;
      text-align: right;
    }}
    .btn {{
      display: inline-block;
      width: 100%;
      box-sizing: border-box;
      padding: 14px 20px;
      margin-top: 24px;
      background-color: #00F5A0;
      color: #000000;
      font-weight: 700;
      font-size: 15px;
      text-align: center;
      text-decoration: none;
      border-radius: 8px;
      letter-spacing: 0.5px;
      box-shadow: 0 4px 14px rgba(0, 245, 160, 0.3);
    }}
    .footer {{
      margin-top: 24px;
      text-align: center;
      font-size: 12px;
      color: #64748B;
      line-height: 1.6;
    }}
  </style>
</head>
<body>
  <div class="container">
    <div class="card">
      <div class="header">
        <div class="logo">DIVERGENCE QUANT</div>
      </div>
      {content_html}
    </div>
    <div class="footer">
      本邮件由 DivergenceQuant 系统安全引擎自动发出。<br>
      链接为一次性有效签名，请勿转发他人。
    </div>
  </div>
</body>
</html>
"""


def _card_header(title: str, badge_html: str) -> str:
    """生成带右侧状态标签的卡片头部。"""
    return (
        '<div style="display: flex; justify-content: space-between; '
        'align-items: center; margin-bottom: 12px;">\n'
        f'  <h2 style="margin: 0; color: #FFFFFF; font-size: 20px;">{html.escape(title)}</h2>\n'
        f"  {badge_html}\n"
        "</div>"
    )


def render_signal_email(
    *,
    inst_id: str,
    side: str,
    price: str,
    stop_loss: str,
    take_profit: str,
    reason: str,
    strategy_name: str,
    score: float | str,
    confirm_url: str | None = None,
    mode: str = "demo",
    ai_comment: str | None = None,
) -> tuple[str, str]:
    """渲染开仓待确认 / 策略信号邮件。返回 (subject, html_body)。"""
    is_long = side.lower() == "long"
    side_text = "做多 (LONG)" if is_long else "做空 (SHORT)"
    side_class = "badge-long" if is_long else "badge-short"
    mode_text = "模拟盘" if mode == "demo" else "实盘"
    action_text = "待确认" if confirm_url else "信号生成"

    subject = f"[DQ][{mode_text}][{action_text}] {inst_id} {side_text}"

    btn_html = ""
    if confirm_url:
        safe_link = html.escape(confirm_url)
        btn_html = f"""
        <div style="margin-top: 20px;">
          <a href="{safe_link}" class="btn" target="_blank">
            前往手机控制台确认下单
          </a>
          <p style="font-size: 12px; color: #94A3B8; text-align: center; margin-top: 8px;">
            15 分钟内有效，需在网页端完成二次确认，严禁 GET 直连产生交易动作。
          </p>
        </div>
        """

    ai_html = ""
    if ai_comment:
        ai_html = f"""
        <div style="margin-top: 16px; padding: 12px; background: rgba(255, 255, 255, 0.04);
                    border-radius: 8px; border-left: 3px solid #00F5A0;">
          <div style="font-size: 12px; color: #00F5A0; font-weight: 700; margin-bottom: 4px;">
            AI 陪审员研判
          </div>
          <div style="font-size: 13px; color: #CBD5E1;">{html.escape(ai_comment)}</div>
        </div>
        """

    header_html = _card_header(inst_id, f'<span class="badge {side_class}">{side_text}</span>')

    content = f"""
      {header_html}
      <div style="font-size: 13px; color: #94A3B8; margin-bottom: 16px;">
        策略来源：{html.escape(strategy_name)} | 评分：{score}
      </div>

      <table class="data-table">
        <tr>
          <td class="data-label">入场参考价</td>
          <td class="data-value">{html.escape(price)}</td>
        </tr>
        <tr>
          <td class="data-label">保护性止损</td>
          <td class="data-value" style="color: #FF4D5E;">{html.escape(stop_loss)}</td>
        </tr>
        <tr>
          <td class="data-label">第一止盈目标</td>
          <td class="data-value" style="color: #00F5A0;">{html.escape(take_profit)}</td>
        </tr>
        <tr>
          <td class="data-label">触发原因</td>
          <td class="data-value" style="font-weight: normal; font-size: 13px;">
            {html.escape(reason)}
          </td>
        </tr>
      </table>

      {ai_html}
      {btn_html}
    """
    return subject, _base_email_shell(subject, content)


def render_fill_email(
    *,
    inst_id: str,
    side: str,
    action: str,
    price: str,
    size: str,
    fee: str,
    pnl: str | None = None,
    mode: str = "demo",
) -> tuple[str, str]:
    """渲染成交确认邮件（开仓 / 平仓）。返回 (subject, html_body)。"""
    is_open = action.lower() == "open"
    action_cn = "开仓" if is_open else "平仓"
    mode_text = "模拟盘" if mode == "demo" else "实盘"
    subject = f"[DQ][{mode_text}][{action_cn}] {inst_id} {side.upper()}"

    pnl_row = ""
    if pnl is not None:
        pnl_val = float(pnl) if pnl.replace(".", "", 1).replace("-", "").isdigit() else 0.0
        pnl_color = "#00F5A0" if pnl_val >= 0 else "#FF4D5E"
        pnl_row = f"""
        <tr>
          <td class="data-label">实现盈亏</td>
          <td class="data-value" style="color: {pnl_color};">{html.escape(pnl)} USDT</td>
        </tr>
        """

    header_html = _card_header(inst_id, f'<span class="badge badge-info">{action_cn}成功</span>')

    content = f"""
      {header_html}
      <table class="data-table">
        <tr>
          <td class="data-label">交易方向</td>
          <td class="data-value">{html.escape(side.upper())}</td>
        </tr>
        <tr>
          <td class="data-label">成交均价</td>
          <td class="data-value">{html.escape(price)}</td>
        </tr>
        <tr>
          <td class="data-label">成交数量</td>
          <td class="data-value">{html.escape(size)}</td>
        </tr>
        <tr>
          <td class="data-label">手续费</td>
          <td class="data-value">{html.escape(fee)} USDT</td>
        </tr>
        {pnl_row}
      </table>
    """
    return subject, _base_email_shell(subject, content)


def render_risk_email(
    *,
    rule_name: str,
    current_value: str,
    action_taken: str,
    inst_id: str | None = None,
    mode: str = "demo",
) -> tuple[str, str]:
    """渲染风控触发告警邮件。返回 (subject, html_body)。"""
    mode_text = "模拟盘" if mode == "demo" else "实盘"
    target = f" - {inst_id}" if inst_id else ""
    subject = f"[DQ][{mode_text}][风控告警] 触发规则：{rule_name}{target}"

    header_html = _card_header("风控熔断触发", '<span class="badge badge-warn">WARN</span>')
    target_row = ""
    if inst_id:
        target_row = (
            f'<tr><td class="data-label">相关标的</td>'
            f'<td class="data-value">{html.escape(inst_id)}</td></tr>'
        )

    content = f"""
      {header_html}
      <table class="data-table">
        <tr>
          <td class="data-label">风控规则</td>
          <td class="data-value">{html.escape(rule_name)}</td>
        </tr>
        {target_row}
        <tr>
          <td class="data-label">监控读数</td>
          <td class="data-value" style="color: #F59E0B;">{html.escape(current_value)}</td>
        </tr>
        <tr>
          <td class="data-label">执行处置动作</td>
          <td class="data-value" style="color: #FF4D5E;">{html.escape(action_taken)}</td>
        </tr>
      </table>
    """
    return subject, _base_email_shell(subject, content)


def render_error_email(
    *,
    module_name: str,
    error_summary: str,
    traceback_snippet: str | None = None,
    mode: str = "demo",
) -> tuple[str, str]:
    """渲染系统异常告警邮件。返回 (subject, html_body)。"""
    mode_text = "模拟盘" if mode == "demo" else "实盘"
    subject = f"[DQ][{mode_text}][系统异常] 模块：{module_name}"

    tb_html = ""
    if traceback_snippet:
        tb_html = f"""
        <div style="margin-top: 16px;">
          <div style="font-size: 12px; color: #94A3B8; margin-bottom: 6px;">堆栈摘要：</div>
          <pre style="background: rgba(0, 0, 0, 0.5); border: 1px solid rgba(255, 255, 255, 0.1);
                      border-radius: 6px; padding: 12px; font-size: 12px; color: #FF4D5E;
                      overflow-x: auto; font-family: monospace;">
{html.escape(traceback_snippet)}
          </pre>
        </div>
        """

    header_html = _card_header("系统运行异常告警", '<span class="badge badge-short">ERROR</span>')

    content = f"""
      {header_html}
      <table class="data-table">
        <tr>
          <td class="data-label">异常模块</td>
          <td class="data-value">{html.escape(module_name)}</td>
        </tr>
        <tr>
          <td class="data-label">错误描述</td>
          <td class="data-value" style="font-weight: normal;">{html.escape(error_summary)}</td>
        </tr>
      </table>

      {tb_html}
    """
    return subject, _base_email_shell(subject, content)


def render_daily_report_email(
    *,
    date_str: str,
    total_equity: str,
    daily_pnl: str,
    daily_pnl_pct: str,
    open_positions_count: int,
    trades_count: int,
    mode: str = "demo",
) -> tuple[str, str]:
    """渲染每日量化运行报告。返回 (subject, html_body)。"""
    mode_text = "模拟盘" if mode == "demo" else "实盘"
    subject = f"[DQ][{mode_text}][每日报告] {date_str} 收益播报"

    pnl_val = float(daily_pnl) if daily_pnl.replace(".", "", 1).replace("-", "").isdigit() else 0.0
    pnl_color = "#00F5A0" if pnl_val >= 0 else "#FF4D5E"

    header_html = _card_header(
        "每日量化运行报告",
        f'<span class="badge badge-info">{html.escape(date_str)}</span>',
    )

    content = f"""
      {header_html}
      <table class="data-table">
        <tr>
          <td class="data-label">当前账户净值</td>
          <td class="data-value">{html.escape(total_equity)} USDT</td>
        </tr>
        <tr>
          <td class="data-label">当日盈亏额</td>
          <td class="data-value" style="color: {pnl_color};">{html.escape(daily_pnl)} USDT</td>
        </tr>
        <tr>
          <td class="data-label">当日收益率</td>
          <td class="data-value" style="color: {pnl_color};">{html.escape(daily_pnl_pct)}%</td>
        </tr>
        <tr>
          <td class="data-label">当前活跃持仓</td>
          <td class="data-value">{open_positions_count}</td>
        </tr>
        <tr>
          <td class="data-label">当日已结交易数</td>
          <td class="data-value">{trades_count}</td>
        </tr>
      </table>
    """
    return subject, _base_email_shell(subject, content)


def render_screener_summary_email(
    *,
    pool_results: dict[str, list[dict[str, Any]]],
    mode: str = "demo",
) -> tuple[str, str]:
    """渲染各池选币结果合并摘要邮件。返回 (subject, html_body)。"""
    mode_text = "模拟盘" if mode == "demo" else "实盘"
    subject = f"[DQ][{mode_text}][选币摘要] 全市场候选池扫描完成"

    pool_sections = []
    for pool_name, candidates in pool_results.items():
        rows = []
        for c in candidates[:5]:
            inst_str = html.escape(str(c.get("inst_id", "")))
            reg_str = html.escape(str(c.get("regime", "")))
            div_str = html.escape(str(c.get("divergence_kind", "-")))
            score_val = c.get("score", 0)
            rows.append(f"""
            <tr>
              <td style="color: #FFFFFF; font-weight: 600;">{inst_str}</td>
              <td style="color: #94A3B8;">{reg_str}</td>
              <td style="color: #CBD5E1;">{div_str}</td>
              <td style="color: #00F5A0; font-weight: 700; text-align: right;">{score_val}</td>
            </tr>
            """)
        table_html = f"""
        <div style="margin-top: 16px;">
          <div style="font-size: 14px; font-weight: 700; color: #FFFFFF; margin-bottom: 8px;">
            池 {html.escape(pool_name)} Top 5
          </div>
          <table style="width: 100%; border-collapse: collapse; font-size: 13px;">
            <tr style="color: #64748B; border-bottom: 1px solid rgba(255, 255, 255, 0.08);">
              <th style="text-align: left; padding: 4px 0;">币种</th>
              <th style="text-align: left;">趋势</th>
              <th style="text-align: left;">背离</th>
              <th style="text-align: right;">评分</th>
            </tr>
            {"".join(rows)}
          </table>
        </div>
        """
        pool_sections.append(table_html)

    content = f"""
      <h2 style="margin: 0 0 12px 0; color: #FFFFFF; font-size: 20px;">全市场多因子选币扫描结果</h2>
      <p style="font-size: 13px; color: #94A3B8; margin-bottom: 16px;">
        系统已自动合并各池候选清单，精选前 5 名标的如下：
      </p>
      {"".join(pool_sections)}
    """
    return subject, _base_email_shell(subject, content)
