#!/usr/bin/env python3
"""Weekly split refresh for market.db:fmp_stock_splits (2026-10-25).

Reads the FMP splits calendar for the last --days days in 7-day windows, and
for each listed symbol that is in the current base universe or already has
split rows, replaces its full split history from the per-symbol endpoint.
Exit 0 = done, 1 = calendar or per-symbol fetch failed (no partial calendar
is trusted), 2 = setup error.
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

WINDOW_DAYS = 7
# A 7-day window normally returns ~70 rows; this many means the vendor cap was hit.
WINDOW_ROW_LIMIT = 400


def calendar_symbols(client, start: date, end: date) -> set:
    symbols = set()
    day = start
    while day <= end:
        window_end = min(day + timedelta(days=WINDOW_DAYS - 1), end)
        rows = client.get_splits_calendar(day.isoformat(), window_end.isoformat())
        if len(rows) >= WINDOW_ROW_LIMIT:
            raise RuntimeError(
                f"splits-calendar {day}..{window_end} returned {len(rows)} rows; possibly truncated")
        symbols.update(str(r["symbol"]).upper() for r in rows)
        day = window_end + timedelta(days=1)
    return symbols


def refresh_splits(store, client, *, days: int, today: date, base_symbols) -> dict:
    listed = calendar_symbols(client, today - timedelta(days=days), today)
    tracked = {s.upper() for s in base_symbols}
    tracked |= {r[0] for r in store._get_conn().execute(
        "SELECT DISTINCT symbol FROM fmp_stock_splits")}
    targets = sorted(listed & tracked)
    refreshed, failed = {}, {}
    for symbol in targets:
        try:
            refreshed[symbol] = store.replace_stock_splits(symbol, client.get_stock_splits(symbol))
        except Exception as exc:  # one bad symbol must not hide the others
            failed[symbol] = str(exc)[:200]
    return {"calendar_symbols": len(listed), "targets": targets,
            "refreshed": refreshed, "failed": failed}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--days", type=int, default=14)
    args = parser.parse_args(argv)
    if args.days < 1:
        parser.error("--days must be positive")
    try:
        from src.data.fmp_client import fmp_client
        from src.data.market_store import get_store
        from src.data.universe_resolver import current_base_universe
        store = get_store()
        base = current_base_universe(store=store)
    except Exception as exc:
        logger.error("setup failed: %s", exc)
        return 2
    try:
        result = refresh_splits(store, fmp_client, days=args.days,
                                today=date.today(), base_symbols=base)
    except Exception as exc:
        logger.error("split refresh failed: %s", exc)
        return 1
    print(json.dumps(result, sort_keys=True))
    return 1 if result["failed"] else 0


if __name__ == "__main__":
    sys.exit(main())
