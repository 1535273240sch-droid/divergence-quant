"""令牌桶限流器与重试策略的确定性测试。"""

import pytest
from m02_helpers import FakeClock, run

from dquant.adapters.errors import ExchangeError
from dquant.adapters.ratelimit import TokenBucketLimiter
from dquant.adapters.retry import RetryPolicy


def _make_limiter(clock: FakeClock, rate: float = 4.0, capacity: float = 2.0) -> TokenBucketLimiter:
    return TokenBucketLimiter(rate, capacity, clock=clock.time, sleep=clock.sleep)


def test_initial_capacity_allows_burst_without_waiting() -> None:
    clock = FakeClock()
    limiter = _make_limiter(clock)

    async def scenario() -> None:
        for _ in range(2):
            await limiter.acquire()

    run(scenario())
    assert clock.sleeps == []


def test_empty_bucket_waits_until_token_refills() -> None:
    clock = FakeClock()
    limiter = _make_limiter(clock)

    async def scenario() -> None:
        await limiter.acquire()
        await limiter.acquire()
        await limiter.acquire()

    run(scenario())
    # 容量 2 已用尽，第三个令牌需要 1/4 秒（rate=4）。
    assert clock.sleeps == [pytest.approx(0.25)]


def test_invalid_parameters_rejected() -> None:
    clock = FakeClock()
    with pytest.raises(ValueError, match="rate_per_sec"):
        TokenBucketLimiter(0, clock=clock.time, sleep=clock.sleep)
    with pytest.raises(ValueError, match="capacity"):
        TokenBucketLimiter(1.0, 0, clock=clock.time, sleep=clock.sleep)


def test_retry_succeeds_after_retriable_error() -> None:
    clock = FakeClock()
    policy = RetryPolicy(max_attempts=3, base_delay_sec=0.1, sleep=clock.sleep)
    attempts: list[int] = []

    async def operation() -> str:
        attempts.append(1)
        if len(attempts) < 3:
            raise ExchangeError("50011", "频率限制", retriable=True)
        return "ok"

    assert run(policy.run(operation)) == "ok"
    assert len(attempts) == 3
    # 指数退避：0.1s、0.2s。
    assert clock.sleeps == [pytest.approx(0.1), pytest.approx(0.2)]


def test_retry_raises_immediately_on_non_retriable_error() -> None:
    clock = FakeClock()
    policy = RetryPolicy(max_attempts=3, base_delay_sec=0.1, sleep=clock.sleep)
    calls: list[int] = []

    async def operation() -> None:
        calls.append(1)
        raise ExchangeError("51000", "参数错误", retriable=False)

    with pytest.raises(ExchangeError, match="51000"):
        run(policy.run(operation))

    assert calls == [1]
    assert clock.sleeps == []


def test_retry_raises_last_error_when_attempts_exhausted() -> None:
    clock = FakeClock()
    policy = RetryPolicy(max_attempts=2, base_delay_sec=0.05, sleep=clock.sleep)
    calls: list[int] = []

    async def operation() -> None:
        calls.append(1)
        raise ExchangeError("50013", "系统繁忙", retriable=True)

    with pytest.raises(ExchangeError, match="50013"):
        run(policy.run(operation))

    assert len(calls) == 2
    assert clock.sleeps == [pytest.approx(0.05)]
