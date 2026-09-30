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

from src.data.prosperity_history import SAME_QUARTER_DAYS, YEAR_DAYS, sue_dependencies, sue_value
from terminal.prosperity.schemes import SCORING_FACTORS
from terminal.prosperity.types import EpsQuarter, InputPacket, QuarterInputs

QUARTER_GAP = (60, 120)          # adjacent quarter: fiscal dates 60–120 days apart
QUARTER_DAYS = (60, 120)         # period_days a quarterly row may carry for day-count adjustment
HALF_YEAR_DAYS = (150, 200)      # semiannual reporters pair unadjusted
ADJUST_DAYS = 91
FCF_OUTLIER_PCT = 200.0          # original site patch: |FCF margin| or its change above 200 is noise

# Any of these in a SUE dependency window blanks the whole factor; M4 blanks their values too
EPS_BLOCKING_LABELS = ("eps_conflicting_quarter", "eps_invalid_announcement_time", "eps_invalid_actual",
                       "eps_split_unconfirmed")
EPS_KEYS = ("eps_sue", "eps_accel", "eps_ttm_leg", "eps_yoy_pct")
AUX_KEYS = ("net_margin_yoy", "gm_slope", "ntm_eps", "ttm_eps", "ep_ntm", "pe_ntm", "pe_ttm", "ntm_growth",
            "eps_yoy_pct", "eps_ttm_leg", "revenue_ttm_leg", "beta", "price")
STATEMENT_KEYS = ("revenue_yoy", "revenue_accel", "gm_level", "gm_yoy", "fcf_margin_yoy", "net_margin_yoy",
                  "gm_slope", "revenue_ttm_leg")


@dataclass(frozen=True)
class FactorOut:
    values: Mapping[str, Optional[float]]
    missing: Mapping[str, str]            # only keys whose value is None
    labels: Tuple[str, ...] = ()


@dataclass(frozen=True)
class FactorRow:
    """One member on one as_of: raw scoring values and gate/display values, independent of any scheme."""
    symbol: str
    as_of: str
    current_fiscal: Optional[str]
    sector: Optional[str]
    industry: Optional[str]
    disclosed_quarters: int
    listing_days: Optional[int]           # raw age; each scheme applies its own new-listing threshold
    values: Mapping[str, Optional[float]]
    missing: Mapping[str, str]
    aux: Mapping[str, Optional[float]]
    labels: Tuple[str, ...]
    packet_flags: Tuple[str, ...]
    pit_basis: str


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


def eps_factors(eps: Sequence[EpsQuarter], empty_reason: str = "eps_behind_current") -> FactorOut:
    """Street EPS factors on M4's window (oldest first, aligned to the current fiscal quarter).

    A blocking label or blank value anywhere in a SUE dependency window blanks the factor;
    the quarter is never dropped to re-form σ. `eps_split_rescaled`, `eps_split_retrospective`
    and `gaap_split_basis_break` pass (D-9, Boss 2026-09-30); the retrospective one is surfaced.
    """
    if not eps:
        return FactorOut({k: None for k in EPS_KEYS}, {k: empty_reason for k in EPS_KEYS})
    newest = list(reversed(eps))
    heads = [q.period_end for q in newest]
    series = [(q.period_end, q.eps_actual) for q in newest]
    labels: List[str] = []

    def blocked(i: int) -> Optional[str]:
        for k in sorted(sue_dependencies(heads, i)):
            hit = next((lab for lab in newest[k].labels if lab in EPS_BLOCKING_LABELS), None)
            if hit:
                return hit
            if newest[k].eps_actual is None:
                return "eps_missing_value"
        return None

    def sue(i: int) -> Tuple[Optional[float], Optional[str]]:
        reason = blocked(i)
        return (None, reason) if reason else sue_value(series, i)

    sue0 = sue(0)
    if len(eps) < 2 or not QUARTER_GAP[0] <= _gap(eps[-1].period_end, eps[-2].period_end) <= QUARTER_GAP[1]:
        accel = (None, "no_prior_quarter")
    else:
        sue1 = sue(1)
        reason = sue0[1] or sue1[1]
        accel = (None, reason) if reason else (sue0[0] - sue1[0], None)

    last = eps[-8:]
    if len(last) < 8 or not contiguous([q.period_end for q in last]) or any(q.eps_actual is None for q in last):
        ttm_leg = (None, "ttm_needs_8_quarters")
    else:
        before, recent = sum(q.eps_actual for q in last[:4]), sum(q.eps_actual for q in last[4:])
        if recent <= 0:
            ttm_leg = (None, "eps_ttm_nonpositive")
        elif before <= 0:
            ttm_leg = (None, "eps_ttm_turnaround")
            labels.append("eps_ttm_turnaround")
        else:
            ttm_leg = ((recent / before) ** 0.25 - 1, None)

    b = year_base([q.period_end for q in eps], len(eps) - 1)
    cur = eps[-1].eps_actual
    if b is None:
        yoy = (None, "no_yoy_base")
    elif cur is None or eps[b].eps_actual is None:
        yoy = (None, "eps_missing_value")
    elif eps[b].eps_actual > 0:
        yoy = ((cur - eps[b].eps_actual) / eps[b].eps_actual * 100, None)
    elif cur > 0:
        yoy = (None, "eps_turnaround")
        labels.append("eps_turnaround")
    else:
        yoy = (None, "nonpositive_base")

    if any("eps_split_retrospective" in q.labels for q in eps):
        labels.append("eps_split_retrospective")
    return _out({"eps_sue": sue0, "eps_accel": accel, "eps_ttm_leg": ttm_leg, "eps_yoy_pct": yoy}, labels)


