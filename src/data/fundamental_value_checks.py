"""Pure quarterly statement value checks for the prosperity engine (M3).

Input rows are merged quarters (income + balance + cash flow keyed by the
income ``date``). Every rule returns stable issue codes; hard issues name the
fields the reader must null, soft issues are badges only. Nothing here reads
or writes a database, and inputs are never mutated.

Q10 deliberately widens the research threshold [800, 1200] to [500, 2000]:
YPF's mis-scaled 2025 quarters entered at 1/1052 and re-entered at 1428
(real growth on top of the ×1000 unit jump), so the original band caught
only half of the run.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any, Dict, List, Mapping, Sequence, Tuple

FLOW_FIELDS: Tuple[str, ...] = ("revenue", "cost_of_revenue", "gross_profit", "net_income",
                                "operating_cash_flow", "capital_expenditure", "free_cash_flow")

Q8_TOLERANCE = 0.01
Q10_UNIT_BAND = (500.0, 2000.0)
Q11_REVENUE_RATIO = 3.0
Q11_GROSS_MARGIN_DELTA = 0.15
Q11_COST_RATIO, Q11_COST_REVENUE_RATIO = 2.0, 1.3
Q14_NONSTANDARD = ((0, 85), (97, 99), (110, 114))   # period_days ranges (inclusive)
Q14_LONG_GAP_DAYS = 150
Q17_GOODWILL_SHARE, Q17_ASSET_RATIO = 0.10, 1.3
E2_MIN_ABS_EPS, E2_RATIO = 0.05, 1.8


@dataclass(frozen=True)
class ValueIssue:
    code: str
    severity: str  # "hard" | "soft"
    fiscal_date: str
    null_fields: Tuple[str, ...] = ()
    detail: Mapping[str, Any] = field(default_factory=dict)


def _num(value):
    return value if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def _unit_step(prev_rev, rev) -> int:
    """+1 / −1 when revenue jumps by a ×1000-like unit factor, else 0."""
    if prev_rev is None or rev is None or prev_rev <= 0 or rev <= 0:
        return 0
    ratio = rev / prev_rev
    lo, hi = Q10_UNIT_BAND
    if lo <= ratio <= hi:
        return 1
    if 1 / hi <= ratio <= 1 / lo:
        return -1
    return 0


def _hard_checks(rows: List[Mapping[str, Any]]) -> List[ValueIssue]:
    issues: List[ValueIssue] = []
    for r in rows:
        d = r["date"][:10]
        rev, cor, gp = _num(r.get("revenue")), _num(r.get("cost_of_revenue")), _num(r.get("gross_profit"))
        if rev is not None and cor is not None and gp is not None \
                and abs(rev - cor - gp) > Q8_TOLERANCE * abs(rev):
            issues.append(ValueIssue("q8_gross_profit_identity", "hard", d, ("gross_profit", "cost_of_revenue"),
                                     {"revenue": rev, "cost_of_revenue": cor, "gross_profit": gp}))
        if rev is not None and rev < 0:
            issues.append(ValueIssue("q9_negative_revenue", "hard", d, ("revenue", "gross_profit"), {"revenue": rev}))
        if rev is not None and rev > 0 and gp is not None and gp > rev:
            issues.append(ValueIssue("q9_gross_profit_exceeds_revenue", "hard", d, ("gross_profit",),
                                     {"revenue": rev, "gross_profit": gp}))
        if cor is not None and cor < 0:
            issues.append(ValueIssue("q9_negative_cost_of_revenue", "hard", d, ("cost_of_revenue", "gross_profit"),
                                     {"cost_of_revenue": cor}))
    # Q10: give each run of quarters a unit index; the newest quarter's run is the base.
    index, ratios = [0], [None]
    for prev, cur in zip(rows, rows[1:]):
        p, c = _num(prev.get("revenue")), _num(cur.get("revenue"))
        index.append(index[-1] + _unit_step(p, c))
        ratios.append(c / p if p and c and p > 0 and c > 0 else None)
    base = index[-1] if rows else 0
    for r, idx, ratio in zip(rows, index, ratios):
        if idx != base:
            issues.append(ValueIssue("q10_unit_scale", "hard", r["date"][:10], FLOW_FIELDS,
                                     {"unit_index": idx - base, "ratio_to_previous": ratio}))
    # Q13: older quarters reported in another currency than the newest one.
    latest = rows[-1].get("reported_currency") if rows else None
    if latest:
        for r in rows[:-1]:
            if r.get("reported_currency") != latest:
                issues.append(ValueIssue("q13_currency_change", "hard", r["date"][:10], FLOW_FIELDS,
                                         {"from": r.get("reported_currency"), "to": latest}))
    return issues


def _soft_checks(rows: List[Mapping[str, Any]]) -> List[ValueIssue]:
    issues: List[ValueIssue] = []
    for prev, cur in zip(rows, rows[1:]):
        d = cur["date"][:10]
        days = (date.fromisoformat(d) - date.fromisoformat(prev["date"][:10])).days
        if any(lo <= days <= hi for lo, hi in Q14_NONSTANDARD):
            issues.append(ValueIssue("q14_nonstandard_quarter", "soft", d, detail={"period_days": days}))
        elif days >= Q14_LONG_GAP_DAYS:
            issues.append(ValueIssue("q14_long_gap", "soft", d, detail={"period_days": days}))
        p_rev, c_rev = _num(prev.get("revenue")), _num(cur.get("revenue"))
        if p_rev and c_rev and p_rev > 0 and c_rev > 0:
            ratio = c_rev / p_rev
            if (ratio > Q11_REVENUE_RATIO or ratio < 1 / Q11_REVENUE_RATIO) and not _unit_step(p_rev, c_rev):
                issues.append(ValueIssue("q11_revenue_jump", "soft", d, detail={"ratio": ratio}))
            p_gp, c_gp = _num(prev.get("gross_profit")), _num(cur.get("gross_profit"))
            if p_gp is not None and c_gp is not None \
                    and abs(c_gp / c_rev - p_gp / p_rev) > Q11_GROSS_MARGIN_DELTA:
                issues.append(ValueIssue("q11_gross_margin_jump", "soft", d,
                                         detail={"from": p_gp / p_rev, "to": c_gp / c_rev}))
            p_cor, c_cor = _num(prev.get("cost_of_revenue")), _num(cur.get("cost_of_revenue"))
            if p_cor and c_cor and p_cor > 0 and c_cor / p_cor > Q11_COST_RATIO and ratio < Q11_COST_REVENUE_RATIO:
                issues.append(ValueIssue("q11_cost_jump", "soft", d,
                                         detail={"cost_ratio": c_cor / p_cor, "revenue_ratio": ratio}))
        p_gw, c_gw = _num(prev.get("goodwill_and_intangible_assets")), _num(cur.get("goodwill_and_intangible_assets"))
        p_ta, c_ta = _num(prev.get("total_assets")), _num(cur.get("total_assets"))
        if p_ta and p_ta > 0 and ((p_gw is not None and c_gw is not None and c_gw - p_gw > Q17_GOODWILL_SHARE * p_ta)
                                  or (c_ta is not None and c_ta / p_ta > Q17_ASSET_RATIO)):
            issues.append(ValueIssue("q17_acquisition_suspect", "soft", d,
                                     detail={"goodwill_delta": None if p_gw is None or c_gw is None else c_gw - p_gw,
                                             "total_assets_ratio": None if c_ta is None else c_ta / p_ta}))
    return issues


def check_quarter_values(quarters: Sequence[Mapping[str, Any]]) -> List[ValueIssue]:
    rows = sorted(quarters, key=lambda r: r["date"][:10])
    return _hard_checks(rows) + _soft_checks(rows)


def check_consensus_jump(base: Mapping[str, float], current: Mapping[str, float]) -> List[ValueIssue]:
    """E2: week-over-week consensus jump per matched fiscal date (caller nulls the field)."""
    issues = []
    for fiscal in sorted(set(base) & set(current)):
        b, c = _num(base[fiscal]), _num(current[fiscal])
        if b is None or c is None or max(abs(b), abs(c)) < E2_MIN_ABS_EPS:
            continue
        if b * c < 0 or b == 0 or c == 0 or not (1 / E2_RATIO <= c / b <= E2_RATIO):
            issues.append(ValueIssue("e2_consensus_jump", "hard", fiscal, detail={"base": b, "current": c}))
    return issues


def apply_hard_issues(quarters: Sequence[Mapping[str, Any]], issues: Sequence[ValueIssue]) -> List[Dict[str, Any]]:
    by_date: Dict[str, List[ValueIssue]] = {}
    for issue in issues:
        by_date.setdefault(issue.fiscal_date, []).append(issue)
    out = []
    for q in quarters:
        row = dict(q)
        nulled = list(row.get("_nulled", ()))
        labels = list(row.get("_labels", ()))
        for issue in by_date.get(row["date"][:10], ()):
            if issue.severity == "hard":
                for f in issue.null_fields:
                    row[f] = None
                    tag = f + ":" + issue.code
                    if tag not in nulled:
                        nulled.append(tag)
            elif issue.code not in labels:
                labels.append(issue.code)
        row["_nulled"] = tuple(nulled)
        row["_labels"] = tuple(labels)
        out.append(row)
    return out
