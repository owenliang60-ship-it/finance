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
    非 dry-run --symbols 且无 --resume / --resume 无 --symbols / resume+dry-run
    / 裸 --dry-run 无 --symbols（防全 universe 真实 API 扫描）→ exit 2

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
# PIT 冻结（review P1）：非 dry-run 只允许写 [today-6d, today] 内的 snapshot_date。
# 6 天 = 同一自然周内修复失败 run；更早的历史快照是不可改写的 PIT 事实。
SNAPSHOT_WRITE_WINDOW_DAYS = 6


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
    earnings_success: List[str] = field(default_factory=list)  # resume 合并用
    estimate_rows: int = 0
    earnings_rows: int = 0
    unmatched_earnings: int = 0
    unprocessed: List[str] = field(default_factory=list)  # 熔断后未请求的余量
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
                        help="derive market.db + pool cache locations for worktrees")
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
    if args.dry_run and not args.symbols:
        # 裸 dry-run 会对全 universe 发起真实 API 扫描却零落库（P1 review finding）
        parser.error("--dry-run requires --symbols (explicit smoke subset)")

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
        normalized = normalize_holdings(basket, snapshot_date, raw,
                                        listing, groups)
        # 全 malformed payload（如 [None]×k）规范化后一行 included 都没有 =
        # endpoint failure；坏行审计留档不等于快照有效（review round-7 P1）
        if not any(r["included"] == 1 for r in normalized):
            raise _FatalRunError(
                f"holdings for {basket} contain zero valid included rows; "
                "treating as endpoint failure")
        holdings_by_basket[basket] = normalized
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

    # 规范化/落库异常按该股 quarter 失败记账，绝不逃逸留下 running manifest
    try:
        # fiscal 匹配用全量 raw quarter 日期集；120d 窗口只约束落库行
        fiscal_dates = extract_valid_quarter_fiscal_dates(quarter_raw)
        q_rows, _ = normalize_estimates(
            symbol, quarter_raw, args.snapshot_date, "Q", args.mode,
            args.backfill_start)
        if not q_rows:
            # 全部行 malformed（如 [None] payload）→ 与传输失败同等对待
            summary.quarter_failed.append(symbol)
            return False

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
        if not dry_run:
            store.upsert_fmp_estimates(symbol, est_rows)
        summary.estimate_rows += len(est_rows)
        summary.quarter_success += 1
    except Exception as exc:
        logger.error("quarter processing failed for %s: %s", symbol, exc)
        summary.quarter_failed.append(symbol)
        return False

    earn_limit = (EARNINGS_LIMIT_WEEKLY if args.mode == "weekly"
                  else EARNINGS_LIMIT_BACKFILL)
    try:
        earn_raw = client.get_earnings(symbol, limit=earn_limit)
        if not earn_raw:
            summary.earnings_failed.append(symbol)
            return True
        e_rows, e_counters = normalize_earnings(symbol, earn_raw, fiscal_dates)
        summary.unmatched_earnings += e_counters["unmatched"]
        if not e_rows:
            # 非空 payload 规范化后零有效行（如 [None]）= endpoint failure，
            # 绝不能静默算成功（review round-7 P1）
            summary.earnings_failed.append(symbol)
            return True
        if not dry_run:
            store.replace_fmp_earnings(symbol, e_rows)
        summary.earnings_rows += len(e_rows)
        summary.earnings_success.append(symbol)
    except Exception as exc:
        logger.error("earnings processing failed for %s: %s", symbol, exc)
        summary.earnings_failed.append(symbol)
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
        "earnings_success": sorted(summary.earnings_success),
        "estimate_rows": summary.estimate_rows,
        "earnings_rows": summary.earnings_rows,
        "unmatched_earnings": summary.unmatched_earnings,
        "unprocessed": sorted(summary.unprocessed),
    }


