"""Prosperity engine D9 history: pure logic behind the backfill targets and
the coverage report (north star layer 1, P2/P3).

Read-only over MarketStore. Membership comes from M1's
`approximate_members_as_of` (10-day market-cap freshness + alias folding);
nothing here re-implements membership, the three-table window or fiscal
matching — those are imported from their owners.
"""
import math
import statistics
from datetime import date
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple

from src.data.prosperity_quality import (
    resolve_eps_quarters, split_basis_audit, statement_known_on,
)

QUARTER_END_MONTH_DAYS = {3: 31, 6: 30, 9: 30, 12: 31}


def _is_quarter_end(d: date) -> bool:
    return QUARTER_END_MONTH_DAYS.get(d.month) == d.day


def quarter_ends(start: str = "2021-09-30", end: str = "2026-06-30") -> List[str]:
    """Calendar quarter ends from `start` to `end`, both inclusive."""
    first, last = date.fromisoformat(start), date.fromisoformat(end)
    if not (_is_quarter_end(first) and _is_quarter_end(last)) or first > last:
        raise ValueError("start/end must be calendar quarter ends with start <= end, "
                         "got {} .. {}".format(start, end))
    out, year, month = [], first.year, first.month
    while (year, month) <= (last.year, last.month):
        out.append(date(year, month, QUARTER_END_MONTH_DAYS[month]).isoformat())
        month += 3
        if month > 12:
            year, month = year + 1, 3
    return out


def members_by_quarter_end(store, qes: List[str],
                           aliases: Optional[List[Dict[str, Any]]] = None,
                           ) -> Dict[str, List[str]]:
    """As-of $10B+ members (canonical codes) at each quarter end."""
    return {qe: list(store.approximate_members_as_of(qe, aliases=aliases)["symbols"])
            for qe in qes}


# ---------------------------------------------------------------------------
# Three tables + street EPS depth (P3 acceptance)
# ---------------------------------------------------------------------------

# Depth: SUE needs 11 consecutive reported quarters at minimum, the full
# window is 13, ΔSUE one more (north star layer 2 / data layer P3).
SUE_MIN_QUARTERS = 11
SUE_FULL_QUARTERS = 13
DSUE_MIN_QUARTERS = SUE_MIN_QUARTERS + 1
# SUE formula (north star, Novy-Marx): (this quarter − same quarter last year)
# ÷ σ, σ = standard deviation of the 8 previous YoY changes, at least 6
# observations, σ finite and ≠ 0. "Not demeaned" is the numerator (no drift
# term); σ is an ordinary standard deviation.
SUE_SIGMA_WINDOW = 8
SUE_SIGMA_MIN_OBS = 6
YEAR_DAYS = 365
SPLIT_MATCH_TOLERANCE = 0.15
# Two fiscal dates this close are one quarter (52/53-week filers: the estimate
# and the income statement can date the same quarter a few days apart).
SAME_QUARTER_DAYS = 20
# Fallback when no statement anchor exists: the newest mapped quarter must be
# at most one season behind a late filer.
LATEST_FISCAL_MAX_AGE_DAYS = 91 + 120

# Reasons that more collection cannot fix: the company is younger than the
# window (with listing evidence), or the EPS series itself makes σ undefined.
INHERENT_GAP_REASONS = {"short_history", "sue_degenerate"}


def _gap_max_days() -> int:
    from config.settings import FUNDAMENTAL_QUARTER_GAP_MAX_DAYS
    return FUNDAMENTAL_QUARTER_GAP_MAX_DAYS


def _d(value: str) -> date:
    return date.fromisoformat(value[:10])


def three_table_ok(store, symbol: str, as_of: str) -> bool:
    """≥8 contiguous quarters in all three statement tables, known by as_of."""
    import scripts.backfill_extended_fundamentals as backfill
    return backfill.has_asof_window(store, symbol, as_of)


def _known_eps_rows(rows, as_of: str):
    return [r for r in rows
            if r.get("announce_date") and r["announce_date"][:10] <= as_of
            and r.get("eps_actual") is not None]


def sue_dependencies(fiscals: Sequence[str], index: int) -> Set[int]:
    """Positions SUE at fiscals[index] reads: it and the eight before it, plus each one's YoY base.

    `fiscals` is newest first; YoY bases are matched by date (365 ± SAME_QUARTER_DAYS).
    """
    required = set(range(index, min(index + 9, len(fiscals))))
    for k in list(required):
        for j in range(k + 1, len(fiscals)):
            if abs((_d(fiscals[k]) - _d(fiscals[j])).days - YEAR_DAYS) <= SAME_QUARTER_DAYS:
                required.add(j)
                break
    return required


