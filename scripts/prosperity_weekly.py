#!/usr/bin/env python3
"""Calculate immutable prosperity boards; production callers hold cron_wrapper locks.

Exit codes: 0 done; 3 a gate failed; 4 invalid arguments; 5 boards computed, publication
unconfirmed; 6 error; 7 boards published, Telegram delivery (M13, --delivery-dir) failed.
Delivery runs only for a live week whose publication is `published` or `already_published`;
replay modes accept --delivery-dir (the cron wrapper always passes it) and never deliver.
"""
from __future__ import annotations

import argparse
from dataclasses import asdict
from datetime import timedelta
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.data.market_store import MarketStore
from src.data.prosperity_backup import backup_bundle, check_space
from src.data.prosperity_gates import CST, instant
from src.data.prosperity_pool import pool_publisher
from src.data.prosperity_run import rebuild_freeze_chain, run_week, this_saturday, utcnow
from src.data.prosperity_storage import canonical
from src.data.prosperity_store import ProsperityStore
from terminal.prosperity.freeze import saturday
from terminal.prosperity.version import code_version


class Parser(argparse.ArgumentParser):
    def error(self, message):
        raise ValueError(message)


def parse_args(argv, now):
    p = Parser(description=__doc__)
    group = p.add_mutually_exclusive_group(required=True)
    group.add_argument('--live', action='store_true')
    group.add_argument('--as-of')
    group.add_argument('--rebuild-chain', action='store_true')
    p.add_argument('--through')
    p.add_argument('--db', type=Path, required=True)
    p.add_argument('--market-db', type=Path, default=ROOT / 'data/market.db')
    p.add_argument('--quality-dir', type=Path, default=ROOT / 'data/quality')
    p.add_argument('--observed-at')
    p.add_argument('--snapshot-sha256', help='optional SHA256 of the fixed input snapshot')
    p.add_argument('--backup-dir', type=Path, help='matched SQLite/state backups, retaining two sets')
    p.add_argument('--pool-path', type=Path,
                   help='publish the M10 pool file here after a live week; omitted = no publication')
    p.add_argument('--delivery-dir', type=Path,
                   help='M13: send the web page to Telegram after a published live week; ledger and outputs here')
    args = p.parse_args(argv)
    current = this_saturday(now())
    if args.db.resolve() == args.market_db.resolve():
        raise ValueError('result_database_must_differ_from_market')
    if args.pool_path is not None:
        pool = args.pool_path.resolve()
        databases = {args.db.resolve(), args.market_db.resolve()}
        # The pool file is replaced atomically; it must never land on (or beside, as a WAL/SHM) a database.
        if pool in databases or any(pool == Path(str(d) + s) for d in databases for s in ('-wal', '-shm', '.state')):
            raise ValueError('pool_path_must_differ_from_databases')
    if args.delivery_dir is not None:
        target = args.delivery_dir.resolve()
        databases = {args.db.resolve(), args.market_db.resolve()}
        if target in databases or any(target == Path(str(d) + s) for d in databases for s in ('-wal', '-shm', '.state')):
            raise ValueError('delivery_dir_must_differ_from_databases')
        if args.live and args.pool_path is None:
            raise ValueError('--delivery-dir with --live requires --pool-path')
    if args.through and not args.rebuild_chain:
        raise ValueError('--through requires --rebuild-chain')
    if args.observed_at and not args.live:
        raise ValueError('--observed-at requires --live')
    if args.live:
        if instant(now()).astimezone(CST).date() != current:
            raise ValueError('--live requires Saturday CST')
        if args.observed_at:
            at = instant(args.observed_at)
            if at > instant(now()) or at.astimezone(CST).date() != current:
                raise ValueError('observation must be this Saturday and not in the future')
    else:
        target = args.as_of or args.through or (current-timedelta(days=7)).isoformat()
        if saturday(target) > current-timedelta(days=7):
            raise ValueError('replay requires a past Saturday before this week')
        if args.rebuild_chain:
            args.through = target
    if args.snapshot_sha256:
        import re
        if not re.fullmatch('[0-9a-fA-F]{64}', args.snapshot_sha256):
            raise ValueError('invalid snapshot SHA256')
    return args


def main(argv=None, *, now=utcnow):
    try:
        args = parse_args(argv, now)
    except (ValueError, TypeError) as exc:
        print('invalid arguments: ' + str(exc), file=sys.stderr)
        return 4
    try:
        check_space(args.db)  # before creating a DB or starting a backup
        sha = subprocess.run(['git', 'rev-parse', 'HEAD'], cwd=ROOT, check=True,
                             capture_output=True, text=True).stdout.strip()
        market = MarketStore(db_path=args.market_db, read_only=True)
        try:
            if args.backup_dir and args.db.exists():
                backup_bundle(args.db, args.backup_dir)
            with ProsperityStore(args.db, read_only=False) as store:
                common = dict(market=market, store=store, now=now, git_sha=sha)
                if args.rebuild_chain:
                    out = rebuild_freeze_chain(through=args.through, **common)
                else:
                    publishers = () if args.pool_path is None else (
                        pool_publisher(store=store, market=market, path=args.pool_path, now=now),)
                    out = run_week(saturday=this_saturday(now()).isoformat() if args.live else args.as_of,
                                   mode='live' if args.live else 'replay', quality_dir=args.quality_dir,
                                   observed_at=args.observed_at, snapshot_sha256=args.snapshot_sha256,
                                   publishers=publishers, **common)
        finally:
            market.close()
        if args.backup_dir and out.exit_code in (0, 5):
            backup_bundle(args.db, args.backup_dir)
        publication = (out.detail or {}).get('publication')
        if (args.live and args.delivery_dir is not None and out.exit_code == 0
                and publication in ('published', 'already_published')):
            delivery = _deliver(args, now)
            print(canonical({**asdict(out), 'delivery': delivery}))
            return 7 if delivery['status'] == 'failed' else 0
        print(canonical(asdict(out)))
        return out.exit_code
    except Exception as exc:
        print(canonical({'exit_code': 6, 'error': f'{type(exc).__name__}: {exc}'}), file=sys.stderr)
        if 'insufficient_space' in str(exc) and args.db.is_file():
            # D5: alert only. Archiving/removal stays a manual, Boss-confirmed step.
            from scripts.prosperity_archive import archivable_hint
            try:
                print(archivable_hint(args.db, current=code_version()), file=sys.stderr)
            except Exception as hint_exc:
                print('archive hint unavailable: ' + str(hint_exc), file=sys.stderr)
        return 6


def _deliver(args, now) -> dict:
    """M13: never raises; an import error or any exception becomes a failed delivery (rc 7)."""
    try:
        from src.data.prosperity_delivery import deliver_week
        result = deliver_week(as_of=this_saturday(now()).isoformat(), code_version=code_version(),
                              db=args.db, market_db=args.market_db, pool_path=args.pool_path,
                              delivery_dir=args.delivery_dir, now=now)
        return asdict(result)
    except Exception as exc:
        return {'status': 'failed', 'sent': [], 'error': f'{type(exc).__name__}: {exc}', 'baseline': None}


if __name__ == '__main__':
    raise SystemExit(main())
