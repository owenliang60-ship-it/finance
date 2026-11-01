#!/usr/bin/env python3
"""M12 A8: the point-in-time Premium adapter vs the production Premium pool file.

Rebuilds Premium with the production function `premium_members` on the 2026-10-31 live F1 weekly
board (members + archived packet beta), as_of 2026-10-30 (the Premium file's as_of), and compares
member by member with a read-only copy of the cloud `data/pool/premium_pool.json`.
Usage: premium_check.py --db prosperity.db --market-db market.db --premium premium_pool.json --out DIR
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backtest.research.prosperity_health import board_view, load_fundamentals, premium_members, universe  # noqa: E402
from src.data.market_store import MarketStore  # noqa: E402
from src.data.prosperity_storage import BoardKey  # noqa: E402
from src.data.prosperity_store import ProsperityStore  # noqa: E402
from terminal.prosperity.schemes import SCHEMES, scheme_hash  # noqa: E402
from terminal.prosperity.version import code_version  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--db', required=True, type=Path)
    ap.add_argument('--market-db', required=True, type=Path)
    ap.add_argument('--premium', required=True, type=Path)
    ap.add_argument('--out', required=True, type=Path)
    ap.add_argument('--live-as-of', default='2026-10-31')
    a = ap.parse_args()
    prem = json.loads(a.premium.read_text())
    with ProsperityStore(a.db, read_only=True) as store:
        v = board_view(store.get_board(BoardKey('weekly', a.live_as_of, scheme_hash(SCHEMES['F1']), code_version())))
    members = universe(v)
    symbols = sorted(r.symbol for r in members)
    sha = hashlib.sha256('\n'.join(symbols).encode('utf-8')).hexdigest()
    market = MarketStore(db_path=a.market_db, read_only=True)
    try:
        funds = load_fundamentals(market, symbols)
    finally:
        market.close()
    ours = premium_members(v, funds, as_of=prem['as_of'])
    theirs = {m['symbol']: m for m in prem['members']}
    mine = set(ours['members'])
    beta = {r.symbol: r.beta for r in members}
    lines = [f'premium_pool.json as_of {prem["as_of"]}, members {len(theirs)}, universe {prem["universe"]["count"]}',
             f'live board {a.live_as_of}: universe {len(symbols)}; universe sha256 match: '
             f'{sha == prem["universe"]["symbols_sha256"]}',
             f'recomputed members {len(mine)}; both {len(mine & set(theirs))}; '
             f'only recomputed {len(mine - set(theirs))}; only production {len(set(theirs) - mine)}']
    for s in sorted(set(theirs) - mine):
        lines.append(f'  only production {s}: production beta_6m {theirs[s].get("beta_6m")}, archived beta {beta.get(s)}')
    for s in sorted(mine - set(theirs)):
        lines.append(f'  only recomputed {s}: archived beta {beta.get(s)}')
    a.out.mkdir(parents=True, exist_ok=True)
    (a.out / 'premium-check.txt').write_text('\n'.join(lines) + '\n')
    print('\n'.join(lines))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
