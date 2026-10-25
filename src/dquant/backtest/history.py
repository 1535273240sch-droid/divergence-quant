"""已收盘行情视图。

策略只能看到已经确认收盘的 K 线。上下文创建时会冻结每个行情流的已收盘数量，
因此即使策略保存旧上下文，也无法在之后读到新的未来 K 线。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from dquant.contracts import Candle, CandleBar


class MarketHistory:
    """引擎内部维护的行情流与收盘游标。"""

    def __init__(self, data: Mapping[str, Mapping[CandleBar, Sequence[Candle]]]) -> None:
        self._data: dict[str, dict[CandleBar, tuple[Candle, ...]]] = {
            inst_id: {bar: tuple(candles) for bar, candles in streams.items()}
            for inst_id, streams in data.items()
        }
        self._closed: dict[tuple[str, CandleBar], int] = {
            (inst_id, bar): 0 for inst_id, streams in self._data.items() for bar in streams
        }

    def mark_closed(self, inst_id: str, bar: CandleBar) -> None:
        key = (inst_id, bar)
        if key not in self._closed:
            raise KeyError(f"未知的行情流: {inst_id} {bar.value}")
        self._closed[key] += 1

    def closed_count(self, inst_id: str, bar: CandleBar) -> int:
        return self._closed[(inst_id, bar)]

    def snapshot(self) -> MarketHistoryView:
        """冻结当前收盘游标，生成只读视图。"""

        return MarketHistoryView(self._data, dict(self._closed))


class MarketHistoryView:
    """上下文持有的只读行情视图。"""

    __slots__ = ("_data", "_closed")

    def __init__(
        self,
        data: Mapping[str, Mapping[CandleBar, Sequence[Candle]]],
        closed: Mapping[tuple[str, CandleBar], int],
    ) -> None:
        self._data = data
        self._closed = dict(closed)

    def candles(
        self,
        inst_id: str,
        bar: CandleBar,
        limit: int | None = None,
    ) -> tuple[Candle, ...]:
        """返回截至视图冻结时刻已收盘的 K 线，最多 `limit` 根。"""

        key = (inst_id, bar)
        if key not in self._closed:
            raise KeyError(f"未知的行情流: {inst_id} {bar.value}")
        if limit is not None and limit <= 0:
            raise ValueError("limit must be positive")
        closed = self._closed[key]
        if limit is None:
            return tuple(self._data[inst_id][bar][:closed])
        return tuple(self._data[inst_id][bar][max(0, closed - limit) : closed])

    def available(self, inst_id: str, bar: CandleBar) -> int:
        """返回视图冻结时该行情流的已收盘根数。"""

        return self._closed[(inst_id, bar)]
