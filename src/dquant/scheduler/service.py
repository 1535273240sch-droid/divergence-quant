"""M05 选币服务：组装输入、运行选币并落库。

ScreenerService 不关心输入从哪里来：行情/趋势/背离的采集由上游模块提供，
这里通过注入的 input_provider 拿到 ScreenerInputs。定时扫描使用确定性 run_id，
同一根 K 线收盘重复触发会替换同一条运行记录；手动触发每次都是独立记录。
"""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable
from dataclasses import replace

from dquant.core.screener import (
    TRIGGER_MANUAL,
    TRIGGER_SCHEDULED,
    ScreenerConfig,
    ScreenerInputs,
    ScreenerRunRecord,
    run_screener,
)
from dquant.scheduler.persistence import ScreenerRunStore, manual_run_id, scheduled_run_id


def utc_now_ms() -> int:
    """当前 UTC 毫秒时间戳。"""

    return time.time_ns() // 1_000_000


InputProvider = Callable[[], Awaitable[ScreenerInputs]]


class ScreenerService:
    """一次完整扫描：取输入 -> 纯函数选币 -> 原子落库。"""

    def __init__(
        self,
        *,
        store: ScreenerRunStore,
        input_provider: InputProvider,
        config: ScreenerConfig | None = None,
        clock: Callable[[], int] = utc_now_ms,
    ) -> None:
        self._store = store
        self._input_provider = input_provider
        self._config = config if config is not None else ScreenerConfig()
        self._clock = clock

    async def run_scheduled(self, close_ts_ms: int) -> ScreenerRunRecord:
        """4H 收盘触发的扫描，使用确定性 run_id。"""

        return await self._execute(scheduled_run_id(close_ts_ms), TRIGGER_SCHEDULED, close_ts_ms)

    async def run_manual(self) -> ScreenerRunRecord:
        """手动触发的扫描，每次都是新的运行记录。"""

        return await self._execute(manual_run_id(), TRIGGER_MANUAL, None)

    async def _execute(
        self,
        run_id: str,
        trigger: str,
        close_ts_ms: int | None,
    ) -> ScreenerRunRecord:
        started_ts_ms = self._clock()
        inputs = await self._input_provider()
        record = run_screener(
            inputs,
            self._config,
            run_id=run_id,
            trigger=trigger,
            now_ts_ms=self._clock(),
            close_ts_ms=close_ts_ms,
        )
        finished_ts_ms = self._clock()
        record = replace(record, started_ts_ms=started_ts_ms, finished_ts_ms=finished_ts_ms)
        await self._store.save_run(record)
        return record
