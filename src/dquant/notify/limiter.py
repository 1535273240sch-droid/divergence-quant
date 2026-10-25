"""邮件通知限频与去重模块。

实现：
1. 滑动窗口限频（默认每小时最多 30 封）。
2. 同类消息时间窗口去重（防重复刷屏）。
3. 选币结果批量合并缓冲器。
"""

from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass, field


@dataclass
class NotificationLimiter:
    """通知限频与去重控制器。"""

    hourly_limit: int = 30
    _timestamps: deque[float] = field(default_factory=deque)
    _dedup_cache: dict[str, float] = field(default_factory=dict)

    def can_send(self, now: float | None = None) -> bool:
        """检查当前是否在每小时限频阈值内。"""
        if now is None:
            now = time.time()
        one_hour_ago = now - 3600.0

        # 清除 1 小时前的记录
        while self._timestamps and self._timestamps[0] <= one_hour_ago:
            self._timestamps.popleft()

        return len(self._timestamps) < self.hourly_limit

    def is_duplicate(
        self, dedup_key: str, cooldown_seconds: float = 300.0, now: float | None = None
    ) -> bool:
        """检查指定 key 的消息在冷却期内是否已发送过。"""
        if now is None:
            now = time.time()

        last_time = self._dedup_cache.get(dedup_key)
        if last_time is not None and (now - last_time) < cooldown_seconds:
            return True
        return False

    def record_send(self, dedup_key: str | None = None, now: float | None = None) -> None:
        """记录一次成功发送。"""
        if now is None:
            now = time.time()

        self._timestamps.append(now)
        if dedup_key:
            self._dedup_cache[dedup_key] = now
            # 清理过期的 dedup 缓存（超过 24 小时）
            cutoff = now - 86400.0
            expired_keys = [k for k, v in self._dedup_cache.items() if v < cutoff]
            for k in expired_keys:
                del self._dedup_cache[k]

    def reset(self) -> None:
        """重置状态（主要用于测试）。"""
        self._timestamps.clear()
        self._dedup_cache.clear()
