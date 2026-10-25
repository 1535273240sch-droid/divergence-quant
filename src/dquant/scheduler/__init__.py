"""调度边界（由 M05 实现）。

对外入口：
- ScreenerRunStore：screener_runs / screener_results 读写。
- ScreenerService：组装输入、运行选币并落库。
- ScreenerScheduler：按 K 线收盘触发扫描，支持手动触发。
"""

from .persistence import (
    ScreenerRunStore,
    ScreenerStoreError,
    manual_run_id,
    parse_run_id,
    record_from_rows,
    result_rows_from_record,
    run_row_from_record,
    scheduled_run_id,
)
from .runner import (
    BAR_PERIOD_MS,
    ScreenerScheduler,
    bar_period_ms,
    last_closed_bar_ts,
    next_close_ts,
)
from .service import InputProvider, ScreenerService, utc_now_ms

__all__ = [
    "BAR_PERIOD_MS",
    "InputProvider",
    "ScreenerRunStore",
    "ScreenerScheduler",
    "ScreenerService",
    "ScreenerStoreError",
    "bar_period_ms",
    "last_closed_bar_ts",
    "manual_run_id",
    "next_close_ts",
    "parse_run_id",
    "record_from_rows",
    "result_rows_from_record",
    "run_row_from_record",
    "scheduled_run_id",
    "utc_now_ms",
]
