"""M03 指标库单元测试。

测试目标：
1. 核心指标与 TA-Lib 对齐（误差 < 1e-6，预热期除外）。
2. 因果性：截断未来数据不会改变历史输出，杜绝未来函数。
3. 非法输入必须显式报错，空输入和短输入行为明确。
4. 纯函数：不修改输入、不使用全局状态。
"""

import numpy as np
import pytest

from dquant.core.indicators import adx, atr, ema, macd, rsi, sma

TOLERANCE = 1e-6


@pytest.fixture(scope="module")
def talib_module():
    """TA-Lib 不在环境中时，仅跳过对照测试，其余测试继续执行。"""

    return pytest.importorskip("talib")


def _random_walk(size: int = 400, seed: int = 42, start: float = 100.0) -> np.ndarray:
    """生成确定性的随机游走价格序列。"""

    rng = np.random.default_rng(seed)
    returns = rng.normal(0.0, 0.01, size)
    return start * np.exp(np.cumsum(returns))


def _ohlc(close: np.ndarray, seed: int = 7) -> tuple[np.ndarray, np.ndarray]:
    """由收盘价生成满足 high >= low 的 OHLC 数组。"""

    rng = np.random.default_rng(seed)
    high = close * (1.0 + rng.uniform(0.0, 0.01, close.size))
    low = close * (1.0 - rng.uniform(0.0, 0.01, close.size))
    return high, low


def _assert_matches(actual: np.ndarray, expected: np.ndarray) -> None:
    """同时比较 NaN 位置和有效数值。"""

    assert actual.shape == expected.shape
    np.testing.assert_array_equal(np.isnan(actual), np.isnan(expected))
    valid = ~np.isnan(expected)
    np.testing.assert_allclose(actual[valid], expected[valid], rtol=0.0, atol=TOLERANCE)


# ---------------------------------------------------------------------------
# 与 TA-Lib 的数值对照
# ---------------------------------------------------------------------------


def test_sma_matches_talib(talib_module) -> None:
    close = _random_walk()
    _assert_matches(sma(close, 200), talib_module.SMA(close, timeperiod=200))
    _assert_matches(sma(close, 20), talib_module.SMA(close, timeperiod=20))


def test_ema_matches_talib(talib_module) -> None:
    close = _random_walk()
    _assert_matches(ema(close, 200), talib_module.EMA(close, timeperiod=200))
    _assert_matches(ema(close, 20), talib_module.EMA(close, timeperiod=20))


def test_macd_matches_talib(talib_module) -> None:
    close = _random_walk(seed=11)
    dif, dea, hist = macd(close, fast=12, slow=26, signal=9)
    expected_dif, expected_dea, expected_hist = talib_module.MACD(
        close, fastperiod=12, slowperiod=26, signalperiod=9
    )
    _assert_matches(dif, expected_dif)
    _assert_matches(dea, expected_dea)
    _assert_matches(hist, expected_hist)


def test_rsi_matches_talib(talib_module) -> None:
    close = _random_walk(seed=13)
    _assert_matches(rsi(close, 14), talib_module.RSI(close, timeperiod=14))
    _assert_matches(rsi(close, 6), talib_module.RSI(close, timeperiod=6))


def test_atr_matches_talib(talib_module) -> None:
    close = _random_walk(seed=17)
    high, low = _ohlc(close)
    _assert_matches(atr(high, low, close, 14), talib_module.ATR(high, low, close, timeperiod=14))


def test_adx_matches_talib(talib_module) -> None:
    close = _random_walk(seed=19)
    high, low = _ohlc(close)
    _assert_matches(adx(high, low, close, 14), talib_module.ADX(high, low, close, timeperiod=14))


# ---------------------------------------------------------------------------
# 已知数值行为
# ---------------------------------------------------------------------------


def test_sma_known_values() -> None:
    result = sma([1.0, 2.0, 3.0, 4.0, 5.0], 3)
    np.testing.assert_array_equal(np.isnan(result[:2]), [True, True])
    np.testing.assert_allclose(result[2:], [2.0, 3.0, 4.0], atol=1e-12)


def test_ema_uses_sma_seed_like_talib() -> None:
    values = np.arange(1.0, 11.0)
    result = ema(values, 3)
    np.testing.assert_array_equal(np.isnan(result[:2]), [True, True])
    assert result[2] == pytest.approx(2.0)  # SMA(1,2,3)
    assert result[3] == pytest.approx(0.5 * 4.0 + 0.5 * 2.0)


def test_macd_hist_equals_dif_minus_dea() -> None:
    close = _random_walk(seed=23)
    dif, dea, hist = macd(close)
    valid = ~np.isnan(dea)
    assert valid.any()
    np.testing.assert_allclose(hist[valid], dif[valid] - dea[valid], atol=1e-12)


