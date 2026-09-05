"""Pure selection-compass screening rules for the morning report."""

from __future__ import annotations

import math
from typing import Any, Dict

import pandas as pd

from config.settings import FUNDAMENTAL_QUARTER_GAP_MAX_DAYS
from src.indicators.rvol import calculate_rvol_series

EMA30_PERIOD = 30
EMA30_COVERAGE_THRESHOLD = 0.95


def _eps_leg(
    current: Any,
    comparison: Any,
    *,
    threshold: float = 0.25,
) -> Dict[str, Any]:
    """Evaluate one EPS growth leg using the frozen turnaround semantics."""
    try:
        current_value = float(current)
        comparison_value = float(comparison)
    except (TypeError, ValueError):
        return {"passes": False, "growth": None, "turnaround": False}

    if current_value <= 0:
        return {"passes": False, "growth": None, "turnaround": False}
    if comparison_value <= 0:
        return {"passes": True, "growth": None, "turnaround": True}

    growth = (current_value - comparison_value) / abs(comparison_value)
    return {
        "passes": growth >= threshold,
        "growth": growth,
        "turnaround": False,
    }


def _evaluate_eps_pair(*, current: Any, prior: Any, yoy: Any) -> Dict[str, Any]:
    """Require the latest EPS to pass both YoY and QoQ legs."""
    yoy_result = _eps_leg(current, yoy)
    qoq_result = _eps_leg(current, prior)
    return {
        "passes": yoy_result["passes"] and qoq_result["passes"],
        "eps_yoy_growth": yoy_result["growth"],
        "eps_yoy_turnaround": yoy_result["turnaround"],
        "eps_qoq_growth": qoq_result["growth"],
        "eps_qoq_turnaround": qoq_result["turnaround"],
    }


def _evaluate_cagr_growth(
    revenue_cagr_4q: Any,
    net_income_cagr_4q: Any,
    *,
    threshold: float = 0.15,
) -> Dict[str, Any]:
    """Apply the arithmetic-average gate to the two existing 4Q CAGRs."""
    try:
        revenue = float(revenue_cagr_4q)
        net_income = float(net_income_cagr_4q)
    except (TypeError, ValueError):
        return {"passes": False, "growth_avg_4q": None}

    average = (revenue + net_income) / 2.0
    return {"passes": average >= threshold, "growth_avg_4q": average}


def _evaluate_growth(raw: Dict[str, Any], metric: Dict[str, Any]) -> Dict[str, Any]:
    """Choose turnaround endpoint growth or the existing CAGR gate, exclusively.

    Validated quarters are newest first. Each of the four target quarters is
    compared to its predecessor, including Q-3 against the fifth (Q-4) row.
    """
    incomes = [float(row["net_income"]) for row in raw["quarters"]]
    turned = any(newer > 0 and older <= 0 for newer, older in zip(incomes, incomes[1:]))
    if turned:
        return {
            "growth_route": "turnaround",
            "passes": (
                incomes[0] > 0
                and incomes[0] > incomes[3]
                and float(raw["current"]["revenue"]) > float(raw["base"]["revenue"])
            ),
            "growth_avg_4q": None,
        }
    return {
        "growth_route": "cagr",
        **_evaluate_cagr_growth(
            metric.get("revenue_cagr_4q"), metric.get("net_income_cagr_4q"),
        ),
    }


def _empty_rvol_result() -> Dict[str, Any]:
    return {
        "ready": False,
        "passes": False,
        "rvol_max_7d": None,
        "rvol_trigger_date": None,
    }


def _evaluate_rvol(
    frame: Any,
    *,
    as_of: str,
    lookback: int = 120,
    recent_days: int = 7,
    threshold: float = 2.0,
) -> Dict[str, Any]:
    """Evaluate the highest existing z-score RVOL in the recent window."""
    if not isinstance(frame, pd.DataFrame) or frame.empty:
        return _empty_rvol_result()
    if "volume" not in frame.columns:
        return _empty_rvol_result()

    if "date" in frame.columns:
        prices = frame.loc[:, ["date", "volume"]].copy()
    elif isinstance(frame.index, pd.DatetimeIndex):
        prices = pd.DataFrame(
            {"date": frame.index.copy(), "volume": frame["volume"].to_numpy(copy=True)}
        )
    else:
        return _empty_rvol_result()
    prices["date"] = pd.to_datetime(prices["date"], errors="coerce")
    prices["volume"] = pd.to_numeric(prices["volume"], errors="coerce")
    prices = prices.sort_values("date").reset_index(drop=True)

    required_rows = lookback + recent_days
    if len(prices) < required_rows or prices[["date", "volume"]].isna().any().any():
        return _empty_rvol_result()

    as_of_date = pd.Timestamp(as_of).normalize()
    if prices["date"].iloc[-1].normalize() != as_of_date:
        return _empty_rvol_result()

    volumes = pd.Series(prices["volume"].to_numpy(), index=prices["date"])
    recent = calculate_rvol_series(volumes, lookback=lookback).iloc[-recent_days:]
    if recent.isna().any():
        return _empty_rvol_result()

    trigger_date = recent.idxmax()
    maximum = float(recent.loc[trigger_date])
    return {
        "ready": True,
        "passes": maximum >= threshold,
        "rvol_max_7d": maximum,
        "rvol_trigger_date": pd.Timestamp(trigger_date).date().isoformat(),
    }


