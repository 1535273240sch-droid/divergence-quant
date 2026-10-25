"""通知模块 (notify) 单元测试。

验证邮件模板渲染、每小时限流、时间窗口去重、失败重试与发件箱持久化机制。
"""

import pytest

from dquant.notify.config import EmailSettings
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


def test_email_template_rendering_signal() -> None:
    """测试待确认信号邮件模板渲染与 XSS 转义。"""
    subject, body = render_signal_email(
        inst_id="BTC-USDT-SWAP",
        side="long",
        price="65000.5",
        stop_loss="63500.0",
        take_profit="68000.0",
        reason="4H 底背离确认 <script>alert(1)</script>",
        strategy_name="S1 顺势回调背离",
        score=88.5,
        confirm_url="https://app.dquant.local/confirm?token=abc123xyz",
        mode="live",
        ai_comment="视觉形态饱满，量价共振明显",
    )

    assert "[DQ][实盘][待确认]" in subject
    assert "BTC-USDT-SWAP" in subject
    assert "做多 (LONG)" in subject

    # 检查纯黑底色与 Liquid Glass 风格
    assert "#000000" in body
    assert "65000.5" in body
    assert "63500.0" in body
    assert "68000.0" in body
    assert "https://app.dquant.local/confirm?token=abc123xyz" in body
    assert "AI 陪审员研判" in body
    # 验证 XSS 转义
    assert "<script>" not in body
    assert "&lt;script&gt;" in body


def test_email_template_rendering_fill_and_risk() -> None:
    """测试开平仓成交与风控告警邮件渲染。"""
    # 成交通知
    subject_fill, body_fill = render_fill_email(
        inst_id="ETH-USDT-SWAP",
        side="short",
        action="close",
        price="3450.2",
        size="10",
        fee="1.25",
        pnl="150.8",
        mode="demo",
    )
    assert "[DQ][模拟盘][平仓]" in subject_fill
    assert "3450.2" in body_fill
    assert "150.8 USDT" in body_fill

    # 风控告警
    subject_risk, body_risk = render_risk_email(
        rule_name="单日亏损熔断 (daily_loss_limit)",
        current_value="3.2% (阈值 3.0%)",
        action_taken="停止新开仓并冻结全部挂单",
        inst_id="BTC-USDT-SWAP",
        mode="demo",
    )
    assert "[DQ][模拟盘][风控告警]" in subject_risk
    assert "单日亏损熔断" in body_risk
    assert "停止新开仓" in body_risk


def test_email_template_rendering_error_and_daily_and_screener() -> None:
    """测试错误报警、每日战报与选币汇总模板。"""
    subject_err, body_err = render_error_email(
        module_name="M09_OMS",
        error_summary="OKX 下单网络超时",
        traceback_snippet="Traceback (most recent call last):\nTimeoutError",
        mode="live",
    )
    assert "[DQ][实盘][系统异常]" in subject_err
    assert "TimeoutError" in body_err

    subject_daily, body_daily = render_daily_report_email(
        date_str="2026-10-24",
        total_equity="10520.50",
        daily_pnl="+240.20",
        daily_pnl_pct="+2.33",
        open_positions_count=2,
        trades_count=5,
        mode="demo",
    )
    assert "2026-10-24" in subject_daily
    assert "10520.50 USDT" in body_daily

    subject_scr, body_scr = render_screener_summary_email(
        pool_results={
            "A": [
                {
                    "inst_id": "BTC-USDT-SWAP",
                    "regime": "UPTREND",
                    "divergence_kind": "BULL_REGULAR",
                    "score": 92,
                }
            ],
            "B": [
                {
                    "inst_id": "SOL-USDT-SWAP",
                    "regime": "RANGE",
                    "divergence_kind": "BEAR_REGULAR",
                    "score": 85,
                }
            ],
        },
        mode="demo",
    )
    assert "选币摘要" in subject_scr
    assert "SOL-USDT-SWAP" in body_scr


def test_limiter_rate_limit() -> None:
    """测试每小时最多 30 封限频逻辑。"""
    limiter = NotificationLimiter(hourly_limit=3)
    base_time = 100000.0

    # 前 3 封可以通过
    assert limiter.can_send(now=base_time)
    limiter.record_send(now=base_time)
    assert limiter.can_send(now=base_time + 10)
    limiter.record_send(now=base_time + 10)
    assert limiter.can_send(now=base_time + 20)
    limiter.record_send(now=base_time + 20)

    # 第 4 封应被拦截
    assert not limiter.can_send(now=base_time + 30)

    # 1 小时后应解除拦截
    assert limiter.can_send(now=base_time + 3601.0)


def test_limiter_dedup() -> None:
    """测试同类消息去重逻辑。"""
    limiter = NotificationLimiter()
    key = "risk:drawdown:kill"
    t0 = 10000.0

    assert not limiter.is_duplicate(key, cooldown_seconds=300, now=t0)
    limiter.record_send(dedup_key=key, now=t0)

    # 冷却期内（60秒后）算重复
    assert limiter.is_duplicate(key, cooldown_seconds=300, now=t0 + 60)

    # 超过 300 秒后不再算重复
    assert not limiter.is_duplicate(key, cooldown_seconds=300, now=t0 + 301)


@pytest.mark.asyncio
async def test_email_notifier_dry_run_and_dedup() -> None:
    """测试 EmailNotifier 在 dry_run 下的发送与去重。"""
    settings = EmailSettings(enabled=True, hourly_rate_limit=5)
    notifier = EmailNotifier(settings=settings, dry_run=True)

    # 第一次发送成功
    ok1 = await notifier.send_signal_alert(
        inst_id="BTC-USDT-SWAP",
        side="long",
        price="65000",
        stop_loss="63000",
        take_profit="68000",
        reason="4H 背离",
        strategy_name="S1",
        score=90,
    )
    assert ok1 is True
    assert len(notifier.sent_history) == 1

    # 相同信号在冷却期内被去重跳过
    ok2 = await notifier.send_signal_alert(
        inst_id="BTC-USDT-SWAP",
        side="long",
        price="65000",
        stop_loss="63000",
        take_profit="68000",
        reason="4H 背离",
        strategy_name="S1",
        score=90,
    )
    assert ok2 is False
    assert len(notifier.sent_history) == 1


@pytest.mark.asyncio
async def test_outbox_queue_and_retry() -> None:
    """测试发件箱保存、补发与失败重试逻辑。"""
    outbox = OutboxManager()

    msg_id = await outbox.save_failed_message(
        subject="[DQ] 测试补发",
        body="<html>内容</html>",
    )
    assert msg_id.startswith("outbox_")

    # 模拟补发回调函数，第一次失败，第二次成功
    call_count = 0

    async def mock_sender(subject: str, body: str) -> bool:
        nonlocal call_count
        call_count += 1
        return call_count > 1

    # 第 1 次处理：失败，retries + 1
    sent = await outbox.process_outbox(mock_sender)
    assert sent == 0
    assert outbox._fallback_queue[0]["status"] == "pending"
    assert outbox._fallback_queue[0]["retries"] == 1

    # 第 2 次处理：成功，status 改为 sent
    sent2 = await outbox.process_outbox(mock_sender)
    assert sent2 == 1
    assert outbox._fallback_queue[0]["status"] == "sent"
