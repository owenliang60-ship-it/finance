"""
数据更新统一入口
用法:
    python scripts/update_data.py --all          # 更新所有数据
    python scripts/update_data.py --pool         # 只更新股票池
    python scripts/update_data.py --price        # 只更新量价数据（FMP tier + yfinance batch）
    python scripts/update_data.py --fundamental  # 只更新基本面数据
    python scripts/update_data.py --price --symbols AAPL,NVDA  # 指定股票
    python scripts/update_data.py --check        # 仅运行健康检查
    python scripts/update_data.py --fundamental --scope base    # 手动/维修，current_base_universe
    python scripts/update_data.py --fundamental --scope events  # 财报窗口增量 (R6)

行为变更 (T11, R6): `--fundamental` 不再直连 `update_all_fundamentals`（该函数
仍保留，仅不再被本脚本调用），一律经 T8 共享采集内核
(`src.data.fundamental_collector.collect_fundamentals_for_symbol`) 逐票写入。
`--scope core`（默认）目标不变（`pool_manager.get_symbols()`），但内核路径会
额外产出 `fundamental_vintage` 历史 + `coverage_status` 覆盖记录 —— 这是本次
新增的副作用，此前的直连路径不写这两张表。`--scope base`/`events` 是 T11 新
增的两个 scope：`base` 仅供手动/维修用（cron 不用它，P1-5），`events` 由
`src.data.fundamental_events.detect_earnings_targets` 驱动，0 个目标是正常
结果（exit 0），不是失败。

行为变更 (矩阵 #6, Boss 拍板 P1): `--price` 的 FMP 腿只覆盖 overlay tier
（holdings ∪ watchlist ∪ benchmarks，`price_fetcher.get_fmp_price_targets()`），
基础池其余部分由同一步内新增的 yfinance batch 腿覆盖；`daily_price` 表 schema
不变，两腿合计覆盖率 ≥ 迁移前。显式 `--symbols` 只跑 FMP 腿（调用方点名了目标）。
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
# NOTE: update_all_fundamentals stays importable (other callers may still use
# it), but --fundamental no longer calls it (P1-3) — see run_fundamental_update.
from src.data.fundamental_fetcher import update_all_fundamentals
from src.data.fundamental_collector import collect_fundamentals_for_symbol
from config.settings import ADANOS_REQUEST_DAYS, ADANOS_TRENDING_LIMIT

FUNDAMENTAL_SCOPE_CHOICES = ("core", "extended", "all", "base", "events")


def _resolve_target_symbols(scope: str, symbols, *, store=None, as_of=None):
    """Resolve target symbols for --forward-estimates / --fundamental based on scope.

    Args:
        scope: "core" / "extended" / "all" / "base" / "events"
        symbols: explicit symbol list (overrides scope) or None/empty
        store: MarketStore, only consulted by "base"/"events" (lazily opens
            the default store via `get_store()` when omitted).
        as_of: "YYYY-MM-DD", only consulted by "events" (defaults to today
            UTC when omitted).

    Returns:
        List of symbols. scope='all' returns deduped + sorted union; 'core' /
        'extended' return the source list as-is (no dedup or normalization);
        'base' returns `current_base_universe()` (active Extended membership
        ∩ SM eligible — manual/repair use only, T11 P1-5: cron never passes
        this scope); 'events' returns `detect_earnings_targets()`; explicit
        ``symbols`` arg is returned as a shallow copy unchanged.

    Raises:
        ValueError: scope not recognized when symbols is empty (validation is
            bypassed when explicit symbols are supplied).
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

    if scope == "base":
        from src.data.universe_resolver import current_base_universe
        return current_base_universe(store=store)

    if scope == "events":
        from src.data.fundamental_events import detect_earnings_targets
        if store is None:
            from src.data.market_store import get_store
            store = get_store()
        effective_as_of = as_of or datetime.now(timezone.utc).strftime("%Y-%m-%d")
        return detect_earnings_targets(store, as_of=effective_as_of)

    raise ValueError(
        f"unknown scope={scope!r} (expected one of {FUNDAMENTAL_SCOPE_CHOICES})")


