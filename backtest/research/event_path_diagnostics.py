from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List

import numpy as np
import pandas as pd

EventMap = Dict[str, List[str]]


@dataclass(frozen=True)
class TailDiagnosticResult:
    cohort: str
    horizon: int
    n_events: int
    n_path_scored: int
    mean: float
    median: float
    p10: float
    p25: float
    p75: float
    p90: float
    top_10pct_contribution: float
    bottom_10pct_contribution: float
    mfe_mean: float
    mae_mean: float
    mfe_to_mae_ratio: float
    right_tail_ratio: float


def run_tail_diagnostics(
    cohort_events: Dict[str, EventMap],
    price_dict: Dict[str, pd.DataFrame],
    horizons: List[int],
) -> List[TailDiagnosticResult]:
    results: List[TailDiagnosticResult] = []
    prepared = {
        symbol: _prepare_price_frame(frame)
        for symbol, frame in price_dict.items()
    }

    for cohort, events in sorted(cohort_events.items()):
        n_events = sum(len(dates) for dates in events.values())
        for horizon in horizons:
            rows = []
            for symbol, dates in events.items():
                frame = prepared.get(symbol)
                if frame is None or frame.empty:
                    continue
                date_index = {date: idx for idx, date in enumerate(frame["date"].tolist())}
                for date_str in dates:
                    row = _event_path_row(frame, date_index, date_str, horizon)
                    if row is not None:
                        rows.append(row)
            results.append(_aggregate_tail_result(cohort, horizon, n_events, rows))
    return results


def _prepare_price_frame(frame: pd.DataFrame) -> pd.DataFrame:
    required = {"date", "close", "high", "low"}
    if not required.issubset(frame.columns):
        return pd.DataFrame()
    ordered = frame.sort_values("date").reset_index(drop=True).copy()
    ordered["date"] = ordered["date"].astype(str).str[:10]
    for column in ("close", "high", "low"):
        ordered[column] = ordered[column].astype(float)
    return ordered


def _event_path_row(
    frame: pd.DataFrame,
    date_index: Dict[str, int],
    date_str: str,
    horizon: int,
) -> dict | None:
    start_idx = date_index.get(date_str)
    if start_idx is None:
        return None

    end_idx = start_idx + horizon
    if start_idx + 1 >= len(frame) or end_idx >= len(frame):
        return None

    entry_price = float(frame.iloc[start_idx]["close"])
    if entry_price <= 0:
        return None

    forward = frame.iloc[start_idx + 1: end_idx + 1]
    if forward[["high", "low", "close"]].isna().any().any():
        return None

    exit_price = float(frame.iloc[end_idx]["close"])
    max_high = float(forward["high"].max())
    min_low = float(forward["low"].min())

    return {
        "return": exit_price / entry_price - 1.0,
        "mfe": max_high / entry_price - 1.0,
        "mae": min_low / entry_price - 1.0,
    }


def _aggregate_tail_result(
    cohort: str,
    horizon: int,
    n_events: int,
    rows: List[dict],
) -> TailDiagnosticResult:
    returns = np.array([row["return"] for row in rows], dtype=float)
    mfes = np.array([row["mfe"] for row in rows], dtype=float)
    maes = np.array([row["mae"] for row in rows], dtype=float)

    p10 = _quantile(returns, 0.10)
    p25 = _quantile(returns, 0.25)
    p75 = _quantile(returns, 0.75)
    p90 = _quantile(returns, 0.90)
    mfe_mean = _mean(mfes)
    mae_mean = _mean(maes)

    return TailDiagnosticResult(
        cohort=cohort,
        horizon=horizon,
        n_events=n_events,
        n_path_scored=len(rows),
        mean=_mean(returns),
        median=_median(returns),
        p10=p10,
        p25=p25,
        p75=p75,
        p90=p90,
        top_10pct_contribution=_tail_contribution(returns, top=True),
        bottom_10pct_contribution=_tail_contribution(returns, top=False),
        mfe_mean=mfe_mean,
        mae_mean=mae_mean,
        mfe_to_mae_ratio=_safe_ratio(mfe_mean, abs(mae_mean)),
        right_tail_ratio=_safe_ratio(p90, abs(p10)),
    )


def _tail_contribution(values: np.ndarray, top: bool) -> float:
    if len(values) == 0:
        return 0.0
    ordered = np.sort(values)
    n_tail = max(1, int(np.ceil(len(ordered) * 0.10)))
    tail = ordered[-n_tail:] if top else ordered[:n_tail]
    denominator = float(np.sum(ordered))
    if abs(denominator) < 1e-12:
        return 0.0
    return float(np.sum(tail) / denominator)


def _mean(values: np.ndarray) -> float:
    return float(np.mean(values)) if len(values) else 0.0


def _median(values: np.ndarray) -> float:
    return float(np.median(values)) if len(values) else 0.0


def _quantile(values: np.ndarray, q: float) -> float:
    return float(np.quantile(values, q)) if len(values) else 0.0


def _safe_ratio(numerator: float, denominator: float) -> float:
    if abs(denominator) < 1e-12:
        return 0.0
    return float(numerator / denominator)
