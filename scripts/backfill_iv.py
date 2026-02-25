#!/usr/bin/env python3
"""
IV 历史数据回填脚本

从 MarketData.app 拉取历史 ATM IV + 本地价格算 HV，写入 company.db iv_daily 表。
支持断点续传、credits 限额、dry-run。

用法:
    python3 scripts/backfill_iv.py --start 2024-02-25 --end 2026-02-24
    python3 scripts/backfill_iv.py --dry-run --start 2026-02-20 --end 2026-02-24
    python3 scripts/backfill_iv.py --symbols AAPL MSFT --start 2025-01-01

约束:
    - 必须在云端跑（aliyun 固定 IP，MarketData.app 单 IP 绑定）
    - 10,000 credits/天，留 500 给当日 cron，默认上限 9500
    - 断点续传：自动跳过 DB 中已有的 (symbol, date) 对
"""
import argparse
import logging
import sys
from datetime import datetime, timedelta
from pathlib import Path

# Ensure project root is in path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from config.settings import BENCHMARK_SYMBOLS, MARKETDATA_API_KEY
from terminal.company_store import get_store
from terminal.options.iv_tracker import compute_hv

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


def generate_trading_days(start_date, end_date):
    """Generate weekday dates between start and end (inclusive).

    Skips weekends. US market holidays are handled by API returning no_data.

    Args:
        start_date: Start date string 'YYYY-MM-DD'
        end_date: End date string 'YYYY-MM-DD'

    Returns:
        List of date strings in chronological order
    """
    start = datetime.strptime(start_date, "%Y-%m-%d")
    end = datetime.strptime(end_date, "%Y-%m-%d")

    days = []
    current = start
    while current <= end:
        # Monday=0, Sunday=6; skip Saturday(5) and Sunday(6)
        if current.weekday() < 5:
            days.append(current.strftime("%Y-%m-%d"))
        current += timedelta(days=1)

    return days


def get_existing_dates(store, symbol):
    """Get set of dates already in DB for a symbol.

    Args:
        store: CompanyStore instance
        symbol: Stock ticker

    Returns:
        Set of date strings that already have IV data
    """
    history = store.get_iv_history(symbol, limit=10000)
    return set(h["date"] for h in history)


