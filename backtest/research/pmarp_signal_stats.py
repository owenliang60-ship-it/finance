from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from typing import Dict, List, Optional

import numpy as np
import pandas as pd
from scipy.stats import ttest_1samp

from src.indicators.pmarp import calculate_pmarp


@dataclass(frozen=True)
class PMARPSignalStatsConfig:
    study_start_date: str = "2021-07-01"
    is_end_date: str = "2023-12-31"
    oos_start_date: str = "2024-01-01"
    ema_period: int = 20
    pmarp_lookback: int = 150


@dataclass(frozen=True)
class PMARPSignalStatResult:
    signal_label: str
    horizon: int
    n_events: int
    n_effective: int
    mean_return: float
    median_return: float
    positive_rate: float
    t_stat: float
    p_value: float


def build_pmarp_feature_frames(
    price_dict: Dict[str, pd.DataFrame],
    config: PMARPSignalStatsConfig,
) -> Dict[str, pd.DataFrame]:
    frames: Dict[str, pd.DataFrame] = {}

    for symbol, raw in price_dict.items():
        frame = _build_symbol_feature_frame(raw, config)
        if not frame.empty:
            frames[symbol] = frame
    return frames


def build_pmarp_signal_events(
    feature_frames: Dict[str, pd.DataFrame],
    config: PMARPSignalStatsConfig,
) -> Dict[str, Dict[str, List[str]]]:
    events: Dict[str, Dict[str, List[str]]] = defaultdict(dict)

    def add_event(bucket: str, symbol: str, date_str: str) -> None:
        events.setdefault(bucket, {}).setdefault(symbol, []).append(date_str)

    for symbol, frame in feature_frames.items():
        ordered = frame.sort_values("date").reset_index(drop=True)
        for _, row in ordered.iterrows():
            date_str = str(row["date"])
            if date_str < config.study_start_date:
                continue
            if bool(row.get("pmarp_down98", False)):
                add_event("pmarp_down98", symbol, date_str)

    return dict(events)


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


def run_signal_event_stats(
    signal_label: str,
    events: Dict[str, List[str]],
    return_matrices: Dict[int, pd.DataFrame],
) -> List[PMARPSignalStatResult]:
    results: List[PMARPSignalStatResult] = []
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
            PMARPSignalStatResult(
                signal_label=signal_label,
                horizon=horizon,
                n_events=n_events,
                n_effective=len(cluster_means),
                mean_return=float(np.mean(cluster_means)) if len(cluster_means) else 0.0,
                median_return=float(np.median(cluster_means)) if len(cluster_means) else 0.0,
                positive_rate=float(np.mean(cluster_means > 0)) if len(cluster_means) else 0.0,
                t_stat=t_stat,
                p_value=p_value,
            )
        )
    return results


def _build_symbol_feature_frame(
    df: pd.DataFrame,
    config: PMARPSignalStatsConfig,
) -> pd.DataFrame:
    ordered = df.sort_values("date").reset_index(drop=True).copy()
    if ordered.empty:
        return ordered

    ordered["date"] = ordered["date"].astype(str).str[:10]
    close = ordered["close"].astype(float)

    pmarp = calculate_pmarp(
        close,
        ema_period=config.ema_period,
        lookback=config.pmarp_lookback,
    )

    ordered["pmarp"] = pmarp
    ordered["pmarp_down98"] = ((pmarp.shift(1) > 98.0) & (pmarp <= 98.0)).fillna(False)
    return ordered


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
