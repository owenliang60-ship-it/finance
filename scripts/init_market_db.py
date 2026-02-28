"""
One-time migration: populate market.db from existing CSV/JSON data files.

Usage:
    python scripts/init_market_db.py              # Full migration
    python scripts/init_market_db.py --price-only  # Only price CSVs
    python scripts/init_market_db.py --fundamental-only  # Only fundamental JSONs
    python scripts/init_market_db.py --verify      # Verify row counts only

Idempotent: uses INSERT OR REPLACE, safe to re-run.
"""
import argparse
import json
import logging
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import pandas as pd
from config.settings import PRICE_DIR, FUNDAMENTAL_DIR
from src.data.market_store import get_store

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


def _load_json(path: Path) -> dict:
    if not path.exists():
        logger.warning(f"File not found: {path}")
        return {}
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def migrate_prices(store) -> int:
    """Migrate data/price/*.csv → daily_price table."""
    csv_files = sorted(PRICE_DIR.glob("*.csv"))
    if not csv_files:
        logger.warning("No CSV files found in %s", PRICE_DIR)
        return 0

    total = 0
    for i, csv_path in enumerate(csv_files, 1):
        symbol = csv_path.stem.upper()
        try:
            df = pd.read_csv(csv_path, parse_dates=["date"])
            count = store.upsert_daily_prices_df(symbol, df)
            total += count
            if i % 20 == 0 or i == len(csv_files):
                logger.info("[%d/%d] %s: %d rows", i, len(csv_files), symbol, count)
        except Exception as e:
            logger.error("Failed to migrate %s: %s", symbol, e)

    logger.info("Price migration complete: %d total rows", total)
    return total


def migrate_fundamental(store, name: str, json_file: Path, upsert_fn) -> int:
    """Migrate a fundamental JSON file → corresponding table."""
    data = _load_json(json_file)
    if not data:
        return 0

    total = 0
    symbols = [k for k in data if k != "_meta"]
    for i, symbol in enumerate(symbols, 1):
        rows = data[symbol]
        if not isinstance(rows, list):
            continue
        count = upsert_fn(symbol, rows)
        total += count
        if i % 20 == 0 or i == len(symbols):
            logger.info("[%s] [%d/%d] %s: %d rows", name, i, len(symbols), symbol, count)

    logger.info("%s migration complete: %d total rows from %d symbols", name, total, len(symbols))
    return total


def verify(store) -> bool:
    """Print and verify row counts."""
    stats = store.get_stats()
    print("\n=== market.db Statistics ===")
    ok = True
    for table, count in sorted(stats.items()):
        status = "OK" if count > 0 else "EMPTY"
        if count == 0:
            ok = False
        print(f"  {table}: {count:,} rows [{status}]")
    print()
    return ok


def main():
    parser = argparse.ArgumentParser(description="Migrate CSV/JSON data into market.db")
    parser.add_argument("--price-only", action="store_true", help="Only migrate price CSVs")
    parser.add_argument("--fundamental-only", action="store_true", help="Only migrate fundamental JSONs")
    parser.add_argument("--verify", action="store_true", help="Only verify row counts")
    args = parser.parse_args()

    store = get_store()

    if args.verify:
        ok = verify(store)
        sys.exit(0 if ok else 1)

    do_price = not args.fundamental_only
    do_fundamental = not args.price_only

    if do_price:
        print("=" * 50)
        print("Migrating price data...")
        print("=" * 50)
        migrate_prices(store)

    if do_fundamental:
        print("=" * 50)
        print("Migrating fundamental data...")
        print("=" * 50)
        migrate_fundamental(store, "income", FUNDAMENTAL_DIR / "income.json", store.upsert_income)
        migrate_fundamental(store, "balance_sheet", FUNDAMENTAL_DIR / "balance_sheet.json", store.upsert_balance_sheet)
        migrate_fundamental(store, "cash_flow", FUNDAMENTAL_DIR / "cash_flow.json", store.upsert_cash_flow)
        migrate_fundamental(store, "ratios", FUNDAMENTAL_DIR / "ratios.json", store.upsert_ratios)

    verify(store)
    print("Migration complete!")


if __name__ == "__main__":
    main()
