from __future__ import annotations

import numpy as np
import pytest

from dquant.core.mhd import MHDConfig, detect_mhd, evaluate_mhd
from dquant.core.pivots import PivotPoint


def _base_arrays(n: int = 20):
    highs = np.linspace(90.0, 105.0, n)
    lows = np.linspace(80.0, 95.0, n)
    hist = np.array(
        [1, 2, 12, 4, 3, 2, 5, 3, 4, 5, 6, 7, 7, 6, 5, 4, 3, 2, 1, 0],
        dtype=float,
    )
    atr = np.ones(n, dtype=float)
    return highs, lows, hist, atr


def _config() -> MHDConfig:
    return MHDConfig(
        swing_left=1,
        swing_right=1,
        stdev_window=20,
        peak_lookback=20,
        min_d=0.1,
        peak_price_tolerance_atr=1.0,
    )


def test_mhd_config_warmup_and_invalid_windows() -> None:
    config = MHDConfig()
    assert config.warmup_bars >= 100
    assert config.macd_warmup == 33
    with pytest.raises(ValueError, match="macd_fast"):
        MHDConfig(macd_fast=30, macd_slow=26)
    with pytest.raises(ValueError, match="stdev_window"):
        MHDConfig(stdev_window=1)


def test_mhd_top_divergence_uses_peak_reference_when_pair_is_weaker() -> None:
    highs, lows, hist, atr = _base_arrays()
    pivots = [
        PivotPoint(index=2, value=float(highs[2]), kind="high", confirmed_index=3),
        PivotPoint(index=7, value=float(highs[7]), kind="high", confirmed_index=8),
        PivotPoint(index=12, value=float(highs[12]), kind="high", confirmed_index=13),
    ]
    signals = evaluate_mhd(highs, lows, hist, atr, pivots, [], _config())

    assert len(signals) == 2
    assert signals[0].kind == "top"
    assert signals[0].reference == "pair"
    assert signals[0].d_pair is not None and signals[0].d_pair >= 0.1

    peak_signal = signals[1]
    assert peak_signal.kind == "top"
    assert peak_signal.reference == "peak"
    assert peak_signal.d_peak is not None
    assert peak_signal.d_peak >= 0.1
    assert peak_signal.reference_index == 2
    assert peak_signal.confirmed_index == 13


def test_mhd_bottom_divergence_is_symmetric() -> None:
    n = 20
    highs = np.linspace(110.0, 95.0, n)
    lows = np.array(
        [100, 99, 100, 97, 96, 95, 96, 98, 97, 96, 95, 94, 94.5, 95, 96, 97, 98, 99, 100, 101],
        dtype=float,
    )
    hist = np.array(
        [-1, -2, -12, -4, -3, -2, -5, -3, -4, -5, -6, -7, -7, -6, -5, -4, -3, -2, -1, 0],
        dtype=float,
    )
    atr = np.ones(n, dtype=float)
    pivots = [
        PivotPoint(index=2, value=float(lows[2]), kind="low", confirmed_index=3),
        PivotPoint(index=7, value=float(lows[7]), kind="low", confirmed_index=8),
        PivotPoint(index=12, value=float(lows[12]), kind="low", confirmed_index=13),
    ]
    signals = evaluate_mhd(highs, lows, hist, atr, [], pivots, _config())

    assert len(signals) == 2
    assert all(signal.kind == "bottom" for signal in signals)
    peak_signal = signals[1]
    assert peak_signal.reference == "peak"
    assert peak_signal.d_peak is not None and peak_signal.d_peak >= 0.1


def test_detect_mhd_returns_empty_before_warmup() -> None:
    n = 40
    result = detect_mhd(
        np.linspace(90, 100, n),
        np.linspace(80, 90, n),
        np.linspace(85, 95, n),
    )
    assert result.signals == ()
    assert np.isnan(result.hist).all()
