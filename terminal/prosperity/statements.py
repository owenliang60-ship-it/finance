"""Point-in-time statement slicing, three-table alignment and the current fiscal quarter.

Availability dates come only from Codex's guards (`src/data/prosperity_quality.py`):
approximate replay passes the stored earnings rows so a statement date earlier
than the same quarter's results release is floored at that release; live and
strict reads are proven by an observation date instead.
"""
from __future__ import annotations

import statistics
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Dict, List, Mapping, Optional, Tuple

from src.data.fundamental_value_checks import apply_hard_issues, check_quarter_values
from src.data.metrics_calculator import _statement_by_income_date
from src.data.prosperity_quality import statement_availability, statement_known_on
from terminal.prosperity.config import (SEMIANNUAL_MEDIAN_GAP_DAYS, STALE_FISCAL_DAYS, STATEMENT_QUARTERS,
                                        STRICT_STATEMENTS_FROM)
from terminal.prosperity.types import QuarterInputs, SymbolHistory

TABLES = ("income", "balance", "cashflow")
INCOME_FIELDS = ("revenue", "cost_of_revenue", "gross_profit", "net_income")
BALANCE_FIELDS = ("goodwill_and_intangible_assets", "total_assets")
CASHFLOW_FIELDS = ("operating_cash_flow", "capital_expenditure", "free_cash_flow")


@dataclass(frozen=True)
class VisibleStatements:
    rows: Mapping[str, List[dict]]
    pit: str
    observed_at: Optional[str]
    earnings_rows: Tuple[Mapping, ...] = ()


@dataclass(frozen=True)
class QuarterBuild:
    quarters: Tuple[QuarterInputs, ...]
    current_fiscal: Optional[str]
    flags: Tuple[str, ...]


def _d(value: str) -> date:
    return date.fromisoformat(value[:10])


def _latest_vintage(rows: List[dict], as_of: str) -> List[dict]:
    """Newest version per fiscal date observed before the end of `as_of` (exclusive next-midnight bound)."""
    bound = (_d(as_of) + timedelta(days=1)).isoformat() + "T00:00:00Z"
    best: Dict[str, dict] = {}
    for r in rows:
        if r["_observed_at"] < bound and (r["date"] not in best or r["_observed_at"] > best[r["date"]]["_observed_at"]):
            best[r["date"]] = r
    return sorted(best.values(), key=lambda r: r["date"])


def visible_statements(history: SymbolHistory, as_of: str, mode: str,
                       observed_at: Optional[str] = None) -> VisibleStatements:
    current = {"income": history.income, "balance": history.balance, "cashflow": history.cashflow}
    if mode == "live":
        if not observed_at or as_of[:10] < observed_at[:10]:
            raise ValueError("live mode needs observed_at and as_of on or after it")
        return VisibleStatements(current, "live", observed_at[:10])
    if mode != "replay":
        raise ValueError(f"unknown mode: {mode!r}")
    if as_of[:10] >= STRICT_STATEMENTS_FROM and history.vintage:
        return VisibleStatements({t: _latest_vintage(history.vintage.get(t, []), as_of) for t in TABLES},
                                 "strict", None)
    return VisibleStatements(current, "approximate", None, tuple(history.earnings))


def _known(row: dict, visible: VisibleStatements) -> Tuple[Optional[str], Optional[str], Tuple[str, ...]]:
    """(known_on, basis, labels) for one statement row."""
    if visible.pit == "approximate":
        avail = statement_availability(row, earnings_rows=list(visible.earnings_rows))
        known = statement_known_on(row, earnings_rows=list(visible.earnings_rows))
        if "statement_date_before_earnings" in avail["issues"]:
            return known, "earnings_floor", ("statement_date_before_earnings",)
        return known, avail["source"], ()
    observed = row["_observed_at"][:10] if visible.pit == "strict" else visible.observed_at
    known = statement_known_on(row, observed_at=observed)
    public = statement_availability(row)
    if known and public["public_available_at"] and public["public_available_at"] <= known:
        return known, public["source"], ()
    return known, "observed_snapshot", ()