def sue_value(series: Sequence[Tuple[str, Optional[float]]], i: int) -> Tuple[Optional[float], Optional[str]]:
    """(SUE, None) at series[i], else (None, why not).

    `series`: (fiscal_date, eps) newest first, one entry per quarter. YoY pairs
    are matched by date (365 ± SAME_QUARTER_DAYS), never by position. Callers
    exclude quarters with missing values through `sue_dependencies` first.
    """
    def yoy(k):
        if k >= len(series):
            return None
        day, eps = _d(series[k][0]), series[k][1]
        for j in range(k + 1, len(series)):
            gap = (day - _d(series[j][0])).days
            if abs(gap - YEAR_DAYS) <= SAME_QUARTER_DAYS:
                return eps - series[j][1]
            if gap > YEAR_DAYS + SAME_QUARTER_DAYS:
                break
        return None

    current = yoy(i)
    if current is None:
        return None, "no_yoy_pair"
    prior = [v for v in (yoy(k) for k in range(i + 1, i + 1 + SUE_SIGMA_WINDOW))
             if v is not None]
    if len(prior) < SUE_SIGMA_MIN_OBS:
        return None, "few_sigma_obs"
    sigma = statistics.stdev(prior)
    scale = max(1.0, max(abs(v) for v in prior))
    if not math.isfinite(sigma) or sigma <= 1e-9 * scale:
        return None, "zero_sigma"
    return current / sigma, None


def street_eps_depth(rows: List[Dict[str, Any]], as_of: str,
                     current_fiscal: Optional[str] = None, *,
                     income_rows: Optional[List[Dict]] = None,
                     splits: Optional[List[Dict]] = None) -> Dict[str, Any]:
    """Street EPS depth and SUE computability at `as_of`.

    Only announced actuals count; unmapped rows lower `mapped_ratio` but never
    extend the run. Fiscal dates within SAME_QUARTER_DAYS are one quarter
    (reported in `dup_fiscal`; conflicting actuals are unavailable). `current_fiscal`
    is the season the three statement tables had reached by `as_of`: every
    factor is judged at that quarter, so depth and SUE are measured from the
    EPS quarter aligned with it (±SAME_QUARTER_DAYS). EPS announced for later
    quarters is shown (`latest_fiscal`) but not used; EPS without the anchor
    quarter is `behind_current` and nothing is computable. Without an anchor,
    the newest EPS quarter is used unless older than LATEST_FISCAL_MAX_AGE_DAYS.

    `depth_ok` / `depth_full`: ≥11 / ≥13 consecutive quarters from the anchor.
    `sue_ok` / `dsue_ok`: the north-star SUE is actually computable at the
    anchor quarter (and the one before it, for ΔSUE).
    """
    known = _known_eps_rows(rows, as_of)
    mapped = sorted((r for r in known if r.get("fiscal_date")),
                    key=lambda r: (r["fiscal_date"][:10], r["announce_date"]))
    resolved = resolve_eps_quarters(rows, as_of)
    quarters = resolved["quarters"]
    dup = resolved["dup_fiscal"]
    series = [(q["fiscal_date"], q["eps_actual"]) for q in quarters]
    split_audit = split_basis_audit(quarters, income_rows or [], splits or [])

    def quality_reason(index):
        if index is None or index >= len(series):
            return None
        # The current YoY and prior eight YoYs define the actual dependencies.
        required = sue_dependencies([f for f, _ in series], index)
        for k in sorted(required):
            if quarters[k]["issues"]:
                return quarters[k]["issues"][0]
        fiscals = [series[k][0] for k in required]
        for issue in split_audit["issues"]:
            if min(fiscals) < issue["boundary_fiscal"] <= max(fiscals):
                return "eps_split_basis_suspect"
        return None

    heads = [f for f, _ in series]

    latest = heads[0] if heads else None
    if current_fiscal:
        anchor = next((i for i, f in enumerate(heads)
                       if abs((_d(f) - _d(current_fiscal)).days) <= SAME_QUARTER_DAYS), None)
    else:
        anchor = 0 if heads else None
    behind = bool(current_fiscal) and anchor is None

    consecutive = 0
    if anchor is not None:
        consecutive = 1
        gap_max = _gap_max_days()
        for newer, older in zip(heads[anchor:], heads[anchor + 1:]):
            if (_d(newer) - _d(older)).days > gap_max:
                break
            consecutive += 1
    stale = behind or (current_fiscal is None and latest is not None
                       and (_d(as_of) - _d(latest)).days > LATEST_FISCAL_MAX_AGE_DAYS)
    run = 0 if stale else consecutive
    if not series:
        sue_missing = "no_rows"
    elif stale:
        sue_missing = "stale"
    else:
        sue_missing = quality_reason(anchor) or sue_value(series, anchor)[1]
    sue_ok = sue_missing is None
    dsue_missing = sue_missing if not sue_ok else (quality_reason(anchor + 1) or sue_value(series, anchor + 1)[1])
    return {
        "consecutive": consecutive,
        "stale": stale,
        "behind_current": behind,
        "depth_ok": run >= SUE_MIN_QUARTERS,
        "depth_full": run >= SUE_FULL_QUARTERS,
        "sue_ok": sue_ok,
        "dsue_ok": dsue_missing is None,
        "dsue_missing": dsue_missing,
        "quality_issues": resolved["issues"] + split_audit["issues"],
        "split_check": split_audit,
        "sue_missing": sue_missing,
        "latest_fiscal": latest,
        "anchor_fiscal": heads[anchor] if anchor is not None else None,
        "mapped_ratio": (len(mapped) / len(known)) if known else None,
        "dup_fiscal": dup,
    }


