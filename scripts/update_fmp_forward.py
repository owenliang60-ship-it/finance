"""FMP forward EPS 数据线编排器（weekly / backfill / dry-run）。

用法:
    python scripts/update_fmp_forward.py --mode weekly [--snapshot-date YYYY-MM-DD]
    python scripts/update_fmp_forward.py --mode backfill --backfill-start 2021-01-01
    python scripts/update_fmp_forward.py --mode weekly --symbols AAPL,MU --dry-run
    python scripts/update_fmp_forward.py --mode weekly --resume --symbols AAPL,MU \\
        --snapshot-date YYYY-MM-DD

冻结语义（plan Task 6）:
    --symbols --dry-run   自由 smoke：不落库、无 manifest，但完整验证 holdings 契约
    --resume --symbols    子集修复：要求既有同日同 kind full manifest，子集 ⊆ manifest，
                          不刷新 holdings、不改 universe/target_count
    非 dry-run --symbols 且无 --resume / --resume 无 --symbols / resume+dry-run → exit 2

Spec: docs/design/2026-07-09-fmp-forward-eps-valuation-spec.md §5.3–§5.5
"""
import argparse
import json
import logging
import sys
import time
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.data.fmp_client import FMPResponseError, _sanitize_log_text
from src.data.fmp_forward_ingestion import (
    ETF_HOLDING_SOURCES,
    extract_valid_quarter_fiscal_dates,
    load_basket_configs,
    normalize_earnings,
    normalize_estimates,
    normalize_holdings,
    resolve_fmp_forward_universe,
)

logger = logging.getLogger(__name__)

CRITICAL_FAILURE_RATE = 0.20  # 严格大于才熔断（恰好 20% 放行）
DEFAULT_BACKFILL_START = "2021-01-01"
ESTIMATES_LIMIT = 100
EARNINGS_LIMIT_WEEKLY = 8
EARNINGS_LIMIT_BACKFILL = 100


@dataclass
class ForwardRunSummary:
    mode: str
    snapshot_date: str
    target_count: int
    holdings_rows: int = 0
    quarter_success: int = 0
    quarter_failed: List[str] = field(default_factory=list)
    quarter_empty: List[str] = field(default_factory=list)  # valid []，非传输错误
    annual_failed: List[str] = field(default_factory=list)
    earnings_failed: List[str] = field(default_factory=list)
    estimate_rows: int = 0
    earnings_rows: int = 0
    unmatched_earnings: int = 0
    duration_seconds: float = 0.0


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _iso_date(value: str) -> str:
    try:
        return date.fromisoformat(value).isoformat()
    except ValueError:
        raise argparse.ArgumentTypeError(f"not an ISO date: {value!r}")


def _csv_symbols(value: str) -> List[str]:
    symbols = [s.strip().upper() for s in value.split(",") if s.strip()]
    if not symbols:
        raise argparse.ArgumentTypeError("empty --symbols list")
    return symbols


def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="FMP forward EPS weekly/backfill orchestrator",
        epilog=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--mode", choices=["weekly", "backfill"], required=True)
    parser.add_argument("--snapshot-date", type=_iso_date, default=None,
                        help="default today; injectable for tests/replay")
    parser.add_argument("--backfill-start", type=_iso_date, default=None,
                        help="only valid with --mode backfill")
    parser.add_argument("--symbols", type=_csv_symbols, default=None,
                        help="explicit smoke (--dry-run) or repair subset (--resume)")
    parser.add_argument("--resume", action="store_true",
                        help="non-dry-run subset repair; requires full manifest")
    parser.add_argument("--dry-run", action="store_true",
                        help="call/normalize/report; no DB writes")
    parser.add_argument("--no-telegram", action="store_true")
    parser.add_argument("--data-root", type=Path, default=None,
                        help="derive market.db location for worktrees")
    parser.add_argument("--config-dir", type=Path, default=None,
                        help=argparse.SUPPRESS)
    args = parser.parse_args(argv)

    # 无效组合在任何 API/DB 初始化前 exit 2
    if args.backfill_start is not None and args.mode != "backfill":
        parser.error("--backfill-start is only valid with --mode backfill")
    if args.resume and args.dry_run:
        parser.error("--resume cannot be combined with --dry-run")
    if args.resume and not args.symbols:
        parser.error("--resume requires --symbols")
    if args.symbols and not (args.dry_run or args.resume):
        parser.error("non-dry-run --symbols requires --resume")

    if args.snapshot_date is None:
        args.snapshot_date = date.today().isoformat()
    if args.backfill_start is None:
        args.backfill_start = DEFAULT_BACKFILL_START
    if args.config_dir is None:
        args.config_dir = PROJECT_ROOT / "config" / "baskets"
    return args


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------

