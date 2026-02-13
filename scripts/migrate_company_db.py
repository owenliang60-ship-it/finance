#!/usr/bin/env python3
"""
One-time migration: merge valuation.db + company JSON files → company.db.

Data sources (in merge order):
1. valuation.db companies table → base company profiles (96 records)
2. data/companies/ directories → OPRMS ratings, kill conditions, analyses
3. Stock pool from config/settings.py → mark in_pool

Usage:
    python scripts/migrate_company_db.py [--dry-run]
"""
import json
import logging
import sqlite3
import sys
from pathlib import Path

# Project root
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from terminal.company_store import CompanyStore
from terminal.deep_pipeline import extract_structured_data

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)


def migrate_valuation_db(store: CompanyStore, dry_run: bool = False) -> int:
    """Import companies from valuation.db."""
    valuation_path = PROJECT_ROOT / "data" / "valuation.db"
    if not valuation_path.exists():
        logger.warning("valuation.db not found, skipping")
        return 0

    conn = sqlite3.connect(str(valuation_path))
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT symbol, company_name, sector, industry, market_cap, exchange "
        "FROM companies"
    ).fetchall()
    conn.close()

    count = 0
    for row in rows:
        if dry_run:
            logger.info("Would import: %s (%s)", row["symbol"], row["company_name"])
        else:
            store.upsert_company(
                symbol=row["symbol"],
                company_name=row["company_name"] or "",
                sector=row["sector"] or "",
                industry=row["industry"] or "",
                exchange=row["exchange"] or "",
                market_cap=row["market_cap"],
                source="valuation_db",
            )
        count += 1

    logger.info("Imported %d companies from valuation.db", count)
    return count


def migrate_company_dirs(store: CompanyStore, dry_run: bool = False) -> dict:
    """Import OPRMS ratings and extract analysis data from company directories."""
    companies_dir = PROJECT_ROOT / "data" / "companies"
    if not companies_dir.exists():
        logger.warning("data/companies/ not found, skipping")
        return {"oprms": 0, "analyses": 0, "kill_conditions": 0}

    stats = {"oprms": 0, "analyses": 0, "kill_conditions": 0}

    for sym_dir in sorted(companies_dir.iterdir()):
        if not sym_dir.is_dir() or not sym_dir.name.isupper():
            continue

        symbol = sym_dir.name

        # Ensure company exists in DB
        if not dry_run:
            store.upsert_company(symbol, source="analysis")

        # --- OPRMS ---
        oprms_path = sym_dir / "oprms.json"
        if oprms_path.exists():
            try:
                oprms = json.loads(oprms_path.read_text(encoding="utf-8"))
                if dry_run:
                    logger.info(
                        "Would import OPRMS for %s: DNA=%s Timing=%s",
                        symbol, oprms.get("dna"), oprms.get("timing"),
                    )
                else:
                    store.save_oprms_rating(
                        symbol=symbol,
                        dna=oprms.get("dna", "?"),
                        timing=oprms.get("timing", "?"),
                        timing_coeff=oprms.get("timing_coeff", 0.5),
                        conviction_modifier=oprms.get("conviction_modifier"),
                        evidence=oprms.get("evidence", []),
                        investment_bucket=oprms.get("investment_bucket", ""),
                        verdict=oprms.get("verdict", ""),
                        position_pct=oprms.get("position_pct"),
                    )
                stats["oprms"] += 1
            except Exception as e:
                logger.warning("Failed to import OPRMS for %s: %s", symbol, e)

        # --- Kill Conditions ---
        kc_path = sym_dir / "kill_conditions.json"
        if kc_path.exists():
            try:
                kc_data = json.loads(kc_path.read_text(encoding="utf-8"))
                raw_conditions = kc_data.get("conditions", [])
                # Normalize: conditions can be strings or dicts
                conditions = []
                for c in raw_conditions:
                    if isinstance(c, str):
                        conditions.append({"description": c})
                    elif isinstance(c, dict) and "description" in c:
                        conditions.append(c)
                if conditions:
                    if dry_run:
                        logger.info("Would import %d kill conditions for %s", len(conditions), symbol)
                    else:
                        store.save_kill_conditions(symbol, conditions)
                    stats["kill_conditions"] += len(conditions)
            except Exception as e:
                logger.warning("Failed to import kill conditions for %s: %s", symbol, e)

        # --- Research / Analysis ---
        research_dir = sym_dir / "research"
        if research_dir.exists():
            # Find the latest research directory (could be timestamped or flat)
            research_dirs = []
            # Check for timestamped subdirectories
            for sub in sorted(research_dir.iterdir(), reverse=True):
                if sub.is_dir() and sub.name[0].isdigit():
                    research_dirs.append(sub)
            # Also check the flat research dir itself
            if (research_dir / "oprms.md").exists():
                research_dirs.append(research_dir)

            for rd in research_dirs[:1]:  # Only import latest
                try:
                    if dry_run:
                        logger.info("Would extract analysis from %s", rd)
                    else:
                        data = extract_structured_data(symbol, rd)
                        data["research_dir"] = str(rd)
                        # Find report files
                        for report_file in rd.glob("full_report_*.md"):
                            data["report_path"] = str(report_file)
                        for html_file in rd.glob("full_report_*.html"):
                            data["html_report_path"] = str(html_file)
                        store.save_analysis(symbol, data)
                    stats["analyses"] += 1
                except Exception as e:
                    logger.warning("Failed to extract analysis for %s from %s: %s", symbol, rd, e)

    logger.info(
        "Migrated company dirs: %d OPRMS, %d analyses, %d kill conditions",
        stats["oprms"], stats["analyses"], stats["kill_conditions"],
    )
    return stats


