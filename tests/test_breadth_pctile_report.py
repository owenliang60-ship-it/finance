"""Tests for verification report generator (Task 12)."""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from backtest.breadth_study.percentile_manifest import (
    load_manifest,
    manifest_sha256,
)
from backtest.breadth_study.percentile_report import (
    VERDICT_ISOLATED,
    VERDICT_PROMOTE,
    VERDICT_REJECT,
    render_report,
    write_report,
)


MANIFEST_PATH = Path(__file__).resolve().parents[1] / (
    "backtest/breadth_study/manifests/breadth_pctile_v1.json"
)


@pytest.fixture(scope="module")
def manifest():
    return load_manifest(MANIFEST_PATH)


@pytest.fixture(scope="module")
def manifest_sha(manifest):
    return manifest_sha256(MANIFEST_PATH)


def _make_summary_row(ma, K, et, passes, *, primary_cell="SPY_10d"):
    """Build a synthetic param_summary row with all required columns."""
    return {
        "ma_window": ma,
        "threshold": K,
        "event_type": et,
        "primary_cell": primary_cell,
        "event_n_short": 12,
        "event_n_long": 4,
        "first_valid_date": "2022-01-31",
        "last_date": "2026-04-28",
        "effective_years": 4.25,
        "events_per_year": 2.8,
        "event_hit": 60.0,
        "baseline_hit": 50.0,
        "hit_lift_pp": 10.0,
        "perm_p": 0.04 if passes >= 4 else 0.20,
        "perm_sampling_method": "rejection",
        "perm_success_rate": 0.85,
        "strategy_cagr_pp": 8.0,
        "bnh_cagr_pp": 4.0,
        "excess_cagr_pp": 4.0 if passes >= 4 else 1.0,
        "excess_cagr_ci_low": 1.0,
        "excess_cagr_ci_high": 6.0 if passes >= 4 else 3.0,
        "excess_cagr_share_negative": 0.05,
        "n_trades": 12,
        "exposure_pct": 22.0,
        "target_same_sign_count": 4 if passes >= 3 else 1,
        "target_same_sign_targets": "SPY,QQQ,SOXX,IWM",
        "short_horizon_same_sign_count": 2,
        "short_horizon_same_sign_horizons": "5,10",
        "long_horizon_diff": 0.001,
        "h1_freq_pass": passes >= 1,
        "h2_hit_pass": passes >= 2,
        "h3_target_pass": passes >= 3,
        "h4_short_horizon_pass": passes >= 4,
        "h5_perm_pass": passes >= 5,
        "h6_strategy_pass": passes >= 6,
        "passes_count_param": passes,
    }


def _build_full_summary(passes_map, primary_cell="SPY_10d"):
    rows = []
    for ma in (20, 50):
        for K in (0.05, 0.10, 0.15):
            rows.append(_make_summary_row(
                ma, K, "low_recovery",
                passes_map.get((ma, K, "low_recovery"), 0),
                primary_cell=primary_cell,
            ))
        for K in (0.80, 0.90, 0.95):
            rows.append(_make_summary_row(
                ma, K, "high_strength",
                passes_map.get((ma, K, "high_strength"), 0),
                primary_cell=primary_cell,
            ))
    return pd.DataFrame(rows)


def _build_table_240():
    """Build a synthetic 240-row diagnostic table with the right schema."""
    rows = []
    for ma in (20, 50):
        for K in (0.05, 0.10, 0.15, 0.80, 0.90, 0.95):
            et = "low_recovery" if K < 0.5 else "high_strength"
            for tgt in ("SPY", "QQQ", "SOXX", "IWM", "XLK"):
                for h in (5, 10, 20, 60):
                    rows.append({
                        "ma_window": ma, "threshold": K, "event_type": et,
                        "target": tgt, "horizon": h,
                        "event_n": 5, "event_mean": 0.001, "non_event_mean": 0.0,
                        "mean_diff": 0.001, "hit_rate": 0.55, "non_event_hit_rate": 0.5,
                    })
    return pd.DataFrame(rows)


def test_report_header_contains_manifest_metadata(manifest, manifest_sha):
    summary = _build_full_summary({})
    body = render_report(
        manifest=manifest, manifest_sha256=manifest_sha,
        primary_summary=summary, sensitivity_summary=summary,
        verification_table=_build_table_240(),
    )
    assert manifest_sha in body
    assert manifest["version"] in body
    assert manifest["frozen_at"] in body


def test_effective_sample_years_appears_with_value(manifest, manifest_sha):
    summary = _build_full_summary({})
    body = render_report(
        manifest=manifest, manifest_sha256=manifest_sha,
        primary_summary=summary, sensitivity_summary=summary,
        verification_table=_build_table_240(),
    )
    assert "effective years" in body
    assert "4.25" in body  # value from fixture


