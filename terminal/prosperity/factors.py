"""M6 factors: one input packet → scheme-independent raw values with a reason for every gap.

Formulas: docs/plans/2026-09-29-prosperity-m5-m6-scheme-and-kernel.md "公式规格"
(north star layer 2). Year-ago bases are paired by date (365 ± SAME_QUARTER_DAYS),
never by position; revenue is day-count adjusted to 91 days (D1). Percent values are ×100.
Pure functions: no reads, no writes.
"""
from __future__ import annotations

import statistics
from dataclasses import dataclass
from datetime import date
from typing import Dict, List, Mapping, Optional, Sequence, Tuple

from src.data.prosperity_history import SAME_QUARTER_DAYS, YEAR_DAYS
from terminal.prosperity.types import QuarterInputs

QUARTER_GAP = (60, 120)          # adjacent quarter: fiscal dates 60–120 days apart
QUARTER_DAYS = (60, 120)         # period_days a quarterly row may carry for day-count adjustment
HALF_YEAR_DAYS = (150, 200)      # semiannual reporters pair unadjusted
ADJUST_DAYS = 91
FCF_OUTLIER_PCT = 200.0          # original site patch: |FCF margin| or its change above 200 is noise

STATEMENT_KEYS = ("revenue_yoy", "revenue_accel", "gm_level", "gm_yoy", "fcf_margin_yoy", "net_margin_yoy",
                  "gm_slope", "revenue_ttm_leg")


@dataclass(frozen=True)
class FactorOut:
    values: Mapping[str, Optional[float]]
    missing: Mapping[str, str]            # only keys whose value is None
    labels: Tuple[str, ...] = ()


def _d(value: str) -> date:
    return date.fromisoformat(value[:10])


def _gap(newer: str, older: str) -> int:
    return (_d(newer) - _d(older)).days


def year_base(fiscals: Sequence[str], i: int) -> Optional[int]:
    """Index of the nearest earlier entry 365 ± SAME_QUARTER_DAYS before fiscals[i] (oldest first)."""
    for j in range(i - 1, -1, -1):
        gap = _gap(fiscals[i], fiscals[j])
        if abs(gap - YEAR_DAYS) <= SAME_QUARTER_DAYS:
            return j
        if gap > YEAR_DAYS + SAME_QUARTER_DAYS:
            return None
    return None


def prior_quarter(fiscals: Sequence[str], i: int) -> Optional[int]:
    """Index of the nearest earlier entry 60–120 days before fiscals[i] (oldest first)."""
    for j in range(i - 1, -1, -1):
        gap = _gap(fiscals[i], fiscals[j])
        if QUARTER_GAP[0] <= gap <= QUARTER_GAP[1]:
            return j
        if gap > QUARTER_GAP[1]:
            return None
    return None


def contiguous(fiscals: Sequence[str]) -> bool:
    return all(QUARTER_GAP[0] <= _gap(b, a) <= QUARTER_GAP[1] for a, b in zip(fiscals, fiscals[1:]))


def _within(days: Optional[int], bounds: Tuple[int, int]) -> bool:
    return days is not None and bounds[0] <= days <= bounds[1]


