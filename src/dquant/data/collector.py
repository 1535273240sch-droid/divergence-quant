"""历史补数、增量更新与缺口修复（计划书 §5.2.1、§5.2.3、T1.5）。

编排层把"适配器拉数"和"数据库写入"串起来，全部为同步执行：

- :class:`SyncCandleSource` 是采集端的最小协议，方便用假适配器做确定性测试；
- :class:`AsyncAdapterRunner` 把异步 :class:`~dquant.adapters.base.ExchangeAdapter`
  适配成同步协议（内部复用一个 ``asyncio.Runner``），供脚本与定时任务使用；
- 所有函数对重复调用幂等：数据库写入按主键覆盖，不产生重复行。
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Protocol

from sqlalchemy.orm import Session

from dquant.adapters.base import ExchangeAdapter
from dquant.contracts import Candle, CandleBar
from dquant.data.candles import (
    AnomalyReport,
    bar_to_ms,
    detect_missing_candles,
    detect_price_anomalies,
    utc_now_ms,
)
from dquant.data.store import (
    count_candles,
    load_candles_between,
    load_latest_candle_ts,
    upsert_candles,
)

logger = logging.getLogger("dquant.data")

HISTORY_PAGE_LIMIT = 100


class BackfillIncomplete(Exception):
    """在允许的页数内没有把指定区间补完；调用方可直接重试（写入是幂等的）。"""


class SyncCandleSource(Protocol):
    """采集端同步协议。"""

    def fetch_candles(
        self, inst_id: str, bar: CandleBar, since_ms: int | None, limit: int
    ) -> list[Candle]: ...

    def fetch_history_candles(
        self,
        inst_id: str,
        bar: CandleBar,
        *,
        older_than_ms: int | None = None,
        newer_than_ms: int | None = None,
        limit: int,
    ) -> list[Candle]: ...


class AsyncAdapterRunner:
    """把异步适配器包成 :class:`SyncCandleSource`，内部复用单个事件循环。"""

    def __init__(self, adapter: ExchangeAdapter, runner: asyncio.Runner | None = None) -> None:
        self._adapter = adapter
        self._runner = runner if runner is not None else asyncio.Runner()
        self._owns_runner = runner is None

    def __enter__(self) -> AsyncAdapterRunner:
        return self

    def __exit__(self, *exc_info: object) -> None:
        if self._owns_runner:
            self._runner.close()

    def fetch_candles(
        self, inst_id: str, bar: CandleBar, since_ms: int | None, limit: int
    ) -> list[Candle]:
        return self._runner.run(self._adapter.fetch_candles(inst_id, bar, since_ms, limit))

    def fetch_history_candles(
        self,
        inst_id: str,
        bar: CandleBar,
        *,
        older_than_ms: int | None = None,
        newer_than_ms: int | None = None,
        limit: int,
    ) -> list[Candle]:
        return self._runner.run(
            self._adapter.fetch_history_candles(
                inst_id,
                bar,
                older_than_ms=older_than_ms,
                newer_than_ms=newer_than_ms,
                limit=limit,
            )
        )

    def sync_time(self) -> int:
        """同步一次交易所时间，返回毫秒偏移（供传输层校准签名时间戳）。"""

        return self._runner.run(self._adapter.sync_time())


@dataclass
class BackfillStats:
    """一次补数/增量更新的结果统计。"""

    inst_id: str
    bar: str
    fetched_rows: int = 0
    upserted_rows: int = 0
    # 剩余未修复的缺口区间（计划书要求补数后报告缺口）。
    missing_windows: list[tuple[int, int]] = field(default_factory=list)
    anomalies: list[AnomalyReport] = field(default_factory=list)


def _walk_history_pages(
    source: SyncCandleSource,
    inst_id: str,
    bar: CandleBar,
    *,
    cursor_ms: int | None,
    stop_before_ms: int | None,
    max_pages: int,
    what: str,
) -> list[Candle]:
    """从最新往旧分页拉取历史 K 线，直到空页、越过 ``stop_before_ms`` 或页数耗尽。

    ``cursor_ms`` 为初始游标（None 表示从最新开始）；``stop_before_ms`` 表示
    收集到时间戳早于该值即停止。页数耗尽仍未到达则抛 :class:`BackfillIncomplete`。
    """
    collected: list[Candle] = []
    cursor = cursor_ms
    for _ in range(max_pages):
        page = source.fetch_history_candles(
            inst_id,
            bar,
            older_than_ms=cursor,
            newer_than_ms=None,
            limit=HISTORY_PAGE_LIMIT,
        )
        if not page:
            return collected
        ordered = sorted(page, key=lambda c: c.ts_ms)
        collected.extend(ordered)
        oldest = ordered[0].ts_ms
        if stop_before_ms is not None and oldest <= stop_before_ms:
            return collected
        cursor = oldest
    raise BackfillIncomplete(
        f"{what}: {inst_id} {bar.value} 在 {max_pages} 页内未补完，最后游标 {cursor}"
    )


def _report_quality(
    stats: BackfillStats,
    session: Session,
    inst_id: str,
    bar: CandleBar,
    since_ms: int,
    until_ms: int,
    source: SyncCandleSource,
) -> None:
    """对已入库窗口做缺口检测、一次定向补数和异常标记。"""

    stored = load_candles_between(session, inst_id, bar, since_ms, until_ms)
    windows = detect_missing_candles(stored, bar)
    if windows:
        for window_start, window_end in windows:
            repaired = _walk_history_pages(
                source,
                inst_id,
                bar,
                cursor_ms=window_end,
                stop_before_ms=window_start - bar_to_ms(bar),
                max_pages=10,
                what="缺口修复",
            )
            in_window = [c for c in repaired if window_start <= c.ts_ms < window_end]
            if in_window:
                stats.upserted_rows += upsert_candles(session, in_window)
        stored = load_candles_between(session, inst_id, bar, since_ms, until_ms)
    stats.missing_windows = detect_missing_candles(stored, bar)
    stats.anomalies = detect_price_anomalies(stored)
    for anomaly in stats.anomalies:
        logger.warning(
            "异常 K 线 %s %s ts=%s 涨跌幅 %.2f%%（前收 %.8f → 收 %.8f）",
            anomaly.inst_id,
            bar.value,
            anomaly.ts_ms,
            anomaly.change_pct * 100,
            anomaly.prev_close,
            anomaly.close,
        )


def backfill_history(
    source: SyncCandleSource,
    session: Session,
    inst_id: str,
    bar: CandleBar,
    start_ms: int,
    end_ms: int | None = None,
    *,
    max_pages: int = 200,
) -> BackfillStats:
    """把 ``[start_ms, end_ms]`` 的历史 K 线补齐入库。

    ``start_ms`` 通常为北京时间对齐的区间起点；``end_ms`` 缺省表示补到最新。
    重复调用是安全的：数据库按主键覆盖，不会产生重复行。
    """
    if max_pages < 1:
        raise ValueError("max_pages must be at least 1")
    stats = BackfillStats(inst_id=inst_id, bar=bar.value)
    pages = _walk_history_pages(
        source,
        inst_id,
        bar,
        cursor_ms=end_ms,
        stop_before_ms=start_ms,
        max_pages=max_pages,
        what="历史补数",
    )
    in_window = [c for c in pages if start_ms <= c.ts_ms and (end_ms is None or c.ts_ms <= end_ms)]
    stats.fetched_rows = len(pages)
    if in_window:
        stats.upserted_rows = upsert_candles(session, in_window)
    newest = max((c.ts_ms for c in in_window), default=end_ms if end_ms is not None else start_ms)
    _report_quality(stats, session, inst_id, bar, start_ms, newest, source)
    return stats


def update_incremental(
    source: SyncCandleSource,
    session: Session,
    inst_id: str,
    bar: CandleBar,
    *,
    lookback_ms: int,
    now_ms: int | None = None,
    max_pages: int = 20,
) -> BackfillStats:
    """增量拉取最新 K 线并入库（计划书 §5.2.2）。

    数据库已有记录时从最新一根（含）开始重拉：这样未收盘 K 线会在下一轮被
    已收盘数据覆盖；空库时按 ``now - lookback_ms`` 起步补数。窗口内出现的中途
    缺口会自动定向修复一次，剩余缺口写入统计。
    """
    if lookback_ms <= 0:
        raise ValueError("lookback_ms must be positive")
    current = now_ms if now_ms is not None else _default_now_ms()
    latest = load_latest_candle_ts(session, inst_id, bar)
    stats = BackfillStats(inst_id=inst_id, bar=bar.value)
    start = current - lookback_ms if latest is None else latest
    pages = _walk_history_pages(
        source,
        inst_id,
        bar,
        cursor_ms=None,
        stop_before_ms=start,
        max_pages=max_pages,
        what="增量更新",
    )
    in_window = [c for c in pages if start <= c.ts_ms <= current]
    stats.fetched_rows = len(pages)
    if in_window:
        stats.upserted_rows = upsert_candles(session, in_window)
    newest = max((c.ts_ms for c in in_window), default=current)
    _report_quality(stats, session, inst_id, bar, start, newest, source)
    return stats


def repair_missing_windows(
    source: SyncCandleSource,
    session: Session,
    inst_id: str,
    bar: CandleBar,
    windows: Sequence[tuple[int, int]],
) -> int:
    """对指定缺口区间执行定向补数，返回补入的行数。

    交易所确实没有打印某些 K 线时，缺口会保留（拉取结果为空），由调用方决定
    是否告警——这里不做静默填补。
    """
    repaired_rows = 0
    step = bar_to_ms(bar)
    for window_start, window_end in windows:
        pages = _walk_history_pages(
            source,
            inst_id,
            bar,
            cursor_ms=window_end,
            stop_before_ms=window_start - step,
            max_pages=10,
            what="缺口修复",
        )
        in_window = [c for c in pages if window_start <= c.ts_ms < window_end]
        if in_window:
            repaired_rows += upsert_candles(session, in_window)
    return repaired_rows


def load_or_ensure_history_size(
    source: SyncCandleSource,
    session: Session,
    inst_id: str,
    bar: CandleBar,
    min_bars: int,
    *,
    now_ms: int | None = None,
    max_pages: int = 200,
) -> int:
    """确保库里至少有 ``min_bars`` 根 K 线，不足时从最新往前补，返回当前行数。

    供指标预热（MA200 需要足够历史）在补数脚本之外做兜底检查。
    """
    existing = count_candles(session, inst_id, bar)
    if existing >= min_bars:
        return existing
    latest = load_latest_candle_ts(session, inst_id, bar)
    step = bar_to_ms(bar)
    now = latest if latest is not None else (now_ms if now_ms is not None else _default_now_ms())
    start = now - (min_bars - 1) * step
    stats = backfill_history(source, session, inst_id, bar, start, max_pages=max_pages)
    logger.info(
        "预热补数 %s %s: 补入 %d 根，剩余缺口 %d",
        inst_id,
        bar.value,
        stats.upserted_rows,
        len(stats.missing_windows),
    )
    return count_candles(session, inst_id, bar)


def _default_now_ms() -> int:
    return utc_now_ms()


def main(argv: list[str] | None = None) -> int:
    """docker-compose collector 服务入口: 对指定币种周期做一次性增量更新。"""
    import argparse
    import logging

    import asyncio

    from dquant.adapters.okx import OkxAdapter, UrllibTransport
    from dquant.db.models import Base
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    parser = argparse.ArgumentParser(prog="dquant.data.collector", description="OKX 行情增量采集")
    parser.add_argument("--inst", action="append", required=True, help="交易对, 可多次传入")
    parser.add_argument("--bar", action="append", default=None, help="周期 1H/4H/1D, 可多次传入")
    parser.add_argument("--db", default="engine.db")
    parser.add_argument("--lookback-bars", type=int, default=320)
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    from dquant.contracts.types import CandleBar as _CandleBar

    bars = [_CandleBar(b) for b in (args.bar or ["1D", "4H", "1H"])]
    engine = create_engine(f"sqlite:///{args.db}", future=True)
    Base.metadata.create_all(engine)
    factory = sessionmaker(engine, future=True)

    async def _run() -> int:
        transport = UrllibTransport("https://www.okx.com", simulated_trading=True)
        adapter = OkxAdapter(transport, mode="demo")
        transport.clock_offset_ms = await adapter.sync_time()
        runner = AsyncAdapterRunner(adapter)
        total = 0
        with factory() as session:
            for inst_id in args.inst:
                for bar in bars:
                    stats = update_incremental(
                        runner, session, inst_id, bar, lookback_ms=args.lookback_bars * bar_to_ms(bar)
                    )
                    total += stats.upserted_rows
            session.commit()
        return total

    written = asyncio.run(_run())
    logger.info("collector 完成: %d 行写入", written)
    return 0


if __name__ == "__main__":
    import logging
    import sys

    sys.exit(main())
