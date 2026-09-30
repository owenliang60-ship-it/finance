"""M7 S1: row-by-row check of our shared statement factors against the original site's 47 boards.

North star layer 2 "S1 对拍工具"; plan docs/plans/2026-09-30-prosperity-m7-s1-comparison.md.
The original site's output is private: it stays in gitignored paths, and nothing this
module returns for the committed summary carries a site or engine value.
Pure functions: no database reads, no file writes.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Dict, List, Mapping, Optional, Sequence, Tuple

from src.data.prosperity_history import SAME_QUARTER_DAYS
from terminal.prosperity.factors import prior_quarter, statement_factors, year_base
from terminal.prosperity.types import QuarterInputs

# (original site key, engine key); both sides in % or pp
FACTORS: Tuple[Tuple[str, str], ...] = (
    ("revenue_yoy_pct", "revenue_yoy"),
    ("revenue_yoy_accel", "revenue_accel"),
    ("gm_pct", "gm_level"),
    ("gm_yoy_pp", "gm_yoy"),
    ("fcf_margin_yoy_pp", "fcf_margin_yoy"),
    ("net_margin_yoy_pp", "net_margin_yoy"),
)
SYMBOL_MAP = {"SQ": "XYZ"}                      # Block renamed its ticker to XYZ in 2025
# Same issuer and statements as BRK.B, which sits on every board BRK.A does after 2021
SKIP = {"BRK.A": "duplicate_share_class"}
# dq.repairs field → input series; unknown fields are kept as-is and never explain a difference
REPAIR_FIELDS = {"revenue": "rev", "gross_margin": "gm", "fcf_margin": "fcfm", "net_margin": "nim"}
_SERIES = (("rev", "rev_abs_series"), ("gm", "gm_series"), ("fcfm", "fcfm_series"), ("nim", "nim_series"))
# "site" is the original site's basis (only M7 uses it): no day-count adjustment, base at [i−4]
BASES = {"site": {"day_adjust": False, "pairing": "position"}, "ours": {"day_adjust": True, "pairing": "date"}}
SERIES_QUARTERS = 8
TOLERANCE_PP = 0.5
# Input agreement when classifying: the site rounds revenue (millions) and margins to one decimal
REV_REL_TOL = 0.001
REV_ABS_TOL_M = 0.06
MARGIN_TOL_PP = 0.1
# engine key → (input series, positions relative to the current quarter on the site's position basis)
DEPENDS: Mapping[str, Tuple[str, Tuple[int, ...]]] = {
    "revenue_yoy": ("rev", (0, -4)),
    "revenue_accel": ("rev", (0, -1, -4, -5)),
    "gm_level": ("gm", (0,)),
    "gm_yoy": ("gm", (0, -4)),
    "fcf_margin_yoy": ("fcfm", (0, -4)),
    "net_margin_yoy": ("nim", (0, -4)),
}


@dataclass(frozen=True)
class SeriesQuarter:
    fiscal_date: str
    rev: Optional[float]          # millions
    gm: Optional[float]           # gross margin, %
    fcfm: Optional[float]         # FCF margin, %
    nim: Optional[float]          # net margin, %


@dataclass(frozen=True)
class SiteRow:
    board: str
    site_symbol: str
    symbol: str
    latest_q: str
    filed: Optional[str]
    values: Mapping[str, Optional[float]]            # engine key → site value
    quarters: Tuple[SeriesQuarter, ...]              # oldest first
    repairs: Tuple[Tuple[str, str], ...]             # (quarter, mapped field)
    skip_reason: Optional[str]


def our_symbol(site_symbol: str) -> str:
    return SYMBOL_MAP.get(site_symbol, site_symbol.replace(".", "-"))


def _at(values, i):
    return values[i] if values is not None and i < len(values) else None


def load_site_rows(data: Mapping) -> List[SiteRow]:
    out = []
    for board in data["boards"]:
        for row in board["rows"]:
            dates = [d[:10] for d in row.get("q_series") or []]
            quarters = tuple(SeriesQuarter(d, **{name: _at(row.get(key), i) for name, key in _SERIES})
                             for i, d in enumerate(dates))
            repairs = tuple((r["quarter"][:10], REPAIR_FIELDS.get(r["field"], r["field"]))
                            for r in (row.get("dq") or {}).get("repairs") or [])
            sym = row["symbol"]
            out.append(SiteRow(board=board["asof"][:10], site_symbol=sym, symbol=our_symbol(sym),
                               latest_q=row["latest_q"][:10], filed=row.get("filed"),
                               values={engine: row.get(site) for site, engine in FACTORS},
                               quarters=quarters, repairs=repairs, skip_reason=SKIP.get(sym)))
    return out


@dataclass(frozen=True)
class OurSide:
    fiscal_date: str
    aligned_by: str                                        # asof | trimmed | rebuilt
    values: Mapping[str, Mapping[str, Optional[float]]]    # basis → engine key → value
    missing: Mapping[str, Mapping[str, str]]               # basis → engine key → reason
    quarters: Tuple[SeriesQuarter, ...]                    # last 8, oldest first
    pairing_differs: Mapping[str, bool]                    # engine key → date and position pick different quarters


def _days(a: str, b: str) -> int:
    return (date.fromisoformat(a[:10]) - date.fromisoformat(b[:10])).days


def match_index(quarters: Sequence[QuarterInputs], latest_q: str) -> Optional[int]:
    """Index of our quarter closest to the site's latest_q, if within SAME_QUARTER_DAYS."""
    near = [(abs(_days(q.fiscal_date, latest_q)), i) for i, q in enumerate(quarters)]
    near = [(gap, i) for gap, i in near if gap <= SAME_QUARTER_DAYS]
    return min(near)[1] if near else None


