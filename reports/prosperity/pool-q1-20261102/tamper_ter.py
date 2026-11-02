"""A3b: a pool file that inflates its own ranked count so an out-of-tier row (TER, rank 178) fits.

Writes pool-20261031-ter.json next to this script (M11 plan A4 ④ reads that path), then checks:
the production validator accepts it (the file alone cannot tell), board_mismatch returns 'ranked',
and verify_pool.py reports differences. Exit 1 if any of the three is not as expected.
"""
import argparse
import copy
import json
from pathlib import Path
import subprocess
import sys

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path.insert(0, str(ROOT))
from src.data.market_store import MarketStore  # noqa: E402
from src.data.prosperity_pool import (board_mismatch, liquidity_ranks, tier_cut,  # noqa: E402
                                      validate_prosperity_pool)
from src.data.prosperity_store import ProsperityStore  # noqa: E402
from src.data.prosperity_storage import BoardKey  # noqa: E402
from terminal.prosperity.schemes import SCHEMES, scheme_hash  # noqa: E402
from terminal.prosperity.version import code_version  # noqa: E402

INFLATED = 899          # = universe count, so ratio 1.0 and the file stays self-consistent
TARGET = 'TER'


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--market-db', type=Path, required=True)
    p.add_argument('--db', type=Path, required=True)
    p.add_argument('--as-of', default='2026-10-31')
    a = p.parse_args()
    pool = json.loads((HERE / 'pool-20261031.json').read_text())
    market = MarketStore(db_path=a.market_db, read_only=True)
    try:
        with ProsperityStore(a.db, read_only=True) as store:
            stored = store.get_board(BoardKey('weekly', a.as_of, scheme_hash(SCHEMES['F1']), code_version()))
        liq = liquidity_ranks(market, stored.meta.members, a.as_of)
    finally:
        market.close()
    row = next(r for r in stored.board.rows if r.symbol == TARGET)
    stock = next(s for s in stored.stocks if s['symbol'] == TARGET)
    real_cut = tier_cut(sum(r.status == 'ranked' for r in stored.board.rows))
    new_cut = tier_cut(INFLATED)
    assert row.status == 'ranked' and row.grade in ('STRICT', 'FULL'), row
    assert real_cut < row.rank <= new_cut and liq.ranks[TARGET] <= 100, (real_cut, row.rank, liq.ranks[TARGET])

    bad = copy.deepcopy(pool)
    cov = bad['coverage']
    cov['ranked'].update(covered=INFLATED, ratio=INFLATED / cov['ranked']['total'])
    cov['tier_cut'] = new_cut
    bad['members'].append({
        'symbol': TARGET, 'name': stock.get('name'), 'sector': stock.get('sector'),
        'industry': stock.get('industry'), 'rank': row.rank, 'grade': row.grade, 'score': row.score,
        'family_scores': dict(row.family_scores), 'badges': list(row.badges),
        'dollar_volume_rank': liq.ranks[TARGET], 'avg_dollar_volume': liq.averages[TARGET]})
    bad['members'].sort(key=lambda m: m['rank'])
    cov['selected'] = len(bad['members'])
    out = HERE / 'pool-20261031-ter.json'
    out.write_text(json.dumps(bad, ensure_ascii=False, indent=2) + '\n')

    lines = [f'{TARGET}: rank {row.rank}, grade {row.grade}, dollar-volume rank {liq.ranks[TARGET]}; '
             f'real tier_cut {real_cut}, inflated ranked {INFLATED} → tier_cut {new_cut}',
             f'written {out.name}: members {len(bad["members"])}']
    ok = True
    try:
        validate_prosperity_pool(bad, check_freshness=False)
        lines.append('validate_prosperity_pool: accepted (expected: the file alone is self-consistent)')
    except Exception as exc:                                            # noqa: BLE001
        ok = False
        lines.append(f'validate_prosperity_pool: rejected {type(exc).__name__}: {exc} (UNEXPECTED)')
    field = board_mismatch(bad, stored)
    lines.append(f'board_mismatch: {field!r} (expected \'ranked\')')
    ok &= field == 'ranked'
    v = subprocess.run([sys.executable, '-I', str(HERE / 'verify_pool.py'), '--market-db', str(a.market_db),
                        '--db', str(a.db), '--pool', str(out), '--as-of', a.as_of],
                       capture_output=True, text=True)
    diff_line = next((x for x in v.stdout.splitlines() if x.startswith('differences:')), '?')
    lines.append(f'verify_pool.py: exit {v.returncode}, {diff_line} (expected exit 1)')
    lines.extend('  ' + x.strip() for x in v.stdout.splitlines() if x.startswith('  ') and 'differs' in x)
    ok &= v.returncode == 1
    lines.append('A3b: ' + ('pass' if ok else 'FAIL'))
    print('\n'.join(lines))
    return 0 if ok else 1


if __name__ == '__main__':
    sys.exit(main())