def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class _FatalRunError(Exception):
    """任何 DB 写入前的致命错误（零写入保证）。"""


def _fetch_and_normalize_holdings(client, snapshot_date, listing, groups):
    """5 个 ETF holdings 全部在内存完成抓取+规范化；任何失败在写库前致命。"""
    holdings_by_basket: Dict[str, List[Dict]] = {}
    for basket, api_symbol in ETF_HOLDING_SOURCES.items():
        try:
            raw = client.get_etf_holdings(api_symbol)
        except Exception as exc:
            raise _FatalRunError(
                f"holdings fetch failed for {basket}: {exc}") from exc
        if not raw:
            raise _FatalRunError(
                f"holdings valid-empty for {basket}; refusing to continue")
        holdings_by_basket[basket] = normalize_holdings(
            basket, snapshot_date, raw, listing, groups)
    return holdings_by_basket


def _process_symbol(client, store, symbol, args, summary, dry_run):
    """单股 quarter → annual → earnings。返回该股是否 quarter 关键成功。"""
    try:
        quarter_raw = client.get_analyst_estimates(
            symbol, period="quarter", limit=ESTIMATES_LIMIT)
    except Exception:
        summary.quarter_failed.append(symbol)
        return False
    if not quarter_raw:
        summary.quarter_empty.append(symbol)
        return False

    # fiscal 匹配用全量 raw quarter 日期集；120d 窗口只约束落库行
    fiscal_dates = extract_valid_quarter_fiscal_dates(quarter_raw)
    q_rows, _ = normalize_estimates(
        symbol, quarter_raw, args.snapshot_date, "Q", args.mode,
        args.backfill_start)

    a_rows: List[Dict] = []
    try:
        annual_raw = client.get_analyst_estimates(
            symbol, period="annual", limit=ESTIMATES_LIMIT)
        if annual_raw:
            a_rows, _ = normalize_estimates(
                symbol, annual_raw, args.snapshot_date, "FY", args.mode,
                args.backfill_start)
        else:
            summary.annual_failed.append(symbol)
    except Exception:
        summary.annual_failed.append(symbol)

    est_rows = q_rows + a_rows
    if est_rows:
        if not dry_run:
            store.upsert_fmp_estimates(symbol, est_rows)
        summary.estimate_rows += len(est_rows)
    summary.quarter_success += 1

    earn_limit = (EARNINGS_LIMIT_WEEKLY if args.mode == "weekly"
                  else EARNINGS_LIMIT_BACKFILL)
    try:
        earn_raw = client.get_earnings(symbol, limit=earn_limit)
    except Exception:
        summary.earnings_failed.append(symbol)
        return True
    if not earn_raw:
        summary.earnings_failed.append(symbol)
        return True
    e_rows, e_counters = normalize_earnings(symbol, earn_raw, fiscal_dates)
    summary.unmatched_earnings += e_counters["unmatched"]
    if e_rows:
        if not dry_run:
            store.replace_fmp_earnings(symbol, e_rows)
        summary.earnings_rows += len(e_rows)
    return True


def _attempt_detail(summary: ForwardRunSummary, targets: List[str]) -> Dict:
    return {
        "at": _utc_now(),
        "mode": summary.mode,
        "targets": len(targets),
        "quarter_success": summary.quarter_success,
        "quarter_failed": sorted(summary.quarter_failed),
        "quarter_empty": sorted(summary.quarter_empty),
        "annual_failed": sorted(summary.annual_failed),
        "earnings_failed": sorted(summary.earnings_failed),
        "estimate_rows": summary.estimate_rows,
        "earnings_rows": summary.earnings_rows,
        "unmatched_earnings": summary.unmatched_earnings,
    }


