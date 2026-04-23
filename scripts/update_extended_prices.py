#!/usr/bin/env python3
"""Update yfinance daily prices for extended or broad universes."""

from __future__ import annotations

import argparse
import logging
import sys
import time
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


def main() -> None:
    parser = argparse.ArgumentParser(description="Update extended universe prices")
    parser.add_argument(
        "--backfill",
        "--full-backfill",
        dest="full_backfill",
        action="store_true",
        help="Force full 5-year backfill for all selected symbols",
    )
    parser.add_argument(
        "--refresh-universe",
        action="store_true",
        help="Refresh extended universe stock list from FMP screener",
    )
    parser.add_argument(
        "--universe",
        choices=["extended", "broad"],
        default="extended",
    )
    parser.add_argument(
        "--incremental",
        action="store_true",
        help="Existing symbols fetch only recent days",
    )
    parser.add_argument("--incremental-days", type=int, default=7)
    parser.add_argument(
        "--incremental-new-symbols",
        action="store_true",
        help="Backfill full history only for symbols absent from daily_price",
    )
    args = parser.parse_args()

    if args.incremental and args.incremental_new_symbols:
        raise SystemExit("--incremental and --incremental-new-symbols are mutually exclusive")

    start = time.time()

    if args.refresh_universe and args.universe == "extended":
        from src.data.extended_universe_manager import refresh_extended_universe

        symbols = refresh_extended_universe()
        logger.info("Extended universe refreshed: %d symbols", len(symbols))

    from src.data.broad_universe_manager import get_broad_symbols, get_new_symbols_vs_price
    from src.data.extended_price_fetcher import update_extended_prices
    from src.data.extended_universe_manager import (
        get_extended_only_symbols,
        get_extended_symbols,
    )
    from src.data.pool_manager import get_symbols as get_pool_symbols

    if args.universe == "broad":
        pool_symbols = set(get_pool_symbols())
        if args.incremental_new_symbols:
            symbols = sorted(set(get_new_symbols_vs_price()) - pool_symbols)
            if not symbols:
                logger.info("No new broad symbols to backfill")
                return
            result = update_extended_prices(full_backfill=True, symbols=symbols)
        else:
            broad = set(get_broad_symbols())
            if not broad:
                logger.error("No broad universe found. Run broad_universe_manager --finalize first.")
                sys.exit(1)
            symbols = sorted(broad - pool_symbols)
            start_date = None
            if args.incremental:
                start_date = (date.today() - timedelta(days=args.incremental_days)).isoformat()
            result = update_extended_prices(
                full_backfill=args.full_backfill,
                symbols=symbols,
                start_date=start_date,
            )
    else:
        ext_symbols = get_extended_symbols()
        ext_only = get_extended_only_symbols()
        if not ext_symbols:
            logger.error("No extended universe found. Run with --refresh-universe first.")
            sys.exit(1)
        logger.info(
            "Extended universe: %d total, %d pool, %d extended-only",
            len(ext_symbols),
            len(ext_symbols) - len(ext_only),
            len(ext_only),
        )
        start_date = None
        if args.incremental:
            start_date = (date.today() - timedelta(days=args.incremental_days)).isoformat()
        result = update_extended_prices(
            full_backfill=args.full_backfill,
            symbols=None,
            start_date=start_date,
        )

    elapsed = time.time() - start
    logger.info(
        "Done in %.1fs — %d/%d success, %d failed, %d rows upserted",
        elapsed,
        result["success"],
        result["total"],
        len(result["failed"]),
        result["rows_inserted"],
    )
    if result["failed"]:
        logger.warning("Failed symbols: %s", result["failed"][:20])


if __name__ == "__main__":
    main()
