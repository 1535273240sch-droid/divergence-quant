"""最近 K 线缓存（计划书 §5.2.5：每个交易对最近 500 根缓存在 Redis）。

缓存只用于加速选币器读取，不属于权威数据源；数据库才是唯一事实。
提供内存实现（测试/单机兜底）和基于 M01 Redis 客户端的 Redis 实现。
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from typing import Any, Protocol

from dquant.contracts import Candle, CandleBar

CACHE_KEY_PREFIX = "dquant:candles"
DEFAULT_CACHE_SIZE = 500


def cache_key(inst_id: str, bar: CandleBar, *, prefix: str = CACHE_KEY_PREFIX) -> str:
    """构造缓存键：``dquant:candles:{inst_id}:{bar}``。"""

    return f"{prefix}:{inst_id}:{bar.value}"


def merge_latest_candles(
    existing: Sequence[Candle],
    incoming: Sequence[Candle],
    *,
    size: int = DEFAULT_CACHE_SIZE,
) -> list[Candle]:
    """按时间戳合并两组 K 线（新的覆盖旧的），保留最近 ``size`` 根，升序返回。"""

    if size < 1:
        raise ValueError("size must be at least 1")
    inst_ids = {c.inst_id for c in existing} | {c.inst_id for c in incoming}
    if len(inst_ids) > 1:
        raise ValueError(f"cannot merge candles of different inst_id: {sorted(inst_ids)}")
    merged: dict[int, Candle] = {c.ts_ms: c for c in existing}
    for candle in incoming:
        merged[candle.ts_ms] = candle
    ordered = sorted(merged.values(), key=lambda c: c.ts_ms)
    return ordered[-size:]


class CandleCache(Protocol):
    """K 线缓存协议。"""

    def store(self, inst_id: str, bar: CandleBar, candles: Sequence[Candle]) -> None:
        """合并写入缓存。"""

    def load(self, inst_id: str, bar: CandleBar) -> list[Candle]:
        """读取缓存，未命中时返回空列表。"""


class InMemoryCandleCache:
    """进程内缓存实现，用于测试和 Redis 不可用时的兜底。"""

    def __init__(self, *, size: int = DEFAULT_CACHE_SIZE) -> None:
        self._size = size
        self._data: dict[tuple[str, str], list[Candle]] = {}

    def store(self, inst_id: str, bar: CandleBar, candles: Sequence[Candle]) -> None:
        key = (inst_id, bar.value)
        self._data[key] = merge_latest_candles(self._data.get(key, []), candles, size=self._size)

    def load(self, inst_id: str, bar: CandleBar) -> list[Candle]:
        return list(self._data.get((inst_id, bar.value), []))


class RedisCandleCache:
    """基于 M01 Redis 客户端的缓存实现。序列化使用 Candle 的 JSON 形式。"""

    def __init__(
        self,
        client: Any,
        *,
        size: int = DEFAULT_CACHE_SIZE,
        key_prefix: str = CACHE_KEY_PREFIX,
    ) -> None:
        self._client = client
        self._size = size
        self._key_prefix = key_prefix

    def store(self, inst_id: str, bar: CandleBar, candles: Sequence[Candle]) -> None:
        key = cache_key(inst_id, bar, prefix=self._key_prefix)
        existing = self._decode(self._client.get(key))
        merged = merge_latest_candles(existing, candles, size=self._size)
        payload = json.dumps(
            [candle.model_dump(mode="json") for candle in merged], ensure_ascii=False
        )
        self._client.set(key, payload)

    def load(self, inst_id: str, bar: CandleBar) -> list[Candle]:
        return self._decode(self._client.get(cache_key(inst_id, bar, prefix=self._key_prefix)))

    def _decode(self, raw: Any) -> list[Candle]:
        if not raw:
            return []
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8")
        rows = json.loads(raw)
        return [Candle.model_validate(row) for row in rows]
