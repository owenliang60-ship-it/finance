"""Consensus inputs: unit check, pre-announcement consensus, NTM, TTM, revisions.

Pure functions over already-loaded rows; the caller slices nothing, every
function applies its own as_of / announce-date bound.
"""
from __future__ import annotations

import math
import statistics
from datetime import date, datetime, timedelta, timezone
from typing import Dict, List, Mapping, Optional, Sequence, Tuple

from src.data.fundamental_value_checks import check_consensus_jump
from src.data.prosperity_quality import split_ratio_eligible
from terminal.prosperity.config import (ESTIMATE_MATCH_DAYS, ESTIMATE_SPLIT_VERIFY_DAYS, FIRST_WEEKLY_SNAPSHOT, FORWARD_STALE_DAYS, NTM_MIN_ANALYSTS,
                                        PRE_ANNOUNCE_MAX_STALENESS_DAYS, PRICE_MAX_STALENESS_DAYS,
                                        REVISION_LONG_WEEKS, REVISION_LONG_WINDOW_FROM, REVISION_SHORT_WEEKS,
                                        SNAPSHOT_MAX_STALENESS_DAYS)
from terminal.prosperity.types import (ConsensusInputs, EpsQuarter, NtmResult, PreAnnouncement,
                                       RevisionResult)

QUARTER_GAP = (60, 120)       # adjacent fiscal quarters (days)
SPLIT_TOLERANCE = math.log(1.25)   # same match band as the price and street EPS split checks
MIXED_BASIS_MIN_RATIO = 1.5        # below this a split ratio is indistinguishable from a normal surprise
QUARTER_DAYS = 91
FY_STEP_DAYS, FY_STEP_TOLERANCE = 365, 20


def _d(value: str) -> date:
    return date.fromisoformat(value[:10])


def _instant(value) -> datetime:
    """Compare aware instants, never ISO strings (offset spellings can differ)."""
    moment = value if isinstance(value, datetime) else datetime.fromisoformat(value.replace('Z', '+00:00'))
    if moment.tzinfo is None or moment.utcoffset() is None:
        raise ValueError('forward cutoff/completed_at requires a timezone')
    return moment.astimezone(timezone.utc)


