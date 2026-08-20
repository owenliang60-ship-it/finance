"""Weekly coverage-reconciliation CLI (T12, R6/R10) — audit -> freeze -> repair -> summary.

Answers a different question from the backfill runner (T10): not "collect the
base universe" but "is what we already collected still trustworthy, and where
has it silently rotted or never landed at all?" Every write this script makes
goes through the SAME shared kernels the rest of the pipeline uses — the
identity kernel (T6) and the fundamentals collection kernel (T8) — so a
second, drifting write path never gets a chance to exist.

Four phases, always in this order:

  Phase 0 (identity queue, --repair only, R2-P1-2 + R3-P1-2). Queue key:
  `coverage_status` rows with `dataset='identity'` and `next_retry_at <= as_of`
  — this covers BOTH a network-failure backoff expiring and a provider_empty
  TTL expiring. `identity_blocked` rows carry no `next_retry_at` (cleared
  explicitly by the identity kernel) and so never enter the queue; those wait
  for a human override. Every queued symbol is re-run through
  `bootstrap_security_master.resolve_identity_for_symbol` — UNCHANGED,
  imported rather than reimplemented — regardless of whether it is currently
  `eligible`; the whole point of this phase is upgrading symbols the SM
  currently blocks. A successful "ok" outcome is grouped through
  `resolve_share_classes` (same as a full bootstrap) and written to SM, so it
  can enter Phase 1's denominator this same run.

  Phase 1 (always; strictly read-only unless --repair is set). For every symbol
  in `current_base_universe()` (active Extended membership ∩ SM eligible —
  the SAME frozen-denominator resolver T10 uses, never the SM full set) times
  {income, balance, cashflow, profile}:
    - no rows in the current table AND no coverage row at all -> `missing`.
    - no rows AND coverage status is `fetch_failed` or `provider_empty` AND
      `next_retry_at <= as_of` -> `retryable` (the backoff/TTL timer already
      recorded on `coverage_status` is the single source of truth for whether
      a failed or empty dataset is due for another try — this applies
      uniformly to fetch_failed and provider_empty, not just the latter, so a
      transient outage's exponential backoff is honoured here exactly as it
      is by the backfill runner's own retry logic).
    - no rows AND coverage status is `not_applicable` -> permanently exempt.
    - HAS rows, for the three statement datasets only (profile has no fiscal
      calendar to go stale against): latest `date` older than
      `--stale-after-days` (default 120) from `as_of` -> `stale`, and the
      repair mode persists a `coverage_status` row with `detail` set to the
      day count; report-only only returns the stale target in memory.

  Freeze: `repair_targets = missing ∪ stale ∪ retryable` (deduped SYMBOLS,
  never symbol×dataset pairs — a repair re-runs the WHOLE kernel for a
  symbol), sorted lexicographically, truncated to `--max-targets` (default
  200), and printed BEFORE Phase 2 touches anything (P1-5: this frozen list,
  never the full pool, is what Phase 2 is allowed to see).

  Phase 2 (--repair only). Only the frozen list goes through
  `fundamental_collector.collect_fundamentals_for_symbol` — the same kernel
  T10/T11 use, one call per symbol, all five datasets. `rebuild_profiles_json`
  runs once at the end of the batch (never per symbol, R2-P2-3).

  Summary: a six-state coverage census (the `coverage_status` status
  whitelist: ok / not_applicable / provider_empty / fetch_failed / stale /
  identity_blocked) plus repair success/fail counts and a truncation notice,
  delivered through an injected `notifier(message: str)` callable so this
  module never imports the Telegram transport directly.

Locking (CONTROLLER RULING #13): self-locks like T10, same lock file and the
same non-blocking flock semantics — reused directly from
`backfill_extended_fundamentals.FileLock` rather than reimplemented.
Report-only mode opens SQLite read-only and performs zero writes, so it needs
no lock. `--repair` acquires the lock FIRST, before Phase 0
touches anything; busy means a peer backfill/reconcile run owns market.db
right now, so this run exits 75 having written nothing.

`run_reconcile`'s `lock` parameter is keyword-only with NO usable default
under `repair=True` — mirroring T10's `run_backfill`, which makes `lock` a
mandatory parameter for exactly this reason (mandatory-no-default, not a
convenience no-op). `repair=True` without `lock` raises `ValueError`
immediately: a future caller that forgets to pass a lock must fail loud
rather than write against market.db unlocked. Every `--repair` caller
therefore makes an explicit choice — `FileLock()` in production, a fake in
tests. Report-only calls never pass (or need) `lock` at all. `main()` is the
only caller that constructs a real `FileLock`.

CLI:
    python scripts/reconcile_fundamentals.py [--repair] [--max-targets 200] \\
        [--stale-after-days 120] [--json] [--as-of YYYY-MM-DD]
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.backfill_extended_fundamentals import FileLock  # noqa: E402
from src.data.entrant_bootstrap import bootstrap_entrants  # noqa: E402
from src.data.fundamental_collector import (  # noqa: E402
    DEFAULT_PROFILES_PATH,
    collect_fundamentals_for_symbol,
    rebuild_profiles_json,
)
from src.data.market_store import COLLECTION_DATASET_TABLES, MarketStore, _is_pure_date  # noqa: E402
from src.data.universe_resolver import current_base_universe  # noqa: E402

logger = logging.getLogger(__name__)

EXIT_OK = 0
EXIT_EMPTY_UNIVERSE = 2
EXIT_LOCK_BUSY = 75

DEFAULT_MAX_TARGETS = 200
DEFAULT_STALE_AFTER_DAYS = 120

SHARE_CLASS_OVERRIDES_PATH = PROJECT_ROOT / "config" / "share_class_overrides.json"

# Phase 1 evaluates these 4 dataset keys per symbol. `profile` maps to
# company_profile, which has no fiscal_date column, so it is exempt from the
# staleness check (STALE_CAPABLE_DATASETS) but still subject to missing/retryable.
PHASE1_DATASETS = ("income", "balance", "cashflow", "profile")
STALE_CAPABLE_DATASETS = frozenset({"income", "balance", "cashflow"})

# Mirrors MarketStore._COVERAGE_STATUSES (market_store.py) — the six-state
# whitelist coverage_status.status is validated against. Kept as a local
# literal (not a private cross-module import) since it is a stable, DDL-level
# contract shared verbatim by every writer of that table.
SIX_COVERAGE_STATES = (
    "ok", "not_applicable", "provider_empty", "fetch_failed", "stale", "identity_blocked",
)

# Statuses whose retryability is governed by coverage_status.next_retry_at
# rather than being unconditionally "missing" or unconditionally exempt —
# R2-P1-3's backoff/TTL timer applies uniformly to both.
_RETRY_GATED_STATUSES = frozenset({"fetch_failed", "provider_empty"})


# ---------------------------------------------------------------------------
# Time helpers
# ---------------------------------------------------------------------------

def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _normalize_as_of(as_of: str) -> str:
    """Pure date -> "<date>T00:00:00Z" (as_of stands in for "now" throughout
    this run — see module docstring); a full timestamp passes through."""
    if _is_pure_date(as_of):
        return as_of + "T00:00:00Z"
    return as_of


def _parse_date(value: str) -> date:
    return datetime.strptime(value[:10], "%Y-%m-%d").date()


def _load_overrides(path: Optional[Path] = None) -> Dict[str, str]:
    p = path or SHARE_CLASS_OVERRIDES_PATH
    if not p.exists():
        return {}
    with open(p, "r", encoding="utf-8") as fh:
        return json.load(fh)


# ---------------------------------------------------------------------------
# Phase 0: identity queue (R2-P1-2 + R3-P1-2)
# ---------------------------------------------------------------------------

def _identity_queue_symbols(store: MarketStore, as_of_ts: str) -> List[str]:
    conn = store._get_conn()
    rows = conn.execute(
        "SELECT symbol FROM coverage_status WHERE dataset = 'identity' "
        "AND next_retry_at IS NOT NULL AND next_retry_at <= ? ORDER BY symbol",
        (as_of_ts,),
    ).fetchall()
    return [r["symbol"] for r in rows]


def _run_identity_phase(store: MarketStore, client: Any, as_of_ts: str,
                        overrides: Dict[str, str]) -> Dict[str, Any]:
    """Retry exactly the queued symbols through the closed-group entrant path.

    `bootstrap_entrants` adds existing same-CIK SM incumbents before resolving
    share classes, so a recovered profile cannot become a second eligible
    primary merely because its sibling was outside this retry batch.
    """
    queue = _identity_queue_symbols(store, as_of_ts)
    if not queue:
        return {"queued": 0, "upgraded": []}
    summary = bootstrap_entrants(
        queue, client=client, store=store, overrides=overrides)
    return {"queued": len(queue), "upgraded": list(summary["eligible"])}


# ---------------------------------------------------------------------------
# Phase 1: coverage audit (always, read-only except stale annotations)
# ---------------------------------------------------------------------------

def _coverage_rows(store: MarketStore, table: str) -> Dict[str, Tuple[str, Optional[str]]]:
    """symbol -> (status, next_retry_at) for one coverage_status dataset."""
    conn = store._get_conn()
    rows = conn.execute(
        "SELECT symbol, status, next_retry_at FROM coverage_status WHERE dataset = ?",
        (table,),
    ).fetchall()
    return {r["symbol"]: (r["status"], r["next_retry_at"]) for r in rows}


def _has_rows(conn, table: str, symbol: str) -> bool:
    row = conn.execute(f"SELECT 1 FROM {table} WHERE symbol = ? LIMIT 1", (symbol,)).fetchone()
    return row is not None


def _latest_fiscal_date(conn, table: str, symbol: str) -> Optional[date]:
    row = conn.execute(f"SELECT MAX(date) AS d FROM {table} WHERE symbol = ?", (symbol,)).fetchone()
    if not row or not row["d"]:
        return None
    return _parse_date(row["d"])


def _phase1_audit(store: MarketStore, symbols: List[str], as_of_ts: str,
                  stale_after_days: int, *, persist_stale: bool) -> Dict[str, Any]:
    conn = store._get_conn()
    as_of_date = _parse_date(as_of_ts)

    missing_set: set = set()
    stale_set: set = set()
    retryable_set: set = set()
    status_counts: Dict[str, int] = {s: 0 for s in SIX_COVERAGE_STATES}
    not_started = 0

    coverage_cache = {
        COLLECTION_DATASET_TABLES[dataset]: _coverage_rows(store, COLLECTION_DATASET_TABLES[dataset])
        for dataset in PHASE1_DATASETS
    }

    for symbol in symbols:
        for dataset in PHASE1_DATASETS:
            table = COLLECTION_DATASET_TABLES[dataset]
            cov_status, cov_next_retry = coverage_cache[table].get(symbol, (None, None))
            has_rows = _has_rows(conn, table, symbol)

            if cov_status is not None:
                status_counts[cov_status] = status_counts.get(cov_status, 0) + 1
            else:
                not_started += 1

            if dataset in STALE_CAPABLE_DATASETS and has_rows:
                latest = _latest_fiscal_date(conn, table, symbol)
                if latest is not None and (as_of_date - latest).days > stale_after_days:
                    if persist_stale:
                        store.upsert_coverage_status([{
                            "symbol": symbol, "dataset": table, "status": "stale",
                            "detail": str((as_of_date - latest).days),
                            "updated_at": as_of_ts,
                        }])
                    stale_set.add(symbol)
                continue

            if has_rows:
                continue

            if cov_status is None:
                missing_set.add(symbol)
            elif cov_status == "not_applicable":
                continue
            elif cov_status in _RETRY_GATED_STATUSES:
                if cov_next_retry is not None and cov_next_retry <= as_of_ts:
                    retryable_set.add(symbol)
                # else: still inside its backoff/TTL window — leave alone
            else:
                # An unexpected terminal status with no rows (e.g. stale/ok
                # somehow left with an empty table, or identity_blocked
                # recorded against a non-identity dataset) — treat
                # conservatively as needing another attempt.
                missing_set.add(symbol)

    return {
        "missing": missing_set, "stale": stale_set, "retryable": retryable_set,
        "status_counts": status_counts, "not_started": not_started,
    }


# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------

def _summary_message(report: Dict[str, Any]) -> str:
    sc = report["status_counts"]
    six = ", ".join(f"{k}={sc.get(k, 0)}" for k in SIX_COVERAGE_STATES)
    truncated_note = " (truncated)" if report["truncated"] else ""
    parts = [
        "Reconcile {} — audited {} symbols".format(report["as_of"], report["audited"]),
        "coverage: {}".format(six),
        "targets: missing={} stale={} retryable={} -> frozen {}{}".format(
            len(report["missing"]), len(report["stale"]), len(report["retryable"]),
            len(report["repair_targets"]), truncated_note),
    ]
    if report["repair"]:
        parts.append("repair: success={} failed={}".format(
            report["repair_success"], report["repair_failed"]))
    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Core
# ---------------------------------------------------------------------------

def run_reconcile(*, store: MarketStore, client: Any, repair: bool = False,
                  max_targets: int = DEFAULT_MAX_TARGETS,
                  stale_after_days: int = DEFAULT_STALE_AFTER_DAYS,
                  notifier: Optional[Callable[[str], None]] = None,
                  as_of: Optional[str] = None,
                  lock: Optional[Any] = None,
                  overrides: Optional[Dict[str, str]] = None,
                  profiles_mirror_path: Optional[Path] = None,
                  json_output: bool = False) -> Tuple[int, List[str]]:
    """Pure(ish)-function reconcile core — every I/O dependency is injected
    (client, notifier, lock) so tests never touch the network, the real lock
    file, or data/. Returns (exit_code, frozen_repair_targets).

    `lock` is keyword-only with no usable default under `repair=True`
    (Ruling #13, mirroring T10's `run_backfill`, which makes `lock` a
    mandatory parameter for exactly this reason): a silent no-op default
    would let a future caller of `run_reconcile(repair=True)` write against
    market.db with no writer lock, which is the exact hazard Ruling #13
    exists to prevent. Every `--repair` caller must make an explicit
    locking decision — `FileLock()` in production, a fake in tests. Passing
    `repair=True` without `lock` raises `ValueError` immediately, before
    anything is touched. Report-only calls stay lock-free (`lock` is never
    even inspected when `repair=False`). `main()` is the only caller that
    constructs a real `FileLock`.
    """
    as_of = as_of or date.today().isoformat()
    as_of_ts = _normalize_as_of(as_of)
    overrides = overrides or {}

    if repair and lock is None:
        raise ValueError(
            "lock is required when repair=True — pass FileLock() (production) or a "
            "test fake (Ruling #13: no silent default may write against market.db "
            "unlocked)"
        )

    active_lock = None
    if repair:
        active_lock = lock
        if not active_lock.acquire():
            print("reconcile: {} is held by another writer — skipping (exit {})".format(
                getattr(active_lock, "path", "market_db_writer"), EXIT_LOCK_BUSY))
            return EXIT_LOCK_BUSY, []

    try:
        identity_report = {"queued": 0, "upgraded": []}
        if repair:
            identity_report = _run_identity_phase(store, client, as_of_ts, overrides)

        try:
            symbols = current_base_universe(store)
        except RuntimeError as exc:
            print(f"reconcile: universe resolution failed: {exc} (exit {EXIT_EMPTY_UNIVERSE})")
            return EXIT_EMPTY_UNIVERSE, []

        audit = _phase1_audit(
            store, symbols, as_of_ts, stale_after_days, persist_stale=repair
        )

        all_targets = sorted(audit["missing"] | audit["stale"] | audit["retryable"])
        frozen = all_targets[:max_targets]
        truncated = len(all_targets) > max_targets

        print("reconcile as_of={} audited={} missing={} stale={} retryable={} "
              "repair_targets={}{}".format(
                  as_of, len(symbols), len(audit["missing"]), len(audit["stale"]),
                  len(audit["retryable"]), len(frozen),
                  " (truncated from {})".format(len(all_targets)) if truncated else ""))
        if frozen:
            print("  " + ", ".join(frozen[:50]))

        repair_success = repair_failed = 0
        if repair and frozen:
            observed_ts = _now_iso()   # full UTC timestamp — CONTROLLER RULING #11
            for symbol in frozen:
                statuses = collect_fundamentals_for_symbol(
                    symbol, client=client, store=store, observed_at=observed_ts)
                if "fetch_failed" in statuses.values():
                    repair_failed += 1
                else:
                    repair_success += 1
            if profiles_mirror_path is not None:
                try:
                    rebuild_profiles_json(store, profiles_mirror_path)
                except Exception as exc:
                    logger.warning("profiles.json mirror refresh failed: %s", exc)

        report = {
            "as_of": as_of, "repair": repair, "audited": len(symbols),
            "status_counts": audit["status_counts"],
            "missing": sorted(audit["missing"]), "stale": sorted(audit["stale"]),
            "retryable": sorted(audit["retryable"]),
            "repair_targets": frozen, "truncated": truncated,
            "candidates_total": len(all_targets),
            "repair_success": repair_success, "repair_failed": repair_failed,
            "identity_queue": identity_report,
        }

        if json_output:
            print(json.dumps(report, default=str))

        if notifier is not None:
            notifier(_summary_message(report))

        return EXIT_OK, frozen
    finally:
        if active_lock is not None:
            active_lock.release()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Weekly extended-universe fundamentals coverage reconciliation: "
                    "audit -> freeze repair targets -> bounded repair -> Telegram summary.")
    parser.add_argument("--repair", action="store_true",
                        help="Run the identity re-probe queue plus a bounded repair over "
                             "the frozen target list. Omit for a report-only audit "
                             "(read-only, zero database writes).")
    parser.add_argument("--max-targets", type=int, default=DEFAULT_MAX_TARGETS,
                        help="Cap on repair targets per run (lexicographic truncation).")
    parser.add_argument("--stale-after-days", type=int, default=DEFAULT_STALE_AFTER_DAYS,
                        help="A statement dataset's latest fiscal date older than this "
                             "many days from --as-of is marked stale.")
    parser.add_argument("--json", action="store_true",
                        help="Also emit the audit report as one JSON line.")
    parser.add_argument("--as-of", default=None,
                        help="YYYY-MM-DD reconciliation anchor date (default: today).")
    args = parser.parse_args(argv)

    if args.max_targets <= 0:
        parser.error("--max-targets must be a positive integer")
    if args.stale_after_days <= 0:
        parser.error("--stale-after-days must be a positive integer")
    if args.as_of:
        try:
            _parse_date(args.as_of)
        except ValueError:
            parser.error(f"--as-of must be YYYY-MM-DD, got {args.as_of!r}")
    return args


def main(argv: Optional[List[str]] = None) -> None:
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    args = parse_args(argv)

    from src.data.fmp_client import FMPClient
    from src.telegram_bot import send_message

    lock = FileLock() if args.repair else None
    if lock is not None and not lock.acquire():
        print("reconcile: {} is held by another writer — skipping (exit {})".format(
            getattr(lock, "path", "market_db_writer"), EXIT_LOCK_BUSY))
        sys.exit(EXIT_LOCK_BUSY)

    try:
        # MarketStore initialization executes additive schema DDL; repair mode
        # must therefore acquire the writer lock before constructing it.
        store = MarketStore(read_only=not args.repair)
        client = FMPClient()

        def notifier(message: str) -> None:
            try:
                send_message(message, channel="private")
            except Exception as exc:  # Telegram failure must not change the run's conclusion
                logger.warning("telegram send failed: %s", exc)

        rc, _targets = run_reconcile(
            store=store, client=client, repair=args.repair,
            max_targets=args.max_targets, stale_after_days=args.stale_after_days,
            notifier=notifier, as_of=args.as_of, lock=lock,
            overrides=_load_overrides(),
            profiles_mirror_path=DEFAULT_PROFILES_PATH if args.repair else None,
            json_output=args.json,
        )
    finally:
        if lock is not None:
            lock.release()
    sys.exit(rc)


if __name__ == "__main__":
    main()