def build_quarters(visible: VisibleStatements, as_of: str) -> QuarterBuild:
    bound = as_of[:10]
    seen: Dict[str, Dict[str, Tuple[dict, str, Optional[str], Tuple[str, ...]]]] = {t: {} for t in TABLES}
    for t in TABLES:
        for r in visible.rows.get(t, []):
            known, basis, labels = _known(r, visible)
            if r["date"][:10] <= bound and known and known <= bound:
                seen[t][r["date"]] = (r, known, basis, labels)
    if not visible.rows.get("income"):
        return QuarterBuild((), None, ("no_statements", "no_current_fiscal"))
    income = [v[0] for v in sorted(seen["income"].values(), key=lambda v: v[0]["date"])]
    try:
        aligned = {t: _statement_by_income_date(income, [v[0] for v in seen[t].values()], t)
                   for t in ("balance", "cashflow")}
    except ValueError:
        return QuarterBuild((), None, ("statement_alignment_conflict",))

    def entry(t, inc_date):
        if t == "income":
            return seen["income"][inc_date]
        row = aligned[t].get(inc_date)
        return seen[t][row["date"]] if row is not None else None

    complete = [r["date"] for r in income if entry("balance", r["date"]) and entry("cashflow", r["date"])]
    if not complete:
        return QuarterBuild((), None, ("no_current_fiscal",))
    current = max(complete)
    merged, meta = [], []
    for r in income:
        if r["date"] > current:
            continue
        parts = {t: entry(t, r["date"]) for t in TABLES}
        row = {"date": r["date"][:10], "reported_currency": r.get("reported_currency"),
               **{f: r.get(f) for f in INCOME_FIELDS}}
        labels = list(parts["income"][3])
        for t, fields in (("balance", BALANCE_FIELDS), ("cashflow", CASHFLOW_FIELDS)):
            src = parts[t][0] if parts[t] else {}
            row.update({f: src.get(f) for f in fields})
            if parts[t] is None:
                labels.append(t + "_unavailable")
            else:
                labels.extend(l for l in parts[t][3] if l not in labels)
        present = [p for p in parts.values() if p]
        latest = max(present, key=lambda p: p[1])
        merged.append(row)
        meta.append((r, latest[1], latest[2], labels))
    fixed = apply_hard_issues(merged, check_quarter_values(merged))
    days = [None] + [(_d(b["date"]) - _d(a["date"])).days for a, b in zip(fixed, fixed[1:])]
    quarters = tuple(
        QuarterInputs(fiscal_date=row["date"], fiscal_year=src.get("fiscal_year"), period=src.get("period"),
                      period_days=gap, reported_currency=row["reported_currency"], available_on=known,
                      availability_basis=basis, revenue=row["revenue"], cost_of_revenue=row["cost_of_revenue"],
                      gross_profit=row["gross_profit"], net_income=row["net_income"],
                      operating_cash_flow=row["operating_cash_flow"],
                      capital_expenditure=row["capital_expenditure"], free_cash_flow=row["free_cash_flow"],
                      labels=tuple(labels) + tuple(l for l in row["_labels"] if l not in labels),
                      nulled=row["_nulled"])
        for row, gap, (src, known, basis, labels) in zip(fixed, days, meta))[-STATEMENT_QUARTERS:]
    flags = []
    gaps = [q.period_days for q in quarters if q.period_days is not None]
    if gaps and statistics.median(gaps) >= SEMIANNUAL_MEDIAN_GAP_DAYS:
        flags.append("semiannual_reporter")
    if (_d(bound) - _d(current)).days > STALE_FISCAL_DAYS:
        flags.append("stale_current_fiscal")
    return QuarterBuild(quarters, current[:10], tuple(flags))