def _evaluate_ema30(frame: Any, *, as_of: str) -> Dict[str, Any]:
    """Require the as-of close above its inclusive, recursive daily EMA30."""
    unavailable = {"ready": False, "passes": False, "close": None, "ema30": None}
    if not isinstance(frame, pd.DataFrame) or "close" not in frame.columns:
        return unavailable
    if "date" in frame.columns:
        prices = frame.loc[:, ["date", "close"]].copy()
    elif isinstance(frame.index, pd.DatetimeIndex):
        prices = pd.DataFrame({"date": frame.index, "close": frame["close"].to_numpy(copy=True)})
    else:
        return unavailable
    prices["date"] = pd.to_datetime(prices["date"], errors="coerce")
    prices["close"] = pd.to_numeric(prices["close"], errors="coerce")
    if (
        len(prices) < EMA30_PERIOD
        or prices["date"].isna().any()
        or prices["date"].dt.normalize().duplicated().any()
        or not prices["close"].map(_is_present_number).all()
        or (prices["close"] <= 0).any()
    ):
        return unavailable
    prices = prices.sort_values("date")
    if prices["date"].iloc[-1].normalize() != pd.Timestamp(as_of).normalize():
        return unavailable
    # Same EMA convention as the existing PMARP indicator; includes today's close.
    ema = float(prices["close"].ewm(
        span=EMA30_PERIOD, adjust=False, min_periods=EMA30_PERIOD,
    ).mean().iloc[-1])
    close = float(prices["close"].iloc[-1])
    return {"ready": True, "passes": close > ema, "close": close, "ema30": ema}


def _raw_not_ready(reason: str) -> Dict[str, Any]:
    return {
        "ready": False,
        "reason": reason,
        "current": None,
        "prior": None,
        "yoy": None,
        "base": None,
        "quarters": [],
    }


def _is_present_number(value: Any) -> bool:
    try:
        return value is not None and math.isfinite(float(value))
    except (TypeError, ValueError):
        return False


def _raw_inputs_ready(income_rows: Any) -> Dict[str, Any]:
    """Validate the raw quarterly rows needed by every compass predicate."""
    if not isinstance(income_rows, (list, tuple)) or len(income_rows) < 5:
        return _raw_not_ready("missing_quarters")

    dated_rows = []
    for row in income_rows:
        if not isinstance(row, dict):
            return _raw_not_ready("invalid_row")
        date = pd.to_datetime(row.get("date"), errors="coerce")
        if pd.isna(date):
            return _raw_not_ready("invalid_date")
        dated_rows.append((pd.Timestamp(date).normalize(), row))

    dated_rows.sort(key=lambda item: item[0], reverse=True)
    first_five = dated_rows[:5]
    for (newer_date, _), (older_date, _) in zip(first_five, first_five[1:]):
        gap_days = (newer_date - older_date).days
        if gap_days <= 0 or gap_days > FUNDAMENTAL_QUARTER_GAP_MAX_DAYS:
            return _raw_not_ready("quarter_gap")

    fiscal_quarters = []
    for _, row in first_five:
        period = row.get("period")
        if period not in {"Q1", "Q2", "Q3", "Q4"}:
            return _raw_not_ready("invalid_fiscal_period")
        try:
            fiscal_year = int(float(row.get("fiscal_year")))
        except (TypeError, ValueError):
            return _raw_not_ready("invalid_fiscal_period")
        fiscal_quarters.append(fiscal_year * 4 + int(period[1]) - 1)
    if any(
        newer != older + 1
        for newer, older in zip(fiscal_quarters, fiscal_quarters[1:])
    ):
        return _raw_not_ready("fiscal_quarter_sequence")

    current = first_five[0][1]
    prior = first_five[1][1]
    base = first_five[3][1]
    current_period = current.get("period")
    try:
        prior_fiscal_year = str(int(float(current.get("fiscal_year"))) - 1)
    except (TypeError, ValueError):
        return _raw_not_ready("invalid_fiscal_period")

    yoy = next(
        (
            row
            for _, row in first_five[1:]
            if row.get("period") == current_period
            and str(row.get("fiscal_year")) == prior_fiscal_year
        ),
        None,
    )
    if current_period is None or yoy is None:
        return _raw_not_ready("missing_yoy_match")

    required_values = (
        current.get("eps_diluted"),
        prior.get("eps_diluted"),
        yoy.get("eps_diluted"),
        current.get("revenue"),
        base.get("revenue"),
        *(row.get("net_income") for _, row in first_five),
    )
    if not all(_is_present_number(value) for value in required_values):
        return _raw_not_ready("missing_raw_value")

    return {
        "ready": True,
        "reason": None,
        "current": current,
        "prior": prior,
        "yoy": yoy,
        "base": base,
        "quarters": [row for _, row in first_five],
    }