def split_suspects(rows: List[Dict[str, Any]], splits: List[Dict[str, Any]],
                   as_of: str) -> List[Dict[str, Any]]:
    """Splits whose adjacent fiscal-quarter EPS jump by ≈ the split ratio.

    A report-only heuristic for unadjusted pre-split EPS: last quarter ending
    before the split vs first quarter ending on/after it, ratio within ±15%.
    """
    mapped = sorted((r for r in _known_eps_rows(rows, as_of) if r.get("fiscal_date")),
                    key=lambda r: r["fiscal_date"])
    out = []
    for sp in splits:
        day = str(sp["date"])[:10]
        if day > as_of:
            continue
        ratio = float(sp["numerator"]) / float(sp["denominator"])
        before = [r for r in mapped if r["fiscal_date"][:10] < day]
        after = [r for r in mapped if r["fiscal_date"][:10] >= day]
        if not before or not after or ratio == 1:
            continue
        pre, post = before[-1]["eps_actual"], after[0]["eps_actual"]
        if not pre or not post or (pre > 0) != (post > 0):
            continue
        observed = pre / post
        if abs(observed / ratio - 1) <= SPLIT_MATCH_TOLERANCE:
            out.append({"split_date": day, "ratio": ratio,
                        "fiscal_before": before[-1]["fiscal_date"], "eps_before": pre,
                        "fiscal_after": after[0]["fiscal_date"], "eps_after": post,
                        "observed_ratio": round(observed, 4)})
    return out


def gap_reason(statement_dates: List[str], job_status: Optional[str],
               window_start: str, listed_after: Optional[str] = None,
               listed_by: Optional[str] = None) -> str:
    """Why a symbol lacks a window at some as-of date.

    `statement_dates`: fiscal dates on hand in ANY statement table, known by
    the as-of (the union: one short table must not make an old company look
    young); `job_status`: the worst backfill job status across the three
    datasets (None = never in the manifest); `window_start`: earliest fiscal
    date the window needs.

    A completed collection only proves we stored what the vendor returned, so
    a history that starts after `window_start` is `short_history` (inherent)
    only with listing evidence: `listed_after` (the company is known to have
    listed after this date) past the window start. Evidence that it listed
    earlier (`listed_by`) makes it `vendor_short`; no evidence at all is
    `history_depth_unknown`. Both stay on the fixable side.
    """
    if job_status is None:
        return "not_attempted"
    if job_status in ("fetch_failed", "pending", "in_progress"):
        return "fetch_failed"
    if job_status == "provider_empty" or not statement_dates:
        return "provider_empty"
    if min(statement_dates) <= window_start:
        return "gap_in_series"
    if listed_after and listed_after > window_start:
        return "short_history"
    if listed_by and listed_by <= window_start:
        return "vendor_short"
    return "history_depth_unknown"


