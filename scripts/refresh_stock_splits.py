#!/usr/bin/env python3
"""Weekly split refresh for market.db:fmp_stock_splits (2026-10-25).

1. Calendar: read the FMP splits calendar for the last --days days in 7-day
   windows. For each listed symbol that is in the current base universe or
   already has split rows, fetch its full history and replace it, but only
   if the fetched history contains every calendar event for that symbol.
2. Coverage: base symbols that have never been fetched get their full
   history (at most --max-new per run, so a new pool fills over a few weeks).
   Checked symbols are kept in data/stock_splits_checked.json, outside
   market_store, so the prosperity code_version is unaffected.

A fetched history must also keep every split date already stored; an empty
or shrunken reply is refused instead of deleting rows.

Exit 0 = done, 1 = calendar or per-symbol failure (no partial calendar is
trusted), 2 = setup error.
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
# The endpoint returns at most ~848 rows, newest first (measured 2026-10-25);
# the busiest 7-day window in 2026 had 133. Reaching this means a cut window.
WINDOW_ROW_LIMIT = 500


def _norm(symbol) -> str:
    return str(symbol).strip().upper().replace(".", "-")


def calendar_events(client, start: date, end: date) -> dict:
    """{symbol: {split dates}} for [start, end]."""
    events = {}
    day = start
    while day <= end:
        window_end = min(day + timedelta(days=WINDOW_DAYS - 1), end)
        rows = client.get_splits_calendar(day.isoformat(), window_end.isoformat())
        if len(rows) >= WINDOW_ROW_LIMIT:
            raise RuntimeError(
                f"splits-calendar {day}..{window_end} returned {len(rows)} rows; possibly truncated")
        for r in rows:
            events.setdefault(_norm(r["symbol"]), set()).add(str(r["date"]))
        day = window_end + timedelta(days=1)
    return events


def _replace(store, client, symbol, required_dates=()):
    rows = client.get_stock_splits(symbol)
    got = {str(r.get("date")) for r in rows}
    missing = set(required_dates) - got
    if missing:
        raise RuntimeError(f"history lacks calendar split(s) {sorted(missing)}")
    lost = {str(r["date"]) for r in store.get_stock_splits(symbol)} - got
    if lost:
        raise RuntimeError(f"history drops stored split(s) {sorted(lost)}")
    return store.replace_stock_splits(symbol, rows)


def load_checked(path: Path) -> set:
    try:
        return set(json.loads(path.read_text()))
    except FileNotFoundError:
        return set()


def save_checked(path: Path, symbols) -> None:
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(sorted(symbols)))
    tmp.replace(path)


def refresh_splits(store, client, *, days: int, today: date, base_symbols,
                   checked: set, max_new: int = 150) -> dict:
    """Mutates `checked` with every symbol whose history was saved."""
    events = calendar_events(client, today - timedelta(days=days), today)
    base = {_norm(s): s for s in base_symbols}
    known = {_norm(s): s for s in store.get_symbols("fmp_stock_splits")}
    fetched = {_norm(s) for s in checked}
    tracked = {**base, **known}
    targets = sorted(tracked[k] for k in events if k in tracked)
    refreshed, failed = {}, {}
    for symbol in targets:
        try:
            refreshed[symbol] = _replace(store, client, symbol, events[_norm(symbol)])
            checked.add(symbol)
        except Exception as exc:  # one bad symbol must not hide the others
            failed[symbol] = str(exc)[:200]
    never = sorted(s for k, s in base.items() if k not in known and k not in fetched
                   and s not in refreshed and s not in failed)
    for symbol in never[:max_new]:
        try:
            refreshed[symbol] = _replace(store, client, symbol)
            checked.add(symbol)
        except Exception as exc:
            failed[symbol] = str(exc)[:200]
    return {"calendar_symbols": len(events), "targets": targets,
            "coverage_fetched": min(len(never), max_new),
            "coverage_remaining": max(len(never) - max_new, 0),
            "refreshed": refreshed, "failed": failed}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--days", type=int, default=35,
                        help="calendar lookback; covers up to four skipped Saturdays")
    parser.add_argument("--max-new", type=int, default=150,
                        help="never-fetched base symbols per run (~513 at rollout -> 4 weeks)")
    args = parser.parse_args(argv)
    if args.days < 1 or args.max_new < 0:
        parser.error("--days must be positive and --max-new non-negative")
    try:
        from src.data.fmp_client import fmp_client
        from src.data.market_store import get_store
        from src.data.universe_resolver import current_base_universe
        store = get_store()
        base = current_base_universe(store=store)
    except Exception as exc:
        logger.error("setup failed: %s", exc)
        return 2
    from config.settings import DATA_DIR
    state = Path(DATA_DIR) / "stock_splits_checked.json"
    checked = load_checked(state)
    try:
        result = refresh_splits(store, fmp_client, days=args.days, today=date.today(),
                                base_symbols=base, checked=checked, max_new=args.max_new)
    except Exception as exc:
        logger.error("split refresh failed: %s", exc)
        return 1
    finally:
        save_checked(state, checked)
    print(json.dumps(result, sort_keys=True))
    return 1 if result["failed"] else 0


if __name__ == "__main__":
    sys.exit(main())
