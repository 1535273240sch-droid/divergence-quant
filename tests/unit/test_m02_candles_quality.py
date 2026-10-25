"""K 线周期工具与数据质量检查测试（对齐、缺口、异常）。"""

import pytest
from m02_helpers import make_candle

from dquant.contracts import CandleBar
from dquant.data.candles import (
    AnomalyReport,
    DataQualityError,
    bar_to_ms,
    beijing_date_to_ms,
    detect_missing_candles,
    detect_price_anomalies,
    validate_bar_alignment,
)


def test_bar_duration_ms_mapping() -> None:
    assert bar_to_ms(CandleBar.ONE_HOUR) == 3_600_000
    assert bar_to_ms(CandleBar.FOUR_HOUR) == 14_400_000
    assert bar_to_ms(CandleBar.ONE_DAY) == 86_400_000


def test_bar_alignment_rules() -> None:
    # 北京时间 00:00 = UTC 16:00 → 模 86400000 = 57_600_000。
    validate_bar_alignment(CandleBar.ONE_DAY, 86_400_000 + 57_600_000)
    validate_bar_alignment(CandleBar.FOUR_HOUR, 4 * 3_600_000)
    validate_bar_alignment(CandleBar.ONE_HOUR, 3_600_000)

    with pytest.raises(DataQualityError, match="北京时间"):
        validate_bar_alignment(CandleBar.ONE_DAY, 86_400_000)  # UTC 00:00，不是北京时间开盘
    with pytest.raises(DataQualityError, match="边界"):
        validate_bar_alignment(CandleBar.FOUR_HOUR, 3 * 3_600_000)
    with pytest.raises(DataQualityError, match="边界"):
        validate_bar_alignment(CandleBar.ONE_HOUR, 3_600_001)


def test_beijing_date_to_ms_converts_midnight() -> None:
    ts = beijing_date_to_ms("2024-01-01")
    validate_bar_alignment(CandleBar.ONE_DAY, ts)
    assert ts % 3_600_000 == 0

    with pytest.raises(ValueError, match="YYYY-MM-DD"):
        beijing_date_to_ms("2024/01/01")


def test_detect_missing_candles_reports_inner_gaps() -> None:
    hour = 3_600_000
    candles = [
        make_candle(0 * hour),
        make_candle(1 * hour),
        # 缺 2、3 点两根。
        make_candle(4 * hour),
        make_candle(5 * hour),
    ]

    windows = detect_missing_candles(candles, CandleBar.ONE_HOUR)

    assert windows == [(2 * hour, 4 * hour)]


def test_detect_missing_candles_handles_duplicates_and_multi_inst() -> None:
    hour = 3_600_000
    candles = [
        make_candle(0 * hour),
        make_candle(0 * hour),  # 重复时间戳不产生缺口
        make_candle(2 * hour),
        make_candle(0 * hour, inst_id="ETH-USDT-SWAP"),
    ]

    windows = detect_missing_candles(candles, CandleBar.ONE_HOUR)

    # BTC: 0→2 缺 1；ETH 只有单根无缺口。
    assert windows == [(hour, 2 * hour)]


def test_detect_missing_candles_empty_and_single() -> None:
    assert detect_missing_candles([], CandleBar.ONE_HOUR) == []
    assert detect_missing_candles([make_candle(0)], CandleBar.ONE_HOUR) == []


def test_detect_missing_candles_validates_bar_consistency() -> None:
    with pytest.raises(DataQualityError, match="周期"):
        detect_missing_candles([make_candle(0, bar=CandleBar.FOUR_HOUR)], CandleBar.ONE_HOUR)
    with pytest.raises(DataQualityError, match="边界"):
        detect_missing_candles([make_candle(1)], CandleBar.ONE_HOUR)


def test_detect_price_anomalies_flags_big_moves_only() -> None:
    hour = 3_600_000
    candles = [
        make_candle(0 * hour, close="100"),
        make_candle(1 * hour, close="110"),  # +10%，正常
        make_candle(2 * hour, close="170"),  # +54.5%，异常
        make_candle(3 * hour, close="169"),  # -0.6%，正常
    ]

    reports = detect_price_anomalies(candles, max_change_pct=0.5)

    assert len(reports) == 1
    report = reports[0]
    assert isinstance(report, AnomalyReport)
    assert report.ts_ms == 2 * hour
    assert report.prev_close == pytest.approx(110.0)
    assert report.change_pct == pytest.approx(60 / 110)


def test_detect_price_anomalies_first_bar_never_flagged_and_groups_by_inst() -> None:
    hour = 3_600_000
    candles = [
        make_candle(0 * hour, close="100", inst_id="BTC-USDT-SWAP"),
        make_candle(0 * hour, close="50", inst_id="ETH-USDT-SWAP"),
    ]

    assert detect_price_anomalies(candles) == []

    with pytest.raises(ValueError, match="max_change_pct"):
        detect_price_anomalies(candles, max_change_pct=0)
