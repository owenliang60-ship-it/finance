"""Point-in-time statement slicing, three-table alignment and the current fiscal quarter.

Availability dates come only from Codex's guards (`src/data/prosperity_quality.py`):
approximate replay passes the stored earnings rows so a statement date earlier
than the same quarter's results release is floored at that release; live and
strict reads are proven by an observation date instead.
"""
from __future__ import annotations

import statistics
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Dict, List, Mapping, Optional, Sequence, Set, Tuple

from src.data.fiscal_repair import _fiscal_key
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
    dropped: Tuple[str, ...] = ()      # income dates left out for conflicting fiscal identity


def _d(value: str) -> date:
    return date.fromisoformat(value[:10])


def _utc(stamp: str) -> datetime:
    """Parse an observation timestamp as an instant; 'Z' and '+00:00' spell the same UTC offset.

    Compared as text, '…T00:00:00+00:00' sorts before '…T00:00:00Z' (Codex M4 review F3).
    Python 3.10's fromisoformat does not accept 'Z', hence the replace. Naive stamps are refused.
    """
    moment = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
    if moment.tzinfo is None:
        raise ValueError(f"observed_at without a UTC offset: {stamp!r}")
    return moment.astimezone(timezone.utc)


def _latest_vintage(rows: List[dict], as_of: str, removed: Sequence[Tuple[str, str]] = ()) -> List[dict]:
    """Newest version per fiscal date observed before the end of `as_of` (exclusive next-midnight bound).

    The vintage is append-only, so a date a repair removed from the current table (e.g. a
    fiscal alias) would live on; it is dropped from the repair's archived_at onward unless it
    was observed again later (Codex M4 review F2).
    """
    bound = datetime.combine(_d(as_of) + timedelta(days=1), datetime.min.time(), timezone.utc)
    best: Dict[str, Tuple[datetime, dict]] = {}
    for r in rows:
        seen = _utc(r["_observed_at"])
        if seen < bound and (r["date"] not in best or seen > best[r["date"]][0]):
            best[r["date"]] = (seen, r)
    gone: Dict[str, datetime] = {}
    for day, archived_at in removed:
        at = _utc(archived_at)
        if at < bound:
            gone[day[:10]] = max(at, gone.get(day[:10], at))
    kept = [r for seen, r in best.values() if not (r["date"][:10] in gone and gone[r["date"][:10]] > seen)]
    return sorted(kept, key=lambda r: r["date"])


def visible_statements(history: SymbolHistory, as_of: str, mode: str,
                       observed_at: Optional[str] = None) -> VisibleStatements:
    current = {"income": history.income, "balance": history.balance, "cashflow": history.cashflow}
    if mode == "live":
        if not observed_at or as_of[:10] < observed_at[:10]:
            raise ValueError("live mode needs observed_at and as_of on or after it")
        return VisibleStatements(current, "live", observed_at[:10])
    if mode != "replay":
        raise ValueError(f"unknown mode: {mode!r}")
    if as_of[:10] >= STRICT_STATEMENTS_FROM:   # no vintage means no proof, never today's tables (review F5)
        return VisibleStatements({t: _latest_vintage(history.vintage.get(t, []), as_of, history.removed.get(t, ()))
                                  for t in TABLES},
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
    observed = _utc(row["_observed_at"]).date().isoformat() if visible.pit == "strict" else visible.observed_at
    known = statement_known_on(row, observed_at=observed)
    public = statement_availability(row)
    if known and public["public_available_at"] and public["public_available_at"] <= known:
        return known, public["source"], ()
    return known, "observed_snapshot", ()


def _fiscal_or_none(row: dict):
    try:
        return _fiscal_key(row)
    except ValueError:          # malformed identity: the per-quarter alignment below rejects it
        return None


def _align(income: List[dict], counterparts: Mapping[str, List[dict]]):
    """Align balance/cash flow to each income date; a conflict drops only that quarter (review F2).

    Two income dates with one fiscal identity are both dropped: the 120-day fiscal match would
    otherwise let a stale alias survive as a second quarter. Returns (income, aligned, dropped).
    """
    by_key: Dict[tuple, List[str]] = {}
    for r in income:
        key = _fiscal_or_none(r)
        if key is not None:
            by_key.setdefault(key, []).append(r["date"])
    dropped: Set[str] = {d for dates in by_key.values() if len(dates) > 1 for d in dates}
    aligned: Dict[str, Dict[str, dict]] = {t: {} for t in counterparts}
    for inc in income:
        if inc["date"] in dropped:
            continue
        try:
            found = {t: _statement_by_income_date([inc], rows, t) for t, rows in counterparts.items()}
        except ValueError:
            dropped.add(inc["date"])
            continue
        for t, index in found.items():
            aligned[t].update(index)
    return [r for r in income if r["date"] not in dropped], aligned, tuple(sorted(d[:10] for d in dropped))


def build_quarters(visible: VisibleStatements, as_of: str) -> QuarterBuild:
    bound = as_of[:10]
    seen: Dict[str, Dict[str, Tuple[dict, str, Optional[str], Tuple[str, ...]]]] = {t: {} for t in TABLES}
    for t in TABLES:
        for r in visible.rows.get(t, []):
            known, basis, labels = _known(r, visible)
            if r["date"][:10] <= bound and known and known <= bound:
                seen[t][r["date"]] = (r, known, basis, labels)
    if not visible.rows.get("income"):
        missing = ("strict_vintage_missing",) if visible.pit == "strict" else ()
        return QuarterBuild((), None, ("no_statements", "no_current_fiscal") + missing)
    income = [v[0] for v in sorted(seen["income"].values(), key=lambda v: v[0]["date"])]
    income, aligned, dropped = _align(income, {t: [v[0] for v in seen[t].values()] for t in ("balance", "cashflow")})
    conflict = ("statement_alignment_conflict",) if dropped else ()

    def entry(t, inc_date):
        if t == "income":
            return seen["income"][inc_date]
        row = aligned[t].get(inc_date)
        return seen[t][row["date"]] if row is not None else None

    complete = [r["date"] for r in income if entry("balance", r["date"]) and entry("cashflow", r["date"])]
    if not complete:
        return QuarterBuild((), None, ("no_current_fiscal",) + conflict, dropped)
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
        if visible.pit == "strict":
            observed_on = max(_utc(p[0]["_observed_at"]) for p in present).date().isoformat()
        else:
            observed_on = visible.observed_at
        merged.append(row)
        meta.append((r, latest[1], latest[2], labels, observed_on))
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
                      nulled=row["_nulled"], observed_on=observed_on)
        for row, gap, (src, known, basis, labels, observed_on) in zip(fixed, days, meta))[-STATEMENT_QUARTERS:]
    flags = list(conflict)
    gaps = [q.period_days for q in quarters if q.period_days is not None]
    if gaps and statistics.median(gaps) >= SEMIANNUAL_MEDIAN_GAP_DAYS:
        flags.append("semiannual_reporter")
    if (_d(bound) - _d(current)).days > STALE_FISCAL_DAYS:
        flags.append("stale_current_fiscal")
    return QuarterBuild(quarters, current[:10], tuple(flags), dropped)
