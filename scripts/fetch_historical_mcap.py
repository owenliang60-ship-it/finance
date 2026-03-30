#!/usr/bin/env python3
"""
批量拉取历史市值数据 → market.db historical_market_cap 表

用法:
    python3 scripts/fetch_historical_mcap.py                      # 拉取 extended universe 全部
    python3 scripts/fetch_historical_mcap.py --symbols AAPL MSFT  # 指定
    python3 scripts/fetch_historical_mcap.py --years 3            # 3年而非默认5年
    python3 scripts/fetch_historical_mcap.py --skip-existing      # 跳过已有数据的 symbol

完成后输出覆盖率报告。
"""
import argparse
import logging
import sys
from datetime import datetime, timedelta
from pathlib import Path

_PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(_PROJECT_ROOT))

from src.data.fmp_client import FMPClient
from src.data.market_store import get_store
from src.data.extended_universe_manager import get_extended_symbols
from src.data.pool_manager import get_symbols as get_pool_symbols

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)


def fetch_all(symbols, years=5, skip_existing=False):
    client = FMPClient()
    store = get_store()

    to_date = datetime.now().strftime("%Y-%m-%d")
    from_date = (datetime.now() - timedelta(days=years * 365)).strftime("%Y-%m-%d")

    # 如果 skip_existing，查已有 symbol（要求行数 >= 年数×200×0.8 才算充分）
    existing = set()
    if skip_existing:
        min_rows = int(years * 200 * 0.8)
        conn = store._get_conn()
        rows = conn.execute(
            "SELECT symbol, COUNT(*) as cnt FROM historical_market_cap GROUP BY symbol"
        ).fetchall()
        for r in rows:
            if r[1] >= min_rows:
                existing.add(r[0])
            else:
                logger.info(f"  {r[0]}: 仅 {r[1]} 行 (需 {min_rows})，将重新拉取")
        logger.info(f"已有 {len(existing)} symbols（>= {min_rows} rows），将跳过")

    total = len(symbols)
    success = 0
    skipped = 0
    failed = []

    for i, sym in enumerate(symbols, 1):
        if sym in existing:
            skipped += 1
            continue

        logger.info(f"[{i}/{total}] {sym}")
        try:
            rows = client.get_historical_market_cap(sym, from_date, to_date)
            if rows:
                store.upsert_historical_market_cap(sym, rows)
                success += 1
                logger.info(f"  ✓ {len(rows)} rows")
            else:
                failed.append(sym)
                logger.warning(f"  ✗ 无数据")
        except Exception as e:
            failed.append(sym)
            logger.error(f"  ✗ {e}")

    # ── 覆盖率报告 ──
    coverage = (success + skipped) / total * 100 if total > 0 else 0
    logger.info(f"\n{'='*50}")
    logger.info(f"覆盖率报告:")
    logger.info(f"  总计: {total} symbols")
    logger.info(f"  成功: {success}, 跳过(已有): {skipped}, 失败: {len(failed)}")
    logger.info(f"  覆盖率: {coverage:.1f}%  {'✓ PASS (≥90%)' if coverage >= 90 else '✗ FAIL (<90%)'}")
    if failed:
        logger.info(f"  失败列表: {failed}")
    logger.info(f"{'='*50}")

    return {"success": success, "skipped": skipped, "failed": failed, "coverage": coverage}


def main():
    parser = argparse.ArgumentParser(description="批量拉取历史市值")
    parser.add_argument("--symbols", nargs="+", help="指定 symbols")
    parser.add_argument("--universe", choices=["pool", "extended"],
                        default="extended", help="股票池")
    parser.add_argument("--years", type=int, default=5, help="回溯年数")
    parser.add_argument("--skip-existing", action="store_true",
                        help="跳过已有数据的 symbol")
    args = parser.parse_args()

    if args.symbols:
        symbols = args.symbols
    elif args.universe == "pool":
        symbols = get_pool_symbols()
    else:
        symbols = get_extended_symbols()

    logger.info(f"目标: {len(symbols)} symbols, {args.years} 年, "
                f"{'跳过已有' if args.skip_existing else '全量'}")
    fetch_all(symbols, years=args.years, skip_existing=args.skip_existing)


if __name__ == "__main__":
    main()