def _finite(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _clean(row: Mapping) -> Mapping:
    """A non-finite consensus value is a missing one (review fix 2)."""
    v = row.get("eps_avg")
    return row if v is None or _finite(v) else dict(row, eps_avg=None)


def _eligible_estimates(estimates: Sequence[Mapping], cutoff) -> List[Mapping]:
    bound = _instant(cutoff)
    eligible = []
    for row in estimates:
        if (row.get('snapshot_kind') != 'weekly' or row.get('forward_run_kind') != 'weekly'
                or row.get('forward_run_status') != 'complete'):
            continue
        try:
            complete = _instant(row.get('forward_completed_at'))
        except (AttributeError, TypeError, ValueError):
            continue
        if complete <= bound:
            eligible.append(_clean(row))
    return eligible


def unit_verified(reported_currency: Optional[str], is_adr: Optional[bool]) -> bool:
    """Per-share USD inputs are comparable only for USD reporters with a 1:1 listing."""
    return reported_currency == "USD" and is_adr is False


def _close_before(closes: Sequence[Tuple[str, float]], bound: str, max_age: int) -> Optional[float]:
    """Last close strictly before `bound`, None if older than `max_age` days."""
    prior = [(d, p) for d, p in closes if d[:10] < bound[:10] and _finite(p)]
    if not prior:
        return None
    day, price = max(prior)
    return price if (_d(bound) - _d(day)).days <= max_age else None


def _split_tolerance(log_r: float) -> float:
    return min(SPLIT_TOLERANCE, 0.25 * abs(log_r))


def split_events(splits: Sequence[Mapping], last_date: Optional[str]) -> Tuple[Tuple[str, float], ...]:
    """(date, ratio) of eligible splits on or before the last stored close, date-sorted.

    Same retrospective basis as the price rescale: a split after the last close
    (SITM 2026-12-07 on 2026-11-24) has not moved any stored series yet.
    """
    if not last_date:
        return ()
    out = []
    for s in splits:
        num, den, day = s.get("numerator"), s.get("denominator"), (s.get("date") or "")[:10]
        if day and day <= last_date[:10] and split_ratio_eligible(num, den):
            out.append((day, float(num) / float(den)))
    return tuple(sorted(out))


def _paired_ratios(ref_rows: Sequence[Mapping], new_rows: Sequence[Mapping], ref_div: float,
                   new_div: float) -> List[float]:
    """ref / new consensus over the same quarters (±ESTIMATE_MATCH_DAYS absorbs FMP's -03 → -02 drift)."""
    ratios = []
    for r in new_rows:
        new_val = r.get("eps_avg")
        if not r.get("fiscal_date") or not isinstance(new_val, (int, float)) or not new_val:
            continue
        ref = _match([x for x in ref_rows if x.get("fiscal_date")], r["fiscal_date"], r.get("period_type"))
        ref_val = ref.get("eps_avg") if ref is not None else None
        if isinstance(ref_val, (int, float)) and ref_val and ref_val * new_val > 0:
            ratios.append((ref_val / ref_div) / (new_val / new_div))
    return ratios


def on_latest_basis(estimates: Sequence[Mapping], events: Sequence[Tuple[str, float]]
                    ) -> Tuple[List[dict], Dict[str, float], Tuple[str, ...]]:
    """Put every weekly snapshot on the post-split unit of the latest stored split (P0-1).

    Snapshots before a split are divided by its ratio. A later snapshot is compared with the
    newest pre-split snapshot over the same quarters: a median ratio near the split ratio means
    FMP already switched (kept, and every later snapshot is kept too); near 1 means still on the
    old unit (divided); anything else is an unknown unit (dropped). Without a usable comparison a
    snapshot within ESTIMATE_SPLIT_VERIFY_DAYS of the split is dropped, a later one is accepted.
    Returns (adjusted copies, divisor per kept snapshot, dropped snapshot dates).
    """
    if not events or not estimates:
        return [dict(r) for r in estimates], {}, ()
    by_snap: Dict[str, List[Mapping]] = {}
    for r in estimates:
        by_snap.setdefault(r["snapshot_date"][:10], []).append(r)
    snaps = sorted(by_snap)
    divisor = {s: 1.0 for s in snaps}
    dropped = set()
    for s_day, r in events:
        log_r = math.log(r)
        tol = _split_tolerance(log_r)
        before = [s for s in snaps if s < s_day and s not in dropped]
        ref_snap = max(before) if before else None
        start = dict(divisor)          # units as left by earlier splits, so a second split compares like with like
        switched = False
        for s in snaps:
            if s in dropped:
                continue
            if s < s_day:
                divisor[s] *= r
                continue
            if switched:
                continue
            in_window = (_d(s) - _d(s_day)).days <= ESTIMATE_SPLIT_VERIFY_DAYS
            ratios = (_paired_ratios(by_snap[ref_snap], by_snap[s], start[ref_snap], start[s])
                      if ref_snap is not None else [])
            if not ratios:
                if in_window:
                    dropped.add(s)
                else:
                    switched = True
                continue
            med = math.log(statistics.median(ratios))
            if abs(med - log_r) <= tol:
                switched = True
            elif abs(med) <= tol:
                divisor[s] *= r
            else:
                dropped.add(s)
    out = []
    for row in estimates:
        s = row["snapshot_date"][:10]
        if s in dropped:
            continue
        copy = dict(row)
        if copy.get("eps_avg") is not None:
            copy["eps_avg"] = copy["eps_avg"] / divisor[s]
        out.append(copy)
    return out, {s: d for s, d in divisor.items() if s not in dropped}, tuple(sorted(dropped))


def _mixed_basis(quarter: EpsQuarter, pre: PreAnnouncement, events: Sequence[Tuple[str, float]]) -> bool:
    """True when actual / pre-announcement value sits at a split ratio the value may not share."""
    val, actual = pre.value, quarter.eps_actual
    if pre.missing_reason is not None or not (_finite(val) and _finite(actual)) or val == 0 or actual * val <= 0:
        return False
    ref_day = pre.snapshot_date if pre.source == "local_snapshot" else quarter.announce_date[:10]
    gap = math.log(actual / val)
    rescaled = "eps_split_rescaled" in quarter.labels
    for s_day, r in events:
        # review fix 3: stock dividends and small adjustments (BBD 1.1, QGEN 0.95) sit inside normal beats
        if ref_day > s_day or max(r, 1 / r) < MIXED_BASIS_MIN_RATIO:
            continue
        log_r = math.log(r)
        if pre.source == "local_snapshot":
            # the snapshot is already on the latest unit; only an unrestated actual can disagree
            miss = abs(gap - log_r)
        elif rescaled:
            # street_eps divided actual and estimate by the ratio; an estimate that was already
            # adjusted is now divided twice (APH 2024-12-31), so both sides are suspect
            miss = min(abs(gap - log_r), abs(gap + log_r))
        else:
            # FMP keeps the unadjusted vendor estimate next to a restated actual: actual / est ≈ 1 / r.
            # The other side is a real miss or beat (MSTR 2024Q2, VMRK 2025Q3)
            miss = abs(gap + log_r)
        if miss <= _split_tolerance(log_r):
            return True
    return False


def pre_announcement_consensus(quarter: EpsQuarter, estimates: Sequence[Mapping],
                               closes: Sequence[Tuple[str, float]], *, cutoff,
                               events: Sequence[Tuple[str, float]] = ()) -> PreAnnouncement:
    pre = _pre_announcement(quarter, estimates, closes, cutoff=cutoff)
    if _mixed_basis(quarter, pre, events):
        return PreAnnouncement(None, pre.source, pre.snapshot_date, pre.price_pre_announce, "split_basis_unconfirmed")
    return pre


def _pre_announcement(quarter: EpsQuarter, estimates: Sequence[Mapping],
                      closes: Sequence[Tuple[str, float]], *, cutoff) -> PreAnnouncement:
    estimates = _eligible_estimates(estimates, cutoff)
    announce = quarter.announce_date[:10]
    price = _close_before(closes, announce, PRICE_MAX_STALENESS_DAYS)
    if announce <= FIRST_WEEKLY_SNAPSHOT:
        value = quarter.eps_estimated if _finite(quarter.eps_estimated) else None
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
    if len(last4) < 4 or not _contiguous(fiscals) or any(not _finite(q.eps_actual) for q in last4):
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
    if (_d(as_of) - _d(snap)).days > FORWARD_STALE_DAYS:
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


def ntm_eps(estimates: Sequence[Mapping], announced: Sequence[EpsQuarter], as_of: str, *, cutoff) -> NtmResult:
    """North-star NTM: the 4 unannounced quarters after the last announced one."""
    estimates = _eligible_estimates(estimates, cutoff)
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


def revision_inputs(estimates: Sequence[Mapping], announced: Sequence[EpsQuarter], as_of: str, *, cutoff) -> RevisionResult:
    """Consensus change on a fixed quarter set taken from the base snapshot."""
    estimates = _eligible_estimates(estimates, cutoff)
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
                    as_of: str, unit_ok: bool, cutoff, forward_runs_available: bool,
                    splits: Sequence[Mapping] = (), last_close_date: Optional[str] = None) -> ConsensusInputs:
    # Filter the complete history before every use: revision bases and jump checks
    # must obey the same admission rule as the newest snapshot.
    estimates = _eligible_estimates(estimates, cutoff) if forward_runs_available else []
    # One unit for every snapshot: the post-split basis of the latest stored split (P0-1)
    events = split_events(splits, last_close_date)
    estimates, _, _ = on_latest_basis(estimates, events)
    snap, reason = _current_snapshot(_weekly(estimates), as_of)
    stale = bool(snap and reason is None
                 and (_d(as_of) - _d(snap)).days >= SNAPSHOT_MAX_STALENESS_DAYS)
    pre = (pre_announcement_consensus(current_eps, estimates, closes, cutoff=cutoff, events=events)
           if current_eps is not None
           else PreAnnouncement(None, None, None, None, "no_current_eps"))
    ntm = ntm_eps(estimates, announced, as_of, cutoff=cutoff)
    ttm, ttm_q = ttm_eps(_announced_by(announced, as_of))
    rev = revision_inputs(estimates, announced, as_of, cutoff=cutoff)
    if not forward_runs_available:
        missing = 'forward_runs_unavailable'
        ntm = NtmResult(None, None, (), None, missing)
        rev = RevisionResult(None, rev.window_weeks, (), None, None, missing)
        if pre.source != 'vendor_estimate':
            pre = PreAnnouncement(None, None, None, pre.price_pre_announce, missing)
    if not unit_ok:
        return ConsensusInputs(
            PreAnnouncement(None, pre.source, pre.snapshot_date, pre.price_pre_announce, "unit_unverified"),
            NtmResult(None, ntm.basis, ntm.quarters, ntm.snapshot_date, "unit_unverified"), None, ttm_q,
            RevisionResult(None, rev.window_weeks, rev.quarters, rev.base_snapshot, rev.current_snapshot,
                           "unit_unverified"),
            None, {k: "unit_unverified" for k in ("pre_announce", "ntm", "ttm", "revision")}, stale)
    reasons = {k: v for k, v in (("pre_announce", pre.missing_reason), ("ntm", ntm.missing_reason),
                                 ("ttm", None if ttm is not None else "ttm_incomplete"),
                                 ("revision", rev.missing_reason)) if v}
    return ConsensusInputs(pre, ntm, ttm, ttm_q, rev, 1.0, reasons, stale)
