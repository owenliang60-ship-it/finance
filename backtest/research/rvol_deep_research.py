from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from typing import Dict, Iterable, List, Optional

import numpy as np
import pandas as pd
from scipy.stats import ttest_ind

from backtest.research.rvol_signal_stats import (
    RVOLSignalStatsConfig,
    _deoverlap_events,
    _pmarp_bucket_names,
)

EventMap = Dict[str, List[str]]
CohortMap = Dict[str, EventMap]


@dataclass(frozen=True)
class ConditionalLiftResult:
    comparison: str
    horizon: int
    accepted_events_raw: int
    accepted_events_dedup: int
    accepted_events_scored: int
    accepted_effective_dates: int
    rejected_events_raw: int
    rejected_events_dedup: int
    rejected_events_scored: int
    rejected_effective_dates: int
    accepted_mean: float
    rejected_mean: float
    mean_diff: float
    accepted_hit_rate: float
    rejected_hit_rate: float
    hit_rate_diff: float
    t_stat: float
    p_value: float


def build_pmarp_rvol_lift_cohorts(
    feature_frames: Dict[str, pd.DataFrame],
    config: RVOLSignalStatsConfig,
) -> CohortMap:
    cohorts: CohortMap = defaultdict(dict)

    for symbol, frame in feature_frames.items():
        ordered = frame.sort_values("date").reset_index(drop=True)
        for _, row in ordered.iterrows():
            date_str = str(row["date"])
            if date_str < config.study_start_date:
                continue
            if not bool(row.get("pmarp_up2", False)):
                continue

            _add_event(cohorts, "pmarp_up2_base", symbol, date_str)

            _add_accept_reject(
                cohorts=cohorts,
                accepted_label="pmarp_up2_accept_rvol_same_day",
                rejected_label="pmarp_up2_reject_rvol_same_day",
                accepted=bool(row.get("rvol_up2", False)),
                symbol=symbol,
                date_str=date_str,
            )
            _add_accept_reject(
                cohorts=cohorts,
                accepted_label="pmarp_up2_accept_rvol_recent3",
                rejected_label="pmarp_up2_reject_rvol_recent3",
                accepted=bool(row.get("rvol_recent_3d", False)),
                symbol=symbol,
                date_str=date_str,
            )
            _add_accept_reject(
                cohorts=cohorts,
                accepted_label="pmarp_up2_accept_rvol_recent5",
                rejected_label="pmarp_up2_reject_rvol_recent5",
                accepted=bool(row.get("rvol_recent_5d", False)),
                symbol=symbol,
                date_str=date_str,
            )

    return _plain_cohorts(cohorts)


def build_strong_state_rvol_cohorts(
    feature_frames: Dict[str, pd.DataFrame],
    config: RVOLSignalStatsConfig,
) -> CohortMap:
    cohorts: CohortMap = defaultdict(dict)
    low_label, _, high_label = _pmarp_bucket_names(
        low_cutoff=config.pmarp_low_cutoff,
        high_cutoff=config.pmarp_high_cutoff,
    )

    for symbol, frame in feature_frames.items():
        ordered = frame.sort_values("date").reset_index(drop=True)
        for _, row in ordered.iterrows():
            date_str = str(row["date"])
            if date_str < config.study_start_date:
                continue
            if not bool(row.get("rvol_up2", False)):
                continue

            pmarp_bucket = row.get("pmarp_bucket")
            event_day_sign = row.get("event_day_sign")
            close_bucket = row.get("close_location_bucket")

            if pmarp_bucket == high_label:
                _add_event(cohorts, "rvol_up2_pmarp_gte60", symbol, date_str)
                if event_day_sign == "sign_pos":
                    _add_event(cohorts, "rvol_up2_pmarp_gte60_sign_pos", symbol, date_str)
                if close_bucket == "near_high":
                    _add_event(cohorts, "rvol_up2_pmarp_gte60_close_near_high", symbol, date_str)
                if event_day_sign == "sign_pos" and close_bucket == "near_high":
                    _add_event(
                        cohorts,
                        "rvol_up2_pmarp_gte60_sign_pos_close_near_high",
                        symbol,
                        date_str,
                    )

            if pmarp_bucket == low_label:
                if event_day_sign == "sign_neg":
                    _add_event(cohorts, "rvol_up2_pmarp_lt30_sign_neg", symbol, date_str)
                if close_bucket == "near_low":
                    _add_event(cohorts, "rvol_up2_pmarp_lt30_close_near_low", symbol, date_str)

    return _plain_cohorts(cohorts)


def run_conditional_lift_comparisons(
    comparisons: Dict[str, tuple[EventMap, EventMap]],
    return_matrices: Dict[int, pd.DataFrame],
    symbol_date_index: Dict[str, Dict[str, int]],
) -> List[ConditionalLiftResult]:
    results: List[ConditionalLiftResult] = []
    for comparison, (accepted_events, rejected_events) in comparisons.items():
        for horizon, ret_df in sorted(return_matrices.items()):
            accepted = _collect_clustered_event_returns(
                events=accepted_events,
                ret_df=ret_df,
                symbol_date_index=symbol_date_index,
                horizon=horizon,
            )
            rejected = _collect_clustered_event_returns(
                events=rejected_events,
                ret_df=ret_df,
                symbol_date_index=symbol_date_index,
                horizon=horizon,
            )
            results.append(_comparison_result(comparison, horizon, accepted, rejected))
    return results


