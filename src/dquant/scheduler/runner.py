"""M05 调度器：按背离周期 K 线收盘触发选币。

采用轮询模型：调用方以固定间隔调用 poll(now_ms)，调度器判断是否跨过了新的
收盘边界。同一收盘只触发一次；进程重启后的首次 poll 只补跑最新收盘，更早的
错过的收盘记录告警后跳过（补跑旧收盘只会产生过期候选）。
run_scheduled 失败时不推进水位，下一次 poll 会自动重试同一收盘。
"""

from __future__ import annotations

import asyncio
import logging

from dquant.contracts import CandleBar
from dquant.core.screener import ScreenerRunRecord
from dquant.scheduler.service import ScreenerService, utc_now_ms

logger = logging.getLogger(__name__)

BAR_PERIOD_MS: dict[CandleBar, int] = {
    CandleBar.FIVE_MINUTE: 300_000,
    CandleBar.FIFTEEN_MINUTE: 900_000,
    CandleBar.ONE_HOUR: 3_600_000,
    CandleBar.FOUR_HOUR: 14_400_000,
    CandleBar.ONE_DAY: 86_400_000,
}


def bar_period_ms(bar: CandleBar) -> int:
    """K 线周期对应的毫秒数。"""

    try:
        return BAR_PERIOD_MS[bar]
    except KeyError as exc:
        raise ValueError(f"unsupported bar for scheduling: {bar}") from exc


def last_closed_bar_ts(now_ms: int, bar: CandleBar) -> int:
    """最近一根已收盘 K 线的收盘时间戳（UTC 毫秒，对齐周期边界）。"""

    period = bar_period_ms(bar)
    return now_ms - now_ms % period


def next_close_ts(now_ms: int, bar: CandleBar) -> int:
    """下一根 K 线的收盘时间戳。"""

    return last_closed_bar_ts(now_ms, bar) + bar_period_ms(bar)


class ScreenerScheduler:
    """收盘触发的选币调度器。

    poll 适合被外部事件循环以固定间隔调用；run_forever 提供内置间隔的便捷循环。
    """

    def __init__(
        self,
        service: ScreenerService,
        bar: CandleBar = CandleBar.FOUR_HOUR,
    ) -> None:
        self._service = service
        self._bar = bar
        self._last_close_ts: int | None = None

    async def poll(self, now_ms: int) -> ScreenerRunRecord | None:
        """若跨过了新的收盘边界则触发一次扫描，否则返回 None。

        只有扫描成功才推进水位；失败时抛出原异常，下一轮 poll 重试同一收盘。
        """

        period = bar_period_ms(self._bar)
        latest_close = last_closed_bar_ts(now_ms, self._bar)
        if self._last_close_ts is not None and latest_close <= self._last_close_ts:
            return None
        if self._last_close_ts is not None:
            missed = latest_close // period - self._last_close_ts // period - 1
            if missed > 0:
                logger.warning(
                    "错过 %d 个 %s 收盘，只补跑最新收盘 %s",
                    missed,
                    self._bar.value,
                    latest_close,
                )
        record = await self._service.run_scheduled(latest_close)
        self._last_close_ts = latest_close
        return record

    async def run_manual(self) -> ScreenerRunRecord:
        """手动触发一次扫描（控制台按钮等场景）。"""

        return await self._service.run_manual()

    async def run_forever(
        self,
        *,
        interval_seconds: float = 30.0,
        stop_event: asyncio.Event | None = None,
    ) -> None:
        """以固定间隔轮询，直到 stop_event 被置位。"""

        stop = stop_event if stop_event is not None else asyncio.Event()
        while not stop.is_set():
            await self.poll(utc_now_ms())
            try:
                await asyncio.wait_for(stop.wait(), timeout=interval_seconds)
            except TimeoutError:
                continue
