"""Street EPS series: Codex dedup/conflict and split detection, then M4's in-memory rescale.

Boss 2026-09-29: the quality layer only detects. Here, adjacent boundaries with
the same ratio are one break (overlapping windows often report it twice, e.g.
MNST); the break sits where the single-quarter street/GAAP jump best matches
the ratio. At that boundary, whichever series jumps more in that one quarter,
and by about the ratio, is the broken one: street → earlier quarters divided by
the ratio (`eps_split_rescaled`); GAAP → street untouched
(`gaap_split_basis_break`); otherwise earlier quarters are blanked
(`eps_split_unconfirmed`). Single-quarter steps keep strong growth from masking
the jump (APH). market.db is never changed.

Breaks are found on the full stored series, then applied to the quarters announced by
as_of, the same retrospective basis as the price rescale; when the evidence needs
quarters announced after as_of the affected quarters also get `eps_split_retrospective`.
"""
from __future__ import annotations

import heapq
import math
from collections import Counter
from dataclasses import replace
from datetime import date
from typing import Dict, List, Mapping, Optional, Sequence, Tuple

from src.data.prosperity_quality import SAME_QUARTER_DAYS, resolve_eps_quarters, split_basis_audit
from terminal.prosperity.config import EPS_QUARTERS, EPS_STATEMENT_MATCH_DAYS, EPS_STATEMENT_PAIR_DAYS
from terminal.prosperity.types import EpsQuarter

CLUSTER_GAP = 2   # boundaries this close with the same ratio are one break seen by overlapping windows
FULL_HISTORY = "9999-12-31"   # split evidence reads the whole stored series, like the price rescale


def _d(value: str) -> date:
    return date.fromisoformat(value[:10])


def _steps(issue: Mapping) -> Tuple[float, float]:
    """Single-quarter log jumps (street, GAAP) across the boundary, from Codex's evidence block."""
    before, after = issue["evidence"][2], issue["evidence"][3]
    return (math.log(abs(before["street_eps"] / after["street_eps"])),
            math.log(abs(before["gaap_eps_diluted"] / after["gaap_eps_diluted"])))


def _clusters(fiscals: List[str], issues: Sequence[Mapping]) -> List[Tuple[frozenset, List[str], Dict[str, Mapping]]]:
    by_boundary: Dict[str, List[Mapping]] = {}
    for issue in issues:
        by_boundary.setdefault(issue["boundary_fiscal"], []).append(issue)
    out: List[Tuple[frozenset, List[str], Dict[str, Mapping]]] = []
    for b in sorted(by_boundary, key=fiscals.index):
        ratios = frozenset(round(i["split_ratio"], 6) for i in by_boundary[b])
        if out and out[-1][0] == ratios and fiscals.index(b) - fiscals.index(out[-1][1][-1]) <= CLUSTER_GAP:
            out[-1][1].append(b)
        else:
            out.append((ratios, [b], {}))
        out[-1][2][b] = by_boundary[b][0]
    return out


def _breaks(fiscals: List[str], issues: Sequence[Mapping]) -> List[Tuple[str, str, str, float, str]]:
    """Per break: (fiscal before, boundary fiscal, verdict, ratio, last evidence fiscal)."""
    out = []
    for ratios, members, evidence in _clusters(fiscals, issues):
        verdict, boundary, r = "unconfirmed", members[0], 1.0
        if len(ratios) == 1:
            r = next(iter(ratios))
            log_r = math.log(r)

            def misfit(b):
                s, g = _steps(evidence[b])
                return min(abs(s - g - log_r), abs(s - g + log_r))

            boundary = min(members, key=misfit)
            s, g = _steps(evidence[boundary])
            if abs(s) > abs(g) and abs(s - log_r) < abs(s):
                verdict = "street"
            elif abs(g) > abs(s) and abs(g - log_r) < abs(g):
                verdict = "gaap"
        cut = fiscals.index(boundary)
        out.append((fiscals[cut - 1], boundary, verdict, r, evidence[boundary]["evidence"][-1]["fiscal_date"]))
    return out


