"""Buy-quality metrics for broad-breadth upcross events."""
from __future__ import annotations

import pandas as pd
import numpy as np


def forward_percentile_rank(closes: pd.Series, signal_idx: int, window: int) -> float:
    """Return signal close percentile within signal day plus forward window.

    ``0.0`` means the signal day was the lowest close in the window, and
    ``1.0`` means it was the highest. A truncated future window returns NaN.
    """
    end_idx = signal_idx + window
    if signal_idx < 0 or end_idx >= len(closes):
        return float("nan")

    forward_window = pd.to_numeric(
        closes.iloc[signal_idx : end_idx + 1],
        errors="coerce",
    )
    signal_close = forward_window.iloc[0]
    if pd.isna(signal_close) or forward_window.isna().any():
        return float("nan")

    rank = (forward_window < signal_close).sum() / window
    return float(rank)


def max_drawdown_after_entry(closes: pd.Series, signal_idx: int, window: int) -> float:
    """Return worst close-to-entry drawdown after the signal day."""
    end_idx = signal_idx + window
    if signal_idx < 0 or end_idx >= len(closes):
        return float("nan")

    signal_close = pd.to_numeric(pd.Series([closes.iloc[signal_idx]]), errors="coerce").iloc[0]
    future = pd.to_numeric(closes.iloc[signal_idx + 1 : end_idx + 1], errors="coerce")
    if pd.isna(signal_close) or future.isna().any() or signal_close <= 0:
        return float("nan")

    future_min = future.min()
    if future_min >= signal_close:
        return 0.0
    return float(future_min / signal_close - 1.0)


def distance_to_future_min(closes: pd.Series, signal_idx: int, window: int) -> float:
    """Return distance from signal close to the future-window minimum close."""
    end_idx = signal_idx + window
    if signal_idx < 0 or end_idx >= len(closes):
        return float("nan")

    signal_close = pd.to_numeric(pd.Series([closes.iloc[signal_idx]]), errors="coerce").iloc[0]
    future = pd.to_numeric(closes.iloc[signal_idx + 1 : end_idx + 1], errors="coerce")
    if pd.isna(signal_close) or future.isna().any():
        return float("nan")

    future_min = future.min()
    if future_min <= 0:
        return float("nan")
    if signal_close <= future_min:
        return 0.0
    return float((signal_close - future_min) / future_min)


def compute_better_than_random_pct_simple(
    event_metric: pd.Series,
    all_days_metric: pd.Series,
    n_iter: int = 10000,
    lower_is_better: bool = True,
    seed: int = 42,
) -> float:
    """Compare event median with simple random same-size samples."""
    event_values = pd.to_numeric(event_metric, errors="coerce").dropna()
    pool = pd.to_numeric(all_days_metric, errors="coerce").dropna().to_numpy()
    n_events = len(event_values)
    if n_events == 0 or len(pool) < n_events:
        return float("nan")

    rng = np.random.default_rng(seed)
    event_median = float(event_values.median())
    random_medians = np.array([
        np.median(rng.choice(pool, size=n_events, replace=False))
        for _ in range(n_iter)
    ])
    if lower_is_better:
        return float((random_medians > event_median).sum() / n_iter)
    return float((random_medians < event_median).sum() / n_iter)


def sample_dates_stratified_cooldown(
    all_days_dates: pd.DatetimeIndex,
    real_event_dates: list[pd.Timestamp],
    cooldown: int,
    rng: np.random.Generator,
    max_attempts: int = 50,
) -> list[pd.Timestamp]:
    """Sample dates by year, preserving per-year positional cooldown only."""
    from backtest.breadth_study.percentile_perm import (
        _sample_sequential,
        _try_one_rejection_trial,
    )

    all_days_dates = pd.DatetimeIndex(pd.to_datetime(all_days_dates)).sort_values()
    real_dates = [pd.Timestamp(d) for d in real_event_dates]
    if not real_dates:
        return []

    events_per_year = (
        pd.Series(real_dates).dt.year.value_counts().sort_index().to_dict()
    )
    dates_by_year = {
        int(year): sorted(pd.Timestamp(d) for d in dates)
        for year, dates in pd.Series(all_days_dates).groupby(all_days_dates.year)
    }

    sampled = _try_one_rejection_trial(
        events_per_year,
        dates_by_year,
        cooldown,
        rng,
        max_attempts,
    )
    if sampled is None:
        sampled = _sample_sequential(events_per_year, dates_by_year, cooldown, rng)
    if sampled is None:
        return []
    return [pd.Timestamp(d) for d in sampled]


def compute_better_than_random_pct_stratified(
    event_metric_with_dates: pd.DataFrame,
    all_days_metric_with_dates: pd.DataFrame,
    cooldown: int,
    n_iter: int = 10000,
    lower_is_better: bool = True,
    seed: int = 42,
) -> float:
    """Compare events with year-stratified, per-year cooldown-preserving samples."""
    event_df = event_metric_with_dates.copy()
    event_df["date"] = pd.to_datetime(event_df["date"])
    event_df["metric_value"] = pd.to_numeric(event_df["metric_value"], errors="coerce")
    event_df = event_df.dropna(subset=["metric_value"])
    if event_df.empty:
        return float("nan")

    baseline = all_days_metric_with_dates.copy()
    baseline["date"] = pd.to_datetime(baseline["date"])
    baseline["metric_value"] = pd.to_numeric(baseline["metric_value"], errors="coerce")
    baseline = baseline.dropna(subset=["metric_value"])
    if baseline.empty:
        return float("nan")

    event_median = float(event_df["metric_value"].median())
    real_event_dates = list(event_df["date"])
    all_dates = pd.DatetimeIndex(baseline["date"])
    metric_lookup = dict(zip(baseline["date"], baseline["metric_value"]))

    rng = np.random.default_rng(seed)
    random_medians: list[float] = []
    for _ in range(n_iter):
        sampled = sample_dates_stratified_cooldown(
            all_dates,
            real_event_dates,
            cooldown=cooldown,
            rng=rng,
        )
        if not sampled:
            continue
        vals = [
            metric_lookup[d]
            for d in sampled
            if d in metric_lookup and not pd.isna(metric_lookup[d])
        ]
        if len(vals) >= max(1, len(real_event_dates) // 2):
            random_medians.append(float(np.median(vals)))

    if not random_medians:
        return float("nan")
    random_medians_arr = np.asarray(random_medians, dtype=float)
    if lower_is_better:
        return float((random_medians_arr > event_median).sum() / len(random_medians_arr))
    return float((random_medians_arr < event_median).sum() / len(random_medians_arr))