def _should_run_price_yfinance_leg(args, symbols) -> bool:
    """`--price` 这一步内部是否要补跑 yfinance 腿（矩阵 #6）。

    不跑的两种情况：
      - 显式 `--symbols`：调用方点名了目标，不替他扩面。
      - `--all` / `--extended-prices` 同时在跑：Step 3d 会用同一批目标
        (`get_yfinance_price_targets()`) 再跑一次，两处都跑等于把 ~950 只的
        yfinance batch 白抓两遍。
    """
    if symbols:
        return False
    return not (args.all or args.extended_prices)


def _yfinance_price_leg_targets(fmp_targets, *, store=None):
    """基础池里 FMP tier 没覆盖到的部分，交给 yfinance batch（矩阵 #6, P1）。

    Args:
        fmp_targets: 本次 FMP 腿实际抓取的名单（`get_fmp_price_targets()` 或
            显式 --symbols）。
        store: MarketStore，省略时 resolver 自行打开默认 store。

    Returns:
        排序去重后的 yfinance 目标列表。基础池尚不可用（bootstrap 之前）时返回
        `[]` —— 那个窗口里 FMP tier 本身还停在 legacy Core 名单上，当天覆盖率
        与现状一致，不该再补一条无源可跑的腿。
    """
    from src.data.universe_resolver import current_base_universe

    try:
        base = current_base_universe(store=store)
    except Exception as e:
        print(f"  yfinance 腿跳过（基础池不可用: {e}）")
        return []

    covered = {s.upper() for s in fmp_targets}
    return sorted({s.upper() for s in base} - covered)


def _resolve_correlation_symbols(*, wide: bool = False, market_store=None,
                                 company_store=None):
    """`--correlation` 的目标名单（矩阵 #9）。

    默认 = overlay tier（holdings ∪ watchlist ∪ benchmarks，~50 只）。相关性矩阵
    是 O(n²)，按全池跑出来的东西既慢也没人读。`--wide` 显式换成 resolver
    eligible 全池。

    默认路径不碰 `extended_membership`，bootstrap 之前照常可用；`--wide` 是显式
    开关，基础池不可用时 fail-loud 冒泡 —— 悄悄缩回 overlay tier 会把一个坏掉的
    universe 藏起来。
    """
    if wide:
        from src.data.universe_resolver import current_base_universe
        return current_base_universe(store=market_store)

    from src.data.overlays import load_overlay_tier
    return load_overlay_tier(store=company_store)


def run_fundamental_update(*, scope: str, symbols=None, store=None, client=None,
                           as_of=None, limit_quarters: int = 8) -> int:
    """Drive `--fundamental` for one scope, entirely through the T8 kernel (P1-3).

    Every dependency is injectable so tests never touch the network or the
    real `market.db`; `main()` passes the production store/client.

    Target resolution is `_resolve_target_symbols` (same helper
    `--forward-estimates` uses), so "core"/"base"/"events" mean exactly what
    they mean there:
      - core:   `pool_manager.get_symbols()` — unchanged Core 209 targets.
      - base:   `current_base_universe()` — manual/repair use only (P1-5:
                cron never passes this scope).
      - events: `detect_earnings_targets()` — 0 targets is a normal outcome
                (nothing announced in the window), not a failure.

    observed_at is always a FULL UTC timestamp computed here (CONTROLLER
    RULING #11) — `as_of` (a pure date) is only ever used for the "events"
    window filter, never as the kernel's observed_at.

    Returns:
        0 on a normal completion, including the "no targets" cases. Per
        dataset failures are recorded by the kernel in `coverage_status`,
        not surfaced as a non-zero exit here (R6: the manifest/coverage
        tables are the source of truth for what failed, not the process
        exit code).
    """
    if store is None:
        from src.data.market_store import get_store
        store = get_store()
    if client is None:
        from src.data.fmp_client import fmp_client
        client = fmp_client

    targets = _resolve_target_symbols(scope, symbols, store=store, as_of=as_of)

    if not targets:
        if scope == "events":
            print("update_data --fundamental --scope events: no earnings events")
        else:
            print(f"update_data --fundamental --scope {scope}: no target symbols")
        return 0

    observed_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    print(f"Scope: {scope}, target {len(targets)} symbols")

    ok = 0
    partial = []
    for i, sym in enumerate(targets, 1):
        statuses = collect_fundamentals_for_symbol(
            sym, client=client, store=store, limit_quarters=limit_quarters,
            observed_at=observed_at)
        if all(v in ("ok", "provider_empty") for v in statuses.values()):
            ok += 1
            print(f"  [{i}/{len(targets)}] {chr(10003)} {sym}: {statuses}")
        else:
            partial.append(sym)
            print(f"  [{i}/{len(targets)}] {chr(10007)} {sym}: {statuses}")

    print(f"\n{chr(9989)} 完整: {ok}/{len(targets)}")
    if partial:
        print(f"{chr(10060)} 部分失败 (见 coverage_status): {partial}")

    return 0