def _rescale(quarters: List[dict], breaks) -> Tuple[List[float], List[bool], List[List[str]]]:
    """Per visible quarter: divisor to the post-split basis, blanked flag, labels (actual and estimate share them).

    Breaks are judged on the full stored series, so a replay before three post-break quarters were
    announced still gets the unit its prices are on (Codex M4 review F1, 2026-09-29).
    """
    divisor, blank = [1.0] * len(quarters), [False] * len(quarters)
    labels: List[List[str]] = [list(q["issues"]) for q in quarters]
    last_visible = quarters[-1]["fiscal_date"] if quarters else None
    # Full-history and as_of groupings may name one quarter by different dates within SAME_QUARTER_DAYS
    gap = lambda a, b: (_d(a) - _d(b)).days
    for before, boundary, verdict, r, evidence_end in breaks:
        late = last_visible is None or gap(evidence_end, last_visible) > SAME_QUARTER_DAYS
        retrospective = ["eps_split_retrospective"] if late else []
        for k, q in enumerate(quarters):
            if verdict == "gaap":
                if any(abs(gap(q["fiscal_date"], x)) <= SAME_QUARTER_DAYS for x in (before, boundary)):
                    labels[k].append("gaap_split_basis_break")
            elif gap(boundary, q["fiscal_date"]) > SAME_QUARTER_DAYS:
                if verdict == "street":
                    divisor[k] *= r
                    labels[k].extend(["eps_split_rescaled"] + retrospective)
                else:
                    blank[k] = True
                    labels[k].extend(["eps_split_unconfirmed"] + retrospective)
    return divisor, blank, labels


def announced_eps(earnings: Sequence[Mapping], income_rows: Sequence[Mapping], splits: Sequence[Mapping],
                  as_of: str) -> Tuple[EpsQuarter, ...]:
    rows = [r for r in earnings if r.get("match_method") != "none"]

    def breaks_in(series):
        return _breaks([q["fiscal_date"] for q in series],
                       split_basis_audit(series, list(income_rows), list(splits))["issues"])

    stored = sorted(resolve_eps_quarters(rows, FULL_HISTORY)["quarters"], key=lambda q: q["fiscal_date"])
    quarters = sorted(resolve_eps_quarters(rows, as_of)["quarters"], key=lambda q: q["fiscal_date"])
    # Later rows can also hide a break (a conflicting duplicate blanks a pre-break quarter), so a
    # break the as_of series shows on its own still counts (code review 2026-09-29)
    full = breaks_in(stored)
    near = lambda b: any(abs((_d(b[1]) - _d(f[1])).days) <= SAME_QUARTER_DAYS for f in full)
    divisor, blank, labels = _rescale(quarters, full + [b for b in breaks_in(quarters) if not near(b)])

    def basis(value, k):
        return None if blank[k] or value is None else value / divisor[k]

    out = []
    for k, (q, tags) in enumerate(zip(quarters, labels)):
        first = next((r for r in rows if r.get("announce_date") == q["announce_date"] and r.get("fiscal_date")
                      and abs((_d(r["fiscal_date"]) - _d(q["fiscal_date"])).days) <= EPS_STATEMENT_MATCH_DAYS), {})
        out.append(EpsQuarter(q["fiscal_date"], q["announce_date"][:10], basis(q["eps_actual"], k),
                              basis(first.get("eps_estimated"), k), tuple(dict.fromkeys(tags))))
    return tuple(out)


def pair_statement_dates(series: Sequence[EpsQuarter], statement_fiscals: Sequence[str]) -> Tuple[EpsQuarter, ...]:
    """Attach the statement quarter each EPS quarter reports (Boss 2026-09-30 ④).

    FMP dates some 52/53-week reporters' EPS weeks off the period end (COST 2026-08-10 vs statement
    2026-08-30), which breaks 365 ± 20 year-on-year pairing. The nearest statement date within
    EPS_STATEMENT_PAIR_DAYS pairs, one to one; a tie, a statement two EPS quarters both sit nearest,
    or nothing in range leaves the quarter on FMP's date. fiscal_date itself is kept for consensus matching.
    """
    ends = sorted({s[:10] for s in statement_fiscals})
    picks = []
    for q in series:
        gaps = heapq.nsmallest(2, ((abs((_d(s) - _d(q.fiscal_date)).days), s) for s in ends))
        unique = len(gaps) == 1 or (len(gaps) == 2 and gaps[1][0] > gaps[0][0])
        picks.append(gaps[0][1] if unique and gaps[0][0] <= EPS_STATEMENT_PAIR_DAYS else None)
    taken = Counter(p for p in picks if p)
    return tuple(replace(q, statement_fiscal=p if p and taken[p] == 1 else None) for q, p in zip(series, picks))


def aligned_eps_window(series: Sequence[EpsQuarter], current_fiscal: Optional[str],
                       n: int = EPS_QUARTERS) -> Tuple[Tuple[EpsQuarter, ...], Optional[str]]:
    if current_fiscal is None:
        return (), "no_current_fiscal"
    ends = [i for i, q in enumerate(series)
            if abs((_d(q.period_end) - _d(current_fiscal)).days) <= EPS_STATEMENT_MATCH_DAYS]
    if not ends:
        return (), "eps_behind_current"
    end = ends[-1]
    return tuple(series[max(0, end + 1 - n):end + 1]), None
