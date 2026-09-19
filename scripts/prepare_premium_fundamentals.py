#!/usr/bin/env python3
"""Fill bounded Extended input gaps before building Premium (no threshold changes).

Default: read-only target report. --apply uses the shared writer lock and the
existing collector/metrics kernels. --no-lock is only for cron_wrapper's
inherited market_db_writer lock; it is verified before opening a writable DB.
Known failed/empty datasets remain owned by the regular reconciliation flow.
This is not the Extended earnings-event scheduler or a full-pool refresh.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import fcntl
import json
import os
from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.backfill_extended_fundamentals import FileLock, LOCK_PATH, NullLock
from src.data.fundamental_collector import collect_fundamentals_for_symbol
from src.data.market_store import MarketStore
from src.data.metrics_calculator import compute_all_metrics
from src.data.universe_resolver import current_base_universe


def find_repair_targets(store: MarketStore) -> dict[str, str]:
    targets = {}
    coverage = store.get_coverage("income_quarterly")
    for symbol in sorted(current_base_universe(store)):
        income = store.get_income(symbol, limit=1)
        if not income and coverage.get(symbol) is None:
            targets[symbol] = "never_collected"
        elif income and coverage.get(symbol) == "ok":
            metrics = store.get_metrics(symbol, limit=1)
            if not metrics or metrics[0]["date"] != income[0]["date"]:
                targets[symbol] = "metrics_not_current"
    return targets


def repair_inputs(store: MarketStore, *, client, max_targets: int) -> dict:
    """Caller holds the writer lock; freeze and cap targets before any writes."""
    targets = find_repair_targets(store)
    print(json.dumps({"premium_input_targets": targets}, sort_keys=True), flush=True)
    if len(targets) > max_targets:
        raise RuntimeError(f"Premium input target cap exceeded: {len(targets)} > {max_targets}")
    failed = []
    collected = []
    observed_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    for symbol in targets:
        statuses = collect_fundamentals_for_symbol(
            symbol, client=client, store=store, limit_quarters=8,
            observed_at=observed_at,
        )
        print(json.dumps({"symbol": symbol, "datasets": statuses}, sort_keys=True), flush=True)
        if any(status != "ok" for status in statuses.values()):
            failed.append(symbol)
        else:
            collected.append(symbol)
    metrics = {}
    if collected:
        metric_failures = []
        metrics = compute_all_metrics(collected, store=store, collect_failures=metric_failures)
        failed.extend(symbol for symbol in collected if symbol not in metrics or symbol in metric_failures)
    return {"targets": targets, "metrics": metrics, "failed": sorted(set(failed))}



def inherited_writer_lock():
    """Verify and reuse cron_wrapper's shared lock without releasing its fd."""
    if os.environ.get("FINANCE_CRON_RESOURCE_KEY") != "market_db_writer":
        raise ValueError("--no-lock requires cron_wrapper's market_db_writer lock")
    inherited = os.fstat(8)
    expected = LOCK_PATH.stat()
    if (inherited.st_dev, inherited.st_ino) != (expected.st_dev, expected.st_ino):
        raise ValueError("fd 8 is not the market_db_writer lock")
    fcntl.flock(8, fcntl.LOCK_EX | fcntl.LOCK_NB)
    return NullLock()


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--max-targets", type=int, default=50,
                        help="abort before writes above this count (default 50, ~250 calls)")
    parser.add_argument("--no-lock", action="store_true",
                        help="use cron_wrapper's inherited writer lock on fd 8")
    args = parser.parse_args(argv)
    if args.max_targets < 1:
        parser.error("--max-targets must be positive")
    lock = None
    if args.apply:
        try:
            lock = inherited_writer_lock() if args.no_lock else FileLock()
        except (OSError, ValueError) as exc:
            parser.error(str(exc))
        if not lock.acquire():
            return 75
    try:
        store = MarketStore(read_only=not args.apply)
        try:
            if args.apply:
                from src.data.fmp_client import fmp_client
                result = repair_inputs(store, client=fmp_client, max_targets=args.max_targets)
                print(json.dumps(result, sort_keys=True))
                return 1 if result["failed"] else 0
            print(json.dumps({"premium_input_targets": find_repair_targets(store)}, sort_keys=True))
            return 0
        finally:
            store.close()
    finally:
        if lock is not None:
            lock.release()


if __name__ == "__main__":
    raise SystemExit(main())
