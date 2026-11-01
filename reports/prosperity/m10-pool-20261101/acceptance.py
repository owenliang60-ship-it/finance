"""M10 real-data acceptance on fixed read-only snapshots (plan Task 4 Step 2).

Builds the 2026-10-31 live pool with the production functions, then rebuilds every F1 weekly
board of the current code_version with the same rule. Replay boards are relabelled 'live' here
only, to pass build_pool's source check; production keeps no such switch.
"""
import argparse
from dataclasses import replace
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from src.data.market_store import MarketStore
from src.data.prosperity_pool import build_pool, liquidity_ranks, validate_prosperity_pool
from src.data.prosperity_store import ProsperityStore
from src.data.prosperity_storage import BoardKey
from terminal.prosperity.schemes import SCHEMES, scheme_hash
from terminal.prosperity.version import code_version


def sha(path):
    h = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1 << 20), b''):
            h.update(block)
    return h.hexdigest()


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--market-db', type=Path, required=True)
    p.add_argument('--db', type=Path, required=True)
    p.add_argument('--out', type=Path, required=True)
    p.add_argument('--live-as-of', default='2026-10-31')
    p.add_argument('--now', default='2026-11-01T04:00:00Z')  # after the 10-31 board's computed_at
    a = p.parse_args()
    now = datetime.fromisoformat(a.now.replace('Z', '+00:00'))
    version, f1 = code_version(), scheme_hash(SCHEMES['F1'])
    a.out.mkdir(parents=True, exist_ok=True)
    market = MarketStore(db_path=a.market_db, read_only=True)
    try:
        with ProsperityStore(a.db, read_only=True) as store:
            live = store.get_board(BoardKey('weekly', a.live_as_of, f1, version))
            assert live is not None and live.meta.mode == 'live', 'live board missing'
            pool = build_pool(live, liquidity_ranks(market, live.meta.members, a.live_as_of),
                              generated_at=a.now)
            validate_prosperity_pool(pool, now=now)
            (a.out / 'pool-20261031.json').write_text(json.dumps(pool, ensure_ascii=False, indent=2) + '\n')
            history = []
            for key in store.list_boards(scheme_hash=f1, code_version=version, board_type='weekly'):
                stored = store.get_board(key)
                row = {'as_of': key.as_of, 'mode': stored.meta.mode}
                stored = replace(stored, meta=replace(stored.meta, mode='live'))
                try:
                    built = build_pool(stored, liquidity_ranks(market, stored.meta.members, key.as_of),
                                       generated_at=a.now)
                    row.update(selected=built['coverage']['selected'],
                               strict=sum(m['grade'] == 'STRICT' for m in built['members']),
                               eligible=built['coverage']['eligible'],
                               ranked_ratio=round(built['coverage']['ranked']['ratio'], 4),
                               window=built['coverage']['trading_window'])
                    try:
                        validate_prosperity_pool(built, check_freshness=False)
                        row['validation'] = 'pass'
                    except Exception as exc:
                        row['validation'] = f'{type(exc).__name__}: {exc}'
                except Exception as exc:
                    row['build_error'] = f'{type(exc).__name__}: {exc}'
                history.append(row)
    finally:
        market.close()
    summary = {'code_version': version, 'f1_scheme_hash': f1,
               'market_db_sha256': sha(a.market_db), 'prosperity_db_sha256': sha(a.db),
               'live': {'as_of': a.live_as_of, 'selected': pool['coverage']['selected'],
                        'strict': sum(m['grade'] == 'STRICT' for m in pool['members']),
                        'eligible': pool['coverage']['eligible'],
                        'window': pool['coverage']['trading_window']},
               'history': history}
    (a.out / 'history.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps({k: summary[k] for k in ('code_version', 'live')}, ensure_ascii=False))
    for row in history:
        print(json.dumps(row, ensure_ascii=False))


if __name__ == '__main__':
    main()
