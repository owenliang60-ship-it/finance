"""Tests for hurdle helpers H1-H4 (Tasks 4-7)."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from backtest.breadth_study.percentile_hurdles import (
    check_h1_trigger_frequency,
    check_h2_hit_rate_lift,
    check_h3_target_consistency,
    check_h4_short_horizon_consistency,
    compute_effective_sample_years,
)


# ---------------- effective sample years ----------------


def test_effective_years_excludes_warmup():
    dates = pd.date_range("2021-02-01", periods=1300, freq="B")
    signal = pd.Series([np.nan] * 257 + list(np.linspace(0.1, 0.9, 1043)))
    first, last, years = compute_effective_sample_years(signal, pd.Series(dates))
    assert first == dates[257]
    assert last == dates[1299]
    # 1043 valid days / 252 ≈ 4.1389
    assert years == pytest.approx(1043 / 252.0, abs=0.01)


def test_effective_years_raises_on_all_nan():
    signal = pd.Series([np.nan] * 100)
    dates = pd.Series(pd.date_range("2021-02-01", periods=100, freq="B"))
    with pytest.raises(ValueError):
        compute_effective_sample_years(signal, dates)


# ---------------- H1 ----------------


def test_h1_uses_effective_years():
    # 12 events / 3.8 yrs = 3.16/yr -> within [1.5, 4.0]
    assert check_h1_trigger_frequency(12, 3.8, 1.5, 4.0) is True


def test_h1_too_rare():
    # 5 / 3.8 = 1.32/yr -> below 1.5
    assert check_h1_trigger_frequency(5, 3.8, 1.5, 4.0) is False


def test_h1_too_frequent():
    # 20 / 3.8 ≈ 5.26/yr -> above 4.0
    assert check_h1_trigger_frequency(20, 3.8, 1.5, 4.0) is False


# ---------------- H2 ----------------


def test_h2_hit_rate_lift_primary():
    """Event hit rate 80% vs baseline 60% -> lift 20pp >= 15pp."""
    event_rets = np.array([0.03, 0.02, -0.01, 0.04, 0.01])
    non_event_rets = np.array([0.01] * 60 + [-0.01] * 40)
    assert check_h2_hit_rate_lift(event_rets, non_event_rets, min_lift_pp=15) is True


def test_h2_hit_rate_lift_fails_when_under_threshold():
    """Lift = 10pp < 15pp threshold."""
    event_rets = np.array([0.01] * 7 + [-0.01] * 3)  # 70% hit
    non_event_rets = np.array([0.01] * 60 + [-0.01] * 40)  # 60% hit
    assert check_h2_hit_rate_lift(event_rets, non_event_rets, min_lift_pp=15) is False


# ---------------- H3 ----------------


def test_h3_cross_target_same_sign():
    target_diffs = {"SPY": 0.02, "QQQ": 0.03, "SOXX": 0.04, "IWM": -0.01, "XLK": 0.025}
    # 4/5 positive
    assert check_h3_target_consistency(target_diffs, expected_sign=+1, min_count=4) is True


def test_h3_fails_when_only_3_match():
    target_diffs = {"SPY": 0.02, "QQQ": -0.01, "SOXX": 0.04, "IWM": -0.01, "XLK": 0.025}
    # 3/5 positive
    assert check_h3_target_consistency(target_diffs, expected_sign=+1, min_count=4) is False


# ---------------- H4 (v1.2 method C) ----------------


def test_h4_short_horizons_same_sign_pass():
    horizon_diffs = {5: 0.015, 10: 0.022, 20: 0.018}
    # 3/3 positive >= 2
    assert check_h4_short_horizon_consistency(
        horizon_diffs, expected_sign=+1, min_count=2
    ) is True


def test_h4_two_out_of_three_pass():
    horizon_diffs = {5: 0.015, 10: 0.022, 20: -0.005}
    # 2/3 positive >= 2
    assert check_h4_short_horizon_consistency(
        horizon_diffs, expected_sign=+1, min_count=2
    ) is True


def test_h4_only_one_pass_fails():
    horizon_diffs = {5: 0.015, 10: -0.022, 20: -0.005}
    # 1/3 positive < 2
    assert check_h4_short_horizon_consistency(
        horizon_diffs, expected_sign=+1, min_count=2
    ) is False
