"""Binance 归档解析与 UTC+8 日线重聚合测试（本地 ZIP fixture，不联网）。"""

import io
import zipfile
from decimal import Decimal

import pytest

from dquant.contracts import CandleBar
from dquant.data.binance_archive import (
    aggregate_daily_shanghai,
    build_archive_url,
    collect_symbol_daily,
    list_month_range,
    parse_kline_csv,
    unpack_archive_zip,
)
from dquant.data.candles import validate_bar_alignment

HOUR = 3_600_000


def hour_row(ts_ms: int, open_: str, high: str, low: str, close: str, vol: str, quote: str) -> str:
    return f"{ts_ms},{open_},{high},{low},{close},{vol},1000,{quote},10,1,1,0"


def test_build_archive_url_and_month_range() -> None:
    assert build_archive_url("BTCUSDT", "1h", "2024-01") == (
        "https://data.binance.vision/data/spot/monthly/klines/BTCUSDT/1h/BTCUSDT-1h-2024-01.zip"
    )
    assert list_month_range("2019-11", "2020-02") == [
        "2019-11",
        "2019-12",
        "2020-01",
        "2020-02",
    ]
    with pytest.raises(ValueError, match="YYYY-MM"):
        build_archive_url("BTCUSDT", "1h", "2024/01")
    with pytest.raises(ValueError, match="1h"):
        build_archive_url("BTCUSDT", "1d", "2024-01")
    with pytest.raises(ValueError, match="start"):
        list_month_range("2024-03", "2024-01")


def test_parse_kline_csv_skips_header_and_normalizes_microseconds() -> None:
    csv_text = (
        "open_time,open,high,low,close,volume,close_time,quote_volume\n"
        + hour_row(1700000000000, "100", "110", "90", "105", "12.5", "1312.5")
        + "\n"
        + hour_row(1700003600000000, "105", "115", "95", "110", "8.5", "909.5")
        + "\n"
    )

    bars = parse_kline_csv(csv_text.encode("utf-8"))

    assert [b.ts_ms for b in bars] == [1700000000000, 1700003600000]
    assert bars[0].open == Decimal("100")
    assert bars[0].close == Decimal("105")
    assert bars[1].quote_volume == Decimal("909.5")


def test_parse_kline_csv_reports_bad_row() -> None:
    bad = hour_row(1700000000000, "oops", "110", "90", "105", "12.5", "1312.5")

    with pytest.raises(ValueError, match="解析失败"):
        parse_kline_csv(bad.encode("utf-8"))


def test_aggregate_daily_shanghai_groups_by_beijing_day() -> None:
    # UTC 16:00/17:00 属于北京次日 00:00/01:00；UTC 40:00 属于再下一个北京日。
    bars = parse_kline_csv(
        (
            hour_row(16 * HOUR, "105", "120", "100", "115", "2", "230")
            + "\n"
            + hour_row(17 * HOUR, "120", "130", "115", "118", "4", "476")
            + "\n"
            + hour_row(40 * HOUR, "118", "128", "112", "125", "5", "610")
            + "\n"
        ).encode("utf-8")
    )

    candles = aggregate_daily_shanghai("BTC-USDT-SWAP", bars)

    assert len(candles) == 2
    first, second = candles
    assert first.bar is CandleBar.ONE_DAY
    # 北京日 1 从 UTC 16:00 开始（北京时间次日 00:00），对齐规则必须通过。
    validate_bar_alignment(CandleBar.ONE_DAY, first.ts_ms)
    assert first.ts_ms == 16 * HOUR
    assert first.open == Decimal("105")
    assert first.close == Decimal("118")
    assert first.high == Decimal("130")
    assert first.low == Decimal("100")
    assert first.volume == Decimal("6")  # 2 + 4
    assert first.quote_volume == Decimal("706")  # 230 + 476
    assert first.confirmed is True
    validate_bar_alignment(CandleBar.ONE_DAY, second.ts_ms)
    assert second.ts_ms == 40 * HOUR
    assert second.open == Decimal("118")


def test_collect_symbol_daily_uses_injected_fetcher() -> None:
    csv_text = (
        hour_row(16 * HOUR, "105", "120", "100", "115", "2", "230")
        + "\n"
        + hour_row(40 * HOUR, "118", "128", "112", "125", "5", "610")
        + "\n"
    )

    def fake_fetcher(url: str) -> bytes:
        assert url.endswith("BTCUSDT-1h-2019-01.zip")
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
            archive.writestr("BTCUSDT-1h-2019-01.csv", csv_text)
        return buffer.getvalue()

    candles = collect_symbol_daily(fake_fetcher, "BTCUSDT", "BTC-USDT-SWAP", "2019-01", "2019-01")

    assert [c.ts_ms for c in candles] == [16 * HOUR, 40 * HOUR]
    assert all(c.inst_id == "BTC-USDT-SWAP" for c in candles)


def test_unpack_archive_zip_rejects_missing_member() -> None:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("OTHER.csv", "x")

    with pytest.raises(ValueError, match="找不到"):
        unpack_archive_zip(buffer.getvalue(), "BTCUSDT", "1h", "2019-01")
