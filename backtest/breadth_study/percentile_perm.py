"""H5 year-stratified permutation with sequential fallback (Task 8).

Null hypothesis: event-aligned forward-return mean is no different from a
year-stratified random sample of dates respecting cooldown.

Two-phase sampler:
  1) probe rejection-based sampling on N=50 trials
  2) if rejection success rate < `fallback_to_sequential_below`, switch to
     sequential sampling (deterministic per-trial success unless year is
     truly impossible).
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd


logger = logging.getLogger(__name__)


def _compute_event_diff(
    event_dates: List[pd.Timestamp],
    panel: pd.DataFrame,
    return_col: str,
) -> float:
    """mean(forward returns on event dates) - mean(forward returns on non-event dates)."""
    panel_dates = panel["date"]
    is_event = panel_dates.isin(set(event_dates))
    rets = panel[return_col]
    event_arr = rets[is_event].dropna().to_numpy()
    non_event_arr = rets[~is_event].dropna().to_numpy()
    if event_arr.size == 0 or non_event_arr.size == 0:
        return float("nan")
    return float(event_arr.mean() - non_event_arr.mean())


def _sample_with_rejection(
    pool_sorted: List[int],
    n: int,
    cooldown_days: int,
    rng: np.random.Generator,
    max_attempts: int,
) -> Optional[List[int]]:
    """Pick `n` indexes from `pool_sorted` such that pairwise gap >= cooldown_days.

    Returns sorted positional indices or None if no valid sample found in
    `max_attempts` tries.
    """
    pool_arr = np.asarray(pool_sorted)
    if len(pool_arr) < n:
        return None
    for _ in range(max_attempts):
        picks = sorted(rng.choice(pool_arr, size=n, replace=False).tolist())
        ok = all((picks[i + 1] - picks[i]) >= cooldown_days for i in range(n - 1))
        if ok:
            return picks
    return None


def _try_one_rejection_trial(
    events_per_year: Dict[int, int],
    dates_by_year: Dict[int, List[pd.Timestamp]],
    cooldown_days: int,
    rng: np.random.Generator,
    max_attempts: int,
) -> Optional[List[pd.Timestamp]]:
    fake_dates: List[pd.Timestamp] = []
    for year, n in events_per_year.items():
        pool = dates_by_year.get(year, [])
        if len(pool) < n:
            return None
        date_to_pos = {d: i for i, d in enumerate(pool)}
        sample_positions = _sample_with_rejection(
            list(range(len(pool))), n, cooldown_days, rng, max_attempts
        )
        if sample_positions is None:
            return None
        fake_dates.extend(pool[i] for i in sample_positions)
    return fake_dates


def _sample_sequential(
    events_per_year: Dict[int, int],
    dates_by_year: Dict[int, List[pd.Timestamp]],
    cooldown_days: int,
    rng: np.random.Generator,
) -> Optional[List[pd.Timestamp]]:
    """Provably-correct uniform sampler: maps to choose-n-from-reduced-range trick.

    For pool of size P, picking n positions x_0 < ... < x_{n-1} with
    pairwise gap >= cooldown is in bijection with picking n strictly-
    increasing positions y_0 < ... < y_{n-1} from {0, ..., P-1-(n-1)*c}
    via x_i = y_i + i*cooldown. Always succeeds when feasible.
    """
    fake_dates: List[pd.Timestamp] = []
    for year, n in events_per_year.items():
        pool = sorted(dates_by_year.get(year, []))
        pool_size = len(pool)
        if pool_size < n:
            return None
        reduced_size = pool_size - (n - 1) * cooldown_days
        if reduced_size < n:
            # Infeasible even with optimal packing
            return None
        ys = np.sort(rng.choice(reduced_size, size=n, replace=False))
        xs = [int(ys[i]) + i * cooldown_days for i in range(n)]
        fake_dates.extend(pool[x] for x in xs)
    return fake_dates


def year_stratified_permutation_p(
    event_dates: List[pd.Timestamp],
    forward_returns_panel: pd.DataFrame,
    target: str,
    horizon: int,
    cooldown_days: int,
    trials: int,
    seed: int,
    rejection_max_attempts_per_event: int = 50,
    fallback_to_sequential_below: float = 0.30,
    warning_threshold: float = 0.70,
) -> Tuple[float, np.ndarray, Dict[str, Any]]:
    """Compute year-stratified permutation p-value for the (target, horizon) cell.

    Returns (p_value, null_distribution, diagnostics) where diagnostics contains
    `sampling_method_used`, `n_trials_succeeded`, `success_rate`,
    `rejection_probe_rate`.
    """
    return_col = f"{target}_fwd_{horizon}d"
    if return_col not in forward_returns_panel.columns:
        raise KeyError(
            f"forward_returns_panel missing column {return_col!r}; "
            f"available={list(forward_returns_panel.columns)}"
        )
    panel = forward_returns_panel.dropna(subset=[return_col]).copy()
    panel["year"] = pd.to_datetime(panel["date"]).dt.year

    # event_dates as Timestamps
    event_dates = [pd.Timestamp(d) for d in event_dates]
    event_dates = [d for d in event_dates if d in set(panel["date"])]
    if not event_dates:
        return 1.0, np.array([]), {
            "rejection_probe_rate": 0.0,
            "sampling_method_used": "n/a",
            "n_trials_succeeded": 0,
            "n_trials_attempted": 0,
            "success_rate": 0.0,
        }

    real_diff = _compute_event_diff(event_dates, panel, return_col)
    if not np.isfinite(real_diff):
        return 1.0, np.array([]), {
            "rejection_probe_rate": 0.0,
            "sampling_method_used": "n/a",
            "n_trials_succeeded": 0,
            "n_trials_attempted": 0,
            "success_rate": 0.0,
        }

    events_per_year = (
        pd.Series(event_dates).dt.year.value_counts().sort_index().to_dict()
    )
    dates_by_year: Dict[int, List[pd.Timestamp]] = {
        int(year): sorted(group["date"].tolist())
        for year, group in panel.groupby("year")
    }

    rng = np.random.default_rng(seed)

    # Phase 1: probe rejection sampling
    probe_n = max(1, min(50, trials // 10))
    probe_success = 0
    for _ in range(probe_n):
        out = _try_one_rejection_trial(
            events_per_year, dates_by_year, cooldown_days,
            rng, rejection_max_attempts_per_event,
        )
        if out is not None:
            probe_success += 1
    probe_rate = probe_success / probe_n

    if probe_rate < fallback_to_sequential_below:
        sampling_method = "sequential"
    else:
        sampling_method = "rejection"

    null_diffs: List[float] = []
    for _ in range(trials):
        if sampling_method == "rejection":
            fake_dates = _try_one_rejection_trial(
                events_per_year, dates_by_year, cooldown_days,
                rng, rejection_max_attempts_per_event,
            )
        else:
            fake_dates = _sample_sequential(
                events_per_year, dates_by_year, cooldown_days, rng
            )
        if fake_dates is None:
            continue
        diff = _compute_event_diff(fake_dates, panel, return_col)
        if np.isfinite(diff):
            null_diffs.append(diff)

    success_rate = len(null_diffs) / trials if trials > 0 else 0.0
    if success_rate < warning_threshold:
        logger.warning(
            "Permutation low success_rate=%.2f method=%s target=%s horizon=%dd",
            success_rate, sampling_method, target, horizon,
        )

    null_arr = np.asarray(null_diffs, dtype=float)
    if null_arr.size == 0:
        p = 1.0
    else:
        # One-sided upper-tail p-value (events expected to increase forward returns)
        p = float((np.sum(null_arr >= real_diff) + 1) / (null_arr.size + 1))

    diagnostics: Dict[str, Any] = {
        "rejection_probe_rate": float(probe_rate),
        "sampling_method_used": sampling_method,
        "n_trials_succeeded": int(null_arr.size),
        "n_trials_attempted": int(trials),
        "success_rate": float(success_rate),
        "real_diff": float(real_diff),
    }
    return p, null_arr, diagnostics


def check_h5_permutation(p_value: float, max_p: float = 0.05) -> bool:
    """Pass if p_value strictly less than max_p."""
    return bool(p_value < max_p)
