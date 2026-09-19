"""Pure-math trend-quality metrics for a single price series.

`trend_metrics` summarises the shape of a price path without any I/O,
timestamps or external data: total return, Kaufman-style efficiency ratio,
the slope and R² of an OLS fit of log price on bar index, and the current
drawdown from the window's highest close.

The caller is responsible for supplying a clean, aligned series; this
module only validates that the values themselves are usable.
"""

from __future__ import annotations

import numpy as np


def _as_float_array(closes):
    """Coerce ``closes`` to a 1-D float64 numpy array, rejecting bad input."""
    if closes is None:
        raise ValueError("closes must not be None")

    # pandas Series / DataFrame-free sequence handling without importing pandas.
    values = getattr(closes, "to_numpy", None)
    if callable(values):
        arr = np.asarray(values(), dtype=np.float64)
    else:
        try:
            arr = np.asarray(closes, dtype=np.float64)
        except (TypeError, ValueError) as exc:
            raise ValueError("closes must be a sequence of numbers") from exc

    if arr.ndim != 1:
        raise ValueError("closes must be one-dimensional")

    if arr.size < 2:
        raise ValueError("closes must contain at least 2 prices")

    if not np.all(np.isfinite(arr)):
        raise ValueError("closes must contain only finite values")

    if np.any(arr <= 0.0):
        raise ValueError("closes must contain only positive prices")

    return arr


def _clamp_unit(value: float) -> float:
    """Clamp a metric that is mathematically in [0, 1] against rounding drift."""
    if value < 0.0:
        return 0.0
    if value > 1.0:
        return 1.0
    return float(value)


def trend_metrics(closes) -> dict:
    """Compute trend metrics for ``closes``.

    Parameters
    ----------
    closes:
        A pandas ``Series`` or any sequence of at least two finite,
        strictly positive prices.

    Returns
    -------
    dict
        ``return``   : last / first - 1
        ``er``       : |log(last/first)| / sum(|log(p[k]/p[k-1])|), in [0, 1]
        ``slope``    : OLS slope of log price on bar index (per bar, not annualised)
        ``r_squared``: coefficient of determination of that fit, in [0, 1]
        ``drawdown`` : 1 - last / max(closes)
    """
    arr = _as_float_array(closes)

    first = float(arr[0])
    last = float(arr[-1])
    total_return = last / first - 1.0

    log_prices = np.log(arr)

    # Efficiency ratio: net log displacement over total log path length.
    net_move = abs(float(log_prices[-1] - log_prices[0]))
    path = float(np.sum(np.abs(np.diff(log_prices))))
    if path <= 0.0:
        er = 0.0
    else:
        er = net_move / path

    # OLS of log price on bar index using centered values for stability.
    x = np.arange(arr.size, dtype=np.float64)
    x_centered = x - x.mean()
    y_centered = log_prices - log_prices.mean()

    sxx = float(np.dot(x_centered, x_centered))
    sxy = float(np.dot(x_centered, y_centered))
    syy = float(np.dot(y_centered, y_centered))

    if path == 0.0 or syy == 0.0:
        # Constant log prices: do not impose a variance floor on real drift.
        slope = 0.0
        r_squared = 0.0
    else:
        slope = sxy / sxx
        r_squared = (sxy * sxy) / (sxx * syy)

    drawdown = 1.0 - last / float(arr.max())

    metrics = {
        "return": total_return,
        "er": _clamp_unit(er),
        "slope": slope,
        "r_squared": _clamp_unit(r_squared),
        "drawdown": drawdown,
    }

    for key, value in metrics.items():
        if not np.isfinite(value):
            raise ValueError(f"metric '{key}' is not finite")

    return metrics
