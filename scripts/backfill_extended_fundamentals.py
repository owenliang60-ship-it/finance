"""Extended fundamentals backfill runner (T10, R6/R14) — the production driver.

Turns "the frozen universe" into rows in `market.db`: take the shared writer
lock, freeze a target list through the T7 resolver, drive the T8 collection
kernel one symbol at a time, and record every dataset outcome in the T9
manifest. Nothing here does its own fetching or writing — the kernel is the
single write path (P1-3/P1-4), and this module only decides WHO gets
collected, in what order, and when to stop.

Four invariants make an unattended cron run safe to interrupt:

  1. LOCK FIRST (MF-2). `/tmp/finance-cron-locks/resource-market_db_writer.lock`
     — the same file and the same non-blocking semantics as
     `scripts/cron_wrapper.sh:83-96`'s `FINANCE_CRON_RESOURCE_KEY` path. Busy
     means another writer owns market.db right now, so we exit 75 having
     touched NOTHING: no manifest row, no provider call.

     ⚠️ Do NOT schedule this script under `cron_wrapper.sh` with
     `FINANCE_CRON_RESOURCE_KEY=market_db_writer`. flock is held per open
     file description, so the wrapper's lock is not inherited by this
     process — the child would contend with its own parent and exit 75 every
     single run. Either the wrapper holds the resource lock (and the job runs
     with `--no-lock`, which is otherwise test-only), or — the intended
     setup — the wrapper is invoked WITHOUT a resource key and this runner
     takes the lock itself.

  2. FROZEN TARGETS (R3-P1-1). `current_base_universe()` = active Extended
     membership ∩ SM eligible. Never the SM full set: historical and delisted
     identities are exactly what a default backfill must not spend quota on.
     Empty (or a fail-loud resolver error) → exit 2, because an empty
     denominator that silently "succeeds" is how coverage numbers start lying.

  3. CIRCUIT BREAKER (R14). Once ≥250 datasets have been processed, a
     fetch_failed rate above 20% aborts the run. A provider outage should
     cost 50 symbols of quota, not 950 — and the untouched remainder stays
     `pending`, so `--resume` picks up exactly where this left off.

  4. NO PERMANENT `in_progress`. Claimed-but-unfinished jobs are reset to
     pending in a `finally`, on every exit path including KeyboardInterrupt.
     A job stuck `in_progress` is invisible to `claim_pending_jobs` forever;
     that is a permanent hole, and it is worse than a loud crash.

Historical mode (`--include-historical --as-of DATE`, R4-P1-2 + R5-P1-1)
answers a different question: "does this company have enough filed history to
be RANKED as of that date?" Targets are therefore chosen by DATA
COMPLETENESS, never by membership — a current member whose tables only hold
the most recent 8 quarters needs a deeper pull for an as-of a year back just
as much as a name that has since dropped out of the pool. Coverage is
reported over ALL as-of candidates (`candidate_coverage_pct`); consumers of a
historical ranking must quote that number and must not claim a complete
ranking when it is short.

CLI:
    python scripts/backfill_extended_fundamentals.py --run-id <id> \\
        [--canary N] [--resume] [--limit-quarters 8] [--dry-run] [--no-lock] \\
        [--include-historical --as-of YYYY-MM-DD]

Exit codes: 0 success · 1 partial failure / circuit breaker · 2 empty or
fail-loud universe · 75 lock busy (matches cron_wrapper's
`FINANCE_CRON_LOCK_BUSY_RC=75` convention for "a peer is running").
"""
from __future__ import annotations

import argparse
import fcntl
import logging
import math
import os
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from config.settings import FUNDAMENTAL_QUARTER_GAP_MAX_DAYS  # noqa: E402
from src.data.fundamental_collector import (  # noqa: E402
    DATASETS,
    DEFAULT_PROFILES_PATH,
    collect_fundamentals_for_symbol,
    rebuild_profiles_json,
)
from src.data.market_store import COLLECTION_DATASET_TABLES, MarketStore  # noqa: E402
from src.data.universe_resolver import current_base_universe  # noqa: E402

logger = logging.getLogger(__name__)

