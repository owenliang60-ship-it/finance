from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from typing import Dict, List, Optional

import numpy as np
import pandas as pd
from scipy.stats import ttest_1samp, ttest_ind


@dataclass(frozen=True)
class BBWPSignalStatsConfig:
    study_start_date: str = "2021-07-01"
    is_end_date: str = "2023-12-31"
    oos_start_date: str = "2024-01-01"
    bb_period: int = 20
    bb_std: float = 2.0
    bbwp_lookback: int = 150


@dataclass(frozen=True)
class BucketStatResult:
    signal_label: str
    horizon: int
    n_events: int
    n_effective: int
    mean_return: float
    median_return: float
    hit_rate: float
    t_stat: float
    p_value: float


@dataclass(frozen=True)
class BucketComparisonResult:
    label: str
    horizon: int
    above_n_events: int
    above_n_effective: int
    below_n_events: int
    below_n_effective: int
    above_mean_return: float
    below_mean_return: float
    diff_below_minus_above: float
    t_stat: float
    p_value: float


@dataclass(frozen=True)
class ReversalScoreResult:
    label: str
    horizon: int
    n_events: int
    n_effective: int
    mean_score: float
    median_score: float
    positive_rate: float
    t_stat: float
    p_value: float


def build_bbwp_feature_frames(
    price_dict: Dict[str, pd.DataFrame],
    config: BBWPSignalStatsConfig,
) -> Dict[str, pd.DataFrame]:
    frames: Dict[str, pd.DataFrame] = {}

    for symbol, raw in price_dict.items():
        frame = _build_symbol_feature_frame(raw, config)
        if not frame.empty:
            frames[symbol] = frame
    return frames


def build_bbwp_signal_buckets(
    feature_frames: Dict[str, pd.DataFrame],
    config: BBWPSignalStatsConfig,
) -> Dict[str, Dict[str, List[str]]]:
    buckets: Dict[str, Dict[str, List[str]]] = defaultdict(dict)

    def add_event(bucket: str, symbol: str, date_str: str) -> None:
        buckets.setdefault(bucket, {}).setdefault(symbol, []).append(date_str)

    for symbol, frame in feature_frames.items():
        ordered = frame.sort_values("date").reset_index(drop=True)
        for _, row in ordered.iterrows():
            date_str = str(row["date"])
            if date_str < config.study_start_date:
                continue
            if not bool(row.get("bbwp_down98", False)):
                continue

            add_event("bbwp_down98_all", symbol, date_str)

            trend_bucket = row.get("trend_bucket")
            if trend_bucket == "above_mid":
                add_event("bbwp_down98_above_mid", symbol, date_str)
            elif trend_bucket == "below_mid":
                add_event("bbwp_down98_below_mid", symbol, date_str)
            elif trend_bucket == "on_mid":
                add_event("bbwp_down98_on_mid", symbol, date_str)

    return dict(buckets)


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