def test_verdict_reject_when_no_clusters(manifest, manifest_sha):
    summary = _build_full_summary({})  # all zeros
    body = render_report(
        manifest=manifest, manifest_sha256=manifest_sha,
        primary_summary=summary, sensitivity_summary=summary,
        verification_table=_build_table_240(),
    )
    assert VERDICT_REJECT in body


def test_verdict_promote_when_both_primary_and_sensitivity_share_cluster(
    manifest, manifest_sha
):
    cluster_passes = {
        (20, 0.05, "low_recovery"): 5,
        (20, 0.10, "low_recovery"): 5,
    }
    primary = _build_full_summary(cluster_passes, primary_cell="SPY_10d")
    sensitivity = _build_full_summary(cluster_passes, primary_cell="QQQ_10d")
    body = render_report(
        manifest=manifest, manifest_sha256=manifest_sha,
        primary_summary=primary, sensitivity_summary=sensitivity,
        verification_table=_build_table_240(),
    )
    assert VERDICT_PROMOTE in body


def test_verdict_isolated_when_only_primary_has_cluster(manifest, manifest_sha):
    primary = _build_full_summary({
        (20, 0.05, "low_recovery"): 5,
        (20, 0.10, "low_recovery"): 5,
    }, primary_cell="SPY_10d")
    sensitivity = _build_full_summary({}, primary_cell="QQQ_10d")
    body = render_report(
        manifest=manifest, manifest_sha256=manifest_sha,
        primary_summary=primary, sensitivity_summary=sensitivity,
        verification_table=_build_table_240(),
    )
    assert VERDICT_ISOLATED in body


def test_sensitivity_comparison_table_has_12_rows(manifest, manifest_sha):
    summary = _build_full_summary({})
    body = render_report(
        manifest=manifest, manifest_sha256=manifest_sha,
        primary_summary=summary, sensitivity_summary=summary,
        verification_table=_build_table_240(),
    )
    # The sensitivity comparison block contains 12 data rows after the header line
    block = body.split("## Sensitivity Comparison")[1].split("##")[0]
    # Count markdown table data rows (start with `|` and have at least 4 pipes)
    data_rows = [l for l in block.splitlines() if l.startswith("|")]
    # 1 header + 1 separator + 12 data = 14
    assert len(data_rows) == 14


def test_report_has_bootstrap_ci_section(manifest, manifest_sha):
    summary = _build_full_summary({})
    body = render_report(
        manifest=manifest, manifest_sha256=manifest_sha,
        primary_summary=summary, sensitivity_summary=summary,
        verification_table=_build_table_240(),
    )
    assert "Bootstrap CI for Excess CAGR" in body
    assert "excess_cagr_ci_low" in body or "ci_low" in body


def test_report_has_perm_diagnostics_section(manifest, manifest_sha):
    summary = _build_full_summary({})
    body = render_report(
        manifest=manifest, manifest_sha256=manifest_sha,
        primary_summary=summary, sensitivity_summary=summary,
        verification_table=_build_table_240(),
    )
    assert "Permutation Diagnostics" in body
    assert "perm_sampling_method" in body or "rejection" in body


def test_collapsible_240_row_table_present(manifest, manifest_sha):
    summary = _build_full_summary({})
    body = render_report(
        manifest=manifest, manifest_sha256=manifest_sha,
        primary_summary=summary, sensitivity_summary=summary,
        verification_table=_build_table_240(),
    )
    assert "<details>" in body
    assert "240-row Verification Table" in body


def test_write_report_persists_to_disk(tmp_path, manifest, manifest_sha):
    summary = _build_full_summary({})
    out_path = tmp_path / "report.md"
    body = write_report(
        out_path,
        manifest=manifest, manifest_sha256=manifest_sha,
        primary_summary=summary, sensitivity_summary=summary,
        verification_table=_build_table_240(),
    )
    assert out_path.exists()
    assert out_path.read_text() == body
    assert manifest_sha in out_path.read_text()


def test_report_is_deterministic_for_same_inputs(manifest, manifest_sha):
    summary = _build_full_summary({})
    body1 = render_report(
        manifest=manifest, manifest_sha256=manifest_sha,
        primary_summary=summary, sensitivity_summary=summary,
        verification_table=_build_table_240(),
    )
    body2 = render_report(
        manifest=manifest, manifest_sha256=manifest_sha,
        primary_summary=summary, sensitivity_summary=summary,
        verification_table=_build_table_240(),
    )
    assert body1 == body2
