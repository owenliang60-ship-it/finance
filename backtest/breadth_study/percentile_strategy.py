"""H6 event-driven strategy CAGR + bootstrap CI (Task 9).

Strategy definition (primary cell SPY 10d):
- Trigger at T close -> enter at T+1 open -> exit at T+horizon close (cash).
- Cooldown=20d guarantees no overlap with subsequent triggers.
- 10bp one-way cost applied at entry and exit.

H6 hurdle: strategy CAGR - B&H CAGR >= 5pp over the effective sample window.
Bootstrap CI: resample event dates with replacement, recompute excess each trial.
"""
from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional

import numpy as np
import pandas as pd


def _slice_prices(
    target_prices: pd.DataFrame,
    first_valid_date: pd.Timestamp,
    last_date: pd.Timestamp,
) -> pd.DataFrame:
    df = target_prices[
        (target_prices["date"] >= pd.Timestamp(first_valid_date))
        & (target_prices["date"] <= pd.Timestamp(last_date))
    ].sort_values("date").reset_index(drop=True)
    return df


def _strategy_capital(
    event_positions: Iterable[int],
    prices: pd.DataFrame,
    horizon: int,
    cost_factor: float,
) -> Dict[str, float]:
    capital = 1.0
    bars_in_market = 0
    n_trades = 0
    n = len(prices)
    last_exit_pos = -1
    open_arr = prices["open"].to_numpy()
    close_arr = prices["close"].to_numpy()
    for ev_pos in event_positions:
        entry_pos = ev_pos + 1  # T+1 open
        exit_pos = entry_pos + horizon - 1  # T+horizon close (relative to T+1)
        if entry_pos >= n or exit_pos >= n:
            continue
        if entry_pos <= last_exit_pos:
            # Overlapping with previous trade; skip
            continue
        entry_px = open_arr[entry_pos]
        exit_px = close_arr[exit_pos]
        if not np.isfinite(entry_px) or not np.isfinite(exit_px) or entry_px <= 0:
            continue
        gross_ret = exit_px / entry_px
        net_ret = gross_ret * cost_factor * cost_factor  # entry + exit cost
        capital *= net_ret
        bars_in_market += (exit_pos - entry_pos + 1)
        n_trades += 1
        last_exit_pos = exit_pos
    return {
        "capital": capital,
        "bars_in_market": bars_in_market,
        "n_trades": n_trades,
    }


def event_strategy_cagr(
    event_dates: List[pd.Timestamp],
    target_prices: pd.DataFrame,
    target: str,  # noqa: ARG001 (interface symmetry, target identifies which prices)
    horizon: int,
    first_valid_date: pd.Timestamp,
    last_date: pd.Timestamp,
    one_way_bps: float,
) -> Dict[str, float]:
    """Compute event-driven strategy CAGR over [first_valid_date, last_date].

    Returns dict with keys: ``strategy_cagr`` (pp), ``bnh_cagr`` (pp),
    ``excess_cagr`` (pp), ``n_trades``, ``exposure_pct``.
    """
    prices = _slice_prices(target_prices, first_valid_date, last_date)
    if prices.empty:
        return {
            "strategy_cagr": 0.0,
            "bnh_cagr": 0.0,
            "excess_cagr": 0.0,
            "n_trades": 0,
            "exposure_pct": 0.0,
        }
    n_total = len(prices)
    years = max(n_total / 252.0, 1e-9)
    cost_factor = 1.0 - one_way_bps / 10000.0

    date_to_pos = {d: i for i, d in enumerate(prices["date"])}
    event_positions: List[int] = []
    for d in sorted(pd.Timestamp(x) for x in event_dates):
        pos = date_to_pos.get(d)
        if pos is None:
            continue
        event_positions.append(pos)

    out = _strategy_capital(event_positions, prices, horizon, cost_factor)
    capital = out["capital"]
    strategy_cagr = (capital ** (1 / years) - 1) * 100

    first_close = prices["close"].iloc[0]
    last_close = prices["close"].iloc[-1]
    bnh_cagr = ((last_close / first_close) ** (1 / years) - 1) * 100

    return {
        "strategy_cagr": float(strategy_cagr),
        "bnh_cagr": float(bnh_cagr),
        "excess_cagr": float(strategy_cagr - bnh_cagr),
        "n_trades": int(out["n_trades"]),
        "exposure_pct": float(out["bars_in_market"] / n_total * 100.0),
    }


def check_h6_strategy_excess_cagr(excess_cagr_pp: float, min_pp: float = 5) -> bool:
    return bool(excess_cagr_pp >= min_pp)


def event_strategy_bootstrap_ci(
    event_dates: List[pd.Timestamp],
    target_prices: pd.DataFrame,
    target: str,
    horizon: int,
    first_valid_date: pd.Timestamp,
    last_date: pd.Timestamp,
    one_way_bps: float,
    trials: int = 500,
    seed: int = 20260430,
    ci_lower_pct: float = 2.5,
    ci_upper_pct: float = 97.5,
) -> Dict[str, Any]:
    """Bootstrap CI for excess_cagr by resampling event dates with replacement."""
    rng = np.random.default_rng(seed)
    point = event_strategy_cagr(
        event_dates, target_prices, target, horizon,
        first_valid_date, last_date, one_way_bps,
    )
    if not event_dates:
        return {
            "excess_cagr_point": point["excess_cagr"],
            "excess_cagr_ci_low": float("nan"),
            "excess_cagr_ci_high": float("nan"),
            "excess_cagr_share_negative": float("nan"),
            "n_trials": 0,
        }
    arr_events = np.array(event_dates, dtype="datetime64[ns]")
    n = len(arr_events)
    excess_samples: List[float] = []
    for _ in range(trials):
        # rng.choice on datetime64 needs index path
        idxs = rng.choice(n, size=n, replace=True)
        resampled = [pd.Timestamp(x) for x in arr_events[idxs]]
        resampled = sorted(resampled)
        result = event_strategy_cagr(
            resampled, target_prices, target, horizon,
            first_valid_date, last_date, one_way_bps,
        )
        excess_samples.append(result["excess_cagr"])
    samples = np.asarray(excess_samples, dtype=float)
    return {
        "excess_cagr_point": float(point["excess_cagr"]),
        "excess_cagr_ci_low": float(np.percentile(samples, ci_lower_pct)),
        "excess_cagr_ci_high": float(np.percentile(samples, ci_upper_pct)),
        "excess_cagr_share_negative": float((samples < 0).mean()),
        "n_trials": int(samples.size),
    }
