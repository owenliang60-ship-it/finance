"""Tests for verification orchestrator (Task 10)."""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from backtest.breadth_study.percentile_manifest import load_manifest
from backtest.breadth_study.percentile_verifier import run_verification


MANIFEST_PATH = Path(__file__).resolve().parents[1] / (
    "backtest/breadth_study/manifests/breadth_pctile_v1.json"
)


def _build_synthetic_inputs(n_days=1500, seed=11):
    """Build synthetic daily_breadth + target_prices_dict + target_returns.

    Daily breadth is a random walk to give meaningful percentile rank dynamics.
    """
    rng = np.random.default_rng(seed)
    dates = pd.bdate_range("2021-02-01", periods=n_days)
    # Breadth as random walk in [0, 1] via logit-transformed AR(1)
    z20 = np.cumsum(rng.normal(0, 0.05, n_days))
    z50 = np.cumsum(rng.normal(0, 0.04, n_days))
    breadth_20 = 1.0 / (1.0 + np.exp(-z20))
    breadth_50 = 1.0 / (1.0 + np.exp(-z50))
    daily_breadth = pd.DataFrame({
        "date": dates,
        "breadth_20": breadth_20,
        "breadth_50": breadth_50,
    })

    targets = ["SPY", "QQQ", "SOXX", "IWM", "XLK"]
    horizons = [5, 10, 20, 60]
    target_prices_dict = {}
    target_returns_data = {"date": dates}
    for tgt in targets:
        rets = rng.normal(0.0003, 0.012, n_days)
        closes = 100.0 * np.cumprod(1 + rets)
        opens = np.concatenate([[100.0], closes[:-1]])
        target_prices_dict[tgt] = pd.DataFrame({
            "date": dates, "open": opens, "close": closes,
        })
        for h in horizons:
            shifted_close = pd.Series(closes).shift(-h)
            entry_open = pd.Series(opens).shift(-1)
            fwd_ret = (shifted_close / entry_open - 1).to_numpy()
            target_returns_data[f"{tgt}_fwd_{h}d"] = fwd_ret
    target_returns = pd.DataFrame(target_returns_data)
    return daily_breadth, target_prices_dict, target_returns


@pytest.fixture(scope="module")
def manifest():
    return load_manifest(MANIFEST_PATH)


@pytest.fixture(scope="module")
def synthetic_inputs():
    return _build_synthetic_inputs()


@pytest.fixture(scope="module")
def verification_outputs(manifest, synthetic_inputs):
    daily_breadth, target_prices_dict, target_returns = synthetic_inputs
    primary, sensitivity, table = run_verification(
        manifest, daily_breadth, target_prices_dict, target_returns,
    )
    return primary, sensitivity, table


def test_verification_outputs_three_tables(verification_outputs):
    primary, sensitivity, table = verification_outputs
    assert len(primary) == 12
    assert len(sensitivity) == 12
    assert len(table) == 240


def test_primary_and_sensitivity_share_schema(verification_outputs):
    primary, sensitivity, _ = verification_outputs
    assert list(primary.columns) == list(sensitivity.columns)
    assert (primary["primary_cell"] == "SPY_10d").all()
    assert (sensitivity["primary_cell"] == "QQQ_10d").all()


def test_param_summary_passes_count_in_range_0_to_6(verification_outputs):
    primary, sensitivity, _ = verification_outputs
    assert primary["passes_count_param"].between(0, 6).all()
    assert sensitivity["passes_count_param"].between(0, 6).all()
    # Schema sanity
    for col in [
        "h1_freq_pass", "h2_hit_pass", "h3_target_pass",
        "h4_short_horizon_pass", "h5_perm_pass", "h6_strategy_pass",
        "long_horizon_diff", "excess_cagr_ci_low", "excess_cagr_ci_high",
        "excess_cagr_share_negative", "perm_sampling_method", "perm_success_rate",
    ]:
        assert col in primary.columns


def test_long_horizon_diff_does_not_affect_passes(verification_outputs):
    """passes_count_param should only sum h1..h6, not be influenced by long_horizon_diff."""
    primary, _, _ = verification_outputs
    # Confirm passes_count == sum of h1..h6 booleans
    bool_cols = ["h1_freq_pass", "h2_hit_pass", "h3_target_pass",
                  "h4_short_horizon_pass", "h5_perm_pass", "h6_strategy_pass"]
    for _, row in primary.iterrows():
        s = sum(int(bool(row[c])) for c in bool_cols)
        assert row["passes_count_param"] == s