class _Statements:
    """Pairing and ratios over one packet's quarters (oldest first), shared by every statement factor."""

    def __init__(self, quarters: Sequence[QuarterInputs], day_adjust: bool, pairing: str):
        self.q = list(quarters)
        self.fiscals = [q.fiscal_date for q in self.q]
        self.day_adjust = day_adjust
        self.pairing = pairing
        self.labels: List[str] = []

    def base(self, i: int) -> Optional[int]:
        if self.pairing == "position":
            return i - 4 if i >= 4 else None
        return year_base(self.fiscals, i)

    def prior(self, i: int) -> Optional[int]:
        if self.pairing == "position":
            return i - 1 if i >= 1 else None
        return prior_quarter(self.fiscals, i)

    def revenue_yoy(self, i: int) -> Tuple[Optional[float], Optional[str]]:
        b = self.base(i)
        if b is None:
            return None, "no_yoy_base"
        cur, old = self.q[i], self.q[b]
        if cur.revenue is None or old.revenue is None:
            return None, "revenue_missing"
        rc, rb = cur.revenue, old.revenue
        if self.day_adjust:
            if _within(cur.period_days, QUARTER_DAYS) and _within(old.period_days, QUARTER_DAYS):
                rc, rb = rc * ADJUST_DAYS / cur.period_days, rb * ADJUST_DAYS / old.period_days
            elif _within(cur.period_days, HALF_YEAR_DAYS) and _within(old.period_days, HALF_YEAR_DAYS):
                self.labels.append("semiannual_period")
            else:
                return None, "period_days_out_of_range"
        if rb <= 0:
            return None, "nonpositive_base"
        return (rc / rb - 1) * 100, None

    def ratio(self, i: int, attr: str) -> Tuple[Optional[float], Optional[str]]:
        q = self.q[i]
        if q.revenue is None:
            return None, "revenue_missing"
        if q.revenue <= 0:
            return None, "nonpositive_revenue"
        value = getattr(q, attr)
        if value is None:
            return None, f"{attr}_missing"
        return value / q.revenue * 100, None

    def ratio_yoy(self, i: int, attr: str) -> Tuple[Optional[float], Optional[str]]:
        b = self.base(i)
        if b is None:
            return None, "no_yoy_base"
        cur, reason = self.ratio(i, attr)
        if reason:
            return None, reason
        old, reason = self.ratio(b, attr)
        if reason:
            return None, reason
        return cur - old, None

    def fcf_margin_yoy(self, i: int) -> Tuple[Optional[float], Optional[str]]:
        b = self.base(i)
        if b is None:
            return None, "no_yoy_base"
        margins = []
        for k in (i, b):
            m, reason = self.ratio(k, "free_cash_flow")
            if reason:
                return None, reason
            if self.q[k].free_cash_flow == 0 or self.q[k].capital_expenditure == 0:
                return None, "fcf_zero_placeholder"
            margins.append(m)
        diff = margins[0] - margins[1]
        if any(abs(v) > FCF_OUTLIER_PCT for v in margins + [diff]):
            return None, "fcf_outlier"
        return diff, None

    def gm_slope(self) -> Tuple[Optional[float], Optional[str]]:
        last = list(range(len(self.q)))[-8:]
        if len(last) < 8 or not contiguous([self.fiscals[k] for k in last]):
            return None, "slope_needs_8_quarters"
        gms = [self.ratio(k, "gross_profit")[0] for k in last]
        if any(v is None for v in gms):
            return None, "slope_needs_8_quarters"
        return statistics.linear_regression(range(8), gms).slope, None

    def revenue_ttm_leg(self) -> Tuple[Optional[float], Optional[str]]:
        last = self.q[-8:]
        if len(last) < 8 or not contiguous([q.fiscal_date for q in last]) or any(q.revenue is None for q in last):
            return None, "ttm_needs_8_quarters"
        revs = []
        for q in last:
            if self.day_adjust:
                if not _within(q.period_days, QUARTER_DAYS):
                    return None, "period_days_out_of_range"
                revs.append(q.revenue * ADJUST_DAYS / q.period_days)
            else:
                revs.append(q.revenue)
        before, recent = sum(revs[:4]), sum(revs[4:])
        if before <= 0:
            return None, "nonpositive_base"
        if recent <= 0:
            return None, "nonpositive_revenue"
        return (recent / before) ** 0.25 - 1, None


def _out(results: Mapping[str, Tuple[Optional[float], Optional[str]]], labels: Sequence[str] = (),
         extra: Optional[Mapping] = None) -> FactorOut:
    values = {k: v for k, (v, _) in results.items()}
    missing = {k: r for k, (v, r) in results.items() if v is None}
    if extra:
        values.update(extra)
    return FactorOut(values, missing, tuple(dict.fromkeys(labels)))


def statement_factors(quarters: Sequence[QuarterInputs], *, day_adjust: bool = True,
                      pairing: str = "date") -> FactorOut:
    """Three-statement factors at the current quarter (`quarters[-1]`).

    `day_adjust=False, pairing="position"` is the original site's basis (no day-count
    adjustment, base at [i−4]); only M7 uses it to separate basis from data differences.
    """
    if pairing not in ("date", "position"):
        raise ValueError(f"unknown pairing {pairing}")
    if not quarters:
        return FactorOut({k: None for k in STATEMENT_KEYS + ("fiscal_date",)},
                         {k: "no_statements" for k in STATEMENT_KEYS + ("fiscal_date",)})
    s = _Statements(quarters, day_adjust, pairing)
    c = len(quarters) - 1
    yoy = s.revenue_yoy(c)
    p = s.prior(c)
    if p is None:
        accel = (None, "no_prior_quarter")
    elif yoy[1]:
        accel = (None, yoy[1])
    else:
        prior_yoy = s.revenue_yoy(p)
        accel = (None, "prior_yoy_missing") if prior_yoy[1] else (yoy[0] - prior_yoy[0], None)
    results = {
        "revenue_yoy": yoy,
        "revenue_accel": accel,
        "gm_level": s.ratio(c, "gross_profit"),
        "gm_yoy": s.ratio_yoy(c, "gross_profit"),
        "fcf_margin_yoy": s.fcf_margin_yoy(c),
        "net_margin_yoy": s.ratio_yoy(c, "net_income"),
        "gm_slope": s.gm_slope(),
        "revenue_ttm_leg": s.revenue_ttm_leg(),
    }
    return _out(results, s.labels, {"fiscal_date": quarters[-1].fiscal_date})
