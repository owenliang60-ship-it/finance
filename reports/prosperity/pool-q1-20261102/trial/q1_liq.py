"""Read-only: inside F1 Q1 (top 20% by rank), does dollar-volume rank change forward returns?"""
import json, statistics, sys
from pathlib import Path
ROOT = Path('/Users/owen/CC workspace/Finance/.worktrees/prosperity-t7')
sys.path.insert(0, str(ROOT))
from backtest.research.prosperity_health import (PriceBook, board_view, evaluate_board, group_excess,
    _series, ranked_rows, universe, HORIZONS)
from src.data.market_store import MarketStore
from src.data.prosperity_pool import liquidity_ranks
from src.data.prosperity_store import ProsperityStore
from src.data.prosperity_storage import BoardKey
from terminal.prosperity.schemes import SCHEMES, scheme_hash
from terminal.prosperity.version import code_version

version, f1 = code_version(), scheme_hash(SCHEMES['F1'])
market = MarketStore(db_path=Path('/tmp/m12/market.db'), read_only=True)
boards = []
with ProsperityStore(Path('/tmp/m12/prosperity.db'), read_only=True) as store:
    for key in store.list_boards(scheme_hash=f1, code_version=version, board_type='quarter_final'):
        qf = store.get_board(key)
        boards.append((board_view(qf), liquidity_ranks(market, qf.meta.members, key.as_of).ranks))
prices = PriceBook.load(market, {r.symbol for v, _ in boards for r in universe(v)})
market.close()

out = {'periods': [], 'stats': {}}
for v, liq in boards:
    ranked = ranked_rows(v)
    n_q1 = len(ranked) // 5 + (1 if len(ranked) % 5 else 0)
    q1 = ranked[:sum(1 for i in range(len(ranked)) if i * 5 // len(ranked) == 0)]
    pool = [r for r in ranked if r.grade in ('STRICT', 'FULL') and liq.get(r.symbol, 10**9) <= 100]
    q1set = {r.symbol for r in q1}
    out['periods'].append({'as_of': v.as_of, 'ranked': len(ranked), 'q1': len(q1),
        'q1_grades': {g: sum(r.grade == g for r in q1) for g in sorted({str(r.grade) for r in q1})},
        'q1_liq100': sum(liq.get(r.symbol, 10**9) <= 100 for r in q1),
        'pool': len(pool), 'pool_in_q1': sum(r.symbol in q1set for r in pool),
        'pool_max_rank_pct': max(r.rank for r in pool) / len(ranked) if pool else None})
for h in HORIZONS:
    pts = {k: [] for k in ('q1', 'q1_liq', 'q1_rest', 'q1_hi', 'q1_lo', 'liq_minus_rest', 'hi_minus_lo',
                           'pool_in_q1', 'pool_out_q1', 'pool_in_minus_out')}
    rows = []
    for v, liq in boards:
        r = evaluate_board(v, prices, horizon=h)
        if r.status != 'complete':
            continue
        ranked = ranked_rows(v)
        q1 = [x.symbol for x in ranked if r.tiers[x.symbol] == 0]
        by_liq = sorted(q1, key=lambda s: liq.get(s, 10**9))
        half = len(by_liq) // 2
        g = {'q1': q1, 'q1_liq': [s for s in q1 if liq.get(s, 10**9) <= 100],
             'q1_rest': [s for s in q1 if liq.get(s, 10**9) > 100],
             'q1_hi': by_liq[:half], 'q1_lo': by_liq[-half:]}
        q1s = set(q1)
        pool = [x.symbol for x in ranked if x.grade in ('STRICT', 'FULL') and liq.get(x.symbol, 10**9) <= 100]
        g['pool_in_q1'] = [s for s in pool if s in q1s]
        g['pool_out_q1'] = [s for s in pool if s not in q1s]
        ex = {k: group_excess(r.returns, s, r.baseline) for k, s in g.items()}
        ex['liq_minus_rest'] = None if None in (ex['q1_liq'], ex['q1_rest']) else ex['q1_liq'] - ex['q1_rest']
        ex['hi_minus_lo'] = ex['q1_hi'] - ex['q1_lo']
        ex['pool_in_minus_out'] = None if None in (ex['pool_in_q1'], ex['pool_out_q1']) else ex['pool_in_q1'] - ex['pool_out_q1']
        for k in pts:
            pts[k].append((r, ex[k]))
        rows.append({'as_of': r.as_of, **{k: ex[k] for k in pts}, 'n_q1_liq': len(g['q1_liq']),
                     'n_q1_rest': len(g['q1_rest']), 'n_pool_in': len(g['pool_in_q1']), 'n_pool_out': len(g['pool_out_q1'])})
    st = {}
    for k, p in pts.items():
        x = _series(p)
        vals = [val for _, val in p if val is not None]
        st[k] = {**{kk: x.get(kk) for kk in ('n', 'mean', 'lo', 'hi', 't', 'p')}, 'wins': sum(v > 0 for v in vals)}
    out['stats'][h] = {'stats': st, 'rows': rows}
Path('/tmp/m12-pool/q1_liq.json').write_text(json.dumps(out, ensure_ascii=False, indent=1))
print('written')
