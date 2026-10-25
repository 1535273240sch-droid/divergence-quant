"""行情数据校验、指纹与事件构建。"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from dquant.contracts import Candle, CandleBar

from .config import BAR_RANK, bar_duration_ms, close_ts_ms, decimal_key, is_bar_aligned
from .errors import BacktestDataError
from .models import StreamQuality


@dataclass(frozen=True)
class BarEvent:
    """按收盘时间排序的一根 K 线事件。"""

    close_ts_ms: int
    rank: int
    inst_id: str
    bar: CandleBar
    candle: Candle
    index: int


def normalize_market_data(
    data: Mapping[str, Mapping[CandleBar, Sequence[Candle]]],
) -> dict[str, dict[CandleBar, tuple[Candle, ...]]]:
    """复制行情数据为不可变元组，避免运行期间被外部修改。"""

    if not data:
        raise BacktestDataError("行情数据为空")
    normalized: dict[str, dict[CandleBar, tuple[Candle, ...]]] = {}
    for inst_id, streams in data.items():
        if not inst_id:
            raise BacktestDataError("inst_id 不能为空")
        if not streams:
            raise BacktestDataError(f"{inst_id} 没有行情流")
        copied: dict[CandleBar, tuple[Candle, ...]] = {}
        for bar, candles in streams.items():
            if not candles:
                raise BacktestDataError(f"{inst_id} {bar.value} 行情流为空")
            copied[bar] = tuple(candles)
        normalized[inst_id] = copied
    return normalized


def validate_market_data(
    data: Mapping[str, Mapping[CandleBar, Sequence[Candle]]],
) -> tuple[tuple[StreamQuality, ...], tuple[str, ...]]:
    """校验 K 线顺序、确认状态与周期对齐，并返回数据质量摘要。"""

    qualities: list[StreamQuality] = []
    warnings: list[str] = []
    for inst_id, streams in data.items():
        for bar, candles in streams.items():
            _validate_stream(inst_id, bar, candles)
            first = candles[0]
            last = candles[-1]
            duration = bar_duration_ms(bar)
            expected = (last.ts_ms - first.ts_ms) // duration + 1
            missing = max(0, expected - len(candles))
            qualities.append(
                StreamQuality(
                    inst_id=inst_id,
                    bar=bar,
                    candle_count=len(candles),
                    first_ts_ms=first.ts_ms,
                    last_ts_ms=last.ts_ms,
                    missing_bars=missing,
                )
            )
            if missing:
                warnings.append(
                    f"{inst_id} {bar.value} 行情缺失 {missing} 根 K 线，回测按实际数据推进"
                )
    qualities.sort(key=lambda item: (item.inst_id, BAR_RANK[item.bar]))
    return tuple(qualities), tuple(warnings)


def build_bar_events(
    data: Mapping[str, Mapping[CandleBar, Sequence[Candle]]],
    *,
    max_close_ts: int,
) -> list[BarEvent]:
    """生成按收盘时间、周期优先级、交易对排序的事件列表。"""

    events: list[BarEvent] = []
    for inst_id in sorted(data):
        for bar in sorted(data[inst_id], key=lambda item: BAR_RANK[item]):
            rank = BAR_RANK[bar]
            for index, candle in enumerate(data[inst_id][bar]):
                event_close = close_ts_ms(candle.ts_ms, bar)
                if event_close > max_close_ts:
                    continue
                events.append(
                    BarEvent(
                        close_ts_ms=event_close,
                        rank=rank,
                        inst_id=inst_id,
                        bar=bar,
                        candle=candle,
                        index=index,
                    )
                )
    events.sort(
        key=lambda event: (
            event.close_ts_ms,
            event.rank,
            event.inst_id,
            event.index,
        )
    )
    if not events:
        raise BacktestDataError("交易窗口内没有可用 K 线")
    return events


def execution_time_range(
    data: Mapping[str, Mapping[CandleBar, Sequence[Candle]]],
    execution_bar: CandleBar,
) -> tuple[int, int]:
    """返回执行周期行情的最早收盘时间和最晚收盘时间。"""

    closes: list[int] = []
    for streams in data.values():
        for candle in streams.get(execution_bar, ()):
            closes.append(close_ts_ms(candle.ts_ms, execution_bar))
    if not closes:
        raise BacktestDataError(f"缺少执行周期 {execution_bar.value} 的 K 线")
    return min(closes), max(closes)


def missing_execution_streams(
    data: Mapping[str, Mapping[CandleBar, Sequence[Candle]]],
    execution_bar: CandleBar,
) -> tuple[str, ...]:
    """返回没有执行周期行情、因而无法撮合的交易对。"""

    return tuple(
        sorted(inst_id for inst_id, streams in data.items() if not streams.get(execution_bar))
    )


def fingerprint_market_data(
    data: Mapping[str, Mapping[CandleBar, Sequence[Candle]]],
) -> str:
    """计算行情数据指纹，用于复现实验记录。"""

    hasher = hashlib.sha256()
    for inst_id in sorted(data):
        for bar in sorted(data[inst_id], key=lambda item: BAR_RANK[item]):
            for candle in data[inst_id][bar]:
                parts = (
                    inst_id,
                    bar.value,
                    str(candle.ts_ms),
                    decimal_key(candle.open),
                    decimal_key(candle.high),
                    decimal_key(candle.low),
                    decimal_key(candle.close),
                    decimal_key(candle.volume),
                    decimal_key(candle.quote_volume),
                    "1" if candle.confirmed else "0",
                )
                hasher.update("|".join(parts).encode("utf-8"))
                hasher.update(b"\n")
    return hasher.hexdigest()


def _validate_stream(inst_id: str, bar: CandleBar, candles: Sequence[Candle]) -> None:
    previous_ts: int | None = None
    for candle in candles:
        if candle.inst_id != inst_id:
            raise BacktestDataError(
                f"{inst_id} {bar.value} 中出现其他交易对的 K 线: {candle.inst_id}"
            )
        if candle.bar != bar:
            raise BacktestDataError(f"{inst_id} {bar.value} 行情流中出现 {candle.bar.value} K 线")
        if not candle.confirmed:
            raise BacktestDataError(
                f"{inst_id} {bar.value} ts={candle.ts_ms} 的 K 线未确认，禁止进入回测"
            )
        if not is_bar_aligned(candle.ts_ms, bar):
            raise BacktestDataError(
                f"{inst_id} {bar.value} ts={candle.ts_ms} 未按周期对齐，1D 必须对齐北京时间 00:00"
            )
        if previous_ts is not None:
            if candle.ts_ms <= previous_ts:
                raise BacktestDataError(
                    f"{inst_id} {bar.value} K 线时间未严格递增: {previous_ts} -> {candle.ts_ms}"
                )
        previous_ts = candle.ts_ms
