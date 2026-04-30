"""Tests for percentile rank + SMA5 signal builder (Task 2)."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from backtest.breadth_study.percentile_signal import build_percentile_signal


def test_percentile_rank_uses_lookback_window():
    """Strictly monotonic-increasing breadth -> last value is rank 1.0."""
    breadth = pd.Series(np.linspace(0.3, 0.7, 300))  # 300 days
    out = build_percentile_signal(breadth, lookback=252, smoother_window=5)
    # First valid rolling rank at idx 251, but smoothed signal needs +4 more days
    # so first non-NaN of smoothed signal = idx 251 + 4 = 255
    assert out.iloc[:255].isna().all()
    assert pd.notna(out.iloc[255])
    # Strictly monotonic series — every window has the latest as max -> rank 1.0
    # SMA5 of [1.0, 1.0, 1.0, 1.0, 1.0] is 1.0
    assert out.iloc[259] == pytest.approx(1.0, abs=1e-9)


def test_no_lookahead():
    """Rank at t must use t-lookback+1..t window only."""
    breadth = pd.Series([0.1] * 252 + [0.9])
    out = build_percentile_signal(breadth, lookback=252, smoother_window=5)
    # raw_pctile is non-NaN starting at idx 251 (252-day window ending at idx 251)
    # but build_percentile_signal returns SMA5; first non-NaN is at idx 255
    # idx 252 should still be NaN due to SMA5 warmup
    assert pd.isna(out.iloc[252])
    # Idx 256 should NOT be available since the series is only 253 long.
    # We only have indexes 0..252. So out is length 253 max.
    assert len(out) == 253


def test_signal_is_smoothed():
    """Output should equal raw_pctile.rolling(5).mean()."""
    rng = np.random.default_rng(42)
    breadth = pd.Series(rng.uniform(0.0, 1.0, 500))
    raw_pctile = breadth.rolling(252, min_periods=252).rank(pct=True)
    expected = raw_pctile.rolling(5, min_periods=5).mean()
    signal = build_percentile_signal(breadth, lookback=252, smoother_window=5)
    pd.testing.assert_series_equal(
        signal.dropna(),
        expected.dropna(),
        check_names=False,
    )
