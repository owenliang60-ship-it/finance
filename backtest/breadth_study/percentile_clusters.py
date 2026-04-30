"""Cluster pattern detector (Task 11).

A *cluster* is a run of >= 2 parameters that all pass the hurdle gate
(``passes_count_param >= pass_threshold``) **and** sit on adjacent thresholds
within the same ``ma_window`` and ``event_type``.

Adjacency is defined by ``|K_i - K_j| <= 0.06`` between consecutive thresholds
when the group is sorted ascending — tolerance covers floating point and
matches the plan's ``±0.05`` adjacency rule. ``low_recovery`` thresholds
(0.05/0.10/0.15) are all adjacent in pairs; ``high_strength`` (0.80/0.90/0.95)
splits into {0.80} and {0.90, 0.95} so the 0.80↔0.90 0.10-gap is *not*
adjacent. Cross-MA and cross-event_type passes never form a cluster.

Isolated passes (single param passing in its (ma, event_type) bucket) are not
returned here; the report layer surfaces them separately.
"""
from __future__ import annotations

from typing import Any, Dict, List

import pandas as pd


_ADJACENCY_TOLERANCE = 0.06


def _summarise_run(ma: int, event_type: str, run: List[pd.Series]) -> Dict[str, Any]:
    thresholds = [float(r["threshold"]) for r in run]
    passes_per_param = {
        f"{ma}_{r['threshold']:.2f}_{event_type}": int(r["passes_count_param"])
        for r in run
    }
    return {
        "ma_window": int(ma),
        "event_type": event_type,
        "thresholds": thresholds,
        "passes_per_param": passes_per_param,
        "size": len(run),
        "min_passes": min(int(r["passes_count_param"]) for r in run),
        "max_passes": max(int(r["passes_count_param"]) for r in run),
        "summary": (
            f"MA{ma} {event_type} cluster of {len(run)} adjacent thresholds "
            f"({thresholds}); passes "
            f"{[int(r['passes_count_param']) for r in run]}"
        ),
    }


def detect_cluster_patterns(
    param_summary: pd.DataFrame,
    pass_threshold: int = 4,
) -> List[Dict[str, Any]]:
    """Return adjacent-threshold clusters of passing parameters.

    Parameters
    ----------
    param_summary
        12-row DataFrame produced by ``run_verification`` (primary or
        sensitivity table). Must carry ``ma_window``, ``threshold``,
        ``event_type``, ``passes_count_param`` columns.
    pass_threshold
        Minimum ``passes_count_param`` for a row to qualify as "passing".
        Default 4 (matches ``manifest["pass_threshold"]``).

    Returns
    -------
    list of dicts, one per cluster (size >= 2). Empty list if no clusters.
    """
    required = {"ma_window", "threshold", "event_type", "passes_count_param"}
    missing = required - set(param_summary.columns)
    if missing:
        raise KeyError(f"param_summary missing columns: {missing}")

    passing = param_summary[
        param_summary["passes_count_param"] >= pass_threshold
    ].copy()
    if passing.empty:
        return []

    clusters: List[Dict[str, Any]] = []
    for (ma, event_type), group in passing.groupby(["ma_window", "event_type"]):
        if len(group) < 2:
            continue
        sorted_group = group.sort_values("threshold").reset_index(drop=True)
        run: List[pd.Series] = [sorted_group.iloc[0]]
        for i in range(1, len(sorted_group)):
            prev_K = float(run[-1]["threshold"])
            cur = sorted_group.iloc[i]
            cur_K = float(cur["threshold"])
            if (cur_K - prev_K) <= _ADJACENCY_TOLERANCE:
                run.append(cur)
            else:
                if len(run) >= 2:
                    clusters.append(_summarise_run(int(ma), str(event_type), run))
                run = [cur]
        if len(run) >= 2:
            clusters.append(_summarise_run(int(ma), str(event_type), run))
    return clusters
