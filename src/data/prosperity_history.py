"""Prosperity engine D9 history: pure logic behind the backfill targets and
the coverage report (north star layer 1, P2/P3).

Read-only over MarketStore. Membership comes from M1's
`approximate_members_as_of` (10-day market-cap freshness + alias folding);
nothing here re-implements membership, the three-table window or fiscal
matching — those are imported from their owners.
"""
from datetime import date
from typing import Any, Dict, List, Optional

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

# SUE needs 11 consecutive reported quarters at minimum, the full window is
# 13, and ΔSUE needs one more quarter than SUE (north star layer 2).
SUE_MIN_QUARTERS = 11
SUE_FULL_QUARTERS = 13
DSUE_MIN_QUARTERS = SUE_MIN_QUARTERS + 1
SPLIT_MATCH_TOLERANCE = 0.15
# Two fiscal dates this close are one quarter (52/53-week filers: the estimate
# and the income statement can date the same quarter a few days apart).
SAME_QUARTER_DAYS = 20
# The newest mapped quarter must be the season just reported, or at most one
# season behind a late filer; anything older means the recent run is broken.
LATEST_FISCAL_MAX_AGE_DAYS = 91 + 120

# "The company simply has no longer history" vs everything that is ours to fix.
INHERENT_GAP_REASONS = {"short_history"}


def _gap_max_days() -> int:
    from config.settings import FUNDAMENTAL_QUARTER_GAP_MAX_DAYS
    return FUNDAMENTAL_QUARTER_GAP_MAX_DAYS


def three_table_ok(store, symbol: str, as_of: str) -> bool:
    """≥8 contiguous quarters in all three statement tables, known by as_of."""
    import scripts.backfill_extended_fundamentals as backfill
    return backfill.has_asof_window(store, symbol, as_of)


def _known_eps_rows(rows, as_of: str):
    return [r for r in rows
            if r.get("announce_date") and r["announce_date"][:10] <= as_of
            and r.get("eps_actual") is not None]


def street_eps_depth(rows: List[Dict[str, Any]], as_of: str) -> Dict[str, Any]:
    """Consecutive mapped street-EPS quarters announced by `as_of`.

    Only rows with an announced actual count; unmapped rows (no fiscal date)
    lower `mapped_ratio` but never extend the run. Fiscal dates within
    SAME_QUARTER_DAYS of each other are one quarter (reported as duplicates).
    Counted from the newest fiscal quarter back while adjacent quarters are ≤
    the quarter-gap cap; if the newest one is older than
    LATEST_FISCAL_MAX_AGE_DAYS the run is `stale` and nothing is computable.
    """
    known = _known_eps_rows(rows, as_of)
    mapped = [r for r in known if r.get("fiscal_date")]
    quarters: List[List[str]] = []          # newest first; each = same-quarter dates
    for f in sorted({r["fiscal_date"][:10] for r in mapped}, reverse=True):
        if quarters and (date.fromisoformat(quarters[-1][-1])
                         - date.fromisoformat(f)).days <= SAME_QUARTER_DAYS:
            quarters[-1].append(f)
        else:
            quarters.append([f])
    per_date: Dict[str, int] = {}
    for r in mapped:
        per_date[r["fiscal_date"][:10]] = per_date.get(r["fiscal_date"][:10], 0) + 1
    dup = sorted(q[0] for q in quarters if len(q) > 1 or per_date[q[0]] > 1)
    heads = [q[0] for q in quarters]
    consecutive = 1 if heads else 0
    gap_max = _gap_max_days()
    for newer, older in zip(heads, heads[1:]):
        if (date.fromisoformat(newer) - date.fromisoformat(older)).days > gap_max:
            break
        consecutive += 1
    stale = bool(heads) and (date.fromisoformat(as_of[:10])
                             - date.fromisoformat(heads[0])).days > LATEST_FISCAL_MAX_AGE_DAYS
    usable = 0 if stale else consecutive
    return {
        "consecutive": consecutive,
        "stale": stale,
        "sue_ok": usable >= SUE_MIN_QUARTERS,
        "sue_full": usable >= SUE_FULL_QUARTERS,
        "dsue_ok": usable >= DSUE_MIN_QUARTERS,
        "latest_fiscal": heads[0] if heads else None,
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
               window_start: str) -> str:
    """Why a symbol lacks a window at some as-of date.

    `statement_dates`: fiscal dates on hand in ANY statement table, known by
    the as-of (the union: one short table must not make an old company look
    young); `job_status`: the worst backfill job status across the three
    datasets (None = never in the manifest); `window_start`: earliest fiscal
    date the window needs. Only a completed collection whose earliest filing
    is after `window_start` is `short_history`.
    """
    if job_status is None:
        return "not_attempted"
    if job_status in ("fetch_failed", "pending", "in_progress"):
        return "fetch_failed"
    if job_status == "provider_empty" or not statement_dates:
        return "provider_empty"
    if min(statement_dates) > window_start:
        return "short_history"
    return "gap_in_series"


def eps_gap_reason(depth: Dict[str, Any], statement_fiscals: List[str],
                   unmapped_rows: List[Dict[str, Any]], job_status: Optional[str],
                   window_start: str) -> str:
    """Why a symbol falls short of the SUE minimum at some as-of date.

    `statement_fiscals` / `job_status`: known statement fiscal dates and the
    worst statement job status, as for `gap_reason`; `window_start`: earliest
    fiscal date the SUE window needs. Filings that are shorter than the window
    make the company too young (`short_history`) only when the statement
    collection itself completed; otherwise the statement gap is ours to fix.
    """
    if len(statement_fiscals) < SUE_MIN_QUARTERS:
        reason = gap_reason(statement_fiscals, job_status, window_start)
        return "short_history" if reason == "short_history" else "statements_" + reason
    if depth["latest_fiscal"] is None and not unmapped_rows:
        return "no_earnings_rows"
    if unmapped_rows:
        return "unmapped"
    return "missing_quarters"


# ---------------------------------------------------------------------------
# Freeze-parameter recheck (north star: day 60 / 95% / day 80)
# ---------------------------------------------------------------------------

# A fiscal quarter "belongs" to calendar quarter end qe when it ends in
# (qe − 85d, qe + 7d]: off-calendar and 52/53-week years land on the right
# season without stealing the previous one (qe − 90d is the prior quarter end).
SEASON_BEFORE_DAYS = 85
SEASON_AFTER_DAYS = 7
FREEZE_DAYS = (60, 80)
FREEZE_COVERAGE = 0.95
FREEZE_SCAN_MAX_DAYS = 120


def _known_on(row: Dict[str, Any]) -> Optional[str]:
    """accepted_date (date part), falling back to filing_date."""
    accepted = (row.get("accepted_date") or "")[:10]
    return accepted or (row.get("filing_date") or "")[:10] or None


def arrival_day(statement_rows: List[Dict[str, Any]], qe: str) -> Optional[int]:
    """Days after `qe` when this season's quarter first became known; None = never."""
    from datetime import timedelta
    end = date.fromisoformat(qe)
    lo = (end - timedelta(days=SEASON_BEFORE_DAYS)).isoformat()
    hi = (end + timedelta(days=SEASON_AFTER_DAYS)).isoformat()
    days = [(date.fromisoformat(k) - end).days
            for r in statement_rows
            if lo < r["date"][:10] <= hi and (k := _known_on(r))]
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