# Same lock file cron_wrapper.sh builds from FINANCE_CRON_RESOURCE_KEY
# (`$LOCK_DIR/resource-<key>.lock`), honouring the same LOCK_DIR override.
LOCK_DIR = Path(os.environ.get("FINANCE_CRON_LOCK_DIR", "/tmp/finance-cron-locks"))
RESOURCE_KEY = "market_db_writer"
LOCK_PATH = LOCK_DIR / ("resource-" + RESOURCE_KEY + ".lock")

EXIT_OK = 0
EXIT_PARTIAL = 1
EXIT_EMPTY_UNIVERSE = 2
EXIT_LOCK_BUSY = 75

# R3-P1-3 (frozen): kernel dataset status -> manifest job terminal state.
# `provider_empty` deliberately keeps its own name rather than collapsing into
# `done` — "the provider has nothing" and "we have the data" must stay
# distinguishable downstream.
JOB_STATUS_MAP = {
    "ok": "done",
    "provider_empty": "provider_empty",
    "fetch_failed": "fetch_failed",
}

# R14 circuit breaker. The floor exists so a handful of genuinely-empty
# small caps at the head of the alphabet cannot trip a run before the failure
# rate means anything.
CIRCUIT_BREAKER_FAILURE_RATIO = 0.2
CIRCUIT_BREAKER_MIN_DATASETS = 250

PROGRESS_LOG_EVERY = 25

DEFAULT_LIMIT_QUARTERS = 8
DEFAULT_ASOF_QUARTERS = 8
DAYS_PER_QUARTER = 91
# Deep pulls are one FMP call with a `limit` (the client has no from/to
# window, audit §3.3), so the limit must cover as-of PLUS everything filed
# since. 40 quarters = 10 years, past which a deeper pull buys nothing the
# provider still serves.
HISTORICAL_LIMIT_QUARTERS_CAP = 40

# The three statement tables an as-of ranking reads. Derived from the kernel's
# map so a dataset→table rename cannot silently desync this check.
ASOF_WINDOW_TABLES = tuple(
    COLLECTION_DATASET_TABLES[key] for key in ("income", "balance", "cashflow")
)


# ---------------------------------------------------------------------------
# Lock
# ---------------------------------------------------------------------------

class FileLock:
    """Non-blocking `flock` on the shared market.db writer lock.

    `acquire()` returns False when a peer holds it rather than raising, so the
    caller can exit 75 before touching the manifest. Injected into
    `run_backfill` so tests never need a real file descriptor.
    """

    def __init__(self, path: Optional[Path] = None):
        self.path = Path(path) if path is not None else LOCK_PATH
        self._fd: Optional[int] = None

    def acquire(self) -> bool:
        if self._fd is not None:
            return True
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(str(self.path), os.O_CREAT | os.O_RDWR, 0o644)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            os.close(fd)
            return False
        self._fd = fd
        return True

    def release(self) -> None:
        if self._fd is None:
            return
        fd, self._fd = self._fd, None
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)


class NullLock:
    """`--no-lock`: test/manual escape hatch. Never use it on the cloud box
    alongside the cron writers — concurrent writers on market.db is exactly
    what the resource lock exists to prevent."""

    def acquire(self) -> bool:
        return True

    def release(self) -> None:
        return None


# ---------------------------------------------------------------------------
# As-of window helpers (R5-P1-1)
# ---------------------------------------------------------------------------

def _parse_date(value: str) -> date:
    if not value or not isinstance(value, str):
        raise ValueError("expected a YYYY-MM-DD date string, got {!r}".format(value))
    return datetime.strptime(value[:10], "%Y-%m-%d").date()


