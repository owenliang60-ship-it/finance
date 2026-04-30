"""Tests for percentile upcross manifest loader (Task 1)."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from backtest.breadth_study.percentile_manifest import (
    ManifestSchemaError,
    load_manifest,
    manifest_sha256,
)

MANIFEST_PATH = Path(__file__).resolve().parents[1] / (
    "backtest/breadth_study/manifests/breadth_pctile_v1.json"
)


def test_manifest_loads_with_required_keys():
    m = load_manifest(MANIFEST_PATH)
    # version is v1.2 per plan freeze
    assert m["version"] == "v1.2"
    assert m["ma_windows"] == [20, 50]
    assert len(m["thresholds"]["low_recovery"]) == 3
    assert len(m["thresholds"]["high_strength"]) == 3
    assert m["percentile_lookback"] == 252
    assert m["cooldown_short_horizon"] == 20
    assert m["cooldown_long_horizon"] == 60
    assert m["primary_target"] == "SPY"
    assert m["primary_horizon"] == 10
    assert m["sensitivity_target"] == "QQQ"
    assert m["sensitivity_horizon"] == 10
    assert "h1_trigger_freq_min_per_year" in m["hurdle_thresholds"]
    assert m["hurdle_thresholds"]["h4_short_horizons"] == [5, 10, 20]
    assert m["pass_threshold"] == 4
    # Permutation diagnostics
    assert m["permutation"]["fallback_to_sequential_below"] == 0.30
    assert m["permutation"]["warning_threshold"] == 0.70
    # Bootstrap CI
    assert m["strategy_bootstrap"]["trials"] == 500


def test_sha256_stable():
    h1 = manifest_sha256(MANIFEST_PATH)
    h2 = manifest_sha256(MANIFEST_PATH)
    assert h1 == h2 and len(h1) == 64
    # All hex chars
    int(h1, 16)


def test_manifest_rejects_missing_keys(tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({"version": "v1"}))
    with pytest.raises(ManifestSchemaError):
        load_manifest(bad)
