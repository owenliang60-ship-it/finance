#!/usr/bin/env python3
"""
社交情感历史数据回填脚本

从 Adanos API 拉取最多 90 天历史数据，写入 market.db social_sentiment 表。
支持断点续传（跳过已有数据的 symbol）、dry-run、指定 symbols。

用法:
    python3 scripts/backfill_social.py                          # 全量回填 90 天
    python3 scripts/backfill_social.py --days 30                # 回填 30 天
    python3 scripts/backfill_social.py --symbols AAPL MSFT      # 指定 symbols
    python3 scripts/backfill_social.py --dry-run                # 预览，不写入
    python3 scripts/backfill_social.py --skip-existing          # 跳过已有 >=60 天数据的 symbol

约束:
    - Adanos Hobby 版最多 90 天回看
    - 每次 API 调用间隔 2 秒
    - 每个 symbol 需 2 次调用 (reddit + x)
"""
import argparse
import logging
import sys
import time
from pathlib import Path

# Ensure project root is in path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from src.data.adanos_client import adanos_client
from src.data.market_store import get_store

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

SOURCES = ("reddit", "x")
MAX_DAYS = 90


def get_existing_day_counts(store, symbols):
    """Check how many days of data each symbol already has.

    Returns dict {symbol: day_count}.
    """
    counts = {}
    for sym in symbols:
        rows = store.get_social_sentiment(sym, limit=200)
        # Count unique dates
        dates = set()
        for r in rows:
            dates.add(r["date"])
        counts[sym] = len(dates)
    return counts


def load_symbols():
    """Load symbols from universe.json."""
    import json
    pool_path = project_root / "data" / "pool" / "universe.json"
    with open(pool_path) as f:
        pool = json.load(f)
    return [s["symbol"] for s in pool]


def main():
    parser = argparse.ArgumentParser(description="回填 Adanos 社交情感历史数据")
    parser.add_argument("--days", type=int, default=MAX_DAYS,
                        help="回看天数 (max 90, default: 90)")
    parser.add_argument("--symbols", nargs="+", default=None,
                        help="指定 symbols (默认全池)")
    parser.add_argument("--skip-existing", action="store_true",
                        help="跳过已有 >=60 天数据的 symbol")
    parser.add_argument("--dry-run", action="store_true",
                        help="预览模式，不写入数据库")
    args = parser.parse_args()

    days = min(args.days, MAX_DAYS)
    symbols = args.symbols or load_symbols()
    store = get_store()

    logger.info("=== 社交情感回填 ===")
    logger.info("Symbols: %d, Days: %d, Dry-run: %s", len(symbols), days, args.dry_run)

    # Check existing data
    existing = get_existing_day_counts(store, symbols)
    to_backfill = []
    for sym in symbols:
        existing_days = existing.get(sym, 0)
        if args.skip_existing and existing_days >= 60:
            logger.info("SKIP %s: already has %d days", sym, existing_days)
            continue
        to_backfill.append(sym)

    total_calls = len(to_backfill) * len(SOURCES)
    est_minutes = total_calls * 2 / 60
    logger.info("To backfill: %d symbols, ~%d API calls, ~%.0f min",
                len(to_backfill), total_calls, est_minutes)

    if args.dry_run:
        logger.info("[DRY-RUN] Would backfill these symbols:")
        for sym in to_backfill:
            logger.info("  %s (existing: %d days)", sym, existing.get(sym, 0))
        return

    # Execute backfill
    success = 0
    failed = []
    total_rows = 0
    start_time = time.time()

    for i, sym in enumerate(to_backfill, 1):
        sym_rows = 0
        sym_ok = True
        for source in SOURCES:
            try:
                rows = adanos_client.get_sentiment_rows(sym, source=source, days=days)
                if rows:
                    store.upsert_social_sentiment(sym, rows)
                    sym_rows += len(rows)
                else:
                    logger.debug("%s/%s: no data", sym, source)
            except Exception as e:
                sym_ok = False
                logger.warning("%s/%s failed: %s", sym, source, e)

        if sym_ok:
            success += 1
            total_rows += sym_rows

        status = chr(10003) if sym_ok else chr(10007)
        elapsed = time.time() - start_time
        eta = (elapsed / i) * (len(to_backfill) - i) if i > 0 else 0
        logger.info("[%d/%d] %s %s: %d rows (ETA: %.0fs)",
                    i, len(to_backfill), status, sym, sym_rows, eta)

        if not sym_ok:
            failed.append(sym)

    elapsed = time.time() - start_time
    logger.info("=== 回填完成 ===")
    logger.info("成功: %d/%d, 总行数: %d, 耗时: %.0fs",
                success, len(to_backfill), total_rows, elapsed)
    if failed:
        logger.warning("失败: %s", failed)


if __name__ == "__main__":
    main()