def has_asof_window(store: MarketStore, symbol: str, as_of: str,
                    quarters: int = DEFAULT_ASOF_QUARTERS) -> bool:
    """Does this symbol already hold enough filed history to be ranked at `as_of`?

    True only when ALL THREE current statement tables independently hold
    `quarters` fiscal dates inside
    `(as_of − quarters×91d − FUNDAMENTAL_QUARTER_GAP_MAX_DAYS, as_of]`, with
    every adjacent gap among those `quarters` rows ≤
    `FUNDAMENTAL_QUARTER_GAP_MAX_DAYS` (config SSOT, shared with T18's
    continuity check).

    Two deliberate choices:
      - the lower bound carries one max-gap of slack, because a company whose
        latest quarter ends well before `as_of` still needs 8 quarters of room
        underneath it;
      - only the most recent `quarters` in-window rows are gap-checked, so an
        ancient gap deep in the history cannot veto an otherwise contiguous
        recent run.

    A missing table row set, a short count, or one oversized gap all mean the
    same thing operationally: this symbol cannot be ranked at `as_of` from
    what we already have, so it belongs in the backfill's target list.
    """
    as_of_date = _parse_date(as_of)
    span_days = quarters * DAYS_PER_QUARTER + FUNDAMENTAL_QUARTER_GAP_MAX_DAYS
    window_start = as_of_date - timedelta(days=span_days)
    sym = symbol.upper()

    conn = store._get_conn()
    for table in ASOF_WINDOW_TABLES:
        # Table names come from COLLECTION_DATASET_TABLES, never from input.
        rows = conn.execute(
            "SELECT date FROM " + table + " WHERE symbol = ? AND date > ? "
            "AND date <= ? AND "
            "COALESCE(NULLIF(substr(accepted_date, 1, 10), ''), "
            "NULLIF(filing_date, '')) <= ? "
            "ORDER BY date DESC LIMIT ?",
            (sym, window_start.isoformat(), as_of_date.isoformat(),
             as_of_date.isoformat(), quarters),
        ).fetchall()
        if len(rows) < quarters:
            return False
        dates = [_parse_date(r["date"]) for r in rows]   # newest first
        for newer, older in zip(dates, dates[1:]):
            if (newer - older).days > FUNDAMENTAL_QUARTER_GAP_MAX_DAYS:
                return False
    return True


def deepen_limit_quarters(base_limit: int, as_of: str,
                          today: Optional[date] = None) -> int:
    """`8 + ceil(days_since(as_of) / 91)`, capped at 40, floored at `base_limit`.

    The FMP statement endpoints take a `limit` and no date window, so reaching
    8 quarters BEFORE `as_of` means also pulling everything filed since.
    """
    today = today or date.today()
    days = (today - _parse_date(as_of)).days
    if days <= 0:
        return base_limit
    deepened = min(DEFAULT_ASOF_QUARTERS + math.ceil(days / DAYS_PER_QUARTER),
                   HISTORICAL_LIMIT_QUARTERS_CAP)
    return max(base_limit, deepened)


# ---------------------------------------------------------------------------
# Target selection
# ---------------------------------------------------------------------------

def _run_symbols(store: MarketStore, run_id: str) -> List[str]:
    """The symbols already frozen into a run's manifest grid (`--resume`)."""
    conn = store._get_conn()
    rows = conn.execute(
        "SELECT DISTINCT symbol FROM fundamental_backfill_jobs WHERE run_id = ? "
        "ORDER BY symbol",
        [run_id],
    ).fetchall()
    return [r["symbol"] for r in rows]


def _historical_targets(store: MarketStore, as_of: str) -> Dict[str, Any]:
    """As-of candidates, split into "needs collecting" and "already covered".

    R5-P1-1: the skip test is `has_asof_window`, NOT current membership.
    `unverified` candidates (qualifying market cap, no SM row yet) are kept —
    the identity contract handles them downstream exactly as it does anywhere
    else; dropping them here would quietly reintroduce survivorship bias.
    """
    approx = store.approximate_members_as_of(as_of)
    candidates = list(approx["symbols"])
    unverified = approx.get("unverified") or []
    if unverified:
        logger.info("%d as-of candidate(s) have no security_master row yet "
                    "(unverified identity); collecting them anyway", len(unverified))
    covered_set = {s for s in candidates if has_asof_window(store, s, as_of)}
    return {
        "candidates": candidates,
        "targets": [s for s in candidates if s not in covered_set],
    }


def _coverage_report(store: MarketStore, candidates: List[str],
                     as_of: str) -> Dict[str, Any]:
    covered = [s for s in candidates if has_asof_window(store, s, as_of)]
    total = len(candidates)
    pct = (100.0 * len(covered) / total) if total else 0.0
    return {"covered": len(covered), "total": total, "pct": pct,
            "missing": [s for s in candidates if s not in set(covered)]}


def _print_coverage(report: Dict[str, Any], as_of: str) -> None:
    """The number a historical ranking MUST quote (R5-P1-1). Denominator is
    printed explicitly so nobody can mistake it for "of what we fetched"."""
    print("  candidate_coverage_pct={:.1f}% ({}/{} as-of candidates satisfy the "
          "{}Q window as of {}, denominator={})".format(
              report["pct"], report["covered"], report["total"],
              DEFAULT_ASOF_QUARTERS, as_of, report["total"]))
    if report["missing"]:
        preview = ", ".join(report["missing"][:20])
        print("  coverage_missing={} -> {}".format(len(report["missing"]), preview))


