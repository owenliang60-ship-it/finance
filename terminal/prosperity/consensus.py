"""Consensus inputs: unit check, pre-announcement consensus, NTM, TTM, revisions.

Pure functions over already-loaded rows; the caller slices nothing, every
function applies its own as_of / announce-date bound.
"""
from __future__ import annotations

from datetime import date, timedelta
from typing import Dict, List, Mapping, Optional, Sequence, Tuple

from src.data.fundamental_value_checks import check_consensus_jump
from terminal.prosperity.config import (ESTIMATE_MATCH_DAYS, FIRST_WEEKLY_SNAPSHOT, NTM_MIN_ANALYSTS,
                                        PRE_ANNOUNCE_MAX_STALENESS_DAYS, PRICE_MAX_STALENESS_DAYS,
                                        REVISION_LONG_WEEKS, REVISION_LONG_WINDOW_FROM, REVISION_SHORT_WEEKS,
                                        SNAPSHOT_MAX_STALENESS_DAYS)
from terminal.prosperity.types import (ConsensusInputs, EpsQuarter, NtmResult, PreAnnouncement,
                                       RevisionResult)

QUARTER_GAP = (60, 120)       # adjacent fiscal quarters (days)
QUARTER_DAYS = 91
FY_STEP_DAYS, FY_STEP_TOLERANCE = 365, 20


def _d(value: str) -> date:
    return date.fromisoformat(value[:10])


def unit_verified(reported_currency: Optional[str], is_adr: Optional[bool]) -> bool:
    """Per-share USD inputs are comparable only for USD reporters with a 1:1 listing."""
    return reported_currency == "USD" and is_adr is False


def _close_before(closes: Sequence[Tuple[str, float]], bound: str, max_age: int) -> Optional[float]:
    """Last close strictly before `bound`, None if older than `max_age` days."""
    prior = [(d, p) for d, p in closes if d[:10] < bound[:10]]
    if not prior:
        return None
    day, price = max(prior)
    return price if (_d(bound) - _d(day)).days <= max_age else None


def pre_announcement_consensus(quarter: EpsQuarter, estimates: Sequence[Mapping],
                               closes: Sequence[Tuple[str, float]]) -> PreAnnouncement:
    announce = quarter.announce_date[:10]
    price = _close_before(closes, announce, PRICE_MAX_STALENESS_DAYS)
    if announce <= FIRST_WEEKLY_SNAPSHOT:
        value = quarter.eps_estimated
        return PreAnnouncement(value, "vendor_estimate" if value is not None else None, None, price,
                               None if value is not None else "no_vendor_estimate")
    rows = [r for r in estimates
            if r.get("snapshot_kind") == "weekly" and r.get("period_type") == "Q"
            and r["snapshot_date"][:10] < announce and r.get("fiscal_date")
            and abs((_d(r["fiscal_date"]) - _d(quarter.fiscal_date)).days) <= ESTIMATE_MATCH_DAYS]
    if not rows:
        return PreAnnouncement(None, None, None, price, "no_pre_announce_snapshot")
    best = max(rows, key=lambda r: r["snapshot_date"])
    snap = best["snapshot_date"][:10]
    if (_d(announce) - _d(snap)).days > PRE_ANNOUNCE_MAX_STALENESS_DAYS:
        return PreAnnouncement(None, None, snap, price, "pre_announce_snapshot_stale")
    return PreAnnouncement(best.get("eps_avg"), "local_snapshot", snap, price,
                           None if best.get("eps_avg") is not None else "no_pre_announce_value")


def _contiguous(fiscals: Sequence[str]) -> bool:
    lo, hi = QUARTER_GAP
    return all(lo <= (_d(b) - _d(a)).days <= hi for a, b in zip(fiscals, fiscals[1:]))


def _announced_by(announced: Sequence[EpsQuarter], as_of: str) -> List[EpsQuarter]:
    return sorted((q for q in announced if q.announce_date[:10] <= as_of[:10]), key=lambda q: q.fiscal_date)


def ttm_eps(announced: Sequence[EpsQuarter]) -> Tuple[Optional[float], Tuple[str, ...]]:
    """Sum of the latest 4 announced street EPS quarters; contiguous and all present."""
    last4 = sorted(announced, key=lambda q: q.fiscal_date)[-4:]
    fiscals = tuple(q.fiscal_date[:10] for q in last4)
    if len(last4) < 4 or not _contiguous(fiscals) or any(q.eps_actual is None for q in last4):
        return None, ()
    return sum(q.eps_actual for q in last4), fiscals


def _weekly(estimates: Sequence[Mapping]) -> Dict[str, List[Mapping]]:
    by_snap: Dict[str, List[Mapping]] = {}
    for r in estimates:
        if r.get("snapshot_kind") == "weekly" and r.get("fiscal_date") and r.get("snapshot_date"):
            by_snap.setdefault(r["snapshot_date"][:10], []).append(r)
    return by_snap