def comparison_pairs_from_cohorts(cohorts: CohortMap) -> Dict[str, tuple[EventMap, EventMap]]:
    return {
        "pmarp_lift_rvol_same_day": (
            cohorts.get("pmarp_up2_accept_rvol_same_day", {}),
            cohorts.get("pmarp_up2_reject_rvol_same_day", {}),
        ),
        "pmarp_lift_rvol_recent3": (
            cohorts.get("pmarp_up2_accept_rvol_recent3", {}),
            cohorts.get("pmarp_up2_reject_rvol_recent3", {}),
        ),
        "pmarp_lift_rvol_recent5": (
            cohorts.get("pmarp_up2_accept_rvol_recent5", {}),
            cohorts.get("pmarp_up2_reject_rvol_recent5", {}),
        ),
    }


def _add_accept_reject(
    cohorts: CohortMap,
    accepted_label: str,
    rejected_label: str,
    accepted: bool,
    symbol: str,
    date_str: str,
) -> None:
    _add_event(cohorts, accepted_label if accepted else rejected_label, symbol, date_str)


def _add_event(cohorts: CohortMap, label: str, symbol: str, date_str: str) -> None:
    cohorts.setdefault(label, {}).setdefault(symbol, []).append(date_str)


def _plain_cohorts(cohorts: CohortMap) -> CohortMap:
    return {label: dict(events) for label, events in cohorts.items()}


def _collect_clustered_event_returns(
    events: EventMap,
    ret_df: pd.DataFrame,
    symbol_date_index: Dict[str, Dict[str, int]],
    horizon: int,
) -> dict:
    raw_count = sum(len(dates) for dates in events.values())
    deduped = _deoverlap_events(events, symbol_date_index, horizon)
    dedup_count = sum(len(dates) for dates in deduped.values())

    scored_count = 0
    event_returns: List[float] = []
    date_bucket: Dict[str, List[float]] = defaultdict(list)

    for symbol, event_dates in deduped.items():
        if symbol not in ret_df.columns:
            continue
        for date_str in event_dates:
            if date_str not in ret_df.index:
                continue
            value = ret_df.loc[date_str, symbol]
            if pd.notna(value):
                value = float(value)
                event_returns.append(value)
                date_bucket[date_str].append(value)
                scored_count += 1

    cluster_means = np.array(
        [np.mean(values) for values in date_bucket.values()],
        dtype=float,
    )
    return {
        "raw_count": raw_count,
        "dedup_count": dedup_count,
        "scored_count": scored_count,
        "event_returns": np.array(event_returns, dtype=float),
        "cluster_means": cluster_means,
    }


def _comparison_result(
    comparison: str,
    horizon: int,
    accepted: dict,
    rejected: dict,
) -> ConditionalLiftResult:
    accepted_clusters = accepted["cluster_means"]
    rejected_clusters = rejected["cluster_means"]

    if len(accepted_clusters) >= 2 and len(rejected_clusters) >= 2:
        t_stat, p_value = ttest_ind(accepted_clusters, rejected_clusters, equal_var=False)
        t_stat = float(t_stat)
        p_value = float(p_value)
    else:
        t_stat = 0.0
        p_value = 1.0

    accepted_events = accepted["event_returns"]
    rejected_events = rejected["event_returns"]
    accepted_mean = _mean(accepted_events)
    rejected_mean = _mean(rejected_events)
    accepted_hit = _hit_rate(accepted_events)
    rejected_hit = _hit_rate(rejected_events)

    return ConditionalLiftResult(
        comparison=comparison,
        horizon=horizon,
        accepted_events_raw=accepted["raw_count"],
        accepted_events_dedup=accepted["dedup_count"],
        accepted_events_scored=accepted["scored_count"],
        accepted_effective_dates=len(accepted_clusters),
        rejected_events_raw=rejected["raw_count"],
        rejected_events_dedup=rejected["dedup_count"],
        rejected_events_scored=rejected["scored_count"],
        rejected_effective_dates=len(rejected_clusters),
        accepted_mean=accepted_mean,
        rejected_mean=rejected_mean,
        mean_diff=accepted_mean - rejected_mean,
        accepted_hit_rate=accepted_hit,
        rejected_hit_rate=rejected_hit,
        hit_rate_diff=accepted_hit - rejected_hit,
        t_stat=t_stat,
        p_value=p_value,
    )


def _mean(values: np.ndarray) -> float:
    return float(np.mean(values)) if len(values) else 0.0


def _hit_rate(values: np.ndarray) -> float:
    return float(np.mean(values > 0)) if len(values) else 0.0