# ---------------------------------------------------------------------------
# Runner core
# ---------------------------------------------------------------------------

def _utc_now_ts() -> str:
    """Full UTC timestamp (CONTROLLER RULING #11).

    NOT a pure date: the kernel normalizes a bare date to `T00:00:00Z`, so two
    collections of the same symbol on the same day would share a vintage PK
    and a changed payload would surface as `fetch_failed` instead of being
    appended.
    """
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _resolve_targets(store: MarketStore, *, resume: bool, include_historical: bool,
                     as_of: Optional[str], run_id: str) -> Dict[str, Any]:
    """Frozen target list plus the historical bookkeeping the report needs.

    Returns {"targets", "candidates", "error"}; `error` set means exit 2.
    """
    if resume:
        frozen = _run_symbols(store, run_id)
        if frozen:
            # The as-of candidate list is the coverage report's denominator,
            # so a historical resume still has to recompute it — it is a
            # property of the as-of date, not of what this run collected.
            candidates = (store.approximate_members_as_of(as_of)["symbols"]
                          if include_historical else None)
            return {"targets": frozen, "candidates": candidates, "error": None}
        # Nothing frozen under this run_id yet: fall through and freeze now,
        # so `--resume` on a run that died before its manifest existed still
        # does the right thing instead of exiting empty.

    if include_historical:
        selection = _historical_targets(store, as_of)
        if not selection["candidates"]:
            return {"targets": [], "candidates": [], "error":
                    "no as-of candidates at {} — historical_market_cap has "
                    "nothing at or before that date".format(as_of)}
        return {"targets": selection["targets"],
                "candidates": selection["candidates"], "error": None}

    try:
        targets = current_base_universe(store)
    except RuntimeError as exc:
        return {"targets": [], "candidates": None,
                "error": "universe resolution failed: {}".format(exc)}
    if not targets:
        return {"targets": [], "candidates": None,
                "error": "active membership ∩ eligible is EMPTY — refusing to "
                         "run a backfill against an empty denominator"}
    return {"targets": targets, "candidates": None, "error": None}


