"""Street EPS history backfill for the prosperity D9 targets (M2 Task 5).

A thin wrapper over the weekly forward chain, reused verbatim:
`FMPClient.get_earnings` → `normalize_earnings` (fiscal match against the
estimates already in `fmp_estimates`; no new estimate pulls) →
`replace_fmp_earnings`, then `remap_unmatched_earnings` fills the remaining
`match_method='none'` rows from the income statement's fiscal dates
(`statement_window`).

Safety mirrors `backfill_extended_fundamentals.py`: the shared
`market_db_writer` lock (busy → exit 75, nothing touched), a per-targets-file
progress file for resume, and a breaker once 50 symbols have been tried with
>20% failures. `--remap-only` makes no API calls.

CLI:
    python scripts/backfill_street_eps.py --targets-file F \\
        [--remap-only] [--dry-run] [--progress PATH] [--no-lock]

Exit codes: 0 ok · 1 failures or breaker · 2 bad targets/progress file · 75 lock busy.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import logging
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.backfill_extended_fundamentals import (  # noqa: E402
    EXIT_EMPTY_UNIVERSE,
    EXIT_LOCK_BUSY,
    EXIT_OK,
    EXIT_PARTIAL,
    FileLock,
    NullLock,
    load_targets_file,
)
from src.data.fmp_forward_ingestion import normalize_earnings  # noqa: E402
from src.data.market_store import MarketStore  # noqa: E402

logger = logging.getLogger(__name__)

# Same depth as update_fmp_forward's backfill mode (EARNINGS_LIMIT_BACKFILL).
EARNINGS_LIMIT = 100
BREAKER_MIN_SYMBOLS = 50
BREAKER_FAILURE_RATIO = 0.2
PROGRESS_LOG_EVERY = 25
DEFAULT_PROGRESS_DIR = PROJECT_ROOT / "data" / "prosperity"


def _fiscal_dates(store: MarketStore, table: str, sql: str, symbol: str) -> List[str]:
    return [r[0] for r in store._get_conn().execute(sql.format(table=table),
                                                     (symbol,)).fetchall()]


def estimate_fiscal_dates(store: MarketStore, symbol: str) -> List[str]:
    """Every quarterly estimate fiscal date on hand, across all snapshots."""
    return _fiscal_dates(store, "fmp_estimates",
                         "SELECT DISTINCT fiscal_date FROM {table} WHERE symbol = ? "
                         "AND period_type = 'Q' ORDER BY fiscal_date", symbol)


def statement_fiscal_dates(store: MarketStore, symbol: str) -> List[str]:
    return _fiscal_dates(store, "income_quarterly",
                         "SELECT date FROM {table} WHERE symbol = ? ORDER BY date", symbol)


def _load_progress(path: Path, targets_sha: str) -> Optional[Dict[str, Any]]:
    if not path.exists():
        return {"targets_sha256": targets_sha, "done": [], "empty": [], "failed": {}}
    doc = json.loads(path.read_text(encoding="utf-8"))
    if doc.get("targets_sha256") != targets_sha:
        return None
    return doc


def _save_progress(path: Path, doc: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(doc, indent=1, sort_keys=True), encoding="utf-8")
    tmp.replace(path)


def _fetch_one(store: MarketStore, client: Any, symbol: str) -> Dict[str, Any]:
    raw = client.get_earnings(symbol, limit=EARNINGS_LIMIT)
    if not raw:
        return {"status": "empty", "rows": 0}
    rows, _ = normalize_earnings(symbol, raw, estimate_fiscal_dates(store, symbol))
    if not rows:
        # Non-empty payload with zero valid rows is an endpoint failure, exactly
        # as update_fmp_forward treats it.
        raise ValueError("earnings payload had no valid rows")
    store.replace_fmp_earnings(symbol, rows)
    return {"status": "done", "rows": len(rows)}


def run_street_eps(*, targets_file, store: MarketStore, client: Any, lock: Any,
                   progress_path: Path, remap_only: bool = False,
                   dry_run: bool = False) -> int:
    try:
        targets = load_targets_file(targets_file)
    except (OSError, ValueError) as exc:
        print("street_eps: {} (exit {})".format(exc, EXIT_EMPTY_UNIVERSE))
        return EXIT_EMPTY_UNIVERSE
    targets_sha = hashlib.sha256(Path(targets_file).read_bytes()).hexdigest()

    if dry_run:
        progress = _load_progress(Path(progress_path), targets_sha) or {"done": []}
        pending = [s for s in targets if s not in set(progress["done"])]
        print("street_eps DRY RUN targets={} pending={} remap_only={}".format(
            len(targets), len(pending), remap_only))
        return EXIT_OK

    if not lock.acquire():
        print("street_eps: market_db_writer lock busy — skipping (exit {})".format(
            EXIT_LOCK_BUSY))
        return EXIT_LOCK_BUSY
    try:
        if remap_only:
            changed = sum(store.remap_unmatched_earnings(s, statement_fiscal_dates(store, s))
                          for s in targets)
            print("street_eps remap-only: {} symbols, {} rows remapped".format(
                len(targets), changed))
            return EXIT_OK

        progress = _load_progress(Path(progress_path), targets_sha)
        if progress is None:
            print("street_eps: {} belongs to a different targets file (exit {})".format(
                progress_path, EXIT_EMPTY_UNIVERSE))
            return EXIT_EMPTY_UNIVERSE
        finished = set(progress["done"]) | set(progress["empty"])
        tried = failed = rows = remapped = 0
        for symbol in targets:
            if symbol in finished:
                continue
            tried += 1
            try:
                result = _fetch_one(store, client, symbol)
                remapped += store.remap_unmatched_earnings(
                    symbol, statement_fiscal_dates(store, symbol))
                progress[result["status"]].append(symbol)
                progress["failed"].pop(symbol, None)
                rows += result["rows"]
            except KeyboardInterrupt:
                raise
            except Exception as exc:
                failed += 1
                progress["failed"][symbol] = str(exc)[:200]
                logger.warning("street_eps %s failed: %s", symbol, exc)
            _save_progress(Path(progress_path), progress)

            if tried % PROGRESS_LOG_EVERY == 0:
                print("street_eps progress: {} tried, {} failed".format(tried, failed))
            if tried >= BREAKER_MIN_SYMBOLS and failed / tried > BREAKER_FAILURE_RATIO:
                print("street_eps ABORTED by breaker: {}/{} failed; rerun to resume".format(
                    failed, tried))
                return EXIT_PARTIAL

        print("street_eps: tried={} failed={} rows={} remapped={} done={} empty={} "
              "failed_total={}".format(tried, failed, rows, remapped,
                                       len(progress["done"]), len(progress["empty"]),
                                       len(progress["failed"])))
        return EXIT_OK if not progress["failed"] else EXIT_PARTIAL
    finally:
        lock.release()


def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--targets-file", required=True)
    parser.add_argument("--remap-only", action="store_true",
                        help="No API calls: only remap unmatched rows from income fiscal dates.")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--progress", default=None,
                        help="Progress file (default data/prosperity/street_eps_progress_<stem>.json)")
    parser.add_argument("--no-lock", action="store_true",
                        help="TEST ONLY: skip the market.db writer lock.")
    return parser.parse_args(argv)


def main(argv: Optional[List[str]] = None) -> None:
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    args = parse_args(argv)
    progress = Path(args.progress) if args.progress else (
        DEFAULT_PROGRESS_DIR / "street_eps_progress_{}.json".format(Path(args.targets_file).stem))
    lock = NullLock() if args.no_lock else FileLock()
    if not args.dry_run and not lock.acquire():
        print("street_eps: market_db_writer lock busy — skipping (exit {})".format(
            EXIT_LOCK_BUSY))
        sys.exit(EXIT_LOCK_BUSY)
    try:
        # MarketStore() runs CREATE TABLE IF NOT EXISTS — a writer op, so it
        # belongs under the lock (same as backfill_extended_fundamentals).
        store = MarketStore(read_only=args.dry_run)
        client = None
        if not (args.remap_only or args.dry_run):
            from src.data.fmp_client import FMPClient
            client = FMPClient()
        rc = run_street_eps(targets_file=args.targets_file, store=store, client=client,
                            lock=lock, progress_path=progress,
                            remap_only=args.remap_only, dry_run=args.dry_run)
    finally:
        lock.release()
    sys.exit(rc)


if __name__ == "__main__":
    main()
