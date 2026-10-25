"""审计日志记录与敏感信息脱敏服务。

严格落实：
1. API Key 仅保留后 4 位（例如 `***...abcd`）。
2. 密码、JWT Token、TOTP Secret、API Secret 自动脱敏为 `******`。
3. 敏感操作（登录、确认下单、修改策略参数、AI 提案批准、停止开仓、全部平仓）写入 `AuditLogModel`。
"""

from __future__ import annotations

import copy
import time
import uuid
from typing import Any

from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from dquant.db.models import AuditLogModel

SENSITIVE_FIELD_NAMES = {
    "password",
    "password_hash",
    "token",
    "access_token",
    "refresh_token",
    "secret",
    "jwt_secret",
    "totp_secret",
    "api_secret",
    "passphrase",
}


def sanitize_dict(data: dict[str, Any] | None) -> dict[str, Any] | None:
    """递归脱敏字典中的敏感数据。"""
    if data is None:
        return None

    cleaned: dict[str, Any] = {}
    for key, value in data.items():
        k_lower = key.lower()
        if k_lower in SENSITIVE_FIELD_NAMES or any(
            s in k_lower for s in ("token", "secret", "password", "passphrase")
        ):
            cleaned[key] = "******"
        elif "api_key" in k_lower or "apikey" in k_lower:
            val_str = str(value)
            if len(val_str) > 4:
                cleaned[key] = f"***...{val_str[-4:]}"
            else:
                cleaned[key] = "******"
        elif isinstance(value, dict):
            cleaned[key] = sanitize_dict(value)
        elif isinstance(value, list):
            cleaned[key] = [
                sanitize_dict(item) if isinstance(item, dict) else item for item in value
            ]
        else:
            cleaned[key] = value
    return cleaned


class AuditService:
    """审计日志持久化服务。"""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession] | None = None) -> None:
        self._session_factory = session_factory
        # 降级内存审计日志（无数据库连接时可用）
        self.memory_audit_logs: list[dict[str, Any]] = []

    async def log_action(
        self,
        *,
        user: str,
        action: str,
        before: dict[str, Any] | None = None,
        after: dict[str, Any] | None = None,
        session: AsyncSession | None = None,
    ) -> str:
        """记录一条审计日志。"""
        log_id = f"audit_{uuid.uuid4().hex[:16]}"
        now_ms = int(time.time() * 1000)

        cleaned_before = sanitize_dict(copy.deepcopy(before)) if before else None
        cleaned_after = sanitize_dict(copy.deepcopy(after)) if after else None

        logger.info(
            "AUDIT | user={} | action={} | before={} | after={}",
            user,
            action,
            cleaned_before,
            cleaned_after,
        )

        if session is not None:
            model = AuditLogModel(
                id=log_id,
                ts_ms=now_ms,
                user=user,
                action=action,
                before=cleaned_before,
                after=cleaned_after,
            )
            session.add(model)
            return log_id

        if self._session_factory is not None:
            async with self._session_factory() as sess:
                async with sess.begin():
                    model = AuditLogModel(
                        id=log_id,
                        ts_ms=now_ms,
                        user=user,
                        action=action,
                        before=cleaned_before,
                        after=cleaned_after,
                    )
                    sess.add(model)
                    return log_id

        self.memory_audit_logs.append(
            {
                "id": log_id,
                "ts_ms": now_ms,
                "user": user,
                "action": action,
                "before": cleaned_before,
                "after": cleaned_after,
            }
        )
        return log_id
