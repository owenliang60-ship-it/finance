#!/usr/bin/env python3
"""One-time 2026-10-25 repair: PGR balance sheet monthly dates stored as quarters.

Evidence: docs/audit/2026-10-25-pgr-balance-dates.md. Holds the shared
market_db_writer lock (busy -> exit 75), takes a consistent SQLite backup, then:
1. relabels 2026-05-31 Q2 -> 2026-06-30 (values equal the 10-Q; hash-locked);
2. archives and deletes 2026-08-31 Q3 (a monthly 8-K figure; hash-locked);
3. re-collects PGR's balance sheet through the shared collector and recomputes
   metrics. Changes no code, so the prosperity code_version is unchanged.
A replay fails on the stale row hashes and changes nothing.
"""
import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_DIR))

from scripts.apply_isolated_date_repair import backup  # noqa: E402
from scripts.backfill_extended_fundamentals import EXIT_LOCK_BUSY, FileLock  # noqa: E402
from src.data.fiscal_repair import (  # noqa: E402
    _archive, apply_reviewed_identity_updates, fiscal_group_hash)
from src.data.market_store import MarketStore  # noqa: E402

PLAN = PROJECT_DIR / "docs/audit/pgr-balance-20261025/plan.json"


def remove_rows(store, plans):
    """Archive then delete exact hash-locked rows, all in one transaction."""
    removed = 0
    with store.transaction() as conn:
        for plan in plans:
            rows = [r for r in store._get_rows(plan["table"], plan["symbol"], limit=0)
                    if r["date"] == plan["date"]]
            if len(rows) != 1 or fiscal_group_hash(rows) != plan["row_hash"]:
                raise ValueError("current row hash differs from reviewed removal")
            _archive(conn, plan["table"], rows, "pgr-balance-20261025",
                     "reviewed_monthly_row_removed", {"plan": plan})
            cur = conn.execute("DELETE FROM {} WHERE symbol = ? AND date = ?".format(
                plan["table"]), (plan["symbol"], plan["date"]))
            if cur.rowcount != 1:
                raise ValueError("removal did not delete exactly one row")
            removed += 1
    return removed


def collect_balance(store, client):
    from src.data.fundamental_collector import collect_fundamentals_for_symbol
    observed = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    statuses = collect_fundamentals_for_symbol(
        "PGR", client=client, store=store, limit_quarters=8,
        observed_at=observed, dataset_keys=["balance"])
    if set(statuses.values()) != {"ok"}:
        raise RuntimeError("PGR balance collection incomplete: {}".format(statuses))
    return statuses


def run(store, client, plan):
    relabel = apply_reviewed_identity_updates(store, plan["relabel"])
    removed = remove_rows(store, plan["remove"])
    statuses = collect_balance(store, client)
    from src.data.metrics_calculator import compute_all_metrics
    metrics = compute_all_metrics(["PGR"], store=store)
    if store._get_conn().execute("PRAGMA quick_check").fetchone()[0] != "ok":
        raise RuntimeError("quick_check failed after repair")
    return {"relabel": relabel, "removed": removed, "balance": statuses, "metrics": metrics}


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    # market.db is cloud-owned (P3); a local copy is replaced by the next pull.
    p.add_argument("--db", default="/root/workspace/Finance/data/market.db")
    p.add_argument("--backup-dir", required=True)
    args = p.parse_args(argv)
    lock = FileLock()
    if not lock.acquire():
        print("market_db_writer lock busy", file=sys.stderr)
        return EXIT_LOCK_BUSY
    try:
        print("backup", backup(args.db, Path(args.backup_dir)))
        from src.data.fmp_client import FMPClient
        store = MarketStore(db_path=args.db)
        try:
            print(json.dumps(run(store, FMPClient(), json.loads(PLAN.read_text())), sort_keys=True))
        finally:
            store.close()
    finally:
        lock.release()
    return 0


if __name__ == "__main__":
    sys.exit(main())
