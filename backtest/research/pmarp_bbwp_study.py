from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from typing import Dict, Iterable, List, Optional

import numpy as np
import pandas as pd
from scipy.stats import ttest_1samp, ttest_ind

from backtest.factor_study.event_study import EventStudyResult
from src.indicators.bbwp import calculate_bbwp
from src.indicators.pmarp import calculate_pmarp


@dataclass(frozen=True)
class PMARPBBWPStudyConfig:
    study_start_date: str = "2021-07-01"
    is_end_date: str = "2023-12-31"
    oos_start_date: str = "2024-01-01"
    pmarp_ema_period: int = 20
    pmarp_lookback: int = 150
    bbwp_period: int = 20
    bbwp_std: float = 2.0
    bbwp_lookback: int = 150
    trend_lookback_days: int = 20
    recent_confirm_window: int = 3


@dataclass(frozen=True)
class ComparisonResult:
    label: str
    horizon: int
    sample: str
    accepted_n_events: int
    accepted_n_effective: int
    rejected_n_events: int
    rejected_n_effective: int
    accepted_mean_return: float
    rejected_mean_return: float
    accepted_hit_rate: float
    rejected_hit_rate: float
    diff_mean_return: float
    t_stat: float
    p_value: float


def build_feature_frames(
    price_dict: Dict[str, pd.DataFrame],
    benchmark_df: pd.DataFrame,
    config: PMARPBBWPStudyConfig,
) -> Dict[str, pd.DataFrame]:
    benchmark_close_lookup = _build_benchmark_close_lookup(benchmark_df)
    frames: Dict[str, pd.DataFrame] = {}

    for symbol, raw in price_dict.items():
        frame = _build_symbol_feature_frame(raw, benchmark_close_lookup, config)
        if not frame.empty:
            frames[symbol] = frame
    return frames


def build_cohorts_from_feature_frames(
    feature_frames: Dict[str, pd.DataFrame],
    config: PMARPBBWPStudyConfig,
) -> Dict[str, Dict[str, List[str]]]:
    cohorts: Dict[str, Dict[str, List[str]]] = defaultdict(dict)

    def add_event(cohort: str, symbol: str, date_str: str) -> None:
        cohorts.setdefault(cohort, {}).setdefault(symbol, []).append(date_str)

    for symbol, frame in feature_frames.items():
        frame = frame.sort_values("date").reset_index(drop=True)
        date_strings = frame["date"].astype(str).tolist()

        for i, row in frame.iterrows():
            date_str = date_strings[i]
            if date_str < config.study_start_date:
                continue

            pmarp_up2 = bool(row.get("pmarp_up2", False))
            bbwp_down98 = bool(row.get("bbwp_down98", False))
            bbwp_highturn = bool(row.get("bbwp_highturn", False))
            prior_downtrend = bool(row.get("prior_downtrend", False))
            prior_uptrend = bool(row.get("prior_uptrend", False))

            lo = max(0, i - config.recent_confirm_window)
            recent_slice = frame.iloc[lo:i + 1]
            recent_down98 = bool(recent_slice["bbwp_down98"].fillna(False).any())
            recent_highturn = bool(recent_slice["bbwp_highturn"].fillna(False).any())

            if bbwp_down98 and prior_downtrend:
                add_event("bbwp_down98_after_downtrend", symbol, date_str)
            if bbwp_down98 and prior_uptrend:
                add_event("bbwp_down98_after_uptrend", symbol, date_str)
            if bbwp_highturn and prior_downtrend:
                add_event("bbwp_highturn_after_downtrend", symbol, date_str)
            if bbwp_highturn and prior_uptrend:
                add_event("bbwp_highturn_after_uptrend", symbol, date_str)

            if not pmarp_up2:
                continue

            add_event("pmarp_up2_base", symbol, date_str)

            if bbwp_down98:
                add_event("pmarp_up2_accept_down98_same_day", symbol, date_str)
            else:
                add_event("pmarp_up2_reject_down98_same_day", symbol, date_str)

            if recent_down98:
                add_event("pmarp_up2_accept_down98_recent3", symbol, date_str)
            else:
                add_event("pmarp_up2_reject_down98_recent3", symbol, date_str)

            if bbwp_highturn:
                add_event("pmarp_up2_accept_highturn_same_day", symbol, date_str)
            else:
                add_event("pmarp_up2_reject_highturn_same_day", symbol, date_str)

            if recent_highturn:
                add_event("pmarp_up2_accept_highturn_recent3", symbol, date_str)
            else:
                add_event("pmarp_up2_reject_highturn_recent3", symbol, date_str)

    return dict(cohorts)


