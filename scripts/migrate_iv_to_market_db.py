#!/usr/bin/env python3
"""
Migrate iv_daily and options_snapshots from company.db to market.db.

Idempotent — uses INSERT OR REPLACE so safe to re-run.

Usage:
    python3 scripts/migrate_iv_to_market_db.py
    python3 scripts/migrate_iv_to_market_db.py --dry-run
"""
import argparse
import logging
import sqlite3
import sys
from pathlib import Path

# Ensure project root is in path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from config.settings import DATA_DIR

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

COMPANY_DB = DATA_DIR / "company.db"
MARKET_DB = DATA_DIR / "market.db"


def count_rows(conn: sqlite3.Connection, table: str) -> int:
    """Count rows in a table."""
    try:
        return conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
    except sqlite3.OperationalError:
        return 0


def migrate_iv_daily(src: sqlite3.Connection, dst: sqlite3.Connection,
                     dry_run: bool = False) -> int:
    """Migrate iv_daily table from company.db to market.db."""
    rows = src.execute(
        "SELECT symbol, date, iv_30d, iv_60d, hv_30d, "
        "put_call_ratio, total_volume, total_oi, created_at "
        "FROM iv_daily"
    ).fetchall()

    if dry_run:
        logger.info("[DRY RUN] Would migrate %d iv_daily rows", len(rows))
        return len(rows)

    count = 0
    for row in rows:
        dst.execute(
            """
            INSERT OR REPLACE INTO iv_daily
                (symbol, date, iv_30d, iv_60d, hv_30d,
                 put_call_ratio, total_volume, total_oi, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            row,
        )
        count += 1

    dst.commit()
    logger.info("Migrated %d iv_daily rows", count)
    return count


def migrate_options_snapshots(src: sqlite3.Connection, dst: sqlite3.Connection,
                              dry_run: bool = False) -> int:
    """Migrate options_snapshots table from company.db to market.db."""
    rows = src.execute(
        "SELECT symbol, snapshot_date, expiration, strike, side, "
        "bid, ask, mid, last, volume, open_interest, iv, "
        "delta, gamma, theta, vega, dte, in_the_money, "
        "underlying_price, created_at "
        "FROM options_snapshots"
    ).fetchall()

    if dry_run:
        logger.info("[DRY RUN] Would migrate %d options_snapshots rows", len(rows))
        return len(rows)

    count = 0
    for row in rows:
        dst.execute(
            """
            INSERT OR REPLACE INTO options_snapshots
                (symbol, snapshot_date, expiration, strike, side,
                 bid, ask, mid, last, volume, open_interest, iv,
                 delta, gamma, theta, vega, dte, in_the_money,
                 underlying_price, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            row,
        )
        count += 1

    dst.commit()
    logger.info("Migrated %d options_snapshots rows", count)
    return count


def main():
    parser = argparse.ArgumentParser(
        description="Migrate IV/options data from company.db to market.db"
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Show plan without writing",
    )
    args = parser.parse_args()

    # Validate paths
    if not COMPANY_DB.exists():
        logger.error("company.db not found at %s", COMPANY_DB)
        sys.exit(1)
    if not MARKET_DB.exists():
        logger.error("market.db not found at %s — run init_market_db.py first", MARKET_DB)
        sys.exit(1)

    # Connect
    src = sqlite3.connect(str(COMPANY_DB))
    dst = sqlite3.connect(str(MARKET_DB))
    dst.execute("PRAGMA journal_mode=WAL")

    # Pre-migration counts
    src_iv = count_rows(src, "iv_daily")
    src_snap = count_rows(src, "options_snapshots")
    dst_iv_before = count_rows(dst, "iv_daily")
    dst_snap_before = count_rows(dst, "options_snapshots")

    logger.info("=== Migration Plan ===")
    logger.info("Source (company.db): iv_daily=%d, options_snapshots=%d", src_iv, src_snap)
    logger.info("Target (market.db) before: iv_daily=%d, options_snapshots=%d",
                dst_iv_before, dst_snap_before)

    # Migrate
    migrate_iv_daily(src, dst, dry_run=args.dry_run)
    migrate_options_snapshots(src, dst, dry_run=args.dry_run)

    # Post-migration counts
    if not args.dry_run:
        dst_iv_after = count_rows(dst, "iv_daily")
        dst_snap_after = count_rows(dst, "options_snapshots")
        logger.info("=== Post-Migration ===")
        logger.info("market.db after: iv_daily=%d, options_snapshots=%d",
                    dst_iv_after, dst_snap_after)
        logger.info("iv_daily: %d -> %d (+%d)", dst_iv_before, dst_iv_after,
                    dst_iv_after - dst_iv_before)
        logger.info("options_snapshots: %d -> %d (+%d)", dst_snap_before, dst_snap_after,
                    dst_snap_after - dst_snap_before)
    else:
        logger.info("[DRY RUN] No data written")

    src.close()
    dst.close()
    logger.info("Done.")


if __name__ == "__main__":
    main()