def _current_snapshot(by_snap: Dict[str, List[Mapping]], as_of: str) -> Tuple[Optional[str], Optional[str]]:
    """(snapshot_date, missing_reason) of the newest weekly snapshot ≤ as_of."""
    snaps = [s for s in by_snap if s <= as_of[:10]]
    if not snaps:
        return None, "no_current_snapshot"
    snap = max(snaps)
    if (_d(as_of) - _d(snap)).days > SNAPSHOT_MAX_STALENESS_DAYS:
        return snap, "snapshot_stale"
    return snap, None


def _match(rows: Sequence[Mapping], fiscal: str, period: str = "Q") -> Optional[Mapping]:
    near = [r for r in rows if r.get("period_type") == period
            and abs((_d(r["fiscal_date"]) - _d(fiscal)).days) <= ESTIMATE_MATCH_DAYS]
    return min(near, key=lambda r: abs((_d(r["fiscal_date"]) - _d(fiscal)).days)) if near else None


def _values(rows: Sequence[Mapping], fiscals: Sequence[str], period: str = "Q") -> Dict[str, float]:
    out = {}
    for f in fiscals:
        r = _match(rows, f, period)
        if r is not None and r.get("eps_avg") is not None:
            out[f] = r["eps_avg"]
    return out


def _last_seen(by_snap: Dict[str, List[Mapping]], snap: str, fiscals: Sequence[str], period: str = "Q") -> Dict[str, float]:
    """Each fiscal's value in the newest earlier snapshot that has it (a missing week cannot hide a jump)."""
    out: Dict[str, float] = {}
    for s in sorted((s for s in by_snap if s < snap), reverse=True):
        for f, v in _values(by_snap[s], [f for f in fiscals if f not in out], period).items():
            out[f] = v
        if len(out) == len(fiscals):
            break
    return out


def _quarter_sum(rows: Sequence[Mapping], anchor: str, announced: Sequence[EpsQuarter]):
    announced_days = [_d(q.fiscal_date) for q in announced]
    cands = sorted((r for r in rows if r.get("period_type") == "Q"
                    and (_d(r["fiscal_date"]) - _d(anchor)).days > ESTIMATE_MATCH_DAYS
                    and all(abs((_d(r["fiscal_date"]) - a).days) > ESTIMATE_MATCH_DAYS for a in announced_days)),
                   key=lambda r: r["fiscal_date"])[:4]
    fiscals = [r["fiscal_date"][:10] for r in cands]
    if len(cands) < 4 or not _contiguous([anchor[:10]] + fiscals) or any(r.get("eps_avg") is None for r in cands):
        return None, "ntm_window_incomplete"
    if any((r.get("num_analysts_eps") or 0) < NTM_MIN_ANALYSTS for r in cands[2:]):
        return None, "ntm_thin_coverage"
    return cands, None


def _fy_blend(rows: Sequence[Mapping], anchor: str):
    a = _d(anchor)
    fy = sorted((r for r in rows if r.get("period_type") == "FY" and r.get("eps_avg") is not None),
                key=lambda r: r["fiscal_date"])
    fy1 = next((r for r in fy if _d(r["fiscal_date"]) >= a + timedelta(days=QUARTER_DAYS - ESTIMATE_MATCH_DAYS)), None)
    # FY1 must be the fiscal year in progress: it ends within a year of the anchor.
    if fy1 is None or _d(fy1["fiscal_date"]) > a + timedelta(days=FY_STEP_DAYS + FY_STEP_TOLERANCE):
        return None
    fy2 = next((r for r in fy if abs((_d(r["fiscal_date"]) - _d(fy1["fiscal_date"])).days - FY_STEP_DAYS)
                <= FY_STEP_TOLERANCE), None)
    if fy2 is None:
        return None
    end1 = _d(fy1["fiscal_date"]) + timedelta(days=ESTIMATE_MATCH_DAYS)
    w = sum(a + timedelta(days=QUARTER_DAYS * k) <= end1 for k in range(1, 5)) / 4
    return fy1, fy2, w