def run_backfill(*, run_id: str, store: MarketStore, client: Any, lock: Any,
                 canary: Optional[int] = None, resume: bool = False,
                 limit_quarters: int = DEFAULT_LIMIT_QUARTERS,
                 dry_run: bool = False, include_historical: bool = False,
                 as_of: Optional[str] = None,
                 profiles_mirror_path: Optional[Path] = None) -> int:
    """Drive one backfill run. Every dependency is injected: tests never touch
    the network, the real lock file or `data/`.

    `profiles_mirror_path` is where the legacy `profiles.json` mirror is
    rebuilt once at the end of the batch (R2-P2-3 — once per run, never per
    symbol). None means "leave the mirror alone", which is what every test
    wants and what keeps this function free of real-filesystem side effects;
    `main()` passes the production path.

    Returns an exit code — see the module docstring's table.
    """
    if include_historical and not as_of:
        raise ValueError("--include-historical requires --as-of")

    if not lock.acquire():
        # Exit BEFORE the manifest exists: a busy lock must leave no trace
        # that a peer's run could later mistake for progress.
        print("backfill: {} is held by another writer — skipping (exit {})".format(
            getattr(lock, "path", RESOURCE_KEY), EXIT_LOCK_BUSY))
        return EXIT_LOCK_BUSY

    try:
        selection = _resolve_targets(store, resume=resume,
                                     include_historical=include_historical,
                                     as_of=as_of, run_id=run_id)
        if selection["error"]:
            print("backfill: {} (exit {})".format(selection["error"],
                                                  EXIT_EMPTY_UNIVERSE))
            return EXIT_EMPTY_UNIVERSE

        targets = list(selection["targets"])
        candidates = selection["candidates"]
        if canary is not None:
            targets = targets[:canary]      # already lexicographic

        effective_limit = limit_quarters
        if include_historical:
            effective_limit = deepen_limit_quarters(limit_quarters, as_of)

        if dry_run:
            print("backfill DRY RUN run_id={} targets={} limit_quarters={}".format(
                run_id, len(targets), effective_limit))
            print("  " + ", ".join(targets[:50]))
            if include_historical:
                _print_coverage(_coverage_report(store, candidates, as_of), as_of)
            return EXIT_OK

        if not targets:
            # Historical only: every candidate already has its window. Nothing
            # to collect is a success, but the coverage number still ships.
            print("backfill: nothing to collect for run_id={}".format(run_id))
            if include_historical:
                _print_coverage(_coverage_report(store, candidates, as_of), as_of)
            return EXIT_OK

        params = {
            "canary": canary, "limit_quarters": effective_limit,
            "include_historical": include_historical, "as_of": as_of,
            "datasets": list(DATASETS),
        }
        try:
            store.create_backfill_run(run_id, targets, list(DATASETS), params)
        except ValueError as exc:
            print("backfill: {} (exit {})".format(exc, EXIT_EMPTY_UNIVERSE))
            return EXIT_EMPTY_UNIVERSE

        # Recover jobs a SIGKILLed predecessor left claimed: `claim_pending_jobs`
        # only sees pending/fetch_failed, so an orphaned `in_progress` row is
        # otherwise invisible forever. No-op for a freshly frozen grid, and safe
        # under the lock we already hold (no peer can be mid-claim).
        recovered = store.reset_in_progress_jobs(run_id)
        if recovered:
            logger.info("recovered %d orphaned in_progress job(s) for run %s",
                        recovered, run_id)

        try:
            return _drive(run_id=run_id, store=store, client=client,
                          targets=targets, limit_quarters=effective_limit,
                          include_historical=include_historical, as_of=as_of,
                          candidates=candidates,
                          profiles_mirror_path=profiles_mirror_path)
        finally:
            # Never leave a job permanently in_progress — including on
            # KeyboardInterrupt, which the kernel deliberately lets through.
            store.reset_in_progress_jobs(run_id)
    finally:
        lock.release()


def _drive(*, run_id: str, store: MarketStore, client: Any, targets: List[str],
           limit_quarters: int, include_historical: bool,
           as_of: Optional[str], candidates: Optional[List[str]],
           profiles_mirror_path: Optional[Path] = None) -> int:
    observed_at = _utc_now_ts()
    processed_datasets = 0
    fetch_failed_events = 0
    collected_symbols = 0

    for index, symbol in enumerate(targets, start=1):
        claimed = store.claim_pending_jobs(run_id, symbol)
        if not claimed:
            continue                        # --resume: every dataset terminal
        claimed_set = set(claimed)

        def _job_writer(conn, dataset, status, detail=None, _symbol=symbol,
                        _claimed=claimed_set):
            # The kernel walks all five datasets; on a resume only some are
            # ours. Writing the others would reset a terminal job's status and
            # inflate its attempts, so they are skipped here — the data write
            # itself is an idempotent upsert and does no harm.
            if dataset not in _claimed:
                return
            store.complete_job_in_conn(conn, run_id, _symbol, dataset,
                                       JOB_STATUS_MAP[status], error=detail)

        statuses = collect_fundamentals_for_symbol(
            symbol, client=client, store=store, limit_quarters=limit_quarters,
            observed_at=observed_at, job_writer=_job_writer)

        collected_symbols += 1
        for dataset, status in statuses.items():
            if dataset not in claimed_set:
                continue
            processed_datasets += 1
            if status == "fetch_failed":
                fetch_failed_events += 1

        if index % PROGRESS_LOG_EVERY == 0:
            print("backfill progress: {}/{} symbols, {} datasets, {} failed".format(
                index, len(targets), processed_datasets, fetch_failed_events))

        if (processed_datasets >= CIRCUIT_BREAKER_MIN_DATASETS
                and fetch_failed_events / processed_datasets
                > CIRCUIT_BREAKER_FAILURE_RATIO):
            store.finish_run(run_id, "aborted")
            print("backfill ABORTED by circuit breaker: {}/{} datasets failed "
                  "({:.0%} > {:.0%}) after {} symbols; the remainder stays "
                  "pending — rerun with --resume".format(
                      fetch_failed_events, processed_datasets,
                      fetch_failed_events / processed_datasets,
                      CIRCUIT_BREAKER_FAILURE_RATIO, index))
            return EXIT_PARTIAL

    if collected_symbols and profiles_mirror_path is not None:
        _refresh_profiles_mirror(store, profiles_mirror_path)

    prog = store.run_progress(run_id)
    failed = prog.get("fetch_failed", 0)
    if prog["is_complete"]:
        store.finish_run(run_id, "complete")
    # Otherwise the header deliberately stays `running`: retriable failures
    # are what `--resume` is for, and `aborted` is reserved for the breaker.

    print("backfill run_id={} symbols={} jobs={} done={} provider_empty={} "
          "fetch_failed={} pending={} complete={}".format(
              run_id, prog["total_symbols"], prog["total_jobs"], prog["done"],
              prog["provider_empty"], failed, prog["pending"],
              prog["is_complete"]))
    if include_historical:
        _print_coverage(_coverage_report(store, candidates, as_of), as_of)

    return EXIT_OK if (prog["is_complete"] and failed == 0) else EXIT_PARTIAL


