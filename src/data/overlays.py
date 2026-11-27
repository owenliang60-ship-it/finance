"""Overlay loaders for the universe resolver (R6, R13).

Each loader is a zero-arg callable (with an optional `store` override for
tests) returning a plain symbol list, wired into `resolve_universe`'s
`overlay_loaders` mapping. Holdings come from the read-only Google Sheet
book of record; watchlist reads company.db. Overlay data never touches
market.db (P3 ownership).
"""
from __future__ import annotations

import logging
from typing import List, Optional

logger = logging.getLogger(__name__)


def load_holdings(store=None) -> List[str]:
    """Quotable US holdings from the Google Sheet book (read-only).

    `store` is unused and kept for caller signature compatibility. Sheet
    failure -> [] with a WARNING (never logs the sheet ID / URL).
    """
    try:
        from portfolio.holdings import sheet_book
        return sheet_book.load_sheet_book().quotable_us_symbols()
    except Exception as exc:
        logger.warning(
            "overlays.load_holdings: Google Sheet unavailable (%s); returning []",
            type(exc).__name__)
        return []


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
    (matrix #9) are both defined against it. Reads the sheet, company.db and settings,
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
