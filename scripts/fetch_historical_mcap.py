#!/usr/bin/env python3
"""Backfill historical market cap into market.db."""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Iterable, List

_PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(_PROJECT_ROOT))

from src.data.broad_universe_manager import get_broad_seed_symbols, get_broad_symbols
from src.data.extended_universe_manager import get_extended_symbols
from src.data.fmp_client import FMPClient
from src.data.market_store import get_store
from src.data.pool_manager import get_symbols as get_pool_symbols

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)


def _load_existing_sufficient_symbols(years: int) -> set[str]:
    min_rows = int(years * 200 * 0.8)
    store = get_store()
    conn = store._get_conn()
    rows = conn.execute(
        "SELECT symbol, COUNT(*) as cnt FROM historical_market_cap GROUP BY symbol"
    ).fetchall()
    existing = set()
    for row in rows:
        if row[1] >= min_rows:
            existing.add(row[0])
        else:
            logger.info("  %s: only %s rows (< %s), will refetch", row[0], row[1], min_rows)
    logger.info("Existing sufficient symbols: %d (>= %d rows)", len(existing), min_rows)
    return existing


def _resolve_symbols(args: argparse.Namespace) -> List[str]:
    if args.symbols:
        return sorted({symbol.upper() for symbol in args.symbols})
    if args.universe == "pool":
        return get_pool_symbols()
    if args.universe == "broad_seed":
        symbols = get_broad_seed_symbols()
        if not symbols:
            raise RuntimeError(
                "broad_universe_seed.json missing. Run "
                "python -m src.data.broad_universe_manager --refresh-seed first."
            )
        logger.info("Loaded %d symbols from broad_universe_seed.json", len(symbols))
        return symbols
    if args.universe == "broad":
        symbols = get_broad_symbols()
        if not symbols:
            raise RuntimeError(
                "broad_universe.json missing. Run "
                "python -m src.data.broad_universe_manager --finalize first."
            )
        logger.info("Loaded %d symbols from broad_universe.json", len(symbols))
        return symbols
    return get_extended_symbols()


def _resolve_incremental_new_symbols(args: argparse.Namespace) -> List[str]:
    symbols = _resolve_symbols(args)
    store = get_store()
    existing = set(store.list_symbols_in_historical_market_cap())
    new_symbols = sorted(set(symbols) - existing)
    if not new_symbols:
        logger.info("No new symbols to backfill")
    return new_symbols


def fetch_all(
    symbols: Iterable[str],
    *,
    years: int = 5,
    skip_existing: bool = False,
    from_date: str | None = None,
    to_date: str | None = None,
) -> dict:
    symbols = list(symbols)
    if not symbols:
        logger.info("No symbols to fetch")
        return {"success": 0, "skipped": 0, "failed": [], "coverage": 100.0}

    client = FMPClient()
    store = get_store()

    resolved_to_date = to_date or date.today().isoformat()
    resolved_from_date = from_date or (
        datetime.now() - timedelta(days=years * 365)
    ).strftime("%Y-%m-%d")

    existing = _load_existing_sufficient_symbols(years) if skip_existing else set()

    total = len(symbols)
    success = 0
    skipped = 0
    failed: List[str] = []

    for index, symbol in enumerate(symbols, 1):
        if symbol in existing:
            skipped += 1
            continue

        logger.info("[%d/%d] %s", index, total, symbol)
        try:
            rows = client.get_historical_market_cap(
                symbol,
                from_date=resolved_from_date,
                to_date=resolved_to_date,
            )
            if rows:
                store.upsert_historical_market_cap(symbol, rows)
                success += 1
                logger.info("  ok %d rows", len(rows))
            else:
                failed.append(symbol)
                logger.warning("  no data")
        except Exception as exc:  # pragma: no cover - defensive logging path
            failed.append(symbol)
            logger.error("  error: %s", exc)

    coverage = (success + skipped) / total * 100 if total else 100.0
    logger.info("=" * 50)
    logger.info("Coverage report")
    logger.info("  Total: %d symbols", total)
    logger.info("  Success: %d, skipped: %d, failed: %d", success, skipped, len(failed))
    logger.info("  Coverage: %.1f%%", coverage)
    if failed:
        logger.info("  Failed: %s", failed)
    logger.info("=" * 50)
    return {"success": success, "skipped": skipped, "failed": failed, "coverage": coverage}


def main() -> None:
    parser = argparse.ArgumentParser(description="Batch fetch historical market cap")
    parser.add_argument("--symbols", nargs="+", help="Explicit symbols")
    parser.add_argument(
        "--universe",
        choices=["pool", "extended", "broad_seed", "broad"],
        default="extended",
        help="Universe selector",
    )
    parser.add_argument("--years", type=int, default=5, help="Lookback years")
    parser.add_argument("--skip-existing", action="store_true")
    parser.add_argument(
        "--incremental",
        action="store_true",
        help="Fetch only recent days for existing symbols",
    )
    parser.add_argument("--incremental-days", type=int, default=7)
    parser.add_argument(
        "--incremental-new-symbols",
        action="store_true",
        help="Backfill full history only for symbols absent from historical_market_cap",
    )
    args = parser.parse_args()

    if args.incremental and args.incremental_new_symbols:
        raise SystemExit("--incremental and --incremental-new-symbols are mutually exclusive")

    if args.incremental_new_symbols:
        symbols = _resolve_incremental_new_symbols(args)
        if not symbols:
            return
        logger.info("Incremental-new-symbols: %d symbols", len(symbols))
        fetch_all(symbols, years=args.years)
        return

    symbols = _resolve_symbols(args)
    logger.info(
        "Target: %d symbols, %s mode",
        len(symbols),
        "incremental" if args.incremental else ("skip-existing" if args.skip_existing else "full"),
    )
    if args.incremental:
        start_date = (date.today() - timedelta(days=args.incremental_days)).isoformat()
        fetch_all(symbols, years=args.years, from_date=start_date)
    else:
        fetch_all(symbols, years=args.years, skip_existing=args.skip_existing)


if __name__ == "__main__":
    main()