def run_bucket_event_stats(
    signal_label: str,
    events: Dict[str, List[str]],
    return_matrices: Dict[int, pd.DataFrame],
) -> List[BucketStatResult]:
    results: List[BucketStatResult] = []
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
            BucketStatResult(
                signal_label=signal_label,
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


def compare_trend_buckets(
    above_events: Dict[str, List[str]],
    below_events: Dict[str, List[str]],
    return_matrices: Dict[int, pd.DataFrame],
) -> List[BucketComparisonResult]:
    results: List[BucketComparisonResult] = []
    for horizon, ret_df in sorted(return_matrices.items()):
        above_n, above_cluster = _cluster_means(above_events, ret_df)
        below_n, below_cluster = _cluster_means(below_events, ret_df)

        if len(above_cluster) >= 2 and len(below_cluster) >= 2:
            t_stat, p_value = ttest_ind(
                below_cluster,
                above_cluster,
                equal_var=False,
                nan_policy="omit",
            )
            t_stat = float(t_stat)
            p_value = float(p_value)
        else:
            t_stat = 0.0
            p_value = 1.0

        above_mean = float(np.mean(above_cluster)) if len(above_cluster) else 0.0
        below_mean = float(np.mean(below_cluster)) if len(below_cluster) else 0.0

        results.append(
            BucketComparisonResult(
                label="bbwp_down98_below_minus_above",
                horizon=horizon,
                above_n_events=above_n,
                above_n_effective=len(above_cluster),
                below_n_events=below_n,
                below_n_effective=len(below_cluster),
                above_mean_return=above_mean,
                below_mean_return=below_mean,
                diff_below_minus_above=below_mean - above_mean,
                t_stat=t_stat,
                p_value=p_value,
            )
        )
    return results


def run_reversal_score_stats(
    above_events: Dict[str, List[str]],
    below_events: Dict[str, List[str]],
    return_matrices: Dict[int, pd.DataFrame],
) -> List[ReversalScoreResult]:
    results: List[ReversalScoreResult] = []
    for horizon, ret_df in sorted(return_matrices.items()):
        n_events, cluster_scores = _cluster_reversal_scores(above_events, below_events, ret_df)
        if len(cluster_scores) >= 2:
            t_stat, p_value = ttest_1samp(cluster_scores, 0.0)
            t_stat = float(t_stat)
            p_value = float(p_value)
        else:
            t_stat = 0.0
            p_value = 1.0

        results.append(
            ReversalScoreResult(
                label="bbwp_down98_reversal_score",
                horizon=horizon,
                n_events=n_events,
                n_effective=len(cluster_scores),
                mean_score=float(np.mean(cluster_scores)) if len(cluster_scores) else 0.0,
                median_score=float(np.median(cluster_scores)) if len(cluster_scores) else 0.0,
                positive_rate=float(np.mean(cluster_scores > 0)) if len(cluster_scores) else 0.0,
                t_stat=t_stat,
                p_value=p_value,
            )
        )
    return results


def _build_symbol_feature_frame(
    df: pd.DataFrame,
    config: BBWPSignalStatsConfig,
) -> pd.DataFrame:
    ordered = df.sort_values("date").reset_index(drop=True).copy()
    if ordered.empty:
        return ordered

    ordered["date"] = ordered["date"].astype(str).str[:10]
    close = ordered["close"].astype(float)

    bb_middle = close.rolling(window=config.bb_period, min_periods=config.bb_period).mean()
    bb_std = close.rolling(window=config.bb_period, min_periods=config.bb_period).std(ddof=0)
    upper = bb_middle + config.bb_std * bb_std
    lower = bb_middle - config.bb_std * bb_std
    bbw = (upper - lower) / bb_middle.replace(0, pd.NA)
    bbwp = _calculate_percentile_series(bbw, config.bbwp_lookback)

    ordered["bb_middle"] = bb_middle
    ordered["bbwp"] = bbwp
    ordered["bbwp_down98"] = ((bbwp.shift(1) > 98.0) & (bbwp <= 98.0)).fillna(False)

    ordered["trend_bucket"] = np.where(
        close > bb_middle,
        "above_mid",
        np.where(close < bb_middle, "below_mid", np.where(close == bb_middle, "on_mid", None)),
    )
    ordered["trend_sign"] = np.where(
        ordered["trend_bucket"] == "above_mid",
        1,
        np.where(ordered["trend_bucket"] == "below_mid", -1, np.nan),
    )
    return ordered


def _calculate_percentile_series(series: pd.Series, lookback: int) -> pd.Series:
    if len(series) < lookback:
        return pd.Series(index=series.index, dtype=float)

    out = pd.Series(index=series.index, dtype=float)
    for i in range(lookback, len(series)):
        current = series.iloc[i]
        hist = series.iloc[i - lookback:i].dropna()
        if pd.isna(current) or hist.empty:
            continue
        out.iloc[i] = (hist <= current).sum() / len(hist) * 100.0
    return out


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


def _cluster_reversal_scores(
    above_events: Dict[str, List[str]],
    below_events: Dict[str, List[str]],
    ret_df: pd.DataFrame,
) -> tuple[int, np.ndarray]:
    date_bucket: Dict[str, List[float]] = defaultdict(list)
    n_raw = 0

    for events, sign in ((above_events, 1.0), (below_events, -1.0)):
        for symbol, event_dates in events.items():
            if symbol not in ret_df.columns:
                continue
            for date in event_dates:
                if date not in ret_df.index:
                    continue
                value = ret_df.loc[date, symbol]
                if pd.notna(value):
                    date_bucket[date].append(float(-sign * value))
                    n_raw += 1

    cluster = np.array([np.mean(values) for values in date_bucket.values()], dtype=float)
    return n_raw, cluster
