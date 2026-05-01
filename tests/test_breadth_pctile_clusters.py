"""Tests for cluster pattern detector (Task 11)."""
from __future__ import annotations

import pandas as pd
import pytest

from backtest.breadth_study.percentile_clusters import detect_cluster_patterns


def _make_summary(rows):
    """rows: list of (ma, K, event_type, passes)."""
    return pd.DataFrame(
        [
            {"ma_window": ma, "threshold": K, "event_type": et, "passes_count_param": p}
            for (ma, K, et, p) in rows
        ]
    )


def _full_12_rows(passes_map=None):
    """Return all 12 manifest params with passes_count = passes_map.get((ma,K,et), 0)."""
    passes_map = passes_map or {}
    rows = []
    for ma in (20, 50):
        for K in (0.05, 0.10, 0.15):
            rows.append((ma, K, "low_recovery", passes_map.get((ma, K, "low_recovery"), 0)))
        for K in (0.80, 0.90, 0.95):
            rows.append((ma, K, "high_strength", passes_map.get((ma, K, "high_strength"), 0)))
    return _make_summary(rows)


def test_returns_empty_when_no_param_meets_threshold():
    df = _full_12_rows()  # all passes_count = 0
    assert detect_cluster_patterns(df, pass_threshold=4) == []


def test_returns_empty_when_only_isolated_passes():
    df = _full_12_rows({(20, 0.05, "low_recovery"): 5, (50, 0.95, "high_strength"): 6})
    assert detect_cluster_patterns(df, pass_threshold=4) == []


def test_two_adjacent_low_recovery_thresholds_form_cluster():
    df = _full_12_rows({
        (20, 0.05, "low_recovery"): 5,
        (20, 0.10, "low_recovery"): 4,
    })
    clusters = detect_cluster_patterns(df, pass_threshold=4)
    assert len(clusters) == 1
    c = clusters[0]
    assert c["ma_window"] == 20
    assert c["event_type"] == "low_recovery"
    assert c["thresholds"] == [0.05, 0.10]
    assert c["size"] == 2


def test_three_adjacent_low_recovery_thresholds_form_one_cluster_of_three():
    df = _full_12_rows({
        (50, 0.05, "low_recovery"): 4,
        (50, 0.10, "low_recovery"): 5,
        (50, 0.15, "low_recovery"): 6,
    })
    clusters = detect_cluster_patterns(df, pass_threshold=4)
    assert len(clusters) == 1
    assert clusters[0]["thresholds"] == [0.05, 0.10, 0.15]
    assert clusters[0]["size"] == 3
    assert clusters[0]["min_passes"] == 4
    assert clusters[0]["max_passes"] == 6


def test_cross_ma_does_not_form_cluster():
    df = _full_12_rows({
        (20, 0.05, "low_recovery"): 4,
        (50, 0.10, "low_recovery"): 4,
    })
    assert detect_cluster_patterns(df, pass_threshold=4) == []


def test_cross_event_type_does_not_form_cluster():
    """K=0.05 (low_recovery) and K=0.95 (high_strength) are in different families."""
    df = _full_12_rows({
        (20, 0.05, "low_recovery"): 5,
        (20, 0.95, "high_strength"): 5,
    })
    assert detect_cluster_patterns(df, pass_threshold=4) == []


def test_high_strength_080_and_090_not_adjacent_due_to_010_gap():
    """high_strength has [0.80, 0.90, 0.95]; 0.80↔0.90 gap is 0.10 > 0.06 tolerance."""
    df = _full_12_rows({
        (20, 0.80, "high_strength"): 4,
        (20, 0.90, "high_strength"): 4,
    })
    # 0.80 and 0.90 should NOT cluster
    clusters = detect_cluster_patterns(df, pass_threshold=4)
    assert clusters == []


def test_high_strength_090_and_095_form_cluster():
    df = _full_12_rows({
        (20, 0.90, "high_strength"): 4,
        (20, 0.95, "high_strength"): 5,
    })
    clusters = detect_cluster_patterns(df, pass_threshold=4)
    assert len(clusters) == 1
    assert clusters[0]["thresholds"] == [0.90, 0.95]


def test_pass_threshold_below_passing_count_filters_out():
    """If pass_threshold raised above the actual passes_count, no cluster."""
    df = _full_12_rows({
        (20, 0.05, "low_recovery"): 4,
        (20, 0.10, "low_recovery"): 4,
    })
    # both pass at threshold 4
    assert len(detect_cluster_patterns(df, pass_threshold=4)) == 1
    # but neither passes at threshold 5
    assert detect_cluster_patterns(df, pass_threshold=5) == []


def test_multi_cluster_in_same_summary():
    """Two distinct clusters: MA20 low_recovery + MA50 low_recovery."""
    df = _full_12_rows({
        (20, 0.05, "low_recovery"): 5,
        (20, 0.10, "low_recovery"): 4,
        (50, 0.10, "low_recovery"): 5,
        (50, 0.15, "low_recovery"): 6,
    })
    clusters = detect_cluster_patterns(df, pass_threshold=4)
    assert len(clusters) == 2
    mas = sorted(c["ma_window"] for c in clusters)
    assert mas == [20, 50]


def test_missing_columns_raises_keyerror():
    df = pd.DataFrame({"ma_window": [20], "threshold": [0.05]})
    with pytest.raises(KeyError):
        detect_cluster_patterns(df, pass_threshold=4)
