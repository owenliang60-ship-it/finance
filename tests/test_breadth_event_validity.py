"""Tests for event-validity statistics."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from backtest.breadth_study.event_validity import (
    bootstrap_mean_lift_ci,
    run_event_validity,
)


def _manifest():
    return {
        "version": "event_validity_test",
        "frozen_at": "2026-05-01",
        "signal_mode": "absolute",
        "ma_windows": [20],
        "thresholds": {
            "low_recovery": [0.30],
            "high_strength": [],
        },
        "percentile_lookback": 0,
        "signal_smoother": "RAW",
        "cooldown_short_horizon": 5,
        "cooldown_long_horizon": 20,
        "targets": ["SPY", "QQQ"],
        "primary_target": "SPY",
        "primary_horizon": 10,
        "sensitivity_target": "QQQ",
        "sensitivity_horizon": 10,
        "horizons_short": [10],
        "horizons_long": [],
        "min_market_cap": 1000000000,
        "max_staleness_days": 90,
        "from_date": "2021-01-01",
        "permutation": {
            "trials": 50,
            "seed": 123,
            "stratify_by": "year",
            "respect_cooldown": True,
            "rejection_max_attempts_per_event": 20,
            "fallback_to_sequential_below": 0.30,
            "warning_threshold": 0.0,
        },
        "strategy_bootstrap": {
            "trials": 50,
            "seed": 456,
            "ci_lower_pct": 2.5,
            "ci_upper_pct": 97.5,
        },
        "hurdle_thresholds": {
            "h1_trigger_freq_min_per_year": 1.5,
            "h1_trigger_freq_max_per_year": 4.0,
            "h2_hit_rate_lift_pp": 15,
            "h3_target_same_sign_min": 2,
            "h4_short_horizon_same_sign_min": 1,
            "h4_short_horizons": [10],
            "h5_permutation_p_max": 0.05,
            "h6_strategy_excess_cagr_pp": 5,
        },
        "pass_threshold": 4,
    }


def test_bootstrap_mean_lift_ci_brackets_point():
    event = np.array([0.03, 0.04, 0.05])
    baseline = np.array([0.00, 0.01, -0.01, 0.00])
    ci = bootstrap_mean_lift_ci(
        event,
        baseline,
        trials=100,
        seed=1,
        ci_lower_pct=2.5,
        ci_upper_pct=97.5,
    )
    assert ci["mean_lift_point"] == pytest.approx(event.mean() - baseline.mean())
    assert ci["mean_lift_ci_low"] <= ci["mean_lift_point"]
    assert ci["mean_lift_point"] <= ci["mean_lift_ci_high"]
    assert ci["mean_lift_share_nonpositive"] < 0.10


def test_event_validity_outputs_cell_table_and_summary():
    dates = pd.bdate_range("2021-01-01", periods=80)
    breadth = np.full(len(dates), 0.20)
    event_idxs = [10, 30, 50]
    for idx in event_idxs:
        breadth[idx: idx + 3] = 0.35
    daily_breadth = pd.DataFrame({
        "date": dates,
        "breadth_20": breadth,
    })

    target_returns = pd.DataFrame({
        "date": dates,
        "SPY_fwd_10d": np.zeros(len(dates)),
        "QQQ_fwd_10d": np.zeros(len(dates)),
    })
    target_returns.loc[event_idxs, "SPY_fwd_10d"] = 0.04
    target_returns.loc[event_idxs, "QQQ_fwd_10d"] = 0.03

    table, summary = run_event_validity(_manifest(), daily_breadth, target_returns)

    assert len(table) == 2
    assert len(summary) == 1
    assert set(table["target"]) == {"SPY", "QQQ"}
    assert (table["mean_lift_pp"] > 0).all()
    assert summary.iloc[0]["positive_mean_targets_count"] == 2
    assert summary.iloc[0]["positive_hit_targets_count"] == 2