def _refresh_profiles_mirror(store: MarketStore, path: Path) -> None:
    """Once per batch, never per symbol (R2-P2-3). A mirror failure must not
    fail a run whose data is already durably committed in market.db — the
    mirror is a legacy read path, not the SSOT."""
    try:
        rebuild_profiles_json(store, path)
    except Exception as exc:
        logger.warning("profiles.json mirror refresh failed: %s", exc)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Backfill extended-universe fundamentals through the shared "
                    "collection kernel, under the market.db writer lock.")
    parser.add_argument("--run-id", required=True,
                        help="Manifest run id. Reuse it with --resume to continue.")
    parser.add_argument("--canary", type=int, default=None,
                        help="Only the lexicographically first N targets (smoke test).")
    parser.add_argument("--resume", action="store_true",
                        help="Reuse an existing run_id's frozen grid; only "
                             "non-terminal jobs are collected.")
    parser.add_argument("--limit-quarters", type=int, default=DEFAULT_LIMIT_QUARTERS,
                        help="Quarters per statement pull (default 8; historical "
                             "mode deepens this automatically).")
    parser.add_argument("--dry-run", action="store_true",
                        help="Print the frozen target list and exit; no manifest, "
                             "no provider calls.")
    parser.add_argument("--no-lock", action="store_true",
                        help="TEST ONLY: skip the market.db writer lock.")
    parser.add_argument("--include-historical", action="store_true",
                        help="Collect as-of candidates that lack an 8Q window "
                             "(requires --as-of).")
    parser.add_argument("--as-of", default=None,
                        help="YYYY-MM-DD as-of date for historical mode.")
    args = parser.parse_args(argv)

    if args.include_historical and not args.as_of:
        parser.error("--include-historical requires --as-of YYYY-MM-DD")
    if args.as_of:
        try:
            _parse_date(args.as_of)
        except ValueError:
            parser.error("--as-of must be YYYY-MM-DD, got {!r}".format(args.as_of))
    if args.canary is not None and args.canary <= 0:
        parser.error("--canary must be a positive integer")
    return args


def main(argv: Optional[List[str]] = None) -> None:
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    args = parse_args(argv)

    from src.data.fmp_client import FMPClient

    lock = NullLock() if args.no_lock else FileLock()
    if not lock.acquire():
        print("backfill: {} is held by another writer — skipping (exit {})".format(
            getattr(lock, "path", RESOURCE_KEY), EXIT_LOCK_BUSY))
        sys.exit(EXIT_LOCK_BUSY)
    try:
        # MarketStore.__init__ runs CREATE TABLE IF NOT EXISTS; even the first
        # construction is therefore a writer operation and belongs under the
        # shared lock (T10-M2).
        store = MarketStore()
        client = FMPClient()
        rc = run_backfill(
            run_id=args.run_id, store=store, client=client, lock=lock,
            canary=args.canary, resume=args.resume,
            limit_quarters=args.limit_quarters, dry_run=args.dry_run,
            include_historical=args.include_historical, as_of=args.as_of,
            profiles_mirror_path=DEFAULT_PROFILES_PATH,
        )
    finally:
        # run_backfill releases the idempotently acquired lock on normal paths;
        # this also covers MarketStore/FMPClient construction failures.
        lock.release()
    sys.exit(rc)


if __name__ == "__main__":
    main()
