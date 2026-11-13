#!/usr/bin/env python3
"""M16 momentum board: this week's F1 board members → the 14-week V4 long board, one JSON.

Read-only on both databases; writes only --out (atomically). Plan:
docs/plans/2026-11-13-prosperity-m16-momentum-boards.md. Exit codes: 0 written; 4 invalid --out
(a directory, a missing parent, or a path that is an input database or one of its
-wal/-shm/.state files: os.replace would destroy it); 6 anything else (nothing written; the last
stderr line names the reason, e.g. weekly_board_missing, momentum_prices_stale,
momentum_window_short, momentum_coverage_below_floor).

The universe is `meta.members` of the live weekly F1 board for --as-of and --code-version, so the
board and the momentum list always describe the same Extended pool.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backtest.research.momentum_board import build_payload, load_dollar_volumes  # noqa: E402
from backtest.research.prosperity_health import PriceBook  # noqa: E402
from scripts.prosperity_web import _overwrites_input  # noqa: E402  (identity-based, APFS-safe)
from src.data.market_store import MarketStore  # noqa: E402
from src.data.prosperity_pool import POOL_SCHEME_ID  # noqa: E402
from src.data.prosperity_storage import BoardKey  # noqa: E402
from src.data.prosperity_store import ProsperityStore  # noqa: E402
from terminal.prosperity.schemes import SCHEMES, scheme_hash  # noqa: E402


def board_members(db: Path, as_of: str, code_version: str) -> List[str]:
    with ProsperityStore(db, read_only=True) as store:
        stored = store.get_board(BoardKey('weekly', as_of, scheme_hash(SCHEMES[POOL_SCHEME_ID]), code_version))
    if stored is None or stored.meta.mode != 'live':
        raise ValueError('weekly_board_missing')
    return sorted(stored.meta.members)


def write_json(payload: dict, out: Path) -> None:
    fd, temp = tempfile.mkstemp(prefix='.momentum-', suffix='.tmp', dir=out.parent)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as handle:
            json.dump(payload, handle, ensure_ascii=False, sort_keys=True, allow_nan=False)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp, out)
        temp = None
    finally:
        if temp is not None:
            Path(temp).unlink(missing_ok=True)


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description='M16 momentum board (read-only)')
    parser.add_argument('--db', type=Path, required=True, help='prosperity.db (snapshot)')
    parser.add_argument('--market-db', type=Path, required=True, help='market.db, opened read-only')
    parser.add_argument('--code-version', required=True)
    parser.add_argument('--as-of', required=True, help='board Saturday, YYYY-MM-DD')
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args(argv)
    if args.out.is_dir() or not args.out.parent.is_dir():
        print(f'--out must be a file in an existing directory: {args.out}', file=sys.stderr)
        return 4
    out = args.out.parent.resolve() / args.out.name
    if any(_overwrites_input(out, path.resolve(), None) for path in (args.db, args.market_db)):
        print(f'--out would overwrite an input: {args.out}', file=sys.stderr)   # review P2: never replace an input
        return 4
    try:
        for path in (args.db, args.market_db):
            if not path.is_file():
                raise FileNotFoundError(f'database not found: {path.name}')
        members = board_members(args.db, args.as_of, args.code_version)
        market = MarketStore(db_path=args.market_db, read_only=True)
        try:
            payload = build_payload(
                book=PriceBook.load(market, members),
                dollar_volumes=lambda days: load_dollar_volumes(market, members, days),
                symbols=members, as_of=args.as_of,
                generated_at=datetime.now(timezone.utc).isoformat(timespec='seconds').replace('+00:00', 'Z'))
        finally:
            market.close()
        write_json(payload, out)
    except Exception as exc:
        print(f'{type(exc).__name__}: {exc}', file=sys.stderr)
        return 6
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
