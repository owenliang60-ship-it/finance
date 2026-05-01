"""Event-validity statistics for breadth upcross signals.

This module answers a narrower question than the event-strategy CAGR test:

    After an upcross event fires, are forward returns statistically better
    than comparable non-event dates?

It does not compare a sparse event strategy against 100% buy-and-hold.
"""
from __future__ import annotations

from typing import Any, Dict, List, Tuple

import numpy as np
import pandas as pd

from backtest.breadth_study.percentile_events import detect_upcross_events
from backtest.breadth_study.percentile_hurdles import compute_effective_sample_years
from backtest.breadth_study.percentile_perm import year_stratified_permutation_p
from backtest.breadth_study.percentile_verifier import (
    _build_signal_for_manifest,
    _enumerate_params,
)


def _slice_returns_in_window(
    target_returns: pd.DataFrame,
    first_valid_date: pd.Timestamp,
    last_date: pd.Timestamp,
) -> pd.DataFrame:
    return target_returns[
        (target_returns["date"] >= pd.Timestamp(first_valid_date))
        & (target_returns["date"] <= pd.Timestamp(last_date))
    ].reset_index(drop=True)


def _event_and_non_event_returns(
    panel: pd.DataFrame,
    return_col: str,
    event_dates: List[pd.Timestamp],
) -> Tuple[np.ndarray, np.ndarray]:
    is_event = panel["date"].isin({pd.Timestamp(d) for d in event_dates})
    rets = panel[return_col]
    return (
        rets[is_event].dropna().to_numpy(dtype=float),
        rets[~is_event].dropna().to_numpy(dtype=float),
    )


def bootstrap_mean_lift_ci(
    event_returns: np.ndarray,
    non_event_returns: np.ndarray,
    *,
    trials: int,
    seed: int,
    ci_lower_pct: float,
    ci_upper_pct: float,
) -> Dict[str, float]:
    """Bootstrap CI for mean(event_returns) - mean(non_event_returns)."""
    event_arr = np.asarray(event_returns, dtype=float)
    base_arr = np.asarray(non_event_returns, dtype=float)
    event_arr = event_arr[np.isfinite(event_arr)]
    base_arr = base_arr[np.isfinite(base_arr)]
    if event_arr.size == 0 or base_arr.size == 0:
        return {
            "mean_lift_point": float("nan"),
            "mean_lift_ci_low": float("nan"),
            "mean_lift_ci_high": float("nan"),
            "mean_lift_share_nonpositive": float("nan"),
            "n_trials": 0,
        }

    point = float(event_arr.mean() - base_arr.mean())
    rng = np.random.default_rng(seed)
    samples: List[float] = []
    for _ in range(trials):
        ev = rng.choice(event_arr, size=event_arr.size, replace=True)
        base = rng.choice(base_arr, size=base_arr.size, replace=True)
        samples.append(float(ev.mean() - base.mean()))
    sample_arr = np.asarray(samples, dtype=float)
    return {
        "mean_lift_point": point,
        "mean_lift_ci_low": float(np.percentile(sample_arr, ci_lower_pct)),
        "mean_lift_ci_high": float(np.percentile(sample_arr, ci_upper_pct)),
        "mean_lift_share_nonpositive": float((sample_arr <= 0).mean()),
        "n_trials": int(sample_arr.size),
    }