def eps_gap_reason(depth: Dict[str, Any], statement_fiscals: List[str],
                   unmapped_rows: List[Dict[str, Any]], job_status: Optional[str],
                   window_start: str, listed_after: Optional[str] = None,
                   listed_by: Optional[str] = None) -> str:
    """Why SUE is not computable for a symbol at some as-of date.

    `statement_fiscals` / `job_status` / listing evidence: as for `gap_reason`,
    over the income statement; `window_start`: earliest fiscal date the SUE
    window needs; `unmapped_rows`: announced actuals without a fiscal date.
    """
    if (depth.get("sue_missing") or "").startswith("eps_"):
        return depth["sue_missing"]
    if len(statement_fiscals) < SUE_MIN_QUARTERS:
        reason = gap_reason(statement_fiscals, job_status, window_start,
                            listed_after, listed_by)
        return reason if reason in INHERENT_GAP_REASONS else "statements_" + reason
    latest = depth["latest_fiscal"]
    ref = depth["anchor_fiscal"] or latest
    recent_unmapped = [r for r in unmapped_rows
                       if ref is None or r["announce_date"][:10] > ref]
    if latest is None and not unmapped_rows:
        return "no_earnings_rows"
    if depth["stale"]:
        if recent_unmapped:
            return "unmapped"
        return "missing_current_quarter" if depth["behind_current"] else "stale_series"
    if not depth["depth_ok"]:
        return "unmapped" if unmapped_rows else "missing_quarters"
    return "sue_degenerate"


# ---------------------------------------------------------------------------
# Freeze-parameter recheck (north star: day 60 / 95% / day 80)
# ---------------------------------------------------------------------------

# Calendar quarter end qe owns fiscal quarters ending in (prev_qe + 7d, qe + 7d]
# — a partition, so no fiscal quarter lands in two seasons, and 52/53-week
# years ending a few days after the calendar quarter stay in it.
SEASON_SHIFT_DAYS = 7
FREEZE_DAYS = (60, 80)
FREEZE_COVERAGE = 0.95
FREEZE_SCAN_MAX_DAYS = 120


def _prev_quarter_end(qe: str) -> date:
    end = _d(qe)
    month = end.month - 3 if end.month > 3 else 12
    year = end.year if end.month > 3 else end.year - 1
    return date(year, month, QUARTER_END_MONTH_DAYS[month])


def _known_on(row: Dict[str, Any], earnings_rows=None) -> Optional[str]:
    """accepted_date (date part), falling back to filing_date."""
    return statement_known_on(row, earnings_rows=earnings_rows)


def arrival_day(tables: Dict[str, List[Dict[str, Any]]], qe: str, *,
                earnings_rows: Optional[List[Dict]] = None) -> Optional[int]:
    """Days after `qe` when this season's quarter was known in ALL three tables.

    `tables`: statement rows per table. A fiscal quarter arrives when the last
    of its three statements became known; None = never (within the data).
    """
    from datetime import timedelta
    lo = (_prev_quarter_end(qe) + timedelta(days=SEASON_SHIFT_DAYS)).isoformat()
    hi = (_d(qe) + timedelta(days=SEASON_SHIFT_DAYS)).isoformat()
    known: List[Dict[str, str]] = []
    for rows in tables.values():
        known.append({r["date"][:10]: k for r in rows
                      if lo < r["date"][:10] <= hi and (k := _known_on(r, earnings_rows))})
    if not known:
        return None
    common = set.intersection(*(set(k) for k in known))
    days = [(_d(max(k[f] for k in known)) - _d(qe)).days for f in common]
    return min(days) if days else None


def freeze_coverage(arrivals: List[Optional[int]]) -> Dict[str, Any]:
    """Coverage at day 60 / 80 and the first day it reaches 95%."""
    n = len(arrivals)
    if not n:
        return {"cov_d60": None, "cov_d80": None, "first_day_ge95": None}

    def cov(day):
        return sum(1 for a in arrivals if a is not None and a <= day) / n

    first = next((d for d in range(FREEZE_SCAN_MAX_DAYS + 1) if cov(d) >= FREEZE_COVERAGE),
                 None)
    return {"cov_d60": round(cov(FREEZE_DAYS[0]), 4), "cov_d80": round(cov(FREEZE_DAYS[1]), 4),
            "first_day_ge95": first}
