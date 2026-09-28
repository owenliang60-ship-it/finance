"""Prosperity engine D9 history: pure logic behind the backfill targets and
the coverage report (north star layer 1, P2/P3).

Read-only over MarketStore. Membership comes from M1's
`approximate_members_as_of` (10-day market-cap freshness + alias folding);
nothing here re-implements membership, the three-table window or fiscal
matching — those are imported from their owners.
"""
from datetime import date
from typing import Any, Dict, List, Optional

QUARTER_END_MONTH_DAYS = {3: 31, 6: 30, 9: 30, 12: 31}


def _is_quarter_end(d: date) -> bool:
    return QUARTER_END_MONTH_DAYS.get(d.month) == d.day


def quarter_ends(start: str = "2021-09-30", end: str = "2026-06-30") -> List[str]:
    """Calendar quarter ends from `start` to `end`, both inclusive."""
    first, last = date.fromisoformat(start), date.fromisoformat(end)
    if not (_is_quarter_end(first) and _is_quarter_end(last)) or first > last:
        raise ValueError("start/end must be calendar quarter ends with start <= end, "
                         "got {} .. {}".format(start, end))
    out, year, month = [], first.year, first.month
    while (year, month) <= (last.year, last.month):
        out.append(date(year, month, QUARTER_END_MONTH_DAYS[month]).isoformat())
        month += 3
        if month > 12:
            year, month = year + 1, 3
    return out


def members_by_quarter_end(store, qes: List[str],
                           aliases: Optional[List[Dict[str, Any]]] = None,
                           ) -> Dict[str, List[str]]:
    """As-of $10B+ members (canonical codes) at each quarter end."""
    return {qe: list(store.approximate_members_as_of(qe, aliases=aliases)["symbols"])
            for qe in qes}
