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
"""
from __future__ import annotations

import math
from datetime import date
from typing import Dict, List, Mapping, Optional, Sequence, Tuple

from src.data.prosperity_quality import resolve_eps_quarters, split_basis_audit
from terminal.prosperity.config import EPS_QUARTERS, EPS_STATEMENT_MATCH_DAYS
from terminal.prosperity.types import EpsQuarter

CLUSTER_GAP = 2   # boundaries this close with the same ratio are one break seen by overlapping windows


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


def _rescale(quarters: List[dict], issues: Sequence[Mapping]) -> Tuple[List[float], List[bool], List[List[str]]]:
    """Per quarter: divisor to the post-split basis, blanked flag, labels (actual and estimate share them)."""
    divisor, blank = [1.0] * len(quarters), [False] * len(quarters)
    labels: List[List[str]] = [list(q["issues"]) for q in quarters]
    fiscals = [q["fiscal_date"] for q in quarters]
    for ratios, members, evidence in _clusters(fiscals, issues):
        verdict, boundary = "unconfirmed", members[0]
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
        if verdict == "street":
            for k in range(cut):
                divisor[k] *= r
                labels[k].append("eps_split_rescaled")
        elif verdict == "gaap":
            for k in (cut - 1, cut):
                labels[k].append("gaap_split_basis_break")
        else:
            for k in range(cut):
                blank[k] = True
                labels[k].append("eps_split_unconfirmed")
    return divisor, blank, labels


def announced_eps(earnings: Sequence[Mapping], income_rows: Sequence[Mapping], splits: Sequence[Mapping],
                  as_of: str) -> Tuple[EpsQuarter, ...]:
    rows = [r for r in earnings if r.get("match_method") != "none"]
    quarters = sorted(resolve_eps_quarters(rows, as_of)["quarters"], key=lambda q: q["fiscal_date"])
    audit = split_basis_audit(quarters, list(income_rows), list(splits))
    divisor, blank, labels = _rescale(quarters, audit["issues"])

    def basis(value, k):
        return None if blank[k] or value is None else value / divisor[k]

    out = []
    for k, (q, tags) in enumerate(zip(quarters, labels)):
        first = next((r for r in rows if r.get("announce_date") == q["announce_date"] and r.get("fiscal_date")
                      and abs((_d(r["fiscal_date"]) - _d(q["fiscal_date"])).days) <= EPS_STATEMENT_MATCH_DAYS), {})
        out.append(EpsQuarter(q["fiscal_date"], q["announce_date"][:10], basis(q["eps_actual"], k),
                              basis(first.get("eps_estimated"), k), tuple(dict.fromkeys(tags))))
    return tuple(out)


def aligned_eps_window(series: Sequence[EpsQuarter], current_fiscal: Optional[str],
                       n: int = EPS_QUARTERS) -> Tuple[Tuple[EpsQuarter, ...], Optional[str]]:
    if current_fiscal is None:
        return (), "no_current_fiscal"
    ends = [i for i, q in enumerate(series)
            if abs((_d(q.fiscal_date) - _d(current_fiscal)).days) <= EPS_STATEMENT_MATCH_DAYS]
    if not ends:
        return (), "eps_behind_current"
    end = ends[-1]
    return tuple(series[max(0, end + 1 - n):end + 1]), None