def backfill(args):
    """Main backfill logic."""
    store = get_store()

    # Determine symbols
    if args.symbols:
        symbols = [s.upper() for s in args.symbols]
    else:
        companies = store.list_companies(in_pool_only=True)
        symbols = [c["symbol"] for c in companies]

        # Fallback: if in_pool is empty, sync from universe.json and retry
        if not symbols:
            logger.warning("No in_pool companies — syncing from universe.json")
            from src.data.pool_manager import sync_db_pool
            synced = sync_db_pool()
            logger.info("sync_db_pool: %d companies synced", synced)
            companies = store.list_companies(in_pool_only=True)
            symbols = [c["symbol"] for c in companies]

        for bm in BENCHMARK_SYMBOLS:
            if bm not in symbols:
                symbols.append(bm)

    if not symbols:
        logger.error("No symbols to backfill")
        sys.exit(1)

    # Generate trading days
    trading_days = generate_trading_days(args.start, args.end)
    if not trading_days:
        logger.error("No trading days in range %s to %s", args.start, args.end)
        sys.exit(1)

    total_pairs = len(symbols) * len(trading_days)
    logger.info(
        "Backfill plan: %d symbols x %d trading days = %d total pairs",
        len(symbols), len(trading_days), total_pairs,
    )
    logger.info("Symbols: %s", ", ".join(symbols[:10]) + ("..." if len(symbols) > 10 else ""))
    logger.info("Date range: %s to %s", trading_days[0], trading_days[-1])
    logger.info("Daily credit limit: %d", args.daily_limit)

    if args.dry_run:
        # Count how many would be skipped
        skip_count = 0
        for symbol in symbols:
            existing = get_existing_dates(store, symbol)
            skip_count += len(existing & set(trading_days))
        logger.info(
            "[DRY RUN] Would process %d pairs, skip %d existing, need %d API calls",
            total_pairs, skip_count, total_pairs - skip_count,
        )
        estimated_days = max(1, (total_pairs - skip_count) // args.daily_limit + 1)
        logger.info(
            "[DRY RUN] Estimated %d days to complete (at %d credits/day)",
            estimated_days, args.daily_limit,
        )
        return

    # Initialize risk-free rate cache for BS solver
    from terminal.options.risk_free_rate import refresh_risk_free_rates
    rfr_count = refresh_risk_free_rates(start_date=args.start)
    logger.info("Risk-free rates loaded: %d data points", rfr_count)

    # Initialize client
    from src.data.marketdata_client import MarketDataClient
    client = MarketDataClient()

    # Pre-load existing dates per symbol for resume
    existing_map = {}
    for symbol in symbols:
        existing_map[symbol] = get_existing_dates(store, symbol)

    # Backfill loop: iterate by date (outer) then symbol (inner)
    # This groups API calls by date for better cache behavior
    processed = 0
    skipped = 0
    success = 0
    failures = 0
    no_data_count = 0
    credits_used = 0

    start_time = datetime.now()

    for day_idx, date in enumerate(trading_days):
        for sym_idx, symbol in enumerate(symbols):
            # Check if already exists
            if date in existing_map[symbol]:
                skipped += 1
                continue

            # Check credit limit
            if credits_used >= args.daily_limit:
                logger.warning(
                    "Daily credit limit reached (%d/%d). "
                    "Stopping. Re-run to continue from checkpoint.",
                    credits_used, args.daily_limit,
                )
                _print_summary(
                    start_time, processed, skipped, success,
                    failures, no_data_count, credits_used,
                )
                return

            # Fetch historical IV
            try:
                iv = client.get_historical_atm_iv(symbol, date)
                credits_used += 1
                processed += 1

                if iv is None:
                    no_data_count += 1
                    # Still count — API was called, credit spent
                    continue

                # Compute HV as of that date
                hv = compute_hv(symbol, window=30, as_of=date)

                # Store
                store.save_iv_daily(
                    symbol=symbol,
                    date=date,
                    iv_30d=iv,
                    hv_30d=hv,
                )
                success += 1

            except Exception as e:
                failures += 1
                processed += 1
                credits_used += 1
                logger.error("Error %s/%s: %s", symbol, date, e)

            # Progress log every 100 API calls
            if processed % 100 == 0 and processed > 0:
                elapsed = (datetime.now() - start_time).total_seconds()
                rate = processed / elapsed if elapsed > 0 else 0
                remaining = args.daily_limit - credits_used
                logger.info(
                    "Progress: %d processed, %d success, %d no_data, "
                    "%d failures, %d skipped | "
                    "Credits: %d used, %d remaining | "
                    "Rate: %.1f/sec",
                    processed, success, no_data_count,
                    failures, skipped,
                    credits_used, remaining, rate,
                )

    _print_summary(
        start_time, processed, skipped, success,
        failures, no_data_count, credits_used,
    )


def _print_summary(start_time, processed, skipped, success, failures, no_data, credits):
    """Print final summary."""
    elapsed = (datetime.now() - start_time).total_seconds()
    logger.info("=" * 60)
    logger.info("Backfill Complete")
    logger.info("  Processed:  %d API calls", processed)
    logger.info("  Success:    %d", success)
    logger.info("  No data:    %d (holidays/no options)", no_data)
    logger.info("  Failures:   %d", failures)
    logger.info("  Skipped:    %d (already in DB)", skipped)
    logger.info("  Credits:    %d used", credits)
    logger.info("  Duration:   %.1f seconds (%.1f min)", elapsed, elapsed / 60)
    logger.info("=" * 60)


def main():
    parser = argparse.ArgumentParser(
        description="Backfill historical IV data from MarketData.app"
    )
    parser.add_argument(
        "--start",
        default=(datetime.now() - timedelta(days=730)).strftime("%Y-%m-%d"),
        help="Start date YYYY-MM-DD (default: 2 years ago)",
    )
    parser.add_argument(
        "--end",
        default=(datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d"),
        help="End date YYYY-MM-DD (default: yesterday)",
    )
    parser.add_argument(
        "--symbols", nargs="+",
        help="Specific symbols (default: pool + benchmarks)",
    )
    parser.add_argument(
        "--daily-limit", type=int, default=9500,
        help="Max credits to use (default: 9500, reserve 500 for daily cron)",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Show plan without making API calls",
    )
    args = parser.parse_args()

    # Validate
    if not MARKETDATA_API_KEY:
        logger.error("MARKETDATA_API_KEY not set — aborting")
        sys.exit(1)

    try:
        datetime.strptime(args.start, "%Y-%m-%d")
        datetime.strptime(args.end, "%Y-%m-%d")
    except ValueError:
        logger.error("Invalid date format. Use YYYY-MM-DD")
        sys.exit(1)

    if args.start > args.end:
        logger.error("Start date must be before end date")
        sys.exit(1)

    logger.info("=== IV Backfill Start: %s ===", datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    backfill(args)


if __name__ == "__main__":
    main()