def test_rsi_flat_series_is_zero_after_warmup() -> None:
    result = rsi(np.full(60, 100.0), 14)
    np.testing.assert_array_equal(np.isnan(result[:14]), np.ones(14, dtype=bool))
    np.testing.assert_allclose(result[14:], 0.0, atol=1e-12)


def test_rsi_monotonic_rise_reaches_one_hundred() -> None:
    result = rsi(np.arange(1.0, 61.0), 14)
    np.testing.assert_allclose(result[14:], 100.0, atol=1e-12)


def test_atr_constant_true_range() -> None:
    close = np.full(40, 100.0)
    high = close + 1.0
    low = close - 1.0
    result = atr(high, low, close, 14)
    np.testing.assert_array_equal(np.isnan(result[:14]), np.ones(14, dtype=bool))
    np.testing.assert_allclose(result[14:], 2.0, atol=1e-12)


def test_adx_pure_uptrend_reaches_one_hundred() -> None:
    close = np.arange(100.0, 160.0) + 0.5
    high = close + 0.5
    low = close - 0.5
    result = adx(high, low, close, 14)
    first = 2 * 14 - 1
    np.testing.assert_array_equal(np.isnan(result[:first]), np.ones(first, dtype=bool))
    np.testing.assert_allclose(result[first:], 100.0, atol=1e-9)


def test_rsi_and_adx_stay_within_bounds() -> None:
    close = _random_walk(seed=29)
    high, low = _ohlc(close)
    rsi_values = rsi(close, 14)
    adx_values = adx(high, low, close, 14)
    rsi_valid = rsi_values[~np.isnan(rsi_values)]
    adx_valid = adx_values[~np.isnan(adx_values)]
    assert np.all((rsi_valid >= 0.0) & (rsi_valid <= 100.0))
    assert np.all((adx_valid >= 0.0) & (adx_valid <= 100.0))


def test_outputs_are_float64() -> None:
    close = _random_walk(50)
    high, low = _ohlc(close)
    for result in (sma(close, 5), ema(close, 5), rsi(close, 5), atr(high, low, close, 5)):
        assert result.dtype == np.float64
        assert result.shape == close.shape
    for result in (adx(high, low, close, 5), *macd(close)):
        assert result.dtype == np.float64
        assert result.shape == close.shape


# ---------------------------------------------------------------------------
# 空输入、短输入与预热期
# ---------------------------------------------------------------------------


def test_empty_input_returns_empty_arrays() -> None:
    empty = np.array([], dtype=float)
    assert sma(empty, 5).shape == (0,)
    assert ema(empty, 5).shape == (0,)
    assert rsi(empty, 5).shape == (0,)
    assert atr(empty, empty, empty, 5).shape == (0,)
    assert adx(empty, empty, empty, 5).shape == (0,)
    dif, dea, hist = macd(empty)
    assert dif.shape == dea.shape == hist.shape == (0,)


def test_short_input_is_all_nan() -> None:
    close = np.linspace(100.0, 110.0, 199)
    assert np.isnan(sma(close, 200)).all()
    assert np.isnan(ema(close, 200)).all()
    assert np.isnan(rsi(close[:14], 14)).all()  # 需要 15 个点才能产生第一个 RSI
    high, low = _ohlc(close)
    assert np.isnan(atr(high, low, close, 200)).all()
    assert np.isnan(adx(high, low, close, 100)).all()


def test_warmup_lengths_match_talib_conventions() -> None:
    close = _random_walk(size=120, seed=31)
    high, low = _ohlc(close)

    sma_value = sma(close, 20)
    assert np.isnan(sma_value[:19]).all() and not np.isnan(sma_value[19])

    atr_value = atr(high, low, close, 14)
    assert np.isnan(atr_value[:14]).all() and not np.isnan(atr_value[14])

    adx_value = adx(high, low, close, 14)
    assert np.isnan(adx_value[:27]).all() and not np.isnan(adx_value[27])

    dif, dea, hist = macd(close, 12, 26, 9)
    assert np.isnan(dif[:33]).all() and not np.isnan(dif[33])
    assert np.isnan(dea[:33]).all() and not np.isnan(dea[33])
    np.testing.assert_array_equal(np.isnan(hist), np.isnan(dea))


def test_adx_requires_full_warmup() -> None:
    close = _random_walk(size=27, seed=37)
    high, low = _ohlc(close)
    # 首个 ADX 需要索引 27，因此 27 个元素仍然全部为 NaN
    assert np.isnan(adx(high, low, close, 14)).all()
    assert np.isfinite(adx(high, low, close, 13)[25])


# ---------------------------------------------------------------------------
# 非法输入
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("bad_period", [0, -1, -20])
def test_indicators_reject_non_positive_period(bad_period: int) -> None:
    close = np.linspace(100.0, 110.0, 50)
    high, low = _ohlc(close)
    with pytest.raises(ValueError):
        sma(close, bad_period)
    with pytest.raises(ValueError):
        ema(close, bad_period)
    with pytest.raises(ValueError):
        rsi(close, bad_period)
    with pytest.raises(ValueError):
        atr(high, low, close, bad_period)
    with pytest.raises(ValueError):
        adx(high, low, close, bad_period)