def ntm_eps(estimates: Sequence[Mapping], announced: Sequence[EpsQuarter], as_of: str) -> NtmResult:
    """North-star NTM: the 4 unannounced quarters after the last announced one."""
    known = _announced_by(announced, as_of)
    if not known:
        return NtmResult(None, None, (), None, "no_ttm_anchor")
    anchor = known[-1].fiscal_date[:10]
    by_snap = _weekly(estimates)
    snap, reason = _current_snapshot(by_snap, as_of)
    if reason:
        return NtmResult(None, None, (), snap, reason)
    rows = by_snap[snap]
    cands, reason = _quarter_sum(rows, anchor, known)
    if cands:
        quarters = tuple((r["fiscal_date"][:10], r["eps_avg"], r.get("num_analysts_eps")) for r in cands)
        fiscals, period = [q[0] for q in quarters], "Q"
        result = NtmResult(sum(q[1] for q in quarters), "quarter_sum", quarters, snap, None)
    else:
        blend = _fy_blend(rows, anchor)
        if blend is None:
            return NtmResult(None, None, (), snap, reason)
        fy1, fy2, w = blend
        quarters = tuple((r["fiscal_date"][:10], r["eps_avg"], r.get("num_analysts_eps")) for r in (fy1, fy2))
        fiscals, period = [q[0] for q in quarters], "FY"
        result = NtmResult(w * fy1["eps_avg"] + (1 - w) * fy2["eps_avg"], "ntm_fy_blend", quarters, snap, None)
    if check_consensus_jump(_last_seen(by_snap, snap, fiscals, period), _values(rows, fiscals, period)):
        return NtmResult(None, result.basis, result.quarters, snap, "e2_consensus_jump")
    return result


def revision_inputs(estimates: Sequence[Mapping], announced: Sequence[EpsQuarter], as_of: str) -> RevisionResult:
    """Consensus change on a fixed quarter set taken from the base snapshot."""
    weeks = REVISION_LONG_WEEKS if as_of[:10] >= REVISION_LONG_WINDOW_FROM else REVISION_SHORT_WEEKS
    by_snap = _weekly(estimates)
    snap, reason = _current_snapshot(by_snap, as_of)
    if reason:
        return RevisionResult(None, weeks, (), None, snap, reason)
    target = _d(as_of) - timedelta(days=7 * weeks)
    bases = [s for s in by_snap if s <= target.isoformat()]
    base = max(bases) if bases else None
    if base is None or (target - _d(base)).days > 7:
        return RevisionResult(None, weeks, (), None, snap, "no_base_snapshot")
    known = _announced_by(announced, as_of)
    if not known:
        return RevisionResult(None, weeks, (), base, snap, "no_announced_quarter")
    floor = _d(known[-1].fiscal_date) + timedelta(days=ESTIMATE_MATCH_DAYS)
    fixed = [r["fiscal_date"][:10] for r in sorted(by_snap[base], key=lambda r: r["fiscal_date"])
             if r.get("period_type") == "Q" and _d(r["fiscal_date"]) > floor and r.get("eps_avg") is not None][:4]
    fixed = [f for f in fixed if f in _values(by_snap[snap], [f])]
    if len(fixed) < 2:
        return RevisionResult(None, weeks, tuple(fixed), base, snap, "revision_set_too_small")
    seen = _values(by_snap[base], fixed)
    for s in sorted(x for x in by_snap if base < x <= snap):
        now = _values(by_snap[s], fixed)
        if check_consensus_jump(seen, now):
            return RevisionResult(None, weeks, tuple(fixed), base, snap, "e2_consensus_jump")
        seen.update(now)
    delta = sum(_values(by_snap[snap], fixed).values()) - sum(_values(by_snap[base], fixed).values())
    return RevisionResult(delta, weeks, tuple(fixed), base, snap, None)


def build_consensus(*, estimates: Sequence[Mapping], announced: Sequence[EpsQuarter],
                    current_eps: Optional[EpsQuarter], closes: Sequence[Tuple[str, float]],
                    as_of: str, unit_ok: bool) -> ConsensusInputs:
    pre = (pre_announcement_consensus(current_eps, estimates, closes) if current_eps is not None
           else PreAnnouncement(None, None, None, None, "no_current_eps"))
    ntm = ntm_eps(estimates, announced, as_of)
    ttm, ttm_q = ttm_eps(_announced_by(announced, as_of))
    rev = revision_inputs(estimates, announced, as_of)
    if not unit_ok:
        return ConsensusInputs(
            PreAnnouncement(None, pre.source, pre.snapshot_date, pre.price_pre_announce, "unit_unverified"),
            NtmResult(None, ntm.basis, ntm.quarters, ntm.snapshot_date, "unit_unverified"), None, ttm_q,
            RevisionResult(None, rev.window_weeks, rev.quarters, rev.base_snapshot, rev.current_snapshot,
                           "unit_unverified"),
            None, {k: "unit_unverified" for k in ("pre_announce", "ntm", "ttm", "revision")})
    reasons = {k: v for k, v in (("pre_announce", pre.missing_reason), ("ntm", ntm.missing_reason),
                                 ("ttm", None if ttm is not None else "ttm_incomplete"),
                                 ("revision", rev.missing_reason)) if v}
    return ConsensusInputs(pre, ntm, ttm, ttm_q, rev, 1.0, reasons)