def filter_events_by_date(
    events: Dict[str, List[str]],
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
) -> Dict[str, List[str]]:
    filtered: Dict[str, List[str]] = {}
    for symbol, dates in events.items():
        kept = [
            d for d in dates
            if (start_date is None or d >= start_date)
            and (end_date is None or d <= end_date)
        ]
        if kept:
            filtered[symbol] = kept
    return filtered


def run_labeled_event_study(
    cohort_label: str,
    events: Dict[str, List[str]],
    return_matrices: Dict[int, pd.DataFrame],
) -> List[EventStudyResult]:
    results: List[EventStudyResult] = []
    for horizon, ret_df in sorted(return_matrices.items()):
        n_events, cluster_means = _cluster_means(events, ret_df)
        if len(cluster_means) >= 2:
            t_stat, p_value = ttest_1samp(cluster_means, 0.0)
            t_stat = float(t_stat)
            p_value = float(p_value)
        else:
            t_stat = 0.0
            p_value = 1.0

        results.append(
            EventStudyResult(
                factor_name="PMARP_BBWP",
                signal_label=cohort_label,
                horizon=horizon,
                n_events=n_events,
                n_effective=len(cluster_means),
                mean_return=float(np.mean(cluster_means)) if len(cluster_means) else 0.0,
                median_return=float(np.median(cluster_means)) if len(cluster_means) else 0.0,
                hit_rate=float(np.mean(cluster_means > 0)) if len(cluster_means) else 0.0,
                t_stat=t_stat,
                p_value=p_value,
            )
        )
    return results


def compare_event_groups(
    label: str,
    accepted_events: Dict[str, List[str]],
    rejected_events: Dict[str, List[str]],
    return_matrices: Dict[int, pd.DataFrame],
    sample: str,
) -> List[ComparisonResult]:
    results: List[ComparisonResult] = []
    for horizon, ret_df in sorted(return_matrices.items()):
        accepted_n, accepted_cluster = _cluster_means(accepted_events, ret_df)
        rejected_n, rejected_cluster = _cluster_means(rejected_events, ret_df)

        if len(accepted_cluster) >= 2 and len(rejected_cluster) >= 2:
            t_stat, p_value = ttest_ind(
                accepted_cluster,
                rejected_cluster,
                equal_var=False,
                nan_policy="omit",
            )
            t_stat = float(t_stat)
            p_value = float(p_value)
        else:
            t_stat = 0.0
            p_value = 1.0

        accepted_mean = float(np.mean(accepted_cluster)) if len(accepted_cluster) else 0.0
        rejected_mean = float(np.mean(rejected_cluster)) if len(rejected_cluster) else 0.0
        accepted_hit = float(np.mean(accepted_cluster > 0)) if len(accepted_cluster) else 0.0
        rejected_hit = float(np.mean(rejected_cluster > 0)) if len(rejected_cluster) else 0.0

        results.append(
            ComparisonResult(
                label=label,
                horizon=horizon,
                sample=sample,
                accepted_n_events=accepted_n,
                accepted_n_effective=len(accepted_cluster),
                rejected_n_events=rejected_n,
                rejected_n_effective=len(rejected_cluster),
                accepted_mean_return=accepted_mean,
                rejected_mean_return=rejected_mean,
                accepted_hit_rate=accepted_hit,
                rejected_hit_rate=rejected_hit,
                diff_mean_return=accepted_mean - rejected_mean,
                t_stat=t_stat,
                p_value=p_value,
            )
        )
    return results


