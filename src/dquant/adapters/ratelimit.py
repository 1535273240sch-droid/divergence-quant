"""令牌桶限流器（计划书 §5.1.2 限频）。

适配器在每次请求前调用 :meth:`TokenBucketLimiter.acquire` 消耗一个令牌；
令牌按固定速率补充，突发量由桶容量决定。时钟与睡眠函数可注入，便于确定性测试。
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable

ClockFn = Callable[[], float]
SleepFn = Callable[[float], Awaitable[None]]


class TokenBucketLimiter:
    """按固定速率补充令牌的异步限流器。"""

    def __init__(
        self,
        rate_per_sec: float,
        capacity: float | None = None,
        *,
        clock: ClockFn = time.monotonic,
        sleep: SleepFn = asyncio.sleep,
    ) -> None:
        if rate_per_sec <= 0:
            raise ValueError("rate_per_sec must be positive")
        if capacity is None:
            capacity = max(1.0, rate_per_sec)
        if capacity <= 0:
            raise ValueError("capacity must be positive")

        self._rate = float(rate_per_sec)
        self._capacity = float(capacity)
        self._tokens = float(capacity)
        self._last = clock()
        self._clock = clock
        self._sleep = sleep
        self._lock = asyncio.Lock()

    async def acquire(self) -> None:
        """消耗一个令牌；令牌不足时等待补充。"""

        async with self._lock:
            while True:
                now = self._clock()
                elapsed = max(0.0, now - self._last)
                self._last = now
                self._tokens = min(self._capacity, self._tokens + elapsed * self._rate)
                if self._tokens >= 1:
                    self._tokens -= 1
                    return
                delay = (1 - self._tokens) / self._rate
                await self._sleep(delay)