def timing_status(current_fiscal: Optional[str], latest_q: str) -> str:
    if current_fiscal is None:
        return "ours_none"
    gap = _days(current_fiscal, latest_q)
    if abs(gap) <= SAME_QUARTER_DAYS:
        return "match"
    return "ours_ahead" if gap > 0 else "ours_behind"


def _ratio(num: Optional[float], revenue: Optional[float]) -> Optional[float]:
    if num is None or revenue is None or revenue <= 0:
        return None
    return num / revenue * 100


def _series(q: QuarterInputs) -> SeriesQuarter:
    return SeriesQuarter(q.fiscal_date, None if q.revenue is None else q.revenue / 1e6,
                         _ratio(q.gross_profit, q.revenue), _ratio(q.free_cash_flow, q.revenue),
                         _ratio(q.net_income, q.revenue))


def _pairing_differs(fiscals: Sequence[str]) -> Dict[str, bool]:
    """Per factor: do date pairing and position pairing pick different quarters?"""
    c = len(fiscals) - 1
    pos = lambda i: i if i >= 0 else None
    cur = year_base(fiscals, c) != pos(c - 4)
    p = prior_quarter(fiscals, c)
    prior = p != pos(c - 1) or (p is not None and year_base(fiscals, p) != pos(c - 5))
    return {"revenue_yoy": cur, "revenue_accel": cur or prior, "gm_level": False, "gm_yoy": cur,
            "fcf_margin_yoy": cur, "net_margin_yoy": cur}


def our_side(quarters: Sequence[QuarterInputs], aligned_by: str) -> OurSide:
    """Both bases on `quarters`, whose last entry is the quarter being compared (caller trims)."""
    values: Dict[str, Dict[str, Optional[float]]] = {}
    missing: Dict[str, Dict[str, str]] = {}
    for basis, kw in BASES.items():
        out = statement_factors(quarters, **kw)
        values[basis] = {engine: out.values[engine] for _, engine in FACTORS}
        missing[basis] = {engine: out.missing[engine] for _, engine in FACTORS if engine in out.missing}
    return OurSide(fiscal_date=quarters[-1].fiscal_date[:10], aligned_by=aligned_by, values=values,
                   missing=missing, quarters=tuple(_series(q) for q in quarters[-SERIES_QUARTERS:]),
                   pairing_differs=_pairing_differs([q.fiscal_date for q in quarters]))


