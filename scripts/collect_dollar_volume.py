#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
每日采集 Dollar Volume Top 200

用法:
    python scripts/collect_dollar_volume.py              # 采集今天
    python scripts/collect_dollar_volume.py --date 2026-02-04  # 指定日期
    python scripts/collect_dollar_volume.py --status      # 查看最近数据
"""

import sys
import time
import argparse
import logging
import math
from datetime import datetime, timedelta
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from config.settings import DOLLAR_VOLUME_DB, DOLLAR_VOLUME_TOP_N, DOLLAR_VOLUME_REPORT_N, DOLLAR_VOLUME_LOOKBACK
from src.data.fmp_client import FMPClient
from src.data.dollar_volume import (
    init_db, store_daily_rankings, get_rankings, get_latest_date,
    detect_new_faces, log_collection, get_collection_log, is_collected,
    collection_minimum, rankings_integrity_error, snapshot_integrity_error,
    get_history_warning, get_previous_day_ranks,
    latest_quality_reference, make_quality_evidence, leader_coverage_error, get_all_dates,
)

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)


def fetch_recent_delisted_symbols(
    client: FMPClient,
    as_of_date: str,
    lookback_days: int = 120,
    max_pages: int = 3,
    page_size: int = 200,
):
    """拉取近期退市名单，过滤 screener 里残留的脏活跃标记。"""
    target_date = datetime.strptime(as_of_date, "%Y-%m-%d").date()
    cutoff_date = target_date - timedelta(days=lookback_days)

    delisted_symbols = set()
    api_calls = 0

    for page in range(max_pages):
        rows = client.get_delisted_companies(page=page, limit=page_size)
        api_calls += 1

        if not rows:
            break

        reached_cutoff = False
        for row in rows:
            symbol = row.get("symbol")
            raw_date = row.get("delistedDate")
            if not symbol or not raw_date:
                continue

            try:
                delisted_date = datetime.strptime(raw_date, "%Y-%m-%d").date()
            except ValueError:
                continue

            if delisted_date > target_date:
                continue
            if delisted_date < cutoff_date:
                reached_cutoff = True
                continue

            delisted_symbols.add(symbol)

        if len(rows) < page_size or reached_cutoff:
            break

    return delisted_symbols, api_calls


def fetch_all_stocks(client: FMPClient, as_of_date: str = None) -> list:
    """分页拉取全市场股票，返回去重列表。"""
    all_stocks = {}
    api_calls = 0

    # 分页拉取（每页 1000，直到返回 < 1000）
    offset = 0
    while True:
        page = client.get_screener_page(offset=offset, limit=1000)
        api_calls += 1
        logger.info(f"  Page offset={offset}: got {len(page)} stocks")

        if not page:
            break

        previous_count = len(all_stocks)
        for s in page:
            symbol = s.get("symbol")
            if symbol:
                all_stocks[symbol] = s

        if len(page) < 1000:
            break
        if len(all_stocks) == previous_count or offset >= 19000:
            raise ValueError("screener pagination did not terminate safely")
        offset += 1000

    # 补充一次高量小盘股（可能被前面分页遗漏）
    extra = client.get_screener_page(offset=0, limit=1000, volume_more_than=500000)
    api_calls += 1
    logger.info(f"  Extra high-volume pass: got {len(extra)} stocks")
    for s in extra:
        symbol = s.get("symbol")
        if symbol and symbol not in all_stocks:
            all_stocks[symbol] = s

    if as_of_date:
        delisted_symbols, delisted_calls = fetch_recent_delisted_symbols(
            client, as_of_date
        )
        api_calls += delisted_calls

        removed = 0
        for symbol in delisted_symbols:
            if all_stocks.pop(symbol, None) is not None:
                removed += 1

        if removed:
            logger.info(
                "  Filtered %d recently delisted symbols as of %s",
                removed,
                as_of_date,
            )

    return list(all_stocks.values()), api_calls


def compute_rankings(stocks: list, top_n: int = DOLLAR_VOLUME_TOP_N) -> list:
    """计算 dollar volume 并排序取 Top N"""
    valid = []
    for s in stocks:
        price = s.get("price")
        volume = s.get("volume")

        if all(isinstance(v, (float, int)) and not isinstance(v, bool)
               and math.isfinite(v) and v > 0 for v in (price, volume)):
            dv = price * volume
            valid.append({
                "symbol": s.get("symbol", ""),
                "company_name": s.get("companyName", ""),
                "price": round(price, 2),
                "volume": int(volume),
                "dollar_volume": round(dv, 2),
                "market_cap": s.get("marketCap"),
                "sector": s.get("sector", ""),
            })

    # 按 dollar volume 降序排序
    valid.sort(key=lambda x: x["dollar_volume"], reverse=True)

    # 取 Top N，加上排名
    rankings = []
    for i, item in enumerate(valid[:top_n], 1):
        item["rank"] = i
        rankings.append(item)

    return rankings


def collect_daily(date: str = None, force: bool = False,
                  db_path: Path = DOLLAR_VOLUME_DB) -> dict:
    """
    执行一次每日采集
    返回采集结果摘要（供 daily_scan.py 使用）
    """
    if date is None:
        date = datetime.now().strftime("%Y-%m-%d")

    init_db(db_path=db_path)

    def unavailable(reason):
        logger.error("Dollar Volume unavailable date=%s: %s", date, reason)
        # Keep good snapshots and their provenance on a failed --force. Record
        # other failures so tomorrow cannot silently compare across the gap.
        if not is_collected(date, db_path=db_path):
            log_collection(date, {"status": "unavailable", "stored": 0}, db_path=db_path)
        return {"date": date, "status": "unavailable", "rankings": [], "new_faces": [],
                "warning": "数据不可用：" + reason}

    def history_warning():
        messages = [get_history_warning(date, db_path=db_path)]
        if get_previous_day_ranks(date, db_path=db_path) is None:
            messages.append("上一采集日数据不完整或缺失，暂停排名变化")
        return "；".join(m for m in messages if m)

    # 检查是否已采集
    if not force and is_collected(date, db_path=db_path):
        error = snapshot_integrity_error(date, db_path)
        if error:
            return unavailable("缓存完整性检查未通过：" + error)
        logger.info(f"{date} already collected, skipping (use --force to override)")
        return {
            "date": date,
            "status": "skipped",
            "rankings": get_rankings(date, DOLLAR_VOLUME_REPORT_N, db_path=db_path),
            "new_faces": detect_new_faces(date, DOLLAR_VOLUME_LOOKBACK, DOLLAR_VOLUME_REPORT_N, db_path=db_path),
            "history_warning": history_warning(),
        }

    start = time.time()

    # 拉取全市场
    client = FMPClient()
    logger.info(f"Fetching all US stocks for {date}...")
    minimum = collection_minimum(date, db_path)
    reference = latest_quality_reference(date, db_path=db_path)
    if reference is None and any(d < date for d in get_all_dates(db_path)):
        return unavailable("历史快照均不可作为可信参照，需核验后恢复")
    api_calls = 0
    for attempt in range(2):
        try:
            stocks, calls = fetch_all_stocks(client, as_of_date=date)
            api_calls += calls
            valid = compute_rankings(stocks, len(stocks))
            rankings = valid[:DOLLAR_VOLUME_TOP_N]
            error = rankings_integrity_error(rankings)
            if len(stocks) < minimum:
                error = f"候选数量 {len(stocks)} 低于完整性门槛 {minimum}"
            valid_symbols = {r["symbol"] for r in valid}
            error = error or leader_coverage_error(valid_symbols, reference)
        except Exception:
            # No provider URL, body or credentials in the public failure reason.
            error = "采集请求或响应解析失败"
        if not error:
            break
        logger.warning("DV integrity attempt=%d/2 date=%s: %s", attempt+1, date, error)
        if attempt == 1:
            return unavailable(error)
        time.sleep(2)
    logger.info("DV integrity passed: scanned=%d minimum=%d stored=%d", len(stocks), minimum, len(rankings))

    elapsed = time.time() - start

    # Rankings, collection metadata and acceptance evidence commit together.
    stats = {
        "total_scanned": len(stocks),
        "stored": len(rankings),
        "api_calls": api_calls,
        "elapsed": round(elapsed, 1),
        "status": "ok",
    }
    evidence = make_quality_evidence(rankings, len(stocks), valid_symbols, reference)
    store_daily_rankings(date, rankings, db_path=db_path, stats=stats, evidence=evidence)

    # 检测新面孔
    new_faces = detect_new_faces(date, DOLLAR_VOLUME_LOOKBACK, DOLLAR_VOLUME_REPORT_N, db_path=db_path)

    result = {
        "date": date,
        "status": "ok",
        "total_scanned": len(stocks),
        "stored": len(rankings),
        "api_calls": api_calls,
        "elapsed": round(elapsed, 1),
        "rankings": get_rankings(date, DOLLAR_VOLUME_REPORT_N, db_path=db_path),
        "new_faces": new_faces,
        "history_warning": history_warning(),
    }

    logger.info(
        f"Collection done: {len(stocks)} scanned, "
        f"{len(rankings)} stored, {len(new_faces)} new faces, "
        f"{api_calls} API calls, {elapsed:.1f}s"
    )

    return result


def show_status():
    """显示最近采集状态"""
    init_db()

    latest = get_latest_date()
    print(f"\nLatest data date: {latest or 'None'}")

    logs = get_collection_log(limit=5)
    if logs:
        print(f"\nRecent collections:")
        print(f"  {'Date':<12} {'Scanned':>8} {'Stored':>7} {'APIs':>5} {'Time':>6} {'Status'}")
        print(f"  {'-'*52}")
        for log in logs:
            print(f"  {log['date']:<12} {log['total_scanned']:>8} "
                  f"{log['stored']:>7} {log['api_calls']:>5} "
                  f"{log['elapsed']:>5.1f}s {log['status']}")

    if latest:
        top10 = get_rankings(latest, 10)
        if top10:
            print(f"\nTop 10 on {latest}:")
            print(f"  {'#':>3} {'Symbol':<8} {'$Vol':>10} {'Price':>8}")
            print(f"  {'-'*32}")
            for r in top10:
                dv = r["dollar_volume"]
                if dv >= 1e9:
                    dv_str = f"${dv/1e9:.1f}B"
                else:
                    dv_str = f"${dv/1e6:.0f}M"
                print(f"  {r['rank']:>3} {r['symbol']:<8} {dv_str:>10} ${r['price']:>7.2f}")


def main():
    parser = argparse.ArgumentParser(description="Collect Dollar Volume Top 200")
    parser.add_argument("--date", type=str, help="Date to collect (YYYY-MM-DD)")
    parser.add_argument("--force", action="store_true", help="Force re-collect")
    parser.add_argument("--status", action="store_true", help="Show recent status")
    args = parser.parse_args()

    if args.status:
        show_status()
        return

    result = collect_daily(date=args.date, force=args.force)
    print(f"\nResult: {result['status']}, stored={result.get('stored', 0)}, "
          f"new_faces={len(result.get('new_faces', []))}")
    if result["status"] == "unavailable":
        sys.exit(1)


if __name__ == "__main__":
    main()