def _coverage_stat(covered: int, total: int) -> Dict[str, Any]:
    return {
        "covered": covered,
        "total": total,
        "ratio": covered / total if total else 0.0,
    }


def _latest_metric(metrics: Any) -> Dict[str, Any] | None:
    if not isinstance(metrics, (list, tuple)):
        return None
    valid = []
    for metric in metrics:
        if not isinstance(metric, dict):
            continue
        date = pd.to_datetime(metric.get("date"), errors="coerce")
        if not pd.isna(date):
            valid.append((pd.Timestamp(date).normalize(), metric))
    return max(valid, key=lambda item: item[0])[1] if valid else None


def _evaluate_fundamental(
    *,
    income_rows: Any,
    metrics: Any,
    coverage_status: Any,
    as_of: str,
) -> Dict[str, Any]:
    raw = _raw_inputs_ready(income_rows)
    if coverage_status != "ok" or not raw["ready"]:
        return {"ready": False, "raw": raw, "metric": None}

    current_date = pd.Timestamp(raw["current"]["date"]).normalize()
    as_of_date = pd.Timestamp(as_of).normalize()
    age_days = (as_of_date - current_date).days
    if age_days < 0 or age_days > 200:
        return {"ready": False, "raw": raw, "metric": None}

    metric = _latest_metric(metrics)
    metric_date = (
        pd.to_datetime(metric.get("date"), errors="coerce")
        if metric is not None
        else pd.NaT
    )
    if pd.isna(metric_date) or pd.Timestamp(metric_date).normalize() != current_date:
        return {"ready": False, "raw": raw, "metric": metric}

    return {"ready": True, "raw": raw, "metric": metric}


