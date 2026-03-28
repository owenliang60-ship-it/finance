#!/usr/bin/env python3
"""
Extended Universe Price Update — yfinance batch download for $10B+ stocks.

Usage:
    python scripts/update_extended_prices.py                    # Incremental (daily)
    python scripts/update_extended_prices.py --backfill         # Full 5-year backfill
    python scripts/update_extended_prices.py --refresh-universe # Also refresh stock list
"""
import argparse
import logging
import sys
import time
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


def main():
    parser = argparse.ArgumentParser(description="Update extended universe prices")
    parser.add_argument(
        "--backfill", action="store_true",
        help="Force full 5-year backfill for all symbols",
    )
    parser.add_argument(
        "--refresh-universe", action="store_true",
        help="Refresh extended universe stock list from FMP screener",
    )
    args = parser.parse_args()

    start = time.time()

    # Step 1: Optionally refresh the stock list
    if args.refresh_universe:
        from src.data.extended_universe_manager import refresh_extended_universe
        symbols = refresh_extended_universe()
        logger.info("Extended universe: %d symbols", len(symbols))

    # Step 2: Update prices
    from src.data.extended_price_fetcher import update_extended_prices
    from src.data.extended_universe_manager import (
        get_extended_only_symbols,
        get_extended_symbols,
    )

    ext_symbols = get_extended_symbols()
    ext_only = get_extended_only_symbols()

    if not ext_symbols:
        logger.error(
            "No extended universe found. Run with --refresh-universe first."
        )
        sys.exit(1)

    logger.info(
        "Extended universe: %d total, %d pool, %d extended-only",
        len(ext_symbols), len(ext_symbols) - len(ext_only), len(ext_only),
    )

    result = update_extended_prices(full_backfill=args.backfill)

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
