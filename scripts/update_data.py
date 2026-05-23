"""
数据更新统一入口
用法:
    python scripts/update_data.py --all          # 更新所有数据
    python scripts/update_data.py --pool         # 只更新股票池
    python scripts/update_data.py --price        # 只更新量价数据
    python scripts/update_data.py --fundamental  # 只更新基本面数据
    python scripts/update_data.py --price --symbols AAPL,NVDA  # 指定股票
    python scripts/update_data.py --check        # 仅运行健康检查
"""
import argparse
import sys
from pathlib import Path
from datetime import datetime, timezone

# 添加项目根目录到 path
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.data.pool_manager import refresh_universe, get_symbols, print_universe_summary
from src.data.price_fetcher import update_all_prices
from src.data.fundamental_fetcher import update_all_fundamentals
from config.settings import ADANOS_REQUEST_DAYS, ADANOS_TRENDING_LIMIT


def _resolve_target_symbols(scope: str, symbols):
    """Resolve target symbols for --forward-estimates based on scope.

    Args:
        scope: "core" / "extended" / "all"
        symbols: explicit symbol list (overrides scope) or None/empty

    Returns:
        List of symbols. scope='all' returns deduped + sorted union; 'core' /
        'extended' return the source list as-is (no dedup or normalization);
        explicit ``symbols`` arg is returned as a shallow copy unchanged.

    Raises:
        ValueError: scope not in {"core","extended","all"} when symbols is empty
            (validation is bypassed when explicit symbols are supplied).
    """
    if symbols:
        return list(symbols)

    if scope == "core":
        from src.data.pool_manager import get_symbols
        return get_symbols()

    if scope == "extended":
        from src.data.extended_universe_manager import get_extended_only_symbols
        return get_extended_only_symbols()

    if scope == "all":
        from src.data.pool_manager import get_symbols
        from src.data.extended_universe_manager import get_extended_only_symbols
        return sorted(set(get_symbols()) | set(get_extended_only_symbols()))

    raise ValueError(f"unknown scope={scope!r} (expected core/extended/all)")