def scan_selection_compass(
    *,
    store: Any,
    symbols: list[str],
    as_of: str,
    price_frames: dict,
    market_cap_observations: dict[str, dict],
    min_fundamental_coverage: float = 0.95,
    min_rvol_coverage: float = 0.95,
) -> Dict[str, Any]:
    """Return coverage-gated compass hits without mutating inputs or storage."""
    universe = sorted({str(symbol).strip().upper() for symbol in symbols if str(symbol).strip()})
    total = len(universe)
    empty_coverage = {
        "fundamental_ready": _coverage_stat(0, total),
        "rvol_ready": _coverage_stat(0, total),
        "ema30_ready": _coverage_stat(0, total),
    }
    if not universe:
        return {
            "available": False,
            "reason": "empty_universe",
            "coverage": empty_coverage,
            "hits": [],
        }

    try:
        coverage_statuses = store.get_coverage("income_quarterly")
    except Exception:
        return {
            "available": False,
            "reason": "fundamental_store_error",
            "coverage": empty_coverage,
            "hits": [],
        }

    fundamental_results: Dict[str, Dict[str, Any]] = {}
    rvol_results: Dict[str, Dict[str, Any]] = {}
    ema30_results: Dict[str, Dict[str, Any]] = {}
    for symbol in universe:
        try:
            income_rows = store.get_income(symbol, limit=20)
            metrics = store.get_metrics(symbol, limit=8)
        except Exception:
            income_rows = []
            metrics = []
        fundamental_results[symbol] = _evaluate_fundamental(
            income_rows=income_rows,
            metrics=metrics,
            coverage_status=coverage_statuses.get(symbol),
            as_of=as_of,
        )
        rvol_results[symbol] = _evaluate_rvol(
            price_frames.get(symbol),
            as_of=as_of,
        )
        ema30_results[symbol] = _evaluate_ema30(price_frames.get(symbol), as_of=as_of)

    fundamental_covered = sum(result["ready"] for result in fundamental_results.values())
    rvol_covered = sum(result["ready"] for result in rvol_results.values())
    coverage = {
        "fundamental_ready": _coverage_stat(fundamental_covered, total),
        "rvol_ready": _coverage_stat(rvol_covered, total),
        "ema30_ready": _coverage_stat(sum(r["ready"] for r in ema30_results.values()), total),
    }
    if coverage["fundamental_ready"]["ratio"] < min_fundamental_coverage:
        return {
            "available": False,
            "reason": "fundamental_coverage_below_threshold",
            "coverage": coverage,
            "hits": [],
        }
    if coverage["rvol_ready"]["ratio"] < min_rvol_coverage:
        return {
            "available": False,
            "reason": "rvol_coverage_below_threshold",
            "coverage": coverage,
            "hits": [],
        }

    if coverage["ema30_ready"]["ratio"] < EMA30_COVERAGE_THRESHOLD:
        return {
            "available": False,
            "reason": "ema30_coverage_below_threshold",
            "coverage": coverage,
            "hits": [],
        }

    hits = []
    for symbol in universe:
        fundamental = fundamental_results[symbol]
        rvol = rvol_results[symbol]
        ema30 = ema30_results[symbol]
        if (
            not fundamental["ready"] or not rvol["ready"] or not rvol["passes"]
            or not ema30["ready"] or not ema30["passes"]
        ):
            continue
        raw = fundamental["raw"]
        metric = fundamental["metric"]
        eps = _evaluate_eps_pair(
            current=raw["current"]["eps_diluted"],
            prior=raw["prior"]["eps_diluted"],
            yoy=raw["yoy"]["eps_diluted"],
        )
        growth = _evaluate_growth(raw, metric)
        if not eps["passes"] or not growth["passes"]:
            continue
        hits.append(
            {
                "symbol": symbol,
                "eps_yoy_growth": eps["eps_yoy_growth"],
                "eps_yoy_turnaround": eps["eps_yoy_turnaround"],
                "eps_qoq_growth": eps["eps_qoq_growth"],
                "eps_qoq_turnaround": eps["eps_qoq_turnaround"],
                "revenue_cagr_4q": (
                    float(metric["revenue_cagr_4q"])
                    if _is_present_number(metric.get("revenue_cagr_4q")) else None
                ),
                "net_income_cagr_4q": (
                    float(metric["net_income_cagr_4q"])
                    if _is_present_number(metric.get("net_income_cagr_4q")) else None
                ),
                "growth_route": growth["growth_route"],
                "growth_avg_4q": growth["growth_avg_4q"],
                "rvol_max_7d": rvol["rvol_max_7d"],
                "rvol_trigger_date": rvol["rvol_trigger_date"],
                "close": ema30["close"],
                "ema30": ema30["ema30"],
            }
        )

    unavailable_market_caps = []
    as_of_date = pd.Timestamp(as_of).normalize()
    for hit in hits:
        symbol = hit["symbol"]
        observation = market_cap_observations.get(symbol)
        if not isinstance(observation, dict):
            unavailable_market_caps.append(symbol)
            continue
        observation_date = pd.to_datetime(observation.get("date"), errors="coerce")
        market_cap = observation.get("marketCap", observation.get("market_cap"))
        try:
            market_cap_value = float(market_cap)
        except (TypeError, ValueError):
            market_cap_value = math.nan
        age_days = (
            (as_of_date - pd.Timestamp(observation_date).normalize()).days
            if not pd.isna(observation_date)
            else None
        )
        if (
            age_days is None
            or age_days < 0
            or age_days > 7
            or not math.isfinite(market_cap_value)
            or market_cap_value <= 0
        ):
            unavailable_market_caps.append(symbol)
            continue
        hit["marketCap"] = market_cap_value

    if unavailable_market_caps:
        return {
            "available": False,
            "reason": "market_cap_unavailable",
            "coverage": coverage,
            "hits": [],
            "market_cap_unavailable_symbols": sorted(unavailable_market_caps),
        }

    hits.sort(key=lambda hit: (-hit["marketCap"], hit["symbol"]))

    return {
        "available": True,
        "reason": None,
        "coverage": coverage,
        "hits": hits,
    }
