#!/usr/bin/env python3
"""Migrate manual/analysis Core exceptions into the local-owned watchlist.

Code is prepared in Stop A; the real mutation runs only at Stop C with Boss
approval, before company.db is pushed to cloud.
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Callable, Iterable, Mapping, Optional

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


def migrate_core_watchlist(
    core_entries: Iterable[Mapping],
    *,
    base_symbols: Iterable[str],
    add_fn: Callable[[str, str], None],
    etf_symbols: Optional[Iterable[str]] = None,
) -> dict:
    base = {str(s).upper() for s in base_symbols}
    from src.data.fmp_forward_ingestion import ETF_HOLDING_SOURCES
    basket_by_etf = {symbol.upper(): basket
                     for basket, symbol in ETF_HOLDING_SOURCES.items()}
    known_etfs = set(basket_by_etf)
    if etf_symbols is not None:
        known_etfs.update(str(s).upper() for s in etf_symbols)

    migrated = []
    skipped_etf = []
    basket_covered = {}
    for entry in core_entries:
        symbol = str(entry.get("symbol") or "").upper()
        source = str(entry.get("source") or "")
        if not symbol or source not in {"analysis", "manual"} or symbol in base:
            continue
        if symbol in known_etfs:
            skipped_etf.append(symbol)
            if symbol in basket_by_etf:
                basket_covered[symbol] = basket_by_etf[symbol]
            continue
        add_fn(symbol, "core-retirement:%s" % source)
        migrated.append(symbol)

    return {
        "migrated": sorted(set(migrated)),
        "skipped_etf": sorted(set(skipped_etf)),
        "basket_covered": dict(sorted(basket_covered.items())),
    }


def main() -> int:
    from src.data.pool_manager import load_universe
    from src.data.universe_resolver import current_base_universe
    from terminal.company_store import get_store

    store = get_store()
    report = migrate_core_watchlist(
        load_universe(),
        base_symbols=current_base_universe(),
        add_fn=lambda symbol, source: store.add_to_watchlist(
            symbol, source=source),
    )
    print("migrated:", report["migrated"])
    print("skipped ETF:", report["skipped_etf"])
    print("ETF basket coverage:", report["basket_covered"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