@pytest.mark.parametrize(("fast", "slow", "signal"), [(26, 12, 9), (12, 12, 9), (12, 26, 0)])
def test_macd_rejects_invalid_periods(fast: int, slow: int, signal: int) -> None:
    with pytest.raises(ValueError):
        macd(_random_walk(60), fast=fast, slow=slow, signal=signal)


def test_atr_rejects_length_mismatch() -> None:
    close = _random_walk(60)
    high, low = _ohlc(close)
    with pytest.raises(ValueError):
        atr(high[:-1], low, close, 14)


def test_adx_rejects_length_mismatch() -> None:
    close = _random_walk(60)
    high, low = _ohlc(close)
    with pytest.raises(ValueError):
        adx(high, low[:-1], close, 14)


def test_atr_rejects_high_below_low() -> None:
    close = np.full(30, 100.0)
    high = np.full(30, 99.0)
    low = np.full(30, 101.0)
    with pytest.raises(ValueError):
        atr(high, low, close, 14)


@pytest.mark.parametrize("bad_value", [np.nan, np.inf, -np.inf])
def test_indicators_reject_non_finite_input(bad_value: float) -> None:
    close = np.full(40, 100.0)
    close[10] = bad_value
    with pytest.raises(ValueError):
        sma(close, 5)
    with pytest.raises(ValueError):
        ema(close, 5)
    with pytest.raises(ValueError):
        rsi(close, 5)
    with pytest.raises(ValueError):
        macd(close)


def test_indicators_reject_multidimensional_input() -> None:
    with pytest.raises(ValueError):
        sma(np.ones((10, 2)), 3)
    with pytest.raises(ValueError):
        rsi(np.ones((10, 2)), 3)


# ---------------------------------------------------------------------------
# 纯函数与因果性
# ---------------------------------------------------------------------------


def test_inputs_are_not_mutated() -> None:
    close = _random_walk(80, seed=41)
    high, low = _ohlc(close)
    snapshots = [close.copy(), high.copy(), low.copy()]
    sma(close, 5)
    ema(close, 5)
    macd(close)
    rsi(close, 5)
    atr(high, low, close, 5)
    adx(high, low, close, 5)
    for before, after in zip(snapshots, (close, high, low), strict=True):
        np.testing.assert_array_equal(before, after)


def test_list_input_is_accepted() -> None:
    result = sma([1, 2, 3, 4], 2)
    np.testing.assert_allclose(result[1:], [1.5, 2.5, 3.5], atol=1e-12)


def test_repeated_calls_are_deterministic() -> None:
    close = _random_walk(100, seed=43)
    first = macd(close)
    second = macd(close)
    for left, right in zip(first, second, strict=True):
        np.testing.assert_array_equal(left, right)


@pytest.mark.parametrize(("indicator", "period"), [(sma, 5), (ema, 5), (rsi, 14)])
def test_single_series_indicators_are_causal(indicator, period: int) -> None:
    close = _random_walk(300, seed=47)
    full = indicator(close, period)
    prefix = indicator(close[:150], period)
    np.testing.assert_array_equal(np.isnan(prefix), np.isnan(full[:150]))
    np.testing.assert_allclose(prefix, full[:150], rtol=0.0, atol=1e-12, equal_nan=True)


def test_macd_is_causal() -> None:
    close = _random_walk(300, seed=53)
    full = macd(close)
    prefix = macd(close[:180])
    for full_part, prefix_part in zip(full, prefix, strict=True):
        np.testing.assert_array_equal(np.isnan(prefix_part), np.isnan(full_part[:180]))
        np.testing.assert_allclose(
            prefix_part, full_part[:180], rtol=0.0, atol=1e-12, equal_nan=True
        )


def test_ohlc_indicators_are_causal() -> None:
    close = _random_walk(300, seed=59)
    high, low = _ohlc(close, seed=61)
    full_atr = atr(high, low, close, 14)
    prefix_atr = atr(high[:200], low[:200], close[:200], 14)
    np.testing.assert_array_equal(np.isnan(prefix_atr), np.isnan(full_atr[:200]))
    np.testing.assert_allclose(prefix_atr, full_atr[:200], rtol=0.0, atol=1e-12, equal_nan=True)

    full_adx = adx(high, low, close, 14)
    prefix_adx = adx(high[:200], low[:200], close[:200], 14)
    np.testing.assert_array_equal(np.isnan(prefix_adx), np.isnan(full_adx[:200]))
    np.testing.assert_allclose(prefix_adx, full_adx[:200], rtol=0.0, atol=1e-12, equal_nan=True)
