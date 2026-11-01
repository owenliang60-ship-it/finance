#!/usr/bin/env python3
"""M12 health report: frozen quarter-end boards → one fixed-format HTML per scheme + summary.json.

Read-only on both databases; writes only to --out. Plan:
docs/plans/2026-11-01-prosperity-m12-health-report.md. Exit codes: 0 written; 3 a database is
missing or a scheme has no quarter-end board for the code version (nothing written);
4 invalid arguments (unknown scheme, --out is a non-directory); 6 an input database changed
while it was read (nothing written).

The BH family always covers all four registered schemes, whatever --schemes selects, so a
subset run reports the same p_bh as the full run.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backtest.research.prosperity_health import (PriceBook, apply_bh, board_view,  # noqa: E402
                                                 load_fundamentals, scheme_report)
from backtest.research.prosperity_health_html import render  # noqa: E402
from src.data.prosperity_backup import _sha  # noqa: E402
from src.data.market_store import MarketStore  # noqa: E402
from src.data.prosperity_storage import BoardKey  # noqa: E402
from src.data.prosperity_store import ProsperityStore  # noqa: E402
from terminal.prosperity.schemes import SCHEMES, scheme_hash  # noqa: E402
from terminal.prosperity.version import code_version  # noqa: E402

DEFAULT_SCHEMES = ('F1', 'F1-rank', 'F1-exfin', 'F0')
BH_SCHEMES = DEFAULT_SCHEMES                 # fixed BH family: every scheme, every run (review #4)


def _json_default(value):
    if isinstance(value, (set, tuple)):
        return list(value)
    raise TypeError(type(value).__name__)


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description='Prosperity health report (local, read-only)')
    parser.add_argument('--db', type=Path, required=True, help='prosperity.db snapshot')
    parser.add_argument('--market-db', type=Path, required=True, help='market.db copy')
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--schemes', default=','.join(DEFAULT_SCHEMES))
    parser.add_argument('--code-version', default=None)
    args = parser.parse_args(argv)

    schemes = [s.strip() for s in args.schemes.split(',') if s.strip()]
    unknown = [s for s in schemes if s not in SCHEMES]
    if not schemes or unknown:
        print(f'unknown scheme: {", ".join(unknown) or "(none)"}', file=sys.stderr)
        return 4
    if args.out.exists() and not args.out.is_dir():
        print(f'--out is not a directory: {args.out}', file=sys.stderr)
        return 4
    for path in (args.db, args.market_db):
        if not path.is_file():
            print(f'database not found: {path}', file=sys.stderr)
            return 3
    version = args.code_version or code_version()
    started = time.monotonic()
    hashes = {'db': _sha(args.db), 'market': _sha(args.market_db)}       # before any read (review #7)
    computed = list(dict.fromkeys([*BH_SCHEMES, *schemes]))

    with ProsperityStore(args.db, read_only=True) as store:
        boards, weekly = {}, {}
        for sid in computed:
            h = scheme_hash(SCHEMES[sid])
            keys = store.list_boards(scheme_hash=h, code_version=version, board_type='quarter_final')
            if not keys:
                print(f'{sid}: no quarter_final board for code_version {version}', file=sys.stderr)
                return 3
            boards[sid] = [board_view(store.get_board(k)) for k in keys]
            live = store._conn.execute(                                  # header only: skip replay boards
                "SELECT as_of FROM board WHERE board_type='weekly' AND mode='live' AND scheme_hash=? "
                'AND code_version=? ORDER BY as_of', (h, version)).fetchall()
            weekly[sid] = [board_view(store.get_board(BoardKey('weekly', r[0], h, version))) for r in live]

    symbols = {r.symbol for views in boards.values() for v in views for r in v.rows if r.status != 'excluded'}
    market = MarketStore(db_path=args.market_db, read_only=True)
    try:
        prices = PriceBook.load(market, symbols)
        fundamentals = load_fundamentals(market, symbols)
    finally:
        market.close()

    if {'db': _sha(args.db), 'market': _sha(args.market_db)} != hashes:
        print('an input database changed while it was read; nothing written', file=sys.stderr)
        return 6
    memo: dict = {}
    every = {sid: scheme_report(boards[sid], weekly[sid], prices, fundamentals, memo=memo) for sid in computed}
    family = apply_bh({sid: every[sid] for sid in BH_SCHEMES})
    reports = {sid: every[sid] for sid in schemes}
    inputs = {'prosperity_db': str(args.db), 'prosperity_db_sha256': hashes['db'],
              'market_db': str(args.market_db), 'market_db_sha256': hashes['market'],
              'code_version': version, 'generated_at': datetime.now(timezone.utc).isoformat(timespec='seconds'),
              'elapsed_seconds': round(time.monotonic() - started, 1)}
    summary = {'generated_at': inputs['generated_at'], 'inputs': inputs, 'bh_family': family,
               'schemes': reports}

    args.out.mkdir(parents=True, exist_ok=True)
    for sid in schemes:
        (args.out / f'{sid}.html').write_text(render(reports[sid], scheme_id=sid, inputs=inputs), encoding='utf-8')
    (args.out / 'summary.json').write_text(
        json.dumps(summary, ensure_ascii=False, indent=1, default=_json_default) + '\n', encoding='utf-8')
    print(json.dumps({'out': str(args.out), 'schemes': schemes, 'bh_family': family,
                      'elapsed_seconds': inputs['elapsed_seconds']}, ensure_ascii=False))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
