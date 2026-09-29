"""Street EPS series: Codex dedup/conflict and split detection, then M4's in-memory rescale.

Boss 2026-09-29: the quality layer only detects. Here, when every split matched
at one boundary has the same ratio and street EPS itself shows that jump, the
quarters before the boundary are divided by the ratio (`eps_split_rescaled`);
a GAAP-only break leaves street untouched (`gaap_split_basis_break`); anything
else blanks the earlier quarters (`eps_split_unconfirmed`). market.db is never
changed.
"""
from __future__ import annotations

import math
import statistics
from datetime import date
from typing import Dict, List, Mapping, Optional, Sequence, Tuple

from src.data.prosperity_quality import resolve_eps_quarters, split_basis_audit
from terminal.prosperity.config import EPS_QUARTERS, EPS_STATEMENT_MATCH_DAYS
from terminal.prosperity.types import EpsQuarter

LEVEL_QUARTERS = 3   # quarters each side used to judge which series broke


def _d(value: str) -> date:
    return date.fromisoformat(value[:10])


def _level(values: Sequence[Optional[float]]) -> Optional[float]:
    vals = [abs(v) for v in values if v is not None and v != 0]
    return statistics.median(vals) if vals else None


def _rescale(quarters: List[dict], issues: Sequence[Mapping]) -> Tuple[List[Optional[float]], List[List[str]]]:
    eps = [q["eps_actual"] for q in quarters]
    labels: List[List[str]] = [list(q["issues"]) for q in quarters]
    ratios: Dict[str, set] = {}
    for issue in issues:
        ratios.setdefault(issue["boundary_fiscal"], set()).add(round(issue["split_ratio"], 6))
    fiscals = [q["fiscal_date"] for q in quarters]
    original = list(eps)
    for boundary, found in ratios.items():
        cut = fiscals.index(boundary)
        before = range(cut)
        verdict = "unconfirmed"
        if len(found) == 1:
            r = next(iter(found))
            pre = _level([original[k] for k in range(max(0, cut - LEVEL_QUARTERS), cut)])
            post = _level([original[k] for k in range(cut, min(len(original), cut + LEVEL_QUARTERS))])
            if pre and post:
                log_l, log_r = math.log(pre / post), math.log(r)
                street, gaap, opposite = abs(log_l - log_r), abs(log_l), abs(log_l + log_r)
                if street < min(gaap, opposite):
                    verdict = "street"
                elif gaap < opposite:
                    verdict = "gaap"
        if verdict == "street":
            for k in before:
                if eps[k] is not None:
                    eps[k] = eps[k] / r
                labels[k].append("eps_split_rescaled")
        elif verdict == "gaap":
            for k in (cut - 1, cut):
                labels[k].append("gaap_split_basis_break")
        else:
            for k in before:
                eps[k] = None
                labels[k].append("eps_split_unconfirmed")
    return eps, labels


def announced_eps(earnings: Sequence[Mapping], income_rows: Sequence[Mapping], splits: Sequence[Mapping],
                  as_of: str) -> Tuple[EpsQuarter, ...]:
    rows = [r for r in earnings if r.get("match_method") != "none"]
    quarters = sorted(resolve_eps_quarters(rows, as_of)["quarters"], key=lambda q: q["fiscal_date"])
    audit = split_basis_audit(quarters, list(income_rows), list(splits))
    eps, labels = _rescale(quarters, audit["issues"])
    out = []
    for q, value, tags in zip(quarters, eps, labels):
        first = next((r for r in rows if r.get("announce_date") == q["announce_date"] and r.get("fiscal_date")
                      and abs((_d(r["fiscal_date"]) - _d(q["fiscal_date"])).days) <= EPS_STATEMENT_MATCH_DAYS), {})
        out.append(EpsQuarter(q["fiscal_date"], q["announce_date"][:10], value, first.get("eps_estimated"),
                              tuple(dict.fromkeys(tags))))
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