def main():
    parser = argparse.ArgumentParser(description="Finance 数据更新")
    parser.add_argument("--all", action="store_true", help="更新所有数据")
    parser.add_argument("--pool", action="store_true", help="更新股票池")
    parser.add_argument("--price", action="store_true", help="更新量价数据")
    parser.add_argument("--fundamental", action="store_true", help="更新基本面数据")
    parser.add_argument("--symbols", type=str, help="指定股票代码，逗号分隔")
    parser.add_argument("--force", action="store_true", help="强制全量更新")
    parser.add_argument("--correlation", action="store_true", help="计算相关性矩阵")
    parser.add_argument("--wide", action="store_true",
                        help="--correlation: 按 resolver eligible 全池计算，"
                             "而非默认的 overlay tier（O(n²)，慎用）")
    parser.add_argument("--forward-estimates", action="store_true",
                        help="更新前瞻预期数据 (yfinance)")
    parser.add_argument("--social-sentiment", action="store_true",
                        help="更新社交情感数据 (Adanos: Reddit + X)")
    parser.add_argument("--extended-prices", action="store_true",
                        help="更新扩展池价格数据 (yfinance, $10B+ stocks)")
    parser.add_argument(
        "--scope",
        choices=list(FUNDAMENTAL_SCOPE_CHOICES),
        default="core",
        help="Symbol scope for --forward-estimates / --fundamental: "
             "core=pool (default; --fundamental's legacy target set), "
             "extended=$10B+ ex-pool, all=union, "
             "base=current_base_universe (manual/repair only, --fundamental), "
             "events=earnings-window targets (--fundamental)",
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

    # 更新量价数据（矩阵 #6 P1: FMP 只跑 overlay tier，其余走 yfinance batch）
    if args.all or args.price:
        print("=" * 40)
        print("Step 2: 更新量价数据 (FMP overlay tier + yfinance batch)")
        print("=" * 40)
        from src.data.price_fetcher import get_fmp_price_targets

        target_symbols = symbols or get_fmp_price_targets()
        print(f"FMP tier: {len(target_symbols)} symbols")
        result = update_all_prices(target_symbols, force_full=args.force)
        print(f"\n✅ FMP 成功: {len(result['success'])}")
        if result['failed']:
            print(f"❌ FMP 失败: {result['failed']}")

        if _should_run_price_yfinance_leg(args, symbols):
            yf_targets = _yfinance_price_leg_targets(target_symbols)
            if yf_targets:
                from src.data.extended_price_fetcher import update_extended_prices
                print(f"\nyfinance batch: {len(yf_targets)} symbols")
                yf_result = update_extended_prices(
                    full_backfill=args.force, symbols=yf_targets)
                print("✅ yfinance 成功: %d/%d, %d rows upserted"
                      % (yf_result["success"], yf_result["total"],
                         yf_result["rows_inserted"]))
                if yf_result["failed"]:
                    print("❌ yfinance 失败: %s" % (yf_result["failed"][:20],))
        print()

    # 更新基本面数据（全走内核 T8：额外产出 vintage + coverage — 行为新增, R6）
    if args.all or args.fundamental:
        print("=" * 40)
        print("Step 3: 更新基本面数据（内核逐票采集）")
        print("=" * 40)
        from src.data.market_store import get_store

        fundamental_store = get_store()
        as_of_today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        target_symbols = _resolve_target_symbols(
            args.scope, symbols, store=fundamental_store, as_of=as_of_today)
        run_fundamental_update(scope=args.scope, symbols=target_symbols,
                               store=fundamental_store)

        # Pre-compute metrics in market.db
        if target_symbols:
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
        corr_symbols = symbols or _resolve_correlation_symbols(wide=args.wide)
        print(f"Scope: {'eligible (--wide)' if args.wide else 'overlay tier'}, "
              f"{len(corr_symbols)} symbols")
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