def expectation_factors(packet: InputPacket) -> FactorOut:
    """Surprise and revision as % of price, plus the NTM/TTM inputs for the gate and PE guard."""
    c = packet.consensus
    reasons = c.missing_reasons
    price = packet.price_asof if packet.price_asof is not None and packet.price_asof > 0 else None
    current = packet.eps[-1].eps_actual if packet.eps else None
    pre = c.pre_announce

    reason = (reasons.get("pre_announce") or pre.missing_reason or ("pre_announce_missing" if pre.value is None else None)
              or ("eps_missing_value" if current is None else None)
              or ("price_missing" if not pre.price_pre_announce or pre.price_pre_announce <= 0 else None))
    surprise = (None, reason) if reason else ((current - pre.value) / pre.price_pre_announce * 100, None)

    reason = (reasons.get("revision") or c.revision.missing_reason
              or ("revision_missing" if c.revision.delta_eps is None else None) or ("price_missing" if price is None else None))
    # Scaled to 4 quarters: sum × 4 ÷ fixed quarters (2–4), Boss 2026-09-30 ①
    revision = (None, reason) if reason else (c.revision.delta_eps * 4 / len(c.revision.quarters) / price * 100, None)

    reason = reasons.get("ntm") or c.ntm.missing_reason or ("ntm_missing" if c.ntm.value is None else None)
    ntm = (None, reason) if reason else (c.ntm.value, None)
    reason = reasons.get("ttm") or ("ttm_incomplete" if c.ttm_eps is None else None)
    ttm = (None, reason) if reason else (c.ttm_eps, None)

    def derived(num, den, positive):
        """num / den from two (value, reason) pairs; `positive` names the reason when den ≤ 0."""
        if num[1] or den[1]:
            return None, num[1] or den[1]
        return (None, positive) if den[0] <= 0 else (num[0] / den[0], None)

    px = (price, None if price is not None else "price_missing")
    ratio = derived(ntm, ttm, "nonpositive_ttm")
    return _out({"surprise": surprise, "revision": revision, "ntm_eps": ntm, "ttm_eps": ttm,
                 "ep_ntm": derived(ntm, px, "price_missing"),
                 "pe_ntm": derived(px, ntm, "nonpositive_ntm"),
                 "pe_ttm": derived(px, ttm, "nonpositive_ttm"),
                 "ntm_growth": ratio if ratio[1] else (ratio[0] - 1, None)})


def compute_factor_row(packet: InputPacket) -> FactorRow:
    # M4 leaves the EPS window empty both when EPS lags the statements and when there is no current quarter
    empty = "eps_behind_current" if packet.current_fiscal else "no_current_fiscal"
    parts = (statement_factors(packet.quarters), eps_factors(packet.eps, empty), expectation_factors(packet))
    values: Dict[str, Optional[float]] = {}
    missing: Dict[str, str] = {}
    for part in parts:
        values.update(part.values)
        missing.update(part.missing)
    # D-1 B: mean of the revenue and EPS TTM legs, ×100; either leg missing blanks the factor
    leg = next((k for k in ("revenue_ttm_leg", "eps_ttm_leg") if values[k] is None), None)
    if leg:
        values["growth_4q"], missing["growth_4q"] = None, missing[leg]
    else:
        values["growth_4q"] = (values["revenue_ttm_leg"] + values["eps_ttm_leg"]) / 2 * 100
    values.update(beta=packet.beta, price=packet.price_asof)
    labels = [lab for part in parts for lab in part.labels]
    listing_days = None
    if packet.listing_date:
        listing_days = (_d(packet.as_of) - _d(packet.listing_date)).days
    else:
        labels.append("listing_date_unknown")
    scoring = {f: values[f] for f in SCORING_FACTORS}
    return FactorRow(
        symbol=packet.symbol, as_of=packet.as_of, current_fiscal=packet.current_fiscal,
        sector=packet.sector, industry=packet.industry, disclosed_quarters=len(packet.quarters),
        listing_days=listing_days, values=scoring,
        missing={f: missing[f] for f, v in scoring.items() if v is None},
        aux={k: values[k] for k in AUX_KEYS}, labels=tuple(dict.fromkeys(labels)),
        packet_flags=tuple(packet.flags), pit_basis=packet.pit_basis)
