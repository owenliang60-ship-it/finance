"""Consensus inputs: unit check, pre-announcement consensus, NTM, TTM, revisions.

Pure functions over already-loaded rows; the caller slices nothing, every
function applies its own as_of / announce-date bound.
"""
from __future__ import annotations

from datetime import date
from typing import Mapping, Optional, Sequence, Tuple

from terminal.prosperity.config import (ESTIMATE_MATCH_DAYS, FIRST_WEEKLY_SNAPSHOT,
                                        PRE_ANNOUNCE_MAX_STALENESS_DAYS, PRICE_MAX_STALENESS_DAYS)
from terminal.prosperity.types import EpsQuarter, PreAnnouncement


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
