"""K 线缓存测试：合并语义、容量上限、键名与 Redis 序列化（假客户端）。"""

from decimal import Decimal

import pytest
from m02_helpers import make_candle

from dquant.contracts import CandleBar
from dquant.data.cache import (
    InMemoryCandleCache,
    RedisCandleCache,
    cache_key,
    merge_latest_candles,
)


def test_cache_key_format() -> None:
    assert cache_key("BTC-USDT-SWAP", CandleBar.ONE_DAY) == "dquant:candles:BTC-USDT-SWAP:1D"


def test_merge_latest_candles_overrides_and_trims() -> None:
    existing = [make_candle(0, close="100"), make_candle(1, close="101")]
    incoming = [make_candle(1, close="101.5", confirmed=False), make_candle(2, close="102")]

    merged = merge_latest_candles(existing, incoming, size=2)

    assert [c.ts_ms for c in merged] == [1, 2]
    assert merged[0].close == Decimal("101.5")


def test_merge_latest_candles_rejects_mixed_inst_ids() -> None:
    with pytest.raises(ValueError, match="inst_id"):
        merge_latest_candles([make_candle(0)], [make_candle(1, inst_id="ETH-USDT-SWAP")])

    with pytest.raises(ValueError, match="size"):
        merge_latest_candles([], [], size=0)


def test_in_memory_cache_roundtrip_and_cap() -> None:
    cache = InMemoryCandleCache(size=3)
    cache.store("BTC-USDT-SWAP", CandleBar.ONE_HOUR, [make_candle(i) for i in range(5)])

    loaded = cache.load("BTC-USDT-SWAP", CandleBar.ONE_HOUR)

    assert [c.ts_ms for c in loaded] == [2, 3, 4]
    assert cache.load("ETH-USDT-SWAP", CandleBar.ONE_HOUR) == []


class FakeRedisClient:
    """模拟 redis-py 客户端的 get/set，不连接网络。"""

    def __init__(self) -> None:
        self.storage: dict[str, str] = {}

    def get(self, key: str) -> str | None:
        return self.storage.get(key)

    def set(self, key: str, value: str) -> None:
        self.storage[key] = value


def test_redis_cache_roundtrip_preserves_decimals_and_flags() -> None:
    client = FakeRedisClient()
    cache = RedisCandleCache(client)
    original = make_candle(3_600_000, close="101.5", confirmed=False)

    cache.store("BTC-USDT-SWAP", CandleBar.ONE_HOUR, [original])

    loaded = cache.load("BTC-USDT-SWAP", CandleBar.ONE_HOUR)

    assert loaded == [original]
    assert client.storage["dquant:candles:BTC-USDT-SWAP:1H"]
    assert cache.load("BTC-USDT-SWAP", CandleBar.ONE_DAY) == []


def test_redis_cache_merges_with_existing_content() -> None:
    client = FakeRedisClient()
    cache = RedisCandleCache(client, size=2)
    cache.store("BTC-USDT-SWAP", CandleBar.ONE_HOUR, [make_candle(0), make_candle(1)])
    cache.store("BTC-USDT-SWAP", CandleBar.ONE_HOUR, [make_candle(2, confirmed=False)])

    loaded = cache.load("BTC-USDT-SWAP", CandleBar.ONE_HOUR)

    assert [c.ts_ms for c in loaded] == [1, 2]
    assert loaded[1].confirmed is False
