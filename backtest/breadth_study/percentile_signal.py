"""Percentile-rank smoother for breadth series.

`signal_t = SMA(window=smoother_window) of rolling_rank(breadth, lookback=L, pct=True)`

No look-ahead: rank at t uses t-L+1..t only.
"""
from __future__ import annotations

import pandas as pd


def build_percentile_signal(
    breadth: pd.Series,
    lookback: int,
    smoother_window: int,
) -> pd.Series:
    """Build the SMA-smoothed rolling percentile rank of a breadth series.

    Parameters
    ----------
    breadth: pd.Series
        Daily breadth value (e.g. % stocks above MA50).
    lookback: int
        Rolling window for percentile-rank calculation (252 trading days).
    smoother_window: int
        SMA window applied to the percentile rank (5 days).

    Returns
    -------
    pd.Series
        Percentile-ranked then smoothed signal. NaN until both the rank
        window and the smoother window are filled.
    """
    raw_pctile = breadth.rolling(lookback, min_periods=lookback).rank(pct=True)
    return raw_pctile.rolling(smoother_window, min_periods=smoother_window).mean()
