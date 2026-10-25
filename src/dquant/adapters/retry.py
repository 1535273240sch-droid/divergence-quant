"""指数退避重试策略（计划书 §5.1.2：429 / 50011 错误码指数退避重试）。

只对 :class:`~dquant.adapters.errors.ExchangeError` 且 ``retriable=True`` 的错误重试；
睡眠函数可注入，便于确定性测试。
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable

from dquant.adapters.errors import ExchangeError

SleepFn = Callable[[float], Awaitable[None]]


class RetryPolicy:
    """带指数退避的同步调用编排策略。"""

    def __init__(
        self,
        *,
        max_attempts: int = 3,
        base_delay_sec: float = 0.5,
        max_delay_sec: float = 8.0,
        sleep: SleepFn = asyncio.sleep,
    ) -> None:
        if max_attempts < 1:
            raise ValueError("max_attempts must be at least 1")
        if base_delay_sec <= 0:
            raise ValueError("base_delay_sec must be positive")
        if max_delay_sec < base_delay_sec:
            raise ValueError("max_delay_sec must not be smaller than base_delay_sec")

        self._max_attempts = max_attempts
        self._base_delay = base_delay_sec
        self._max_delay = max_delay_sec
        self._sleep = sleep

    async def run(self, operation: Callable[[], Awaitable]) -> object:
        """执行 ``operation``，遇到可重试错误按指数退避重试。

        返回类型与 ``operation`` 的返回类型一致；用 ``object`` 标注以保持简单，
        调用方拿到结果后自行按具体类型使用。
        """

        last_error: ExchangeError | None = None
        for attempt in range(1, self._max_attempts + 1):
            try:
                return await operation()
            except ExchangeError as exc:
                if not exc.retriable or attempt == self._max_attempts:
                    raise
                last_error = exc
                delay = min(self._base_delay * (2 ** (attempt - 1)), self._max_delay)
                await self._sleep(delay)
        raise AssertionError("unreachable") from last_error