def run_update(args, *, client, store,
               core_loader: Callable[[], List[str]],
               extended_loader: Callable[[], List[str]],
               send_message_fn,
               verify_fn: Optional[Callable] = None,
               today_fn: Callable[[], date] = date.today) -> Tuple[int, ForwardRunSummary]:
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

    # PIT 冻结守卫（review P1）：历史/未来 snapshot_date 的非 dry-run 写入一律拒绝。
    # 今天抓到的 consensus 只能盖"今天附近"的戳；改写更早日期 = 伪造 PIT 历史。
    if not args.dry_run:
        today = today_fn()  # clock 只能由代码注入，不暴露 CLI 后门（round-7 P2）
        snap = date.fromisoformat(args.snapshot_date)
        age_days = (today - snap).days
        if snap > today or age_days > SNAPSHOT_WRITE_WINDOW_DAYS:
            return fail(
                f"snapshot_date {args.snapshot_date} outside writable window "
                f"[today-{SNAPSHOT_WRITE_WINDOW_DAYS}d, today={today}]; "
                "historical PIT snapshots are frozen — zero writes performed")

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
        if manifest["status"] == "complete":
            # complete 是终态（review P1）：已裁决合格的历史快照不可被改写
            return fail("manifest status is complete (terminal); "
                        "refusing to rewrite a verified snapshot")
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
        existing = None
        if not args.dry_run:
            # 终态检查必须在 5 次 holdings API 调用之前（round-7 P2）
            existing = store.get_fmp_forward_run(args.snapshot_date, args.mode)
            if existing and existing["status"] == "complete":
                return fail(
                    "manifest status is complete (terminal); "
                    "refusing full rerun over a verified snapshot")
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

    # 提前熔断（review P1）：累计关键失败一旦不可逆越过 20% 立即停，
    # 余量记 unprocessed 供 resume——全局故障时不再烧掉剩余 ~80% 配额。
    breaker_threshold = int(CRITICAL_FAILURE_RATE * len(targets))
    processed: List[str] = []
    manifest_live = not args.dry_run

    def _mark_failed_best_effort(reason: str) -> None:
        """异常收尾：只附加 error，绝不抹掉既有 run_state/attempts 证据链。

        round-8 P1：finalizer 覆盖 summary_json 会清空 run-wide earnings/quarter
        失败状态，让下一次 partial resume 错误地 complete。规则：
        - 既有 summary_json 可解码 → 原结构保留 + append errors[]
        - 无法解码 → 原字符串原样回写（fail closed，不破坏现场）
        - manifest 读不到 → 才允许写 error-only 骨架
        """
        try:
            try:
                current = store.get_fmp_forward_run(args.snapshot_date, args.mode)
            except Exception:
                current = None
            prior_json = (current or {}).get("summary_json")
            error_entry = {"at": _utc_now(), "error": _safe(reason)[:500]}
            if prior_json:
                try:
                    payload = json.loads(prior_json)
                    payload["errors"] = list(payload.get("errors") or []) + [error_entry]
                    summary_json_out = json.dumps(payload)
                except (TypeError, ValueError):
                    summary_json_out = prior_json  # 不可解码：原样保留
            else:
                summary_json_out = json.dumps({
                    "run_state": {"quarter_empty": [], "earnings_failed": []},
                    "attempts": [],
                    "errors": [error_entry],
                })
            # 统计字段同样保留 manifest 既有值，不用本次 partial attempt 覆盖
            store.upsert_fmp_forward_run({
                "snapshot_date": args.snapshot_date,
                "run_kind": args.mode,
                "status": "failed",
                "target_universe": None,
                "quarter_success": (current or {}).get(
                    "quarter_success", summary.quarter_success),
                "quarter_failure_count": (current or {}).get(
                    "quarter_failure_count",
                    len(summary.quarter_failed) + len(summary.quarter_empty)),
                "completed_at": _utc_now(),
                "summary_json": summary_json_out,
                "started_at": manifest["started_at"] if manifest else None,
            })
        except Exception as persist_exc:
            logger.error("failed to persist failure state: %s",
                         _safe(persist_exc))

    try:
        return _run_symbols_and_finalize(
            args, client=client, store=store, targets=targets,
            processed=processed, breaker_threshold=breaker_threshold,
            summary=summary, manifest=manifest, prior_state=prior_state,
            holdings_by_basket=holdings_by_basket,
            t0=t0, verify_fn=verify_fn, notify=notify, fail=fail)
    except Exception as exc:
        # manifest 已开（running）后任何未预期异常都必须收尾成 failed，
        # 绝不遗留永久 running（review P1）
        if manifest_live:
            _mark_failed_best_effort(str(exc))
        return fail(f"unexpected exception after manifest open: {exc}")


