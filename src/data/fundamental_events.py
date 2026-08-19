"""Earnings-event-driven incremental targets (T11, R6).

`--scope events` exists so a symbol that just reported (or is about to) gets
a fresh fundamentals pull without waiting for the weekly `--scope core` pass.
`detect_earnings_targets` answers one question: "which SM-eligible symbols
had an announce_date land inside the trailing window?" — nothing here
collects data; `scripts.update_data.run_fundamental_update` drives the T8
kernel over whatever this returns.

Window semantics: `(as_of - window_days, as_of]` — exclusive lower bound,
inclusive upper bound, so a symbol whose earnings landed exactly on `as_of`
is included and one from exactly `window_days` before is not (it already had
its chance in the previous window, and inclusive-both would double-count
across two adjacent runs).

Eligibility: filtered through `MarketStore.get_security_eligibility()` (T3),
the same SM table `current_base_universe` (T7) uses — ETFs, funds, secondary
share classes and other blocked identities in `fmp_earnings` (a street-wide
feed, not universe-scoped) never make it into a target list.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import List

from src.data.market_store import MarketStore

DEFAULT_WINDOW_DAYS = 8


def detect_earnings_targets(store: MarketStore, *, window_days: int = DEFAULT_WINDOW_DAYS,
                            as_of: str) -> List[str]:
    """Symbols with `fmp_earnings.announce_date` in `(as_of - window_days, as_of]`,
    intersected with SM eligibility.

    Args:
        store: MarketStore owning `fmp_earnings` and `security_master`.
        window_days: trailing window size in days (default 8).
        as_of: "YYYY-MM-DD" — the run's reference date (inclusive upper bound).

    Returns:
        Sorted (by announce_date via the query, symbol as tiebreak) list of
        eligible symbols. Empty when nothing announced in the window, or when
        every announcer in it is ineligible.
    """
    as_of_date = datetime.strptime(as_of, "%Y-%m-%d").date()
    lower_bound = (as_of_date - timedelta(days=window_days)).isoformat()

    conn = store._get_conn()
    rows = conn.execute(
        "SELECT DISTINCT symbol FROM fmp_earnings "
        "WHERE announce_date > ? AND announce_date <= ? ORDER BY symbol",
        (lower_bound, as_of),
    ).fetchall()
    candidates = [row["symbol"] for row in rows]

    eligibility = store.get_security_eligibility()  # fail-loud on empty SM (T3)
    return [s for s in candidates if eligibility.get(s, False)]
