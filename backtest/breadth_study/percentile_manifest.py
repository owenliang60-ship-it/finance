"""Manifest loader for breadth percentile upcross verification (v1.2).

Pre-registered parameters are frozen in `manifests/breadth_pctile_v1.json`.
This module loads + validates schema + computes deterministic SHA256.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Dict


REQUIRED_TOP_KEYS = (
    "version",
    "frozen_at",
    "ma_windows",
    "thresholds",
    "percentile_lookback",
    "signal_smoother",
    "cooldown_short_horizon",
    "cooldown_long_horizon",
    "targets",
    "primary_target",
    "primary_horizon",
    "sensitivity_target",
    "sensitivity_horizon",
    "horizons_short",
    "horizons_long",
    "min_market_cap",
    "max_staleness_days",
    "from_date",
    "permutation",
    "strategy_costs",
    "strategy_bootstrap",
    "hurdle_thresholds",
    "pass_threshold",
)

REQUIRED_HURDLE_KEYS = (
    "h1_trigger_freq_min_per_year",
    "h1_trigger_freq_max_per_year",
    "h2_hit_rate_lift_pp",
    "h3_target_same_sign_min",
    "h4_short_horizon_same_sign_min",
    "h4_short_horizons",
    "h5_permutation_p_max",
    "h6_strategy_excess_cagr_pp",
)

REQUIRED_PERM_KEYS = (
    "trials",
    "seed",
    "stratify_by",
    "respect_cooldown",
    "rejection_max_attempts_per_event",
    "fallback_to_sequential_below",
    "warning_threshold",
)

REQUIRED_BOOTSTRAP_KEYS = (
    "trials",
    "seed",
    "ci_lower_pct",
    "ci_upper_pct",
)


class ManifestSchemaError(ValueError):
    """Raised when manifest fails schema validation."""


def load_manifest(path: Path) -> Dict[str, Any]:
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"Manifest not found: {path}")
    raw = path.read_text()
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ManifestSchemaError(f"Invalid JSON in {path}: {exc}") from exc

    missing = [k for k in REQUIRED_TOP_KEYS if k not in data]
    if missing:
        raise ManifestSchemaError(
            f"Manifest {path} missing required keys: {missing}"
        )

    hurdle = data.get("hurdle_thresholds", {})
    missing_h = [k for k in REQUIRED_HURDLE_KEYS if k not in hurdle]
    if missing_h:
        raise ManifestSchemaError(
            f"hurdle_thresholds missing keys: {missing_h}"
        )

    perm = data.get("permutation", {})
    missing_p = [k for k in REQUIRED_PERM_KEYS if k not in perm]
    if missing_p:
        raise ManifestSchemaError(f"permutation missing keys: {missing_p}")

    boot = data.get("strategy_bootstrap", {})
    missing_b = [k for k in REQUIRED_BOOTSTRAP_KEYS if k not in boot]
    if missing_b:
        raise ManifestSchemaError(
            f"strategy_bootstrap missing keys: {missing_b}"
        )

    if not isinstance(data["thresholds"], dict):
        raise ManifestSchemaError("thresholds must be dict")
    for sub in ("low_recovery", "high_strength"):
        if sub not in data["thresholds"]:
            raise ManifestSchemaError(
                f"thresholds.{sub} missing"
            )
        if not isinstance(data["thresholds"][sub], list):
            raise ManifestSchemaError(
                f"thresholds.{sub} must be list"
            )

    return data


def manifest_sha256(path: Path) -> str:
    """Deterministic SHA256 of the file bytes."""
    path = Path(path)
    return hashlib.sha256(path.read_bytes()).hexdigest()