def sync_stock_pool(store: CompanyStore, dry_run: bool = False) -> int:
    """Mark in_pool based on config/settings.py stock pool."""
    try:
        from config.settings import STOCK_POOL
        pool_symbols = [s["symbol"] for s in STOCK_POOL if "symbol" in s]
    except ImportError:
        logger.warning("Could not import STOCK_POOL from config.settings")
        return 0

    if dry_run:
        logger.info("Would sync pool with %d symbols", len(pool_symbols))
        return len(pool_symbols)

    count = store.sync_pool(pool_symbols)
    logger.info("Synced stock pool: %d symbols", count)
    return count


def main():
    dry_run = "--dry-run" in sys.argv

    if dry_run:
        logger.info("=== DRY RUN MODE ===")

    db_path = PROJECT_ROOT / "data" / "company.db"
    if db_path.exists() and not dry_run:
        logger.info("company.db already exists at %s", db_path)
        response = input("Overwrite? (y/N): ").strip().lower()
        if response != "y":
            logger.info("Aborted.")
            return

        db_path.unlink()
        logger.info("Removed existing company.db")

    store = CompanyStore(db_path=db_path)

    # 1. Import from valuation.db
    val_count = migrate_valuation_db(store, dry_run)

    # 2. Import from company directories
    dir_stats = migrate_company_dirs(store, dry_run)

    # 3. Sync stock pool
    pool_count = sync_stock_pool(store, dry_run)

    # Summary
    if not dry_run:
        stats = store.get_stats()
        logger.info("")
        logger.info("=== Migration Complete ===")
        logger.info("Total companies: %d", stats["total_companies"])
        logger.info("In pool: %d", stats["in_pool"])
        logger.info("With OPRMS: %d", stats["rated"])
        logger.info("With analyses: %d", stats["analyzed"])
        logger.info("DNA distribution: %s", stats["dna_distribution"])
        logger.info("Database: %s", db_path)
    else:
        logger.info("")
        logger.info("=== Dry Run Summary ===")
        logger.info("Would import %d from valuation.db", val_count)
        logger.info("Would import %d OPRMS + %d analyses", dir_stats["oprms"], dir_stats["analyses"])
        logger.info("Would sync %d pool symbols", pool_count)

    store.close()


if __name__ == "__main__":
    main()
