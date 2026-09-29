"""Assemble one point-in-time input packet; pure except for the pandas beta helper."""
from __future__ import annotations

import math
from bisect import bisect_left
from dataclasses import asdict
from datetime import date, timedelta
from typing import Any, Dict, List, Optional, Sequence, Tuple

import pandas as pd

from src.data.prosperity_quality import split_ratio_eligible
from src.indicators.beta import compute_beta
from terminal.prosperity.config import MCAP_MAX_STALENESS_DAYS, PIT_RANK, PRICE_MAX_STALENESS_DAYS
from terminal.prosperity.consensus import build_consensus, unit_verified
from terminal.prosperity.statements import build_quarters, visible_statements
from terminal.prosperity.street_eps import aligned_eps_window, announced_eps
from terminal.prosperity.types import InputPacket, SymbolHistory

SEASON_SHIFT_DAYS = 7          # same season bucketing as prosperity_history.SEASON_SHIFT_DAYS
PRICE_SPLIT_TOLERANCE = math.log(1.25)   # same match band as prosperity_quality.split_basis_audit
BETA_CLOSES = 400              # compute_beta uses the last 126 returns; keep a margin for gaps


def _d(value: str) -> date:
    return date.fromisoformat(value[:10])


def _last_on_or_before(series: Sequence[Tuple[str, float]], as_of: str, max_age: int):
    prior = [(d, v) for d, v in series if d[:10] <= as_of[:10]]
    if not prior:
        return None, None
    day, value = max(prior)
    return (value, day[:10]) if (_d(as_of) - _d(day)).days <= max_age else (None, None)


def _quarter_bucket(fiscal: Optional[str]) -> Optional[str]:
    if fiscal is None:
        return None
    shifted = _d(fiscal) - timedelta(days=SEASON_SHIFT_DAYS)
    return f"{shifted.year}Q{(shifted.month - 1) // 3 + 1}"


def _split_adjusted(closes: Sequence[Tuple[str, float]], splits: Sequence[dict]) -> Tuple[List[Tuple[str, float]], bool]:
    """Divide closes before a split date by its ratio when the stored series still jumps there.

    Same principle as the street EPS rescale (Boss 2026-09-29): only a price jump
    at the split date matching the ratio counts as confirmation; market.db is untouched.
    """
    out, changed = list(closes), False
    for s in splits:
        num, den, day = s.get("numerator"), s.get("denominator"), (s.get("date") or "")[:10]
        if not (day and split_ratio_eligible(num, den)):
            continue
        i = bisect_left(closes, day, key=lambda c: c[0][:10])        # closes are date-sorted
        if i == 0 or i == len(closes) or not (closes[i - 1][1] and closes[i][1]) \
                or closes[i - 1][1] <= 0 or closes[i][1] <= 0:
            continue
        jump, log_r = math.log(closes[i - 1][1] / closes[i][1]), math.log(num / den)
        if abs(jump - log_r) <= min(PRICE_SPLIT_TOLERANCE, 0.25 * abs(log_r)):
            out = [(d, p / (num / den) if d[:10] < day else p) for d, p in out]
            changed = True
    return out, changed


def _series(closes: Sequence[Tuple[str, float]], as_of: str) -> pd.Series:
    prior = [(d, v) for d, v in closes if d[:10] <= as_of[:10]][-BETA_CLOSES:]
    return pd.Series([v for _, v in prior], index=[d for d, _ in prior], dtype=float)


