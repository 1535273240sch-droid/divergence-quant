"""K 线周期工具、对齐校验与数据质量检查（计划书 §5.2）。

时间规则（与 `协作/00_总控/接口契约.md` 一致）：

- 所有时间戳为 UTC 毫秒。
- 日线使用 OKX ``bar=1D``：北京时间 00:00 开盘，即 UTC 16:00，禁止 ``1Dutc``。
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from dquant.contracts import Candle, CandleBar

BEIJING_TZ = ZoneInfo("Asia/Shanghai")
# 北京时间 00:00 = UTC 16:00，对应 UTC 毫秒对 86400000 取模为 57_600_000。
DAILY_BEIJING_OPEN_OFFSET_MS = 57_600_000
DAY_MS = 86_400_000

BAR_DURATION_MS: dict[CandleBar, int] = {
    CandleBar.FIVE_MINUTE: 300_000,
    CandleBar.FIFTEEN_MINUTE: 900_000,
    CandleBar.ONE_HOUR: 3_600_000,
    CandleBar.FOUR_HOUR: 14_400_000,
    CandleBar.ONE_DAY: DAY_MS,
}


class DataQualityError(Exception):
    """K 线数据不满足质量规则（对齐、周期、序列合法性）。"""


def bar_to_ms(bar: CandleBar) -> int:
    """返回一个周期的毫秒数。"""

    return BAR_DURATION_MS[bar]


def validate_bar_alignment(bar: CandleBar, ts_ms: int) -> None:
    """校验时间戳是否落在该周期的合法边界上。

    - 1H：UTC 整点；4H：UTC 0/4/8/12/16/20 点；
    - 1D：北京时间 00:00（UTC 16:00）。
    """

    if bar == CandleBar.ONE_DAY:
        if ts_ms % DAY_MS != DAILY_BEIJING_OPEN_OFFSET_MS:
            raise DataQualityError(f"日线时间戳 {ts_ms} 未对齐北京时间 00:00（UTC 16:00）")
        return
    if ts_ms % BAR_DURATION_MS[bar] != 0:
        raise DataQualityError(f"{bar.value} 时间戳 {ts_ms} 未落在周期边界上")


def beijing_date_to_ms(text: str) -> int:
    """把 ``YYYY-MM-DD``（北京时间日期）转成 UTC 毫秒时间戳，非法输入显式报错。"""

    try:
        parsed = datetime.strptime(text, "%Y-%m-%d").replace(tzinfo=BEIJING_TZ)
    except ValueError as exc:
        raise ValueError(f"日期 {text!r} 不是合法的 YYYY-MM-DD 格式") from exc
    return int(parsed.timestamp() * 1000)


def utc_now_ms() -> int:
    """当前 UTC 毫秒时间戳。独立成函数便于测试替身替换。"""

    return int(datetime.now(tz=UTC).timestamp() * 1000)


def _group_by_inst(candles: Sequence[Candle]) -> dict[str, list[Candle]]:
    grouped: dict[str, list[Candle]] = defaultdict(list)
    for candle in candles:
        grouped[candle.inst_id].append(candle)
    return grouped


def detect_missing_candles(candles: Sequence[Candle], bar: CandleBar) -> list[tuple[int, int]]:
    """检测时间戳不连续的缺口，返回缺失区间列表。

    返回值为 ``(start_ms, end_ms)`` 的列表，含义为 ``[start_ms, end_ms)`` 之间
    按周期步长应存在但缺失的时间戳；区间两端都按周期对齐。
    """

    step = bar_to_ms(bar)
    missing: list[tuple[int, int]] = []
    for inst_id, group in _group_by_inst(candles).items():
        ts_to_bar: dict[int, CandleBar] = {}
        for candle in group:
            if candle.ts_ms in ts_to_bar and ts_to_bar[candle.ts_ms] != candle.bar:
                raise DataQualityError(
                    f"{inst_id} 时间戳 {candle.ts_ms} 存在不同周期 {candle.bar.value} 的记录"
                )
            ts_to_bar[candle.ts_ms] = candle.bar
        for ts in sorted(ts_to_bar):
            found_bar = ts_to_bar[ts]
            if found_bar != bar:
                raise DataQualityError(
                    f"{inst_id} 时间戳 {ts} 的周期 {found_bar.value} 与查询周期 {bar.value} 不一致"
                )
            validate_bar_alignment(bar, ts)
        unique_ts = sorted(ts_to_bar)
        for prev_ts, next_ts in zip(unique_ts, unique_ts[1:], strict=False):
            expected = prev_ts + step
            if next_ts > expected:
                missing.append((expected, next_ts))
    missing.sort()
    return missing


@dataclass(frozen=True)
class AnomalyReport:
    """异常 K 线标记（计划书 §5.2.3：单根涨跌超过阈值 → 标记并告警）。"""

    inst_id: str
    ts_ms: int
    prev_close: float
    close: float
    change_pct: float


def detect_price_anomalies(
    candles: Sequence[Candle],
    max_change_pct: float = 0.5,
) -> list[AnomalyReport]:
    """检测相邻两根已收盘 K 线之间的涨跌幅是否超过阈值（默认 50%）。

    每个交易对独立按时间排序计算；序列首根没有前值，不会被判为异常。
    """

    if max_change_pct <= 0:
        raise ValueError("max_change_pct must be positive")
    reports: list[AnomalyReport] = []
    for inst_id, group in _group_by_inst(candles).items():
        ordered = sorted(group, key=lambda c: c.ts_ms)
        for prev, cur in zip(ordered, ordered[1:], strict=False):
            change = abs(float(cur.close) / float(prev.close) - 1)
            if change > max_change_pct:
                reports.append(
                    AnomalyReport(
                        inst_id=inst_id,
                        ts_ms=cur.ts_ms,
                        prev_close=float(prev.close),
                        close=float(cur.close),
                        change_pct=change,
                    )
                )
    reports.sort(key=lambda r: (r.inst_id, r.ts_ms))
    return reports