@dataclass(frozen=True)
class Comparison:
    board: str
    symbol: str
    site_symbol: str
    fiscal_date: str               # our aligned quarter (the site's latest_q when none was found)
    factor: str                    # engine key
    basis: str                     # site | ours
    site_value: Optional[float]
    our_value: Optional[float]
    diff: Optional[float]          # ours − site
    counted: bool                  # the site has a value: enters the denominator
    passed: bool
    category: str
    detail: str
    aligned_by: str


def _at_offset(quarters: Sequence[SeriesQuarter], k: int) -> Optional[SeriesQuarter]:
    i = len(quarters) - 1 + k
    return quarters[i] if 0 <= i < len(quarters) else None


def _inputs_differ(field: str, mine: Optional[float], theirs: Optional[float]) -> bool:
    if mine is None or theirs is None:
        return (mine is None) != (theirs is None)
    if field == "rev":
        return abs(mine - theirs) > max(REV_REL_TOL * abs(theirs), REV_ABS_TOL_M)
    return abs(mine - theirs) > MARGIN_TOL_PP


def _classify(site: SiteRow, ours: OurSide, factor: str, basis: str) -> Tuple[str, str]:
    """Cause of a failed comparison: the first of the plan's five rules that applies."""
    if ours.values[basis][factor] is None:
        return "ours_missing", ours.missing[basis].get(factor, "")
    field, offsets = DEPENDS[factor]
    for k in offsets:
        theirs = _at_offset(site.quarters, k)
        if theirs and any(f == field and abs(_days(q, theirs.fiscal_date)) <= SAME_QUARTER_DAYS
                          for q, f in site.repairs):
            return "site_repaired", f"{field}@{theirs.fiscal_date}"
    pairs = [(k, _at_offset(ours.quarters, k), _at_offset(site.quarters, k)) for k in offsets]
    for k, mine, theirs in pairs:
        if mine is None or theirs is None or abs(_days(mine.fiscal_date, theirs.fiscal_date)) > SAME_QUARTER_DAYS:
            return "quarter_sequence", (f"offset {k}: ours {mine.fiscal_date if mine else '-'} "
                                        f"site {theirs.fiscal_date if theirs else '-'}")
    for k, mine, theirs in pairs:
        if _inputs_differ(field, getattr(mine, field), getattr(theirs, field)):
            return "input_diff", f"{field}@{theirs.fiscal_date}"
    return "unexplained", ""


def compare(site: SiteRow, ours: Optional[OurSide], missing_reason: str = "") -> List[Comparison]:
    """Six factors × two bases; `ours` is None when our side has no matching quarter."""
    out, site_passed = [], {}
    for basis in ("site", "ours"):
        for _, factor in FACTORS:
            sv = site.values[factor]
            ov = ours.values[basis][factor] if ours else None
            diff = None if sv is None or ov is None else ov - sv
            common = dict(board=site.board, symbol=site.symbol, site_symbol=site.site_symbol,
                          fiscal_date=ours.fiscal_date if ours else site.latest_q, factor=factor, basis=basis,
                          site_value=sv, our_value=ov, diff=diff, aligned_by=ours.aligned_by if ours else "none")
            if sv is None:
                c = Comparison(**common, counted=False, passed=False, category="site_missing",
                               detail="ours_has_value" if ov is not None else "both_missing")
            elif diff is not None and abs(diff) < TOLERANCE_PP:
                c = Comparison(**common, counted=True, passed=True, category="match", detail="")
            else:
                if ours is None:
                    category, detail = "ours_missing", missing_reason
                elif basis == "ours" and site_passed.get(factor):
                    category = "basis_only_pairing" if ours.pairing_differs[factor] else "basis_only_day_adjust"
                    detail = ""
                else:
                    category, detail = _classify(site, ours, factor, basis)
                c = Comparison(**common, counted=True, passed=False, category=category, detail=detail)
            if basis == "site":
                site_passed[factor] = c.passed
            out.append(c)
    return out
