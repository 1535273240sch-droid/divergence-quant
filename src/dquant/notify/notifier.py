"""邮件通知核心门面类 EmailNotifier。

支持：
1. 异步 SMTP 发送（支持 SSL 与 STARTTLS）。
2. 每小时 30 封限流与时间窗口去重。
3. 立即重试 3 次 + 发件箱 (notify_outbox) 补发双重可靠性保障。
4. 模拟盘 / 实盘前缀自适应。
5. 测试与离线模式降级。
"""

from __future__ import annotations

import asyncio
import smtplib
import time
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Any

from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from dquant.notify.config import EmailSettings, load_email_settings
from dquant.notify.limiter import NotificationLimiter
from dquant.notify.outbox import OutboxManager
from dquant.notify.templates import (
    render_daily_report_email,
    render_error_email,
    render_fill_email,
    render_risk_email,
    render_screener_summary_email,
    render_signal_email,
)


class EmailNotifier:
    """邮件通知器。系统外部通讯的统一门面。"""

    def __init__(
        self,
        settings: EmailSettings | None = None,
        limiter: NotificationLimiter | None = None,
        outbox: OutboxManager | None = None,
        session_factory: async_sessionmaker[AsyncSession] | None = None,
        dry_run: bool = False,
    ) -> None:
        self.settings = settings or load_email_settings()
        self.limiter = limiter or NotificationLimiter(hourly_limit=self.settings.hourly_rate_limit)
        self.outbox = outbox or OutboxManager(session_factory=session_factory)
        self.dry_run = dry_run
        # 记录已发送邮件记录（测试与审计追踪）
        self.sent_history: list[dict[str, Any]] = []

    def _send_smtp_sync(self, subject: str, html_body: str, to_addrs: list[str]) -> None:
        """同步执行 SMTP 邮件发送（在工作线程中调用）。"""
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"] = self.settings.from_addr
        msg["To"] = ", ".join(to_addrs)

        part = MIMEText(html_body, "html", "utf-8")
        msg.attach(part)

        if self.settings.use_ssl:
            with smtplib.SMTP_SSL(
                self.settings.host,
                self.settings.port,
                timeout=self.settings.timeout_sec,
            ) as server:
                if self.settings.user and self.settings.password:
                    server.login(self.settings.user, self.settings.password)
                server.sendmail(self.settings.from_addr, to_addrs, msg.as_string())
        else:
            with smtplib.SMTP(
                self.settings.host,
                self.settings.port,
                timeout=self.settings.timeout_sec,
            ) as server:
                if self.settings.use_tls:
                    server.starttls()
                if self.settings.user and self.settings.password:
                    server.login(self.settings.user, self.settings.password)
                server.sendmail(self.settings.from_addr, to_addrs, msg.as_string())

    async def send_raw(
        self,
        subject: str,
        html_body: str,
        to_addrs: list[str] | None = None,
        dedup_key: str | None = None,
        cooldown_sec: float = 300.0,
    ) -> bool:
        """发送原始 HTML 邮件。具备限流、去重、3次重试与发件箱持久化。"""
        if not self.settings.enabled:
            logger.info("邮件通知未启用，跳过发送: {}", subject)
            return False

        # 检查去重
        if dedup_key and self.limiter.is_duplicate(dedup_key, cooldown_seconds=cooldown_sec):
            logger.info("通知命中去重规则，跳过发送: dedup_key={}, subject={}", dedup_key, subject)
            return False

        # 检查每小时限频
        if not self.limiter.can_send():
            logger.warning(
                "通知超过每小时限额 ({} 封)，存入发件箱等待补发: {}",
                self.settings.hourly_rate_limit,
                subject,
            )
            await self.outbox.save_failed_message(subject=subject, body=html_body)
            return False

        recipients = to_addrs or self.settings.to_addrs

        # 如果开启 dry_run，直接记录成功
        if self.dry_run:
            self.sent_history.append(
                {
                    "subject": subject,
                    "body": html_body,
                    "to": recipients,
                    "ts": time.time(),
                }
            )
            self.limiter.record_send(dedup_key=dedup_key)
            return True

        # 尝试最多 3 次发送
        last_exc: Exception | None = None
        for attempt in range(1, 4):
            try:
                await asyncio.to_thread(self._send_smtp_sync, subject, html_body, recipients)
                self.sent_history.append(
                    {
                        "subject": subject,
                        "body": html_body,
                        "to": recipients,
                        "ts": time.time(),
                    }
                )
                self.limiter.record_send(dedup_key=dedup_key)
                logger.info("邮件发送成功 (尝试 {}/3): {}", attempt, subject)
                return True
            except Exception as exc:
                last_exc = exc
                logger.warning("邮件发送失败 (尝试 {}/3): {} - {}", attempt, subject, exc)
                if attempt < 3:
                    await asyncio.sleep(0.5 * attempt)

        # 3 次均失败，存入发件箱
        logger.error("邮件重试 3 次均失败，写入发件箱待后续补发: {} - {}", subject, last_exc)
        await self.outbox.save_failed_message(subject=subject, body=html_body, retries=3)
        return False

    async def send_signal_alert(
        self,
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
    ) -> bool:
        """发送开仓 / 待确认信号通知。"""
        subject, body = render_signal_email(
            inst_id=inst_id,
            side=side,
            price=price,
            stop_loss=stop_loss,
            take_profit=take_profit,
            reason=reason,
            strategy_name=strategy_name,
            score=score,
            confirm_url=confirm_url,
            mode=mode,
            ai_comment=ai_comment,
        )
        dedup_key = f"signal:{inst_id}:{side}:{reason}"
        return await self.send_raw(subject, body, dedup_key=dedup_key, cooldown_sec=600.0)

    async def send_fill_notification(
        self,
        *,
        inst_id: str,
        side: str,
        action: str,
        price: str,
        size: str,
        fee: str,
        pnl: str | None = None,
        mode: str = "demo",
    ) -> bool:
        """发送成交（开仓/平仓）通知。"""
        subject, body = render_fill_email(
            inst_id=inst_id,
            side=side,
            action=action,
            price=price,
            size=size,
            fee=fee,
            pnl=pnl,
            mode=mode,
        )
        return await self.send_raw(subject, body)

    async def send_risk_alert(
        self,
        *,
        rule_name: str,
        current_value: str,
        action_taken: str,
        inst_id: str | None = None,
        mode: str = "demo",
    ) -> bool:
        """发送风控熔断/预警通知。"""
        subject, body = render_risk_email(
            rule_name=rule_name,
            current_value=current_value,
            action_taken=action_taken,
            inst_id=inst_id,
            mode=mode,
        )
        dedup_key = f"risk:{rule_name}:{inst_id or 'all'}"
        return await self.send_raw(subject, body, dedup_key=dedup_key, cooldown_sec=1800.0)

    async def send_error_alert(
        self,
        *,
        module_name: str,
        error_summary: str,
        traceback_snippet: str | None = None,
        mode: str = "demo",
    ) -> bool:
        """发送系统严重异常报警。"""
        subject, body = render_error_email(
            module_name=module_name,
            error_summary=error_summary,
            traceback_snippet=traceback_snippet,
            mode=mode,
        )
        dedup_key = f"error:{module_name}:{error_summary[:40]}"
        return await self.send_raw(subject, body, dedup_key=dedup_key, cooldown_sec=900.0)

    async def send_daily_report(
        self,
        *,
        date_str: str,
        total_equity: str,
        daily_pnl: str,
        daily_pnl_pct: str,
        open_positions_count: int,
        trades_count: int,
        mode: str = "demo",
    ) -> bool:
        """发送每日量化战报。"""
        subject, body = render_daily_report_email(
            date_str=date_str,
            total_equity=total_equity,
            daily_pnl=daily_pnl,
            daily_pnl_pct=daily_pnl_pct,
            open_positions_count=open_positions_count,
            trades_count=trades_count,
            mode=mode,
        )
        return await self.send_raw(subject, body)

    async def send_screener_summary(
        self,
        *,
        pool_results: dict[str, list[dict[str, Any]]],
        mode: str = "demo",
    ) -> bool:
        """发送合并选币结果摘要。"""
        subject, body = render_screener_summary_email(pool_results=pool_results, mode=mode)
        dedup_key = f"screener:{time.strftime('%Y%m%d%H')}"
        return await self.send_raw(subject, body, dedup_key=dedup_key, cooldown_sec=3600.0)

    async def flush_outbox(self) -> int:
        """主动触发发件箱补发。"""

        async def _resend_callback(subject: str, body: str) -> bool:
            try:
                recipients = self.settings.to_addrs
                if self.dry_run:
                    self.sent_history.append(
                        {"subject": subject, "body": body, "to": recipients, "ts": time.time()}
                    )
                    return True
                await asyncio.to_thread(self._send_smtp_sync, subject, body, recipients)
                return True
            except Exception:
                return False

        return await self.outbox.process_outbox(_resend_callback)