def _run_symbols_and_finalize(args, *, client, store, targets, processed,
                              breaker_threshold, summary, manifest,
                              prior_state, holdings_by_basket, t0, verify_fn,
                              notify, fail) -> Tuple[int, ForwardRunSummary]:
    # manifest 规则通过后才允许落 holdings 快照（在外层异常收尾保护之内）
    if not args.dry_run and not args.resume:
        for basket, rows in holdings_by_basket.items():
            store.replace_fmp_etf_holdings(basket, args.snapshot_date, rows)

    for i, symbol in enumerate(targets):
        _process_symbol(client, store, symbol, args, summary, args.dry_run)
        processed.append(symbol)
        critical_so_far = (len(summary.quarter_failed)
                           + len(summary.quarter_empty))
        if critical_so_far > breaker_threshold:
            summary.unprocessed = list(targets[i + 1:])
            logger.error(
                "circuit breaker tripped: %d critical failures > %d threshold "
                "after %d/%d symbols; %d left unprocessed for resume",
                critical_so_far, breaker_threshold, len(processed),
                len(targets), len(summary.unprocessed))
            break

    summary.duration_seconds = time.time() - t0
    critical_failures = len(summary.quarter_failed) + len(summary.quarter_empty)
    failure_rate = critical_failures / len(targets) if targets else 1.0

    if args.dry_run:
        # dry-run 同样执行两道 gate（round-8 P1）：earnings endpoint 失效时
        # Task 11 live contract probe 绝不能误报成功
        dry_earnings_rate = (len(summary.earnings_failed) / len(targets)
                             if targets else 1.0)
        dry_failed = (failure_rate > CRITICAL_FAILURE_RATE
                      or dry_earnings_rate > CRITICAL_FAILURE_RATE)
        logger.info("dry-run complete; verifier skipped (no writes); "
                    "quarter_fail=%.0f%% earnings_fail=%.0f%%",
                    failure_rate * 100, dry_earnings_rate * 100)
        return (1 if dry_failed else 0), summary

    # run_state 证据链（round-5/7）：full run 重置；resume 按 processed 合并。
    # earnings_failed 与 quarter_empty 一样是 run-wide 状态——resume 只修一票
    # 不能让其余未修复的 earnings 失败被遗忘（round-7 P1）。
    attempt = _attempt_detail(summary, targets)
    if args.resume:
        prior_run_state = prior_state.get("run_state") or {}
        prior_empty = set(prior_run_state.get("quarter_empty") or [])
        # 只从既有 empty 集扣掉真正处理过的 symbol；熔断余量不算已修复
        run_empty = sorted((prior_empty - set(processed))
                           | set(summary.quarter_empty))
        prior_earn_failed = set(prior_run_state.get("earnings_failed") or [])
        run_earnings_failed = sorted(
            (prior_earn_failed - set(summary.earnings_success))
            | set(summary.earnings_failed))
        attempts = list(prior_state.get("attempts") or []) + [attempt]
        # 子集统计绝不覆盖 full-run 字段
        quarter_success = manifest["quarter_success"]
        quarter_failure_count = manifest["quarter_failure_count"]
    else:
        run_empty = sorted(summary.quarter_empty)
        run_earnings_failed = sorted(summary.earnings_failed)
        attempts = [attempt]
        quarter_success = summary.quarter_success
        quarter_failure_count = critical_failures

    summary_json = json.dumps({
        "run_state": {"quarter_empty": run_empty,
                      "earnings_failed": run_earnings_failed},
        "attempts": attempts,
    })
    # earnings 覆盖门槛（round-6/7 P1）：run-wide unresolved 集 ÷ 完整 manifest
    # 分母裁决——quarter 达标但 earnings 大面积断供同样不允许标 complete
    denominator = summary.target_count or len(targets) or 1
    earnings_failure_rate = len(run_earnings_failed) / denominator
    writer_failed = failure_rate > CRITICAL_FAILURE_RATE
    earnings_gate_failed = (not writer_failed
                            and earnings_failure_rate > CRITICAL_FAILURE_RATE)
    writer_failed = writer_failed or earnings_gate_failed
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

    if earnings_gate_failed:
        return fail(
            f"run-wide earnings failure rate {earnings_failure_rate:.0%} > "
            f"{CRITICAL_FAILURE_RATE:.0%} "
            f"(unresolved={len(run_earnings_failed)}, "
            f"denominator={denominator}); "
            f"estimates snapshot preserved; repair earnings via resume")

    if writer_failed:
        return fail(
            f"critical quarter failure rate "
            f"{failure_rate:.0%} > {CRITICAL_FAILURE_RATE:.0%} "
            f"(failed={len(summary.quarter_failed)}, "
            f"empty={len(summary.quarter_empty)}, targets={len(targets)}, "
            f"unprocessed={len(summary.unprocessed)}); "
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

def build_pool_loaders(data_root: Optional[Path]):
    """--data-root 完整契约（review P1）：同时派生 market.db 与 pool cache。

    data_root 为 None → 模块默认 loaders（生产路径）；
    否则 core/extended 都从 data_root/pool/ 读取，绝不回落 worktree 默认目录。
    """
    if data_root is None:
        from src.data.extended_universe_manager import get_extended_symbols
        from src.data.pool_manager import get_symbols
        return get_symbols, get_extended_symbols

    pool_dir = Path(data_root) / "pool"

    def core_loader() -> List[str]:
        with open(pool_dir / "universe.json", encoding="utf-8") as f:
            entries = json.load(f)
        return sorted({(e["symbol"] if isinstance(e, dict) else str(e)).upper()
                       for e in entries})

    def extended_loader() -> List[str]:
        with open(pool_dir / "extended_universe.json", encoding="utf-8") as f:
            cache = json.load(f)
        return list(cache.get("symbols", []))

    return core_loader, extended_loader


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

    get_symbols, get_extended_symbols = build_pool_loaders(args.data_root)
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