def _cell_metrics(
    *,
    manifest: Dict[str, Any],
    panel: pd.DataFrame,
    event_dates: List[pd.Timestamp],
    target: str,
    horizon: int,
    cooldown_days: int,
    bootstrap_seed: int,
) -> Dict[str, Any]:
    return_col = f"{target}_fwd_{horizon}d"
    if return_col not in panel.columns:
        raise KeyError(f"target_returns missing column {return_col!r}")

    event_rets, non_event_rets = _event_and_non_event_returns(
        panel, return_col, event_dates
    )
    if event_rets.size == 0 or non_event_rets.size == 0:
        event_mean = baseline_mean = mean_lift = float("nan")
        event_median = baseline_median = median_lift = float("nan")
        event_hit = baseline_hit = hit_lift = float("nan")
    else:
        event_mean = float(event_rets.mean())
        baseline_mean = float(non_event_rets.mean())
        mean_lift = event_mean - baseline_mean
        event_median = float(np.median(event_rets))
        baseline_median = float(np.median(non_event_rets))
        median_lift = event_median - baseline_median
        event_hit = float((event_rets > 0).mean())
        baseline_hit = float((non_event_rets > 0).mean())
        hit_lift = event_hit - baseline_hit

    perm_cfg = manifest["permutation"]
    perm_p, _, perm_diag = year_stratified_permutation_p(
        event_dates,
        panel,
        target=target,
        horizon=horizon,
        cooldown_days=cooldown_days,
        trials=perm_cfg["trials"],
        seed=perm_cfg["seed"] + bootstrap_seed,
        rejection_max_attempts_per_event=perm_cfg["rejection_max_attempts_per_event"],
        fallback_to_sequential_below=perm_cfg["fallback_to_sequential_below"],
        warning_threshold=perm_cfg["warning_threshold"],
    )

    boot_cfg = manifest.get("event_validity_bootstrap", manifest["strategy_bootstrap"])
    ci = bootstrap_mean_lift_ci(
        event_rets,
        non_event_rets,
        trials=boot_cfg["trials"],
        seed=int(boot_cfg["seed"]) + bootstrap_seed,
        ci_lower_pct=boot_cfg["ci_lower_pct"],
        ci_upper_pct=boot_cfg["ci_upper_pct"],
    )

    return {
        "event_n": int(event_rets.size),
        "non_event_n": int(non_event_rets.size),
        "event_mean_pct": event_mean * 100.0,
        "baseline_mean_pct": baseline_mean * 100.0,
        "mean_lift_pp": mean_lift * 100.0,
        "event_median_pct": event_median * 100.0,
        "baseline_median_pct": baseline_median * 100.0,
        "median_lift_pp": median_lift * 100.0,
        "event_hit_pct": event_hit * 100.0,
        "baseline_hit_pct": baseline_hit * 100.0,
        "hit_lift_pp": hit_lift * 100.0,
        "perm_p": perm_p,
        "perm_sampling_method": perm_diag["sampling_method_used"],
        "perm_success_rate": perm_diag["success_rate"],
        "bootstrap_mean_lift_ci_low_pp": ci["mean_lift_ci_low"] * 100.0,
        "bootstrap_mean_lift_ci_high_pp": ci["mean_lift_ci_high"] * 100.0,
        "bootstrap_share_nonpositive": ci["mean_lift_share_nonpositive"],
    }


def run_event_validity(
    manifest: Dict[str, Any],
    daily_breadth: pd.DataFrame,
    target_returns: pd.DataFrame,
) -> Tuple[pd.DataFrame, pd.DataFrame]:
    """Return (event_validity_table, event_validity_summary)."""
    table_rows: List[Dict[str, Any]] = []

    horizons = sorted(set(manifest["horizons_short"] + manifest["horizons_long"]))
    for ma, threshold, event_type in _enumerate_params(manifest):
        breadth_col = f"breadth_{ma}"
        if breadth_col not in daily_breadth.columns:
            raise KeyError(f"daily_breadth missing column {breadth_col!r}")
        signal = _build_signal_for_manifest(manifest, daily_breadth, breadth_col)
        first_valid, last_date, effective_years = compute_effective_sample_years(
            signal, daily_breadth["date"]
        )
        panel = _slice_returns_in_window(target_returns, first_valid, last_date)
        date_series = daily_breadth["date"]

        for horizon in horizons:
            cooldown = (
                manifest["cooldown_short_horizon"]
                if horizon in manifest["horizons_short"]
                else manifest["cooldown_long_horizon"]
            )
            events = detect_upcross_events(
                signal, threshold=threshold, cooldown_days=cooldown
            )
            event_dates = [
                pd.Timestamp(date_series.iloc[ev["index"]])
                for ev in events
            ]
            for target_idx, target in enumerate(manifest["targets"]):
                bootstrap_seed = ma * 100_000 + int(round(threshold * 10_000)) + horizon * 100 + target_idx
                metrics = _cell_metrics(
                    manifest=manifest,
                    panel=panel,
                    event_dates=event_dates,
                    target=target,
                    horizon=int(horizon),
                    cooldown_days=cooldown,
                    bootstrap_seed=bootstrap_seed,
                )
                table_rows.append({
                    "ma_window": int(ma),
                    "threshold": float(threshold),
                    "event_type": event_type,
                    "horizon": int(horizon),
                    "target": target,
                    "event_n_raw": len(event_dates),
                    "first_valid_date": pd.Timestamp(first_valid).strftime("%Y-%m-%d"),
                    "last_date": pd.Timestamp(last_date).strftime("%Y-%m-%d"),
                    "effective_years": round(float(effective_years), 4),
                    "events_per_year": (
                        round(len(event_dates) / effective_years, 4)
                        if effective_years > 0 else float("nan")
                    ),
                    **metrics,
                })

    table = pd.DataFrame(table_rows)
    summary = summarize_event_validity(table, manifest)
    return table, summary


