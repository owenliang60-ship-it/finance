"""Buy-quality metrics for broad-breadth upcross events."""
from __future__ import annotations

import pandas as pd


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
