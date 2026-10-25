"""发件箱补发服务与重试管理。

当邮件发送由于网络波动、SMTP 临时不可达等原因失败时：
1. 立即重试最多 3 次；
2. 若依然失败，持久化写入 `notify_outbox` 数据表；
3. 后台守护任务或下一次调度轮询 `notify_outbox` 进行补发，确保关键交易通知零丢失。
"""

from __future__ import annotations

import datetime
import uuid
from collections.abc import Awaitable, Callable
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from dquant.db.models import NotifyOutboxModel


class OutboxManager:
    """通知发件箱管理器。"""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession] | None = None) -> None:
        self._session_factory = session_factory
        # 降级内存队列（在没有数据库连接的环境下也能工作）
        self._fallback_queue: list[dict[str, Any]] = []

    async def save_failed_message(
        self,
        *,
        subject: str,
        body: str,
        channel: str = "email",
        attachments: list[str] | None = None,
        retries: int = 0,
        session: AsyncSession | None = None,
    ) -> str:
        """将发送失败的消息存入发件箱。返回记录 ID。"""
        msg_id = f"outbox_{uuid.uuid4().hex[:16]}"
        now = datetime.datetime.now(datetime.UTC)
        att = attachments or []

        if session is not None:
            model = NotifyOutboxModel(
                id=msg_id,
                channel=channel,
                subject=subject,
                body=body,
                attachments=att,
                retries=retries,
                status="pending",
                created_at=now,
            )
            session.add(model)
            return msg_id

        if self._session_factory is not None:
            async with self._session_factory() as sess:
                async with sess.begin():
                    model = NotifyOutboxModel(
                        id=msg_id,
                        channel=channel,
                        subject=subject,
                        body=body,
                        attachments=att,
                        retries=retries,
                        status="pending",
                        created_at=now,
                    )
                    sess.add(model)
                    return msg_id

        # 降级存储到内存队列
        self._fallback_queue.append(
            {
                "id": msg_id,
                "channel": channel,
                "subject": subject,
                "body": body,
                "attachments": att,
                "retries": retries,
                "status": "pending",
                "created_at": now,
            }
        )
        return msg_id

    async def process_outbox(
        self,
        send_callback: Callable[[str, str], Awaitable[bool]],
        max_batch: int = 20,
    ) -> int:
        """处理发件箱中待补发的消息。

        参数:
            send_callback: 异步发送函数，入参为 (subject, body)，返回是否成功布尔值。
            max_batch: 本批次最多处理条数。

        返回:
            本批次成功补发的消息数量。
        """
        success_count = 0

        # 处理数据库中的消息
        if self._session_factory is not None:
            async with self._session_factory() as session:
                stmt = (
                    select(NotifyOutboxModel)
                    .where(NotifyOutboxModel.status == "pending")
                    .order_by(NotifyOutboxModel.created_at.asc())
                    .limit(max_batch)
                )
                result = await session.execute(stmt)
                records = result.scalars().all()

                for record in records:
                    success = False
                    try:
                        success = await send_callback(record.subject, record.body)
                    except Exception:
                        success = False

                    if success:
                        record.status = "sent"
                        success_count += 1
                    else:
                        record.retries += 1
                        if record.retries >= 3:
                            record.status = "failed"
                await session.commit()
                return success_count

        # 处理降级内存队列
        pending_items = [item for item in self._fallback_queue if item["status"] == "pending"][
            :max_batch
        ]
        for item in pending_items:
            success = False
            try:
                success = await send_callback(item["subject"], item["body"])
            except Exception:
                success = False

            if success:
                item["status"] = "sent"
                success_count += 1
            else:
                item["retries"] += 1
                if item["retries"] >= 3:
                    item["status"] = "failed"

        return success_count
