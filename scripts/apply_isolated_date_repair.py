#!/usr/bin/env python3
"""One-time 2026-10-25 repair: isolated statement dates + EQR→VMRK identity.

Evidence: docs/audit/2026-10-25-isolated-statement-dates.md. Holds the shared
market_db_writer lock (busy → exit 75, nothing touched), takes a consistent
SQLite backup, then applies the frozen identity plan and settles CIK
0000906107 with config/share_class_overrides.json. A replay after success
fails on the stale row hashes and changes nothing.
"""
import argparse
import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_DIR))

from scripts.backfill_extended_fundamentals import EXIT_LOCK_BUSY, FileLock  # noqa: E402
from src.data.fiscal_repair import apply_reviewed_identity_updates  # noqa: E402
from src.data.market_store import MarketStore  # noqa: E402

PLAN = PROJECT_DIR / "docs/audit/isolated-dates-20261025/identity-plan.json"
OVERRIDES = PROJECT_DIR / "config/share_class_overrides.json"
CIK, PRIMARY, SECONDARY = "0000906107", "VMRK", "EQR"


def backup(db_path, out_dir):
    out_dir.mkdir(parents=True, exist_ok=True)
    target = out_dir / "market.db"
    if target.exists():
        raise FileExistsError(target)
    src = sqlite3.connect(Path(db_path).resolve().as_uri() + "?mode=ro", uri=True)
    try:
        dst = sqlite3.connect(str(target))
        try:
            src.backup(dst)
            if dst.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                raise RuntimeError("backup quick_check failed")
        finally:
            dst.close()
    finally:
        src.close()
    return target


def settle_vmrk(store):
    if json.loads(OVERRIDES.read_text()).get(CIK) != PRIMARY:
        raise ValueError("share_class_overrides.json must name VMRK for CIK " + CIK)
    rows = {r["symbol"]: dict(r) for r in store._get_conn().execute(
        "SELECT * FROM security_master WHERE cik = ?", (CIK,)).fetchall()}
    if set(rows) != {PRIMARY, SECONDARY}:
        raise ValueError("unexpected CIK group: {}".format(sorted(rows)))
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    store.upsert_security_master([
        {**rows[PRIMARY], "eligible": 1, "reason": "ok", "share_class_of": None, "updated_at": now},
        {**rows[SECONDARY], "eligible": 0, "reason": "secondary_share_class",
         "share_class_of": PRIMARY, "updated_at": now},
    ])
    store.upsert_coverage_status([
        {"symbol": s, "dataset": "identity", "status": "ok",
         "detail": "reviewed rename 2026-10-25 (SEC EDGAR CIK 0000906107)", "updated_at": now}
        for s in (PRIMARY, SECONDARY)])


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--db", default=str(PROJECT_DIR / "data/market.db"))
    p.add_argument("--backup-dir", required=True)
    args = p.parse_args(argv)
    lock = FileLock()
    if not lock.acquire():
        print("market_db_writer lock busy", file=sys.stderr)
        return EXIT_LOCK_BUSY
    try:
        print("backup", backup(args.db, Path(args.backup_dir)))
        store = MarketStore(db_path=args.db)
        try:
            print("dates", apply_reviewed_identity_updates(store, json.loads(PLAN.read_text())))
            settle_vmrk(store)
            print("vmrk settled")
            if store._get_conn().execute("PRAGMA quick_check").fetchone()[0] != "ok":
                raise RuntimeError("quick_check failed after repair")
        finally:
            store.close()
    finally:
        lock.release()
    return 0


if __name__ == "__main__":
    sys.exit(main())