def build_packet(history: SymbolHistory, as_of: str, *, mode: str, membership_basis: str,
                 benchmark_closes: Sequence[Tuple[str, float]], observed_at: Optional[str] = None,
                 identity_unverified: bool = False, with_beta: bool = True) -> InputPacket:
    closes, price_rescaled = _split_adjusted(history.closes, history.splits)
    visible = visible_statements(history, as_of, mode, observed_at)
    qb = build_quarters(visible, as_of)
    announced = announced_eps(history.earnings, history.income, history.splits, as_of)
    window, eps_reason = aligned_eps_window(announced, qb.current_fiscal)
    estimates = [r for r in history.estimates if r["snapshot_date"][:10] <= as_of[:10]]
    currency = qb.quarters[-1].reported_currency if qb.quarters else None
    unit_ok = unit_verified(currency, history.is_adr)
    consensus = build_consensus(estimates=estimates, announced=announced, current_eps=window[-1] if window else None,
                                closes=closes, as_of=as_of, unit_ok=unit_ok)
    price, price_date = _last_on_or_before(closes, as_of, PRICE_MAX_STALENESS_DAYS)
    mcap, mcap_date = _last_on_or_before(history.market_caps, as_of, MCAP_MAX_STALENESS_DAYS)
    beta = None
    if with_beta and closes and benchmark_closes:
        beta = compute_beta(_series(closes, as_of), _series(benchmark_closes, as_of))
    live = mode == "live"
    pit = {"statements": visible.pit,
           "street_eps": "live" if live else "approximate",
           # strict only when the pre-announcement consensus came from our own weekly snapshots
           "consensus": "live" if live else ("strict" if consensus.pre_announce.source == "local_snapshot"
                                             else "approximate")}
    flags = list(qb.flags)
    for flag, on in ((eps_reason, eps_reason is not None), ("unit_unverified", not unit_ok),
                     ("price_missing", price is None), ("price_split_rescaled", price_rescaled),
                     ("identity_unverified", identity_unverified)):
        if on and flag not in flags:
            flags.append(flag)
    latest = qb.quarters[-1] if qb.quarters else None
    reported = bool(latest and latest.available_on
                    and 0 <= (_d(as_of) - _d(latest.available_on)).days < 7)
    archive = {
        "eps_window": [[q.fiscal_date, q.announce_date, q.eps_actual, list(q.labels)] for q in window],
        "pre_announce": asdict(consensus.pre_announce),
        "ntm": asdict(consensus.ntm),
        "ttm": {"eps": consensus.ttm_eps, "quarters": list(consensus.ttm_quarters)},
        "revision": asdict(consensus.revision),
        "unit_factor": consensus.unit_factor,
        "market_cap_date": mcap_date,
    }
    profile = history.profile or {}
    return InputPacket(
        symbol=history.symbol, as_of=as_of[:10], membership_basis=membership_basis,
        sector=profile.get("sector"), industry=profile.get("industry"),
        current_fiscal=qb.current_fiscal, quarter_bucket=_quarter_bucket(qb.current_fiscal),
        data_age_days=(_d(as_of) - _d(qb.current_fiscal)).days if qb.current_fiscal else None,
        reported_this_week=reported, quarters=qb.quarters, eps=window, consensus=consensus,
        price_asof=price, price_date=price_date, market_cap_asof=mcap, beta=beta,
        pit=pit, pit_basis=min(pit.values(), key=PIT_RANK.__getitem__), flags=tuple(flags), archive=archive)


def packet_leaks(packet: InputPacket) -> List[str]:
    """Every input date later than as_of (and pre-announcement snapshots not before the release)."""
    bound, out = packet.as_of, []

    def check(label: str, value: Optional[str]):
        if value and value[:10] > bound:
            out.append(f"{label} {value[:10]} > as_of {bound}")

    for q in packet.quarters:
        check(f"quarter {q.fiscal_date} fiscal_date", q.fiscal_date)
        check(f"quarter {q.fiscal_date} available_on", q.available_on)
    for e in packet.eps:
        check(f"eps {e.fiscal_date} announce_date", e.announce_date)
    check("price_date", packet.price_date)
    check("market_cap_date", packet.archive.get("market_cap_date"))
    c = packet.consensus
    check("ntm snapshot", c.ntm.snapshot_date)
    check("revision base snapshot", c.revision.base_snapshot)
    check("revision current snapshot", c.revision.current_snapshot)
    check("pre-announce snapshot", c.pre_announce.snapshot_date)
    if c.pre_announce.snapshot_date and packet.eps and c.pre_announce.snapshot_date[:10] >= packet.eps[-1].announce_date[:10]:
        out.append(f"pre-announce snapshot {c.pre_announce.snapshot_date[:10]} not before announcement "
                   f"{packet.eps[-1].announce_date[:10]}")
    return out


def packet_to_dict(packet: InputPacket) -> Dict[str, Any]:
    return asdict(packet)
