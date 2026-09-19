"""Tests for scripts.crypto_trend_metrics.trend_metrics."""

import math

import numpy as np
import pandas as pd
import pytest

from scripts.crypto_trend_metrics import trend_metrics


def _series(values):
    return pd.Series(values, dtype=float)


def test_representable_small_drift_is_not_discarded_by_variance_floor():
    out = trend_metrics(100*np.exp(1e-10*np.arange(43)))
    assert out['slope'] == pytest.approx(1e-10, rel=1e-5, abs=0)
    assert out['r_squared'] > .99999


def test_exponential_monotone():
    # p_k = 100 * 1.1**k  => perfect exponential uptrend
    closes = _series([100.0 * (1.1 ** k) for k in range(6)])
    out = trend_metrics(closes)

    assert out["return"] == pytest.approx(closes.iloc[-1] / closes.iloc[0] - 1.0)
    assert out["er"] == pytest.approx(1.0)
    assert out["r_squared"] == pytest.approx(1.0)
    assert out["slope"] == pytest.approx(math.log(1.1))
    assert out["drawdown"] == pytest.approx(0.0)


def test_falling_exponential():
    closes = _series([100.0 * (0.9 ** k) for k in range(6)])
    out = trend_metrics(closes)

    assert out["return"] < 0.0
    assert out["slope"] < 0.0
    assert out["er"] == pytest.approx(1.0)
    assert out["r_squared"] == pytest.approx(1.0)
    assert out["drawdown"] > 0.0
    assert out["drawdown"] == pytest.approx(1.0 - closes.iloc[-1] / closes.max())


def test_updown_returns_to_start_er_zero():
    closes = _series([100.0, 110.0, 121.0, 110.0, 100.0])
    out = trend_metrics(closes)

    assert out["return"] == pytest.approx(0.0)
    assert out["er"] == pytest.approx(0.0)
    assert out["drawdown"] == pytest.approx(1.0 - 100.0 / 121.0)


def test_jump_then_flat_has_lower_r_squared_than_steady():
    steady = _series([100.0 * (1.05 ** k) for k in range(6)])
    jumpy = _series([100.0, 200.0, 200.0, 200.0, 200.0, 200.0])

    steady_out = trend_metrics(steady)
    jumpy_out = trend_metrics(jumpy)

    assert jumpy_out["r_squared"] < steady_out["r_squared"]
    assert 0.0 <= jumpy_out["r_squared"] <= 1.0


def test_constant_is_all_zero():
    out = trend_metrics(_series([42.0, 42.0, 42.0, 42.0]))

    for key in ("return", "er", "slope", "r_squared", "drawdown"):
        assert out[key] == pytest.approx(0.0)


def test_scale_invariance():
    closes = _series([100.0, 103.0, 99.0, 110.0, 108.0, 120.0])
    base = trend_metrics(closes)
    scaled = trend_metrics(closes * 1000.0)

    for key in ("return", "er", "slope", "r_squared", "drawdown"):
        assert scaled[key] == pytest.approx(base[key])


def test_tiny_drift_is_not_treated_as_zero():
    # ~0.0001% per bar exponential drift must retain a near-perfect fit.
    rate = 1e-6
    closes = _series([100.0 * ((1.0 + rate) ** k) for k in range(8)])
    out = trend_metrics(closes)

    assert out["r_squared"] == pytest.approx(1.0, abs=1e-6)
    assert out["slope"] == pytest.approx(math.log1p(rate), rel=1e-3)
    assert out["slope"] != 0.0


def test_returns_clamped_to_unit_interval():
    closes = _series([100.0, 100.0 + 1e-12, 100.0, 100.0 + 1e-12, 100.0])
    out = trend_metrics(closes)

    assert 0.0 <= out["er"] <= 1.0
    assert 0.0 <= out["r_squared"] <= 1.0


@pytest.mark.parametrize(
    "bad",
    [
        [100.0, 0.0, 101.0],
        [100.0, -1.0, 101.0],
        [100.0, float("nan"), 101.0],
        [100.0, float("inf"), 101.0],
        [100.0],
        [],
    ],
)
def test_invalid_values_rejected(bad):
    with pytest.raises((ValueError, TypeError)):
        trend_metrics(_series(bad) if bad else pd.Series([], dtype=float))


def test_accepts_plain_sequence():
    out = trend_metrics([100.0, 110.0, 121.0])

    assert out["er"] == pytest.approx(1.0)
    assert out["r_squared"] == pytest.approx(1.0)