def main():
    parser = argparse.ArgumentParser(description="Finance 数据更新")
    parser.add_argument("--all", action="store_true", help="更新所有数据")
    parser.add_argument("--pool", action="store_true", help="更新股票池")
    parser.add_argument("--price", action="store_true", help="更新量价数据")
    parser.add_argument("--fundamental", action="store_true", help="更新基本面数据")
    parser.add_argument("--symbols", type=str, help="指定股票代码，逗号分隔")
    parser.add_argument("--force", action="store_true", help="强制全量更新")
    parser.add_argument("--correlation", action="store_true", help="计算相关性矩阵")
    parser.add_argument("--forward-estimates", action="store_true",
                        help="更新前瞻预期数据 (yfinance)")
    parser.add_argument("--social-sentiment", action="store_true",
                        help="更新社交情感数据 (Adanos: Reddit + X)")
    parser.add_argument("--extended-prices", action="store_true",
                        help="更新扩展池价格数据 (yfinance, $10B+ stocks)")
    parser.add_argument(
        "--scope",
        choices=["core", "extended", "all"],
        default="core",
        help="Symbol scope for --forward-estimates: core=pool 156 (default), "
             "extended=$10B+ ex-pool ~819, all=union ~949 (post-A1)",
    )
    parser.add_argument("--check", action="store_true", help="仅运行数据健康检查")

    args = parser.parse_args()

    # --check 模式: 仅运行健康检查
    if args.check:
        from src.data.data_health import health_check
        report = health_check(verbose=True)
        sys.exit(0 if report.level != "FAIL" else 1)

    # 如果没有指定任何选项，显示帮助
    if not any([args.all, args.pool, args.price, args.fundamental,
                args.forward_estimates, args.social_sentiment,
                args.extended_prices, args.correlation]):
        parser.print_help()
        return

    print(f"\n{'='*60}")
    print(f"Valuation Agent 数据更新")
    print(f"时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"{'='*60}\n")

    # 解析指定的股票
    symbols = None
    if args.symbols:
        symbols = [s.strip().upper() for s in args.symbols.split(",")]
        print(f"指定股票: {symbols}\n")

    # 更新股票池
    if args.all or args.pool:
        print("=" * 40)
        print("Step 1: 更新股票池")
        print("=" * 40)
        stocks, entered, exited = refresh_universe()
        if entered:
            print(f"\n✨ 新进入: {entered}")
        if exited:
            print(f"\n👋 退出: {exited}")
        print_universe_summary()
        print()

    # 更新量价数据
    if args.all or args.price:
        print("=" * 40)
        print("Step 2: 更新量价数据 (含基准: SPY, QQQ)")
        print("=" * 40)
        target_symbols = symbols or get_symbols()
        result = update_all_prices(target_symbols, force_full=args.force)
        print(f"\n✅ 成功: {len(result['success'])}")
        if result['failed']:
            print(f"❌ 失败: {result['failed']}")
        print()

    # 更新基本面数据
    if args.all or args.fundamental:
        print("=" * 40)
        print("Step 3: 更新基本面数据")
        print("=" * 40)
        target_symbols = symbols or get_symbols()
        update_all_fundamentals(target_symbols)

        # Pre-compute metrics in market.db
        try:
            from src.data.metrics_calculator import compute_all_metrics
            print("\n--- 预计算 metrics ---")
            result = compute_all_metrics(target_symbols)
            print(f"Metrics computed for {len(result)} symbols")
        except Exception as e:
            import traceback
            print(f"ERROR: metrics computation failed: {e}")
            traceback.print_exc()
        print()

    # 更新前瞻预期数据
    if args.all or args.forward_estimates:
        print("=" * 40)
        print("Step 3b: 更新前瞻预期数据 (yfinance)")
        print("=" * 40)
        import time
        from src.data.yfinance_client import yfinance_client
        from src.data.market_store import get_store

        store = get_store()
        target_symbols = _resolve_target_symbols(args.scope, symbols)
        print(f"Scope: {args.scope}, target {len(target_symbols)} symbols")
        success = 0
        failed = []

        for sym in target_symbols:
            try:
                estimates, metadata = yfinance_client.get_forward_estimates(sym)
                if estimates:
                    store.upsert_forward_estimates(sym, estimates)
                if metadata:
                    store.upsert_forward_metadata(sym, [metadata])
                success += 1
                print(f"  ✓ {sym}: {len(estimates)} periods")
            except Exception as e:
                failed.append(sym)
                print(f"  ✗ {sym}: {e}")
            time.sleep(1)  # polite to Yahoo

        print(f"\n✅ 成功: {success}")
        if failed:
            print(f"❌ 失败: {failed}")
        print()

    # 更新社交情感数据
    if args.all or args.social_sentiment:
        print("=" * 40)
        print("Step 3c: 更新社交情感数据 (Adanos: Reddit + X)")
        print("=" * 40)
        from src.data.adanos_client import adanos_client
        from src.data.market_store import get_store

        store = get_store()
        target_symbols = symbols or get_symbols()
        market_date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        success = 0
        failed = []

        for source in ("reddit", "x"):
            try:
                row = adanos_client.get_market_sentiment_row(
                    source=source,
                    days=ADANOS_REQUEST_DAYS,
                )
                if row is None:
                    print("  {} market {}/market-sentiment: request failed".format(chr(10007), source))
                else:
                    count = store.upsert_market_sentiment([row])
                    print("  {} market {}/market-sentiment: {} row".format(chr(10003), source, count))
            except Exception as e:
                print("  {} market {}/market-sentiment: {}".format(chr(10007), source, e))

            try:
                rows = adanos_client.get_trending_rows(
                    source=source,
                    days=ADANOS_REQUEST_DAYS,
                    limit=ADANOS_TRENDING_LIMIT,
                )
                if rows is None:
                    print("  {} market {}/trending: request failed".format(chr(10007), source))
                else:
                    count = store.upsert_social_trending(market_date, source, rows)
                    print("  {} market {}/trending: {} rows".format(chr(10003), source, count))
            except Exception as e:
                print("  {} market {}/trending: {}".format(chr(10007), source, e))

            try:
                rows = adanos_client.get_trending_sectors_rows(
                    source=source,
                    days=ADANOS_REQUEST_DAYS,
                    limit=ADANOS_TRENDING_LIMIT,
                )
                if rows is None:
                    print("  {} market {}/trending/sectors: request failed".format(chr(10007), source))
                else:
                    count = store.upsert_social_trending_sectors(market_date, source, rows)
                    print("  {} market {}/trending/sectors: {} rows".format(chr(10003), source, count))
            except Exception as e:
                print("  {} market {}/trending/sectors: {}".format(chr(10007), source, e))

        for sym in target_symbols:
            sym_ok = True
            for source in ("reddit", "x"):
                try:
                    rows = adanos_client.get_sentiment_rows(sym, source=source)
                    if rows:
                        store.upsert_social_sentiment(sym, rows)
                        print("  {} {}: {} days".format(
                            chr(10003), sym, len(rows)), end="")
                    else:
                        print("  - {}/{}: no data".format(sym, source), end="")
                except Exception as e:
                    sym_ok = False
                    print("  {} {}/{}: {}".format(chr(10007), sym, source, e), end="")
            print()  # newline after both sources
            if sym_ok:
                success += 1
            else:
                failed.append(sym)

        print("\n{} 成功: {}".format(chr(9989), success))
        if failed:
            print("{} 失败: {}".format(chr(10060), failed))
        print()

    # 更新扩展池价格数据
    if args.all or args.extended_prices:
        print("=" * 40)
        print("Step 3d: 更新扩展池价格 (yfinance, $10B+ stocks)")
        print("=" * 40)
        from src.data.extended_price_fetcher import update_extended_prices
        result = update_extended_prices(full_backfill=args.force, symbols=symbols)
        print(
            "\n%s 成功: %d/%d, %d rows upserted"
            % (chr(9989), result["success"], result["total"], result["rows_inserted"])
        )
        if result["failed"]:
            print("%s 失败: %s" % (chr(10060), result["failed"][:20]))
        print()

    # 计算相关性矩阵
    if args.all or args.correlation:
        print("=" * 40)
        print("Step 4: 计算相关性矩阵")
        print("=" * 40)
        from src.analysis.correlation import get_correlation_matrix
        corr_symbols = symbols or get_symbols()
        matrix = get_correlation_matrix(corr_symbols, use_cache=False)
        print(f"\n✅ 相关性矩阵: {len(matrix)} 只股票")
        print()

    # 更新后健康检查
    print("=" * 40)
    print("Final: 数据健康检查")
    print("=" * 40)
    from src.data.data_health import health_check
    report = health_check(verbose=True)
    print()

    print(f"{'='*60}")
    print("数据更新完成!")
    print(f"{'='*60}\n")

    if report.level == "FAIL":
        sys.exit(1)


if __name__ == "__main__":
    main()
