"""DivergenceQuant 通知模块。

邮件为系统唯一外部通知渠道。负责格式化告警、深色模板渲染、一次性快速确认链接、
限流、同类去重、失败重试与发件箱补发。
"""

from dquant.notify.config import EmailSettings, load_email_settings
from dquant.notify.limiter import NotificationLimiter
from dquant.notify.notifier import EmailNotifier
from dquant.notify.outbox import OutboxManager
from dquant.notify.templates import (
    render_daily_report_email,
    render_error_email,
    render_fill_email,
    render_risk_email,
    render_screener_summary_email,
    render_signal_email,
)

__all__ = [
    "EmailNotifier",
    "EmailSettings",
    "load_email_settings",
    "NotificationLimiter",
    "OutboxManager",
    "render_signal_email",
    "render_fill_email",
    "render_risk_email",
    "render_error_email",
    "render_daily_report_email",
    "render_screener_summary_email",
]