def summarize_event_validity(
    table: pd.DataFrame,
    manifest: Dict[str, Any],
) -> pd.DataFrame:
    """Aggregate cell rows into one row per (ma, threshold, event_type, horizon)."""
    p_max = float(manifest["hurdle_thresholds"]["h5_permutation_p_max"])
    rows: List[Dict[str, Any]] = []
    group_cols = ["ma_window", "threshold", "event_type", "horizon"]
    for key, group in table.groupby(group_cols, sort=True):
        ma, threshold, event_type, horizon = key
        positive_mean = group[group["mean_lift_pp"] > 0]["target"].tolist()
        positive_hit = group[group["hit_lift_pp"] > 0]["target"].tolist()
        perm_sig = group[group["perm_p"] < p_max]["target"].tolist()
        boot_pos = group[group["bootstrap_mean_lift_ci_low_pp"] > 0]["target"].tolist()
        all_positive_mean = len(positive_mean) == len(manifest["targets"])
        all_positive_hit = len(positive_hit) == len(manifest["targets"])
        score = (
            len(positive_mean)
            + len(positive_hit)
            + 2 * len(perm_sig)
            + 2 * len(boot_pos)
        )
        rows.append({
            "ma_window": int(ma),
            "threshold": float(threshold),
            "event_type": event_type,
            "horizon": int(horizon),
            "event_n_min": int(group["event_n"].min()),
            "event_n_max": int(group["event_n"].max()),
            "avg_mean_lift_pp": float(group["mean_lift_pp"].mean()),
            "min_mean_lift_pp": float(group["mean_lift_pp"].min()),
            "max_mean_lift_pp": float(group["mean_lift_pp"].max()),
            "avg_hit_lift_pp": float(group["hit_lift_pp"].mean()),
            "min_perm_p": float(group["perm_p"].min()),
            "max_perm_p": float(group["perm_p"].max()),
            "positive_mean_targets_count": len(positive_mean),
            "positive_mean_targets": ",".join(positive_mean),
            "positive_hit_targets_count": len(positive_hit),
            "positive_hit_targets": ",".join(positive_hit),
            "perm_sig_targets_count": len(perm_sig),
            "perm_sig_targets": ",".join(perm_sig),
            "bootstrap_positive_targets_count": len(boot_pos),
            "bootstrap_positive_targets": ",".join(boot_pos),
            "all_targets_positive_mean": bool(all_positive_mean),
            "all_targets_positive_hit": bool(all_positive_hit),
            "validity_score": int(score),
        })
    return pd.DataFrame(rows).sort_values(
        [
            "validity_score",
            "perm_sig_targets_count",
            "bootstrap_positive_targets_count",
            "positive_mean_targets_count",
            "avg_mean_lift_pp",
        ],
        ascending=[False, False, False, False, False],
    ).reset_index(drop=True)
