"""Upcross event detector with adaptive cooldown.

An upcross at index t is defined as `signal[t-1] < threshold AND signal[t] >= threshold`.
NaN values do not trigger events.

After an event at t, no further events are recorded until index t + cooldown_days.
"""
from __future__ import annotations

from typing import Any, Dict, List

import pandas as pd


def detect_upcross_events(
    signal: pd.Series,
    threshold: float,
    cooldown_days: int,
) -> List[Dict[str, Any]]:
    """Detect strict upcrosses of `threshold` with cooldown.

    Parameters
    ----------
    signal: pd.Series
        Smoothed signal (e.g. SMA5 of rolling-rank percentile).
    threshold: float
        K value to cross.
    cooldown_days: int
        Minimum positional spacing (in series-index steps) between events.

    Returns
    -------
    list of dict with keys: ``index`` (positional, int) and ``label`` (the
    series index value at that position, useful when caller has dates).
    """
    events: List[Dict[str, Any]] = []
    last_event_pos = -10**9
    arr = signal.to_numpy()
    labels = signal.index
    for i in range(1, len(arr)):
        prev = arr[i - 1]
        curr = arr[i]
        if pd.isna(prev) or pd.isna(curr):
            continue
        if prev < threshold and curr >= threshold:
            if i - last_event_pos < cooldown_days:
                continue
            events.append({"index": i, "label": labels[i]})
            last_event_pos = i
    return events
