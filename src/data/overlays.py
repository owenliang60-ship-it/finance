"""Overlay loaders for the universe resolver (R6, R13).

Each loader is a zero-arg callable (with an optional `store` override for
tests) returning a plain symbol list, wired into `resolve_universe`'s
`overlay_loaders` mapping. All loaders read company.db only — Overlay data
(holdings, watchlist) never touches market.db (P3 ownership).
"""
from __future__ import annotations

from typing import List, Optional


def load_holdings(store=None) -> List[str]:
    """Open equity holdings from company.db `holdings` (status='OPEN')."""
    if store is None:
        from terminal.company_store import get_store
        store = get_store()
    rows = store.get_all_open_holdings()
    return sorted({r["symbol"].upper() for r in rows})


def load_watchlist(store=None) -> List[str]:
    """Manually tracked symbols from company.db `watchlist` (table created in T16).

    Table missing or empty -> [] without raising (pre-T16 tolerance).
    """
    if store is None:
        from terminal.company_store import get_store
        store = get_store()
    conn = store._get_conn()
    exists = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='watchlist'"
    ).fetchone()
    if not exists:
        return []
    rows = conn.execute("SELECT symbol FROM watchlist").fetchall()
    return sorted({r["symbol"].upper() for r in rows})


def load_benchmarks(store=None) -> List[str]:
    """Benchmark/index ETFs (settings.BENCHMARK_SYMBOLS). `store` unused, kept
    for a uniform zero-or-one-arg loader signature."""
    from config.settings import BENCHMARK_SYMBOLS
    return list(BENCHMARK_SYMBOLS)


OVERLAY_TIER = ("holdings", "watchlist", "benchmarks")


def load_overlay_tier(store=None) -> List[str]:
    """The three overlays as one set: holdings ∪ watchlist ∪ benchmarks (~50).

    The tier every consumer means by "the names Boss actually looks at": the
    FMP daily-price budget (matrix #6) and the default correlation scope
    (matrix #9) are both defined against it. Reads company.db + settings only,
    so it resolves before `extended_membership` is ever bootstrapped — callers
    that also need a base universe must handle that separately.
    """
    from src.data.universe_resolver import resolve_universe

    resolved = resolve_universe(
        base="none",
        overlays=OVERLAY_TIER,
        overlay_loaders={
            "holdings": lambda: load_holdings(store=store),
            "watchlist": lambda: load_watchlist(store=store),
            "benchmarks": lambda: load_benchmarks(store=store),
        },
    )
    return list(resolved.symbols)
