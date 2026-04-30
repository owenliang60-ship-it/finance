"""Tests for H6 event-driven strategy CAGR + bootstrap CI (Task 9)."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from backtest.breadth_study.percentile_strategy import (
    check_h6_strategy_excess_cagr,
    event_strategy_bootstrap_ci,
    event_strategy_cagr,
)


def _make_synthetic_prices(years=4, drift=0.10, vol=0.01, seed=42) -> pd.DataFrame:
    """Synthetic OHLC: deterministic drift + small daily noise."""
    rng = np.random.default_rng(seed)
    n = int(252 * years)
    dates = pd.bdate_range("2021-01-04", periods=n)
    daily_drift = (1 + drift) ** (1 / 252.0) - 1
    daily_rets = rng.normal(daily_drift, vol, n)
    closes = 100.0 * np.cumprod(1 + daily_rets)
    opens = np.concatenate([[100.0], closes[:-1]])  # T+1 open ≈ T close
    return pd.DataFrame({
        "date": dates, "open": opens, "close": closes,
    })


def test_strategy_cagr_no_events_equals_zero_strategy():
    prices = _make_synthetic_prices(years=4)
    result = event_strategy_cagr(
        event_dates=[],
        target_prices=prices,
        target="SPY",
        horizon=10,
        first_valid_date=prices["date"].iloc[0],
        last_date=prices["date"].iloc[-1],
        one_way_bps=10,
    )
    assert result["strategy_cagr"] == pytest.approx(0.0, abs=1e-9)
    assert result["n_trades"] == 0
    assert result["exposure_pct"] == pytest.approx(0.0, abs=1e-9)


def test_strategy_cagr_random_events_underperform_bnh_in_trend():
    """Trending market + random events that exit to cash -> excess < 0."""
    prices = _make_synthetic_prices(years=4, drift=0.15)
    rng = np.random.default_rng(123)
    n_events = 10
    idxs = rng.choice(len(prices) - 30, size=n_events, replace=False)
    events = [prices["date"].iloc[i] for i in sorted(idxs)]
    result = event_strategy_cagr(
        events, prices, "SPY", 10,
        prices["date"].iloc[0], prices["date"].iloc[-1], one_way_bps=10,
    )
    assert result["excess_cagr"] < 0


def test_strategy_cagr_perfect_signal_beats_bnh():
    """Strategy that catches +5% pumps then exits before the -5% dumps beats B&H."""
    n = 1000
    dates = pd.bdate_range("2021-01-04", periods=n)
    closes = np.full(n, 100.0)
    # 5 "pump-then-dump" cycles. Pump for 10 bars, then dump for 10 bars,
    # net zero by the end, so B&H earns 0%.
    event_idxs = [50, 200, 400, 600, 800]
    for ev in event_idxs:
        # bars (ev+1)..(ev+10): pump +5% spread linearly
        for k in range(1, 11):
            closes[ev + k] = closes[ev] * (1 + 0.05 * (k / 10.0))
        # bars (ev+11)..(ev+20): dump back to baseline
        for k in range(1, 11):
            closes[ev + 10 + k] = closes[ev + 10] * (1 - 0.05 * (k / 10.0) / 1.05)
        # bars after (ev+20): hold flat at baseline
        closes[ev + 21 :] = closes[ev + 20]
    opens = np.concatenate([[100.0], closes[:-1]])
    prices = pd.DataFrame({"date": dates, "open": opens, "close": closes})
    events = [prices["date"].iloc[i] for i in event_idxs]
    result = event_strategy_cagr(
        events, prices, "SPY", 10,
        prices["date"].iloc[0], prices["date"].iloc[-1], one_way_bps=0,
    )
    assert result["n_trades"] == 5
    # B&H stays roughly flat (pumps net to ~zero); strategy catches each +5% pump
    assert result["excess_cagr"] > 0
    assert result["strategy_cagr"] > result["bnh_cagr"]


def test_strategy_cagr_includes_costs():
    prices = _make_synthetic_prices(years=4, drift=0.10)
    events = [prices["date"].iloc[i] for i in [50, 200, 400, 600]]
    cheap = event_strategy_cagr(
        events, prices, "SPY", 10,
        prices["date"].iloc[0], prices["date"].iloc[-1], one_way_bps=0,
    )
    expensive = event_strategy_cagr(
        events, prices, "SPY", 10,
        prices["date"].iloc[0], prices["date"].iloc[-1], one_way_bps=50,
    )
    assert cheap["strategy_cagr"] > expensive["strategy_cagr"]


def test_check_h6_threshold():
    assert check_h6_strategy_excess_cagr(5.5, 5) is True
    assert check_h6_strategy_excess_cagr(5.0, 5) is True
    assert check_h6_strategy_excess_cagr(4.99, 5) is False


def test_bootstrap_ci_brackets_point_estimate():
    prices = _make_synthetic_prices(years=4)
    events = [prices["date"].iloc[i] for i in [50, 200, 400, 600, 800]]
    ci = event_strategy_bootstrap_ci(
        events, prices, "SPY", 10,
        prices["date"].iloc[0], prices["date"].iloc[-1],
        one_way_bps=10, trials=200, seed=42,
        ci_lower_pct=2.5, ci_upper_pct=97.5,
    )
    # Point estimate should fall within (or at edge of) the [low, high] band
    assert ci["excess_cagr_ci_low"] <= ci["excess_cagr_point"] + 1e-6
    assert ci["excess_cagr_point"] <= ci["excess_cagr_ci_high"] + 1e-6


def test_bootstrap_ci_is_non_degenerate():
    """CI low <= high, non-zero width when events have variance."""
    prices = _make_synthetic_prices(years=4, vol=0.02, seed=11)
    rng = np.random.default_rng(11)
    idxs = rng.choice(len(prices) - 30, size=10, replace=False)
    events = [prices["date"].iloc[i] for i in sorted(idxs)]
    ci = event_strategy_bootstrap_ci(
        events, prices, "SPY", 10,
        prices["date"].iloc[0], prices["date"].iloc[-1],
        one_way_bps=10, trials=300, seed=42,
    )
    assert ci["excess_cagr_ci_low"] <= ci["excess_cagr_ci_high"]
    width = ci["excess_cagr_ci_high"] - ci["excess_cagr_ci_low"]
    assert width > 0
    assert 0.0 <= ci["excess_cagr_share_negative"] <= 1.0
    assert ci["n_trials"] == 300
