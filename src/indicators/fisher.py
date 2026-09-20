"""Ehlers/TradingView Fisher transform on a price series (pure math).

The transform is a recursive smoother over the normalised position of HL2
inside its rolling range.  This module deliberately contains no I/O, no
timestamps and no pandas dependency so the recursion can be unit-tested and
reused by the weekly report adapter.

Formula (TradingView ``Fisher Transform``):
    HL2   = (high + low) / 2
    value = .66 * ((HL2 - rolling_low) / (rolling_high - rolling_low) - .5)
            + .67 * value[1]
    value = clamp(value, -.999, .999) when outside +/-.99
    fisher = .5 * ln((1 + value) / (1 - value)) + .5 * fisher[1]
    trigger = fisher[1]

A flat rolling range has no defined normalised position; that case uses the
deterministic neutral input ``0`` before smoothing so the recursion can never
produce an infinity.  The first ``length - 1`` bars are warm-up and are
returned as ``NaN``; the recursion is initialised with ``value = fisher = 0``
on the first valid rolling window.
"""

from __future__ import annotations

import numpy as np

DEFAULT_LENGTH = 9


def _as_arrays(highs, lows):
    high = np.asarray(highs, dtype=np.float64)
    low = np.asarray(lows, dtype=np.float64)
    if high.ndim != 1 or low.ndim != 1 or high.shape != low.shape:
        raise ValueError("highs/lows must be equal-length one-dimensional sequences")
    if high.size == 0:
        raise ValueError("highs/lows must not be empty")
    if not np.all(np.isfinite(high)) or not np.all(np.isfinite(low)):
        raise ValueError("highs/lows must contain only finite values")
    if np.any(high < low):
        raise ValueError("high must not be below low")
    if np.any(low <= 0.0):
        raise ValueError("high/low must contain only positive prices")
    return high, low


def fisher_transform(highs, lows, length: int = DEFAULT_LENGTH) -> dict:
    """Return ``value``/``fisher``/``trigger`` arrays for the given OHLC series.

    ``NaN`` marks the warm-up bars before a full rolling window exists.
    """
    high, low = _as_arrays(highs, lows)
    if isinstance(length, bool) or not isinstance(length, (int, np.integer)) or length < 2:
        raise ValueError("length must be an integer >= 2")
    length = int(length)
    n = high.size
    if n < length:
        raise ValueError(f"at least {length} observations are required")

    hl2 = (high + low) / 2.0
    values = np.full(n, np.nan)
    fishers = np.full(n, np.nan)
    triggers = np.full(n, np.nan)

    prev_value = 0.0
    prev_fisher = 0.0
    for i in range(length - 1, n):
        window = hl2[i - length + 1:i + 1]
        rolling_high = float(window.max())
        rolling_low = float(window.min())
        span = rolling_high - rolling_low
        normalised = 0.0 if span == 0.0 else (float(hl2[i]) - rolling_low) / span - 0.5

        value = 0.66 * normalised + 0.67 * prev_value
        if value > 0.99:
            value = 0.999
        elif value < -0.99:
            value = -0.999

        trigger = prev_fisher
        fisher = 0.5 * np.log((1.0 + value) / (1.0 - value)) + 0.5 * prev_fisher

        values[i] = value
        fishers[i] = fisher
        triggers[i] = trigger
        prev_value = value
        prev_fisher = fisher

    return {"value": values, "fisher": fishers, "trigger": triggers}


def latest_crossing(fisher) -> str:
    """Classify a *new* cross at the last bar.

    ``trigger`` is the previous fisher, so an up-cross is
    ``fisher[-2] <= fisher[-3] and fisher[-1] > fisher[-2]``; a down-cross is
    the mirror.  A continuing trend, an equal final value or insufficient /
    non-finite warm-up history all return ``None`` and must not be reported as
    a no-cross signal.
    """
    arr = np.asarray(fisher, dtype=np.float64)
    if arr.ndim != 1 or arr.size < 3:
        return None
    prev_trigger = float(arr[-3])
    prev_fisher = float(arr[-2])
    current = float(arr[-1])
    if not (np.isfinite(prev_trigger) and np.isfinite(prev_fisher) and np.isfinite(current)):
        return None
    if prev_fisher <= prev_trigger and current > prev_fisher:
        return "up"
    if prev_fisher >= prev_trigger and current < prev_fisher:
        return "down"
    return None
