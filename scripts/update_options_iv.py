#!/usr/bin/env python3
"""
Daily IV Update Script — 采集股票池 ATM IV 并存入 company.db

用法:
    python scripts/update_options_iv.py              # 更新全部股票池
    python scripts/update_options_iv.py --symbols AAPL MSFT  # 指定股票
    python scripts/update_options_iv.py --dry-run    # 干跑不存储

部署:
    云端 cron: 周二-六 06:50 北京时间（在量价更新 06:30 之后）
    日志: logs/cron_options_iv.log

Credit 预算: ~350 credits/天 (116 stocks × ~3 credits/stock)
"""
import argparse
import logging
import sys
from datetime import datetime
from pathlib import Path

# Ensure project root is in path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from config.settings import MARKETDATA_API_KEY
from terminal.company_store import get_store
from terminal.options.iv_tracker import update_daily_iv

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


def main():
    parser = argparse.ArgumentParser(description="Daily IV Update")
    parser.add_argument(
        "--symbols", nargs="+",
        help="Specific symbols to update (default: all pool stocks)",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Dry run: check API but don't store",
    )
    args = parser.parse_args()

    # Pre-flight checks
    if not MARKETDATA_API_KEY:
        logger.error("MARKETDATA_API_KEY not set — aborting")
        sys.exit(1)

    store = get_store()

    # Determine symbols
    if args.symbols:
        symbols = [s.upper() for s in args.symbols]
        logger.info("Updating IV for %d specified symbols", len(symbols))
    else:
        companies = store.list_companies(in_pool_only=True)
        symbols = [c["symbol"] for c in companies]
        logger.info("Updating IV for %d pool stocks", len(symbols))

    if not symbols:
        logger.warning("No symbols to update — exiting")
        sys.exit(0)

    # Run update
    start = datetime.now()
    logger.info(
        "=== IV Update Start: %s ===",
        start.strftime("%Y-%m-%d %H:%M:%S"),
    )

    if args.dry_run:
        logger.info("[DRY RUN] Would update %d symbols", len(symbols))
        # Just test API connectivity with first symbol
        from src.data.marketdata_client import marketdata_client
        test = marketdata_client.get_atm_iv_data(symbols[0])
        if test and test.get("s") == "ok":
            logger.info("[DRY RUN] API connection OK (tested %s)", symbols[0])
        else:
            logger.error("[DRY RUN] API connection failed")
        sys.exit(0)

    result = update_daily_iv(symbols, store)

    elapsed = (datetime.now() - start).total_seconds()
    logger.info(
        "=== IV Update Complete: %d/%d success in %.1fs ===",
        result["success_count"], result["total"], elapsed,
    )

    if result["errors"]:
        logger.warning("Errors: %s", result["errors"])

    # Exit with error code if too many failures
    fail_rate = result["fail_count"] / max(result["total"], 1)
    if fail_rate > 0.5:
        logger.error(
            "High failure rate: %.0f%% — check API key/credits",
            fail_rate * 100,
        )
        sys.exit(1)


if __name__ == "__main__":
    main()
