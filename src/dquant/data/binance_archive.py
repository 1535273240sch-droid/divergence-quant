"""Binance 公开历史归档解析与 UTC+8 日线重聚合（计划书 §5.2.1，仅用于长周期回测）。

数据来源为 Binance 公开数据仓库（data.binance.vision）的月度 ZIP 归档，属于公开
历史数据，不涉及任何账户与密钥。聚合规则：以北京时间 00:00 为日线边界，把 UTC
小时级 K 线重聚合为 UTC+8 日线，保证与 OKX ``bar=1D`` 的时间轴一致。
"""

from __future__ import annotations

import csv
import io
import zipfile
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from decimal import Decimal

from dquant.contracts import Candle, CandleBar
from dquant.data.candles import DAY_MS, validate_bar_alignment

ARCHIVE_BASE_URL = "https://data.binance.vision/data/spot/monthly/klines"
SUPPORTED_INTERVAL = "1h"
# 部分月份的归档把 open_time 写成微秒；大于该阈值按微秒处理换算回毫秒。
_MICROSECOND_TS_THRESHOLD = 10**14

Fetcher = Callable[[str], bytes]


@dataclass(frozen=True)
class HourBar:
    """归档中的一根小时级 K 线。"""

    ts_ms: int
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal
    quote_volume: Decimal


def build_archive_url(symbol: str, interval: str, month: str) -> str:
    """构造月度归档 ZIP 的下载地址，例如 ``BTCUSDT/1h/BTCUSDT-1h-2024-01.zip``。"""

    _validate_month(month)
    if interval != SUPPORTED_INTERVAL:
        raise ValueError(f"当前只支持 {SUPPORTED_INTERVAL} 归档重聚合，收到 {interval!r}")
    return f"{ARCHIVE_BASE_URL}/{symbol}/{interval}/{symbol}-{interval}-{month}.zip"


def list_month_range(start: str, end: str) -> list[str]:
    """列出 ``[start, end]`` 之间的月份（含两端），格式 ``YYYY-MM``。"""

    start_year, start_month = _validate_month(start)
    end_year, end_month = _validate_month(end)
    if (start_year, start_month) > (end_year, end_month):
        raise ValueError(f"start {start} 不能晚于 end {end}")
    months: list[str] = []
    year, month = start_year, start_month
    while (year, month) <= (end_year, end_month):
        months.append(f"{year:04d}-{month:02d}")
        month += 1
        if month > 12:
            year += 1
            month = 1
    return months


def parse_kline_csv(data: bytes) -> list[HourBar]:
    """解析归档 CSV。跳过表头；时间戳兼容毫秒与微秒两种格式。"""

    text = data.decode("utf-8")
    bars: list[HourBar] = []
    reader = csv.reader(io.StringIO(text))
    for line_no, row in enumerate(reader, start=1):
        if not row or not row[0].strip().lstrip("-").isdigit():
            continue  # 表头或空行
        if len(row) < 7:
            raise ValueError(f"归档第 {line_no} 行字段数不足: {row!r}")
        try:
            ts = int(row[0])
            if ts >= _MICROSECOND_TS_THRESHOLD:
                ts //= 1000
            bars.append(
                HourBar(
                    ts_ms=ts,
                    open=Decimal(row[1]),
                    high=Decimal(row[2]),
                    low=Decimal(row[3]),
                    close=Decimal(row[4]),
                    volume=Decimal(row[5]),
                    quote_volume=Decimal(row[7]) if len(row) > 7 else Decimal(row[6]),
                )
            )
        except (ValueError, ArithmeticError) as exc:
            raise ValueError(f"归档第 {line_no} 行解析失败: {row!r}: {exc}") from exc
    return bars


def _shanghai_day_index(ts_ms: int) -> int:
    """北京时间日期序号（以 1970-01-01 北京时间为基准的天数）。"""

    return (ts_ms + 8 * 3_600_000) // DAY_MS


def aggregate_daily_shanghai(inst_id: str, bars: Sequence[HourBar]) -> list[Candle]:
    """把小时级 K 线重聚合为 UTC+8 日线（北京时间 00:00 开盘），升序返回。"""

    ordered = sorted(bars, key=lambda b: b.ts_ms)
    grouped: dict[int, list[HourBar]] = {}
    for bar in ordered:
        grouped.setdefault(_shanghai_day_index(bar.ts_ms), []).append(bar)

    candles: list[Candle] = []
    for day_index, day_bars in sorted(grouped.items()):
        day_open_ms = day_index * DAY_MS - 8 * 3_600_000
        validate_bar_alignment(CandleBar.ONE_DAY, day_open_ms)
        candles.append(
            Candle(
                inst_id=inst_id,
                bar=CandleBar.ONE_DAY,
                ts_ms=day_open_ms,
                open=day_bars[0].open,
                high=max(b.high for b in day_bars),
                low=min(b.low for b in day_bars),
                close=day_bars[-1].close,
                volume=sum((b.volume for b in day_bars), Decimal("0")),
                quote_volume=sum((b.quote_volume for b in day_bars), Decimal("0")),
                confirmed=True,
            )
        )
    return candles


def unpack_archive_zip(data: bytes, symbol: str, interval: str, month: str) -> bytes:
    """从月度 ZIP 中取出 K 线 CSV 内容。"""

    expected_name = f"{symbol}-{interval}-{month}.csv"
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        names = archive.namelist()
        target = next((n for n in names if n.split("/")[-1] == expected_name), None)
        if target is None:
            raise ValueError(f"归档 {names} 中找不到 {expected_name}")
        return archive.read(target)


def collect_symbol_daily(
    fetcher: Fetcher,
    symbol: str,
    inst_id: str,
    start_month: str,
    end_month: str,
) -> list[Candle]:
    """拉取指定区间的月度归档并聚合成 UTC+8 日线。``fetcher`` 由调用方注入。"""

    bars: list[HourBar] = []
    for month in list_month_range(start_month, end_month):
        url = build_archive_url(symbol, SUPPORTED_INTERVAL, month)
        payload = unpack_archive_zip(fetcher(url), symbol, SUPPORTED_INTERVAL, month)
        bars.extend(parse_kline_csv(payload))
    return aggregate_daily_shanghai(inst_id, bars)


def _validate_month(month: str) -> tuple[int, int]:
    parts = month.split("-")
    if len(parts) != 2:
        raise ValueError(f"月份 {month!r} 不是合法的 YYYY-MM 格式")
    year, month_value = int(parts[0]), int(parts[1])
    if not (1 <= month_value <= 12):
        raise ValueError(f"月份 {month!r} 的月份数字必须在 1..12")
    return year, month_value
