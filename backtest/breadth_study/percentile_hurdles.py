"""Hurdle helpers H1-H4 + effective sample years (Tasks 4-7).

Each hurdle is a pure-function boolean check operating on aggregates already
produced upstream (events list, forward-return arrays, mean diffs by target /
horizon). The orchestrator (Task 10) is responsible for slicing the data
into the inputs each hurdle expects.
"""
from __future__ import annotations

from typing import Dict, Tuple

import numpy as np
import pandas as pd


# ---------------- effective sample years ----------------


def compute_effective_sample_years(
    signal: pd.Series,
    dates: pd.Series,
) -> Tuple[pd.Timestamp, pd.Timestamp, float]:
    """Returns (first_valid_signal_date, last_date, effective_years).

    effective_years counts the number of non-NaN days from the first valid
    timestamp through the last valid timestamp, divided by 252.
    """
    valid = signal.notna()
    if not valid.any():
        raise ValueError("signal has no valid (non-NaN) values")
    valid_arr = valid.to_numpy()
    first_pos = int(np.argmax(valid_arr))
    # Last True position
    last_pos = len(valid_arr) - 1 - int(np.argmax(valid_arr[::-1]))
    first_date = pd.Timestamp(dates.iloc[first_pos])
    last_date = pd.Timestamp(dates.iloc[last_pos])
    n_valid_days = int(valid_arr[first_pos : last_pos + 1].sum())
    return first_date, last_date, n_valid_days / 252.0


# ---------------- H1 ----------------


def check_h1_trigger_frequency(
    events_count: int,
    effective_years: float,
    lo: float,
    hi: float,
) -> bool:
    """Pass if `events_count / effective_years` is within [lo, hi]."""
    if effective_years <= 0:
        return False
    rate = events_count / effective_years
    return lo <= rate <= hi


# ---------------- H2 ----------------


def check_h2_hit_rate_lift(
    event_rets: np.ndarray,
    non_event_rets: np.ndarray,
    min_lift_pp: float,
) -> bool:
    """Pass if event hit-rate exceeds non-event hit-rate by at least `min_lift_pp`.

    Hit = forward return > 0. `min_lift_pp` is in *percentage points*.
    """
    event_arr = np.asarray(event_rets, dtype=float)
    non_event_arr = np.asarray(non_event_rets, dtype=float)
    event_arr = event_arr[~np.isnan(event_arr)]
    non_event_arr = non_event_arr[~np.isnan(non_event_arr)]
    if event_arr.size == 0 or non_event_arr.size == 0:
        return False
    event_hit_pp = float((event_arr > 0).mean()) * 100.0
    base_hit_pp = float((non_event_arr > 0).mean()) * 100.0
    return bool((event_hit_pp - base_hit_pp) >= min_lift_pp)


# ---------------- H3 ----------------


def check_h3_target_consistency(
    target_diffs: Dict[str, float],
    expected_sign: int,
    min_count: int,
) -> bool:
    """Pass if at least `min_count` targets have mean_diff matching `expected_sign`.

    `expected_sign` is +1 (positive) or -1 (negative).
    NaN diffs are skipped (count as no contribution to either side).
    """
    if expected_sign not in (-1, 1):
        raise ValueError("expected_sign must be -1 or +1")
    same = 0
    for v in target_diffs.values():
        if v is None or (isinstance(v, float) and np.isnan(v)):
            continue
        if v > 0 and expected_sign > 0:
            same += 1
        elif v < 0 and expected_sign < 0:
            same += 1
    return same >= min_count


# ---------------- H4 (v1.2 method C: short horizons only) ----------------


def check_h4_short_horizon_consistency(
    horizon_diffs: Dict[int, float],
    expected_sign: int,
    min_count: int,
) -> bool:
    """Pass if at least `min_count` short horizons have mean_diff matching `expected_sign`.

    Short horizons are 5/10/20d (60d is informational, evaluated separately).
    """
    if expected_sign not in (-1, 1):
        raise ValueError("expected_sign must be -1 or +1")
    same = 0
    for v in horizon_diffs.values():
        if v is None or (isinstance(v, float) and np.isnan(v)):
            continue
        if v > 0 and expected_sign > 0:
            same += 1
        elif v < 0 and expected_sign < 0:
            same += 1
    return same >= min_count
