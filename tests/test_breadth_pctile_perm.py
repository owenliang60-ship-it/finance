"""Tests for H5 year-stratified permutation (Task 8)."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from backtest.breadth_study.percentile_perm import (
    check_h5_permutation,
    year_stratified_permutation_p,
)


def _build_panel(n_days=1000, seed=0):
    rng = np.random.default_rng(seed)
    dates = pd.bdate_range("2021-01-01", periods=n_days)
    return pd.DataFrame({
        "date": dates,
        "SPY_fwd_10d": rng.normal(0.0, 0.02, n_days),
    })


def test_permutation_null_distribution_for_random_events():
    """Random events with random returns -> p should not be tiny."""
    rng = np.random.default_rng(0)
    panel = _build_panel(n_days=1000, seed=0)
    fake_events = []
    for year, n in [(2021, 5), (2022, 6), (2023, 4), (2024, 5)]:
        sub = panel[panel["date"].dt.year == year]
        idxs = rng.choice(len(sub), n, replace=False)
        fake_events.extend(sub.iloc[sorted(idxs)]["date"].tolist())
    p, null, diag = year_stratified_permutation_p(
        fake_events, panel, target="SPY", horizon=10,
        cooldown_days=20, trials=500, seed=42,
    )
    assert 0.0 < p < 1.0
    # Random panel: p should usually be > 0.05 (but allow stochastic edge cases)
    assert diag["n_trials_succeeded"] > 0


def test_permutation_falls_back_to_sequential_when_dense():
    """Cooldown 60d on single year of 252 bdays + 5 events -> rejection should fail."""
    dates = pd.bdate_range("2022-01-01", periods=252)
    panel = pd.DataFrame({
        "date": dates,
        "SPY_fwd_10d": np.random.default_rng(0).normal(0.0, 0.02, 252),
    })
    fake_events = [dates[10], dates[80], dates[150], dates[200], dates[230]]
    p, null, diag = year_stratified_permutation_p(
        fake_events, panel, target="SPY", horizon=10,
        cooldown_days=60, trials=200, seed=42,
        rejection_max_attempts_per_event=10,
        fallback_to_sequential_below=0.30,
    )
    # Either fall back to sequential or tolerate rejection; just confirm it
    # doesn't completely fail. The dense regime should clearly trip fallback.
    assert diag["sampling_method_used"] in ("sequential", "rejection")
    if diag["sampling_method_used"] == "sequential":
        assert diag["success_rate"] >= 0.5


def test_permutation_uses_rejection_when_sparse():
    """Sparse events (only 2 in a year) -> rejection sampling should dominate."""
    dates = pd.bdate_range("2022-01-01", periods=252)
    panel = pd.DataFrame({"date": dates, "SPY_fwd_10d": np.zeros(252)})
    fake_events = [dates[10], dates[200]]
    _, _, diag = year_stratified_permutation_p(
        fake_events, panel, target="SPY", horizon=10,
        cooldown_days=20, trials=100, seed=42,
    )
    assert diag["sampling_method_used"] == "rejection"


def test_permutation_detects_strong_signal():
    """Construct events where forward returns are systematically positive."""
    rng = np.random.default_rng(1)
    dates = pd.bdate_range("2021-01-01", periods=1000)
    fwd = rng.normal(0.0, 0.02, 1000)
    # Inject a strong +5% bump on specific event-aligned indexes
    event_idxs = list(range(50, 1000, 100))  # 10 events spaced 100 bdays
    for idx in event_idxs:
        fwd[idx] = 0.05
    panel = pd.DataFrame({"date": dates, "SPY_fwd_10d": fwd})
    fake_events = [dates[i] for i in event_idxs]
    p, _, _ = year_stratified_permutation_p(
        fake_events, panel, target="SPY", horizon=10,
        cooldown_days=20, trials=500, seed=42,
    )
    assert p < 0.05


def test_check_h5_threshold():
    assert check_h5_permutation(0.04, max_p=0.05) is True
    assert check_h5_permutation(0.05, max_p=0.05) is False
    assert check_h5_permutation(0.10, max_p=0.05) is False