def _build_symbol_feature_frame(
    df: pd.DataFrame,
    benchmark_close_lookup: Dict[str, float],
    config: PMARPBBWPStudyConfig,
) -> pd.DataFrame:
    ordered = df.sort_values("date").reset_index(drop=True).copy()
    if ordered.empty:
        return ordered

    ordered["date"] = ordered["date"].astype(str).str[:10]
    close = ordered["close"].astype(float)

    pmarp = calculate_pmarp(
        close,
        ema_period=config.pmarp_ema_period,
        lookback=config.pmarp_lookback,
    )
    bbwp = calculate_bbwp(
        close,
        bb_period=config.bbwp_period,
        bb_std=config.bbwp_std,
        lookback=config.bbwp_lookback,
    )

    ordered["pmarp"] = pmarp
    ordered["bbwp"] = bbwp
    ordered["pmarp_up2"] = ((pmarp.shift(1) < 2.0) & (pmarp >= 2.0)).fillna(False)
    ordered["bbwp_down98"] = ((bbwp.shift(1) > 98.0) & (bbwp <= 98.0)).fillna(False)
    ordered["bbwp_highturn"] = ((bbwp.shift(1) > 98.0) & (bbwp < bbwp.shift(1))).fillna(False)

    ordered["prior_excess_20d"] = _prior_excess_return_series(
        ordered["date"].tolist(),
        close.tolist(),
        benchmark_close_lookup=benchmark_close_lookup,
        lookback_days=config.trend_lookback_days,
    ).to_numpy()
    ordered["prior_downtrend"] = ordered["prior_excess_20d"] < 0
    ordered["prior_uptrend"] = ordered["prior_excess_20d"] > 0

    return ordered


def _prior_excess_return_series(
    dates: List[str],
    closes: List[float],
    benchmark_close_lookup: Dict[str, float],
    lookback_days: int,
) -> pd.Series:
    values: List[float] = []

    benchmark_dates = list(benchmark_close_lookup.keys())
    benchmark_index = {d: i for i, d in enumerate(benchmark_dates)}
    benchmark_closes = benchmark_close_lookup

    for idx, date_str in enumerate(dates):
        if idx < lookback_days or date_str not in benchmark_index:
            values.append(np.nan)
            continue

        bench_idx = benchmark_index[date_str]
        if bench_idx < lookback_days:
            values.append(np.nan)
            continue

        stock_prev = closes[idx - lookback_days]
        stock_curr = closes[idx]
        bench_prev_date = benchmark_dates[bench_idx - lookback_days]
        bench_prev = benchmark_closes[bench_prev_date]
        bench_curr = benchmark_closes[date_str]

        if stock_prev <= 0 or bench_prev <= 0:
            values.append(np.nan)
            continue

        stock_ret = stock_curr / stock_prev - 1.0
        bench_ret = bench_curr / bench_prev - 1.0
        values.append(stock_ret - bench_ret)

    return pd.Series(values, index=dates, dtype=float)


def _build_benchmark_close_lookup(benchmark_df: pd.DataFrame) -> Dict[str, float]:
    ordered = benchmark_df.sort_values("date").reset_index(drop=True)
    return {
        str(row["date"])[:10]: float(row["close"])
        for _, row in ordered.iterrows()
    }


def _cluster_means(
    events: Dict[str, List[str]],
    ret_df: pd.DataFrame,
) -> tuple[int, np.ndarray]:
    date_bucket: Dict[str, List[float]] = defaultdict(list)
    n_raw = 0

    for symbol, event_dates in events.items():
        if symbol not in ret_df.columns:
            continue
        for date in event_dates:
            if date not in ret_df.index:
                continue
            value = ret_df.loc[date, symbol]
            if pd.notna(value):
                date_bucket[date].append(float(value))
                n_raw += 1

    cluster = np.array([np.mean(values) for values in date_bucket.values()], dtype=float)
    return n_raw, cluster