def run_update(args, *, client, store,
               core_loader: Callable[[], List[str]],
               extended_loader: Callable[[], List[str]],
               send_message_fn,
               verify_fn: Optional[Callable] = None) -> Tuple[int, ForwardRunSummary]:
    """verify_fn(snapshot_date, run_kind) -> (rc, report)。

    状态机（冻结）: manifest running → writer gate FAIL → failed；
    writer PASS 保持 running → verifier PASS → complete / FAIL·异常 → failed。
    verify_fn 为 None 时（未接线场景）writer PASS 后保持 running。
    """
    t0 = time.time()
    summary = ForwardRunSummary(mode=args.mode,
                                snapshot_date=args.snapshot_date,
                                target_count=0)
    api_key = getattr(client, "api_key", "")

    def _safe(text: object) -> str:
        return _sanitize_log_text(text, api_key)

    def notify(message: str) -> None:
        if args.no_telegram:
            return
        try:
            send_message_fn(_safe(message), channel="private")
        except Exception as exc:  # Telegram 失败不改变数据结论
            logger.warning("telegram send failed: %s", _safe(exc))

    def fail(message: str) -> Tuple[int, ForwardRunSummary]:
        summary.duration_seconds = time.time() - t0
        logger.error("%s", _safe(message))
        notify(f"❌ FMP forward {args.mode} {args.snapshot_date}: {message}")
        return 1, summary

    try:
        listing, groups, mags = load_basket_configs(args.config_dir)
    except Exception as exc:
        return fail(f"basket config invalid: {exc}")

    # backfill 同日守卫：weekly 行已存在则在任何 API 调用前拒绝
    if args.mode == "backfill" and not args.dry_run:
        if store.has_fmp_weekly_estimates(args.snapshot_date):
            return fail("weekly rows already exist for this snapshot date; "
                        "backfill refused")

    manifest = None
    prior_state: Dict = {}
    holdings_by_basket: Dict[str, List[Dict]] = {}

    if args.resume:
        # 子集修复：manifest 是唯一分母，不刷新 holdings
        manifest = store.get_fmp_forward_run(args.snapshot_date, args.mode)
        if not manifest:
            return fail("resume requires an existing full manifest; none found")
        manifest_universe = manifest["target_universe"]
        requested = list(args.symbols)
        outside = sorted(set(requested) - set(manifest_universe))
        if outside:
            return fail(f"resume symbols not in manifest: {outside}")
        try:
            prior_state = json.loads(manifest.get("summary_json") or "{}")
        except (TypeError, ValueError):
            prior_state = {}
        targets = requested
        summary.target_count = manifest["target_count"]
    else:
        try:
            holdings_by_basket = _fetch_and_normalize_holdings(
                client, args.snapshot_date, listing, groups)
        except _FatalRunError as exc:
            return fail(str(exc))
        all_norm = [r for rows in holdings_by_basket.values() for r in rows]
        summary.holdings_rows = len(all_norm)

        if args.dry_run and args.symbols:
            targets = list(args.symbols)      # 自由 smoke：无 manifest/分母
            summary.target_count = len(targets)
        else:
            try:
                universe = resolve_fmp_forward_universe(
                    core_loader(), extended_loader(), all_norm, mags)
            except ValueError as exc:
                return fail(str(exc))
            targets = universe
            summary.target_count = len(universe)

            if not args.dry_run:
                # 同日 full rerun：任何 DB 写入前核对 manifest 相等
                existing = store.get_fmp_forward_run(args.snapshot_date, args.mode)
                if existing and existing["target_universe"] != universe:
                    return fail(
                        "resolved universe differs from immutable manifest for "
                        f"{args.snapshot_date}/{args.mode}; zero writes performed")
                # exact 分母在首个逐股请求前持久化
                store.upsert_fmp_forward_run({
                    "snapshot_date": args.snapshot_date,
                    "run_kind": args.mode,
                    "status": "running",
                    "target_universe": universe,
                    "started_at": _utc_now(),
                })
                # manifest 规则通过后才允许落 holdings 快照
                for basket, rows in holdings_by_basket.items():
                    store.replace_fmp_etf_holdings(
                        basket, args.snapshot_date, rows)

    for symbol in targets:
        _process_symbol(client, store, symbol, args, summary, args.dry_run)

    summary.duration_seconds = time.time() - t0
    critical_failures = len(summary.quarter_failed) + len(summary.quarter_empty)
    failure_rate = critical_failures / len(targets) if targets else 1.0

    if args.dry_run:
        logger.info("dry-run complete; verifier skipped (no writes)")
        return (1 if failure_rate > CRITICAL_FAILURE_RATE else 0), summary

    # run_state 证据链（round-5）：full run 重置；resume 合并
    attempt = _attempt_detail(summary, targets)
    if args.resume:
        prior_empty = set((prior_state.get("run_state") or {})
                          .get("quarter_empty") or [])
        run_empty = sorted((prior_empty - set(targets))
                           | set(summary.quarter_empty))
        attempts = list(prior_state.get("attempts") or []) + [attempt]
        # 子集统计绝不覆盖 full-run 字段
        quarter_success = manifest["quarter_success"]
        quarter_failure_count = manifest["quarter_failure_count"]
    else:
        run_empty = sorted(summary.quarter_empty)
        attempts = [attempt]
        quarter_success = summary.quarter_success
        quarter_failure_count = critical_failures

    summary_json = json.dumps({
        "run_state": {"quarter_empty": run_empty},
        "attempts": attempts,
    })
    writer_failed = failure_rate > CRITICAL_FAILURE_RATE
    store.upsert_fmp_forward_run({
        "snapshot_date": args.snapshot_date,
        "run_kind": args.mode,
        "status": "failed" if writer_failed else "running",
        "target_universe": None,   # 更新执行统计，不触碰不可变 universe
        "quarter_success": quarter_success,
        "quarter_failure_count": quarter_failure_count,
        "completed_at": _utc_now() if writer_failed else None,
        "summary_json": summary_json,
        "started_at": manifest["started_at"] if manifest else None,
    })

    if writer_failed:
        return fail(
            f"critical quarter failure rate "
            f"{failure_rate:.0%} > {CRITICAL_FAILURE_RATE:.0%} "
            f"(failed={len(summary.quarter_failed)}, "
            f"empty={len(summary.quarter_empty)}, targets={len(targets)}); "
            f"partial snapshot preserved for resume; verifier skipped")

    if verify_fn is None:
        # 未接线场景：writer PASS 保持 running，绝不自行 complete
        notify(
            f"✅ FMP forward {args.mode} {args.snapshot_date} writer pass "
            f"(verifier not wired): {summary.quarter_success}/{len(targets)} "
            f"quarter ok, {summary.estimate_rows} estimate rows, "
            f"{summary.earnings_rows} earnings rows, "
            f"{summary.unmatched_earnings} unmatched, "
            f"duration {summary.duration_seconds:.0f}s")
        return 0, summary

    def _persist_final(status: str, qs: int, qf: int) -> None:
        store.upsert_fmp_forward_run({
            "snapshot_date": args.snapshot_date,
            "run_kind": args.mode,
            "status": status,
            "target_universe": None,
            "quarter_success": qs,
            "quarter_failure_count": qf,
            "completed_at": _utc_now(),
            "summary_json": summary_json,
            "started_at": manifest["started_at"] if manifest else None,
        })

    try:
        v_rc, v_report = verify_fn(args.snapshot_date, args.mode)
    except Exception as exc:
        _persist_final("failed", quarter_success, quarter_failure_count)
        return fail(f"verifier exception: {exc}")

    universe_report = (v_report or {}).get("universe") or {}
    covered = int(universe_report.get("covered_4q") or 0)
    expected = int(universe_report.get("expected") or 0)

    if v_rc != 0:
        # run-wide 计数以 full report 为准；subset attempt 只留在 summary_json
        _persist_final("failed", covered, max(expected - covered, 0))
        missing = universe_report.get("missing") or []
        reasons = "; ".join((v_report or {}).get("failures") or ["verifier FAIL"])
        return fail(f"verifier FAIL: {reasons}; "
                    f"missing (top 20): {missing[:20]}")

    _persist_final("complete", covered, max(expected - covered, 0))
    notify(
        f"✅ FMP forward {args.mode} {args.snapshot_date} PASS: "
        f"covered {covered}/{expected}, "
        f"estimate rows {summary.estimate_rows}, "
        f"earnings rows {summary.earnings_rows}, "
        f"unmatched {summary.unmatched_earnings}, "
        f"duration {summary.duration_seconds:.0f}s")
    return 0, summary


# ---------------------------------------------------------------------------
# main（真实依赖仅在参数校验通过后构建）
# ---------------------------------------------------------------------------

def main(argv: Optional[List[str]] = None) -> int:
    args = parse_args(argv)

    from config.settings import FMP_FORWARD_API_CALL_INTERVAL
    from src.data.fmp_client import FMPClient

    client = FMPClient(call_interval=FMP_FORWARD_API_CALL_INTERVAL)

    store = None
    if not args.dry_run:
        from src.data.market_store import MarketStore
        db_path = (args.data_root / "market.db") if args.data_root else None
        store = MarketStore(db_path)

    from src.data.extended_universe_manager import get_extended_symbols
    from src.data.pool_manager import get_symbols
    from src.telegram_bot import send_message

    verify_fn = None
    if not args.dry_run:
        from scripts.verify_fmp_forward import verify_run as _verify_run
        db_path = store.db_path
        data_root = db_path.parent

        def verify_fn(snapshot_date, run_kind):
            return _verify_run(db_path, data_root, snapshot_date,
                               run_kind=run_kind, stage="data")

    rc, summary = run_update(
        args, client=client, store=store,
        core_loader=get_symbols,
        extended_loader=get_extended_symbols,
        send_message_fn=send_message,
        verify_fn=verify_fn,
    )
    print(f"[{summary.mode}] snapshot={summary.snapshot_date} "
          f"targets={summary.target_count} "
          f"quarter_ok={summary.quarter_success} "
          f"failed={len(summary.quarter_failed)} "
          f"empty={len(summary.quarter_empty)} "
          f"est_rows={summary.estimate_rows} earn_rows={summary.earnings_rows} "
          f"unmatched={summary.unmatched_earnings} "
          f"duration={summary.duration_seconds:.0f}s rc={rc}")
    if args.dry_run:
        print("verifier skipped (no writes)")
    return rc


if __name__ == "__main__":
    sys.exit(main())
