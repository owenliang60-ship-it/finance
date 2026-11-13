"""One-off spike: for every weekly F1 board, the pool ∩ V4-long list under each single momentum window.
Usage: sweep.py START_SATURDAY OUT_JSON [PROCS]. Read-only on market.db and the scratch prosperity DBs."""
import bisect, glob, json, multiprocessing as mp, sys
from dataclasses import replace
from datetime import date, timedelta

sys.path.insert(0, '/Users/owen/CC workspace/Finance/.worktrees/prosperity-m16')
from backtest.research import momentum_board as mb
from backtest.research.prosperity_health import PriceBook
from src.data.market_store import MarketStore
from src.data.prosperity_pool import build_pool, liquidity_ranks
from src.data.prosperity_storage import BoardKey
from src.data.prosperity_store import ProsperityStore
from terminal.prosperity.schemes import SCHEMES, scheme_hash

CV, H = '13e5f9d5183f1b8e', scheme_hash(SCHEMES['F1'])
MARKET = '/Users/owen/CC workspace/Finance/data/market.db'
WINDOWS = [10, 15, 20, 25, 30, 35, 40, 50, 60, 70, 85, 105, 126]      # trading days = 2 … 25 weeks
start, out_path = sys.argv[1], sys.argv[2]
procs = int(sys.argv[3]) if len(sys.argv) > 3 else 1
weeks, d = [], date.fromisoformat(start)
while d <= date(2026, 11, 7):
    weeks.append(d.isoformat()); d += timedelta(days=7)
boards = {}
for path in ['/tmp/m16-history/prosperity.db', *sorted(glob.glob('/tmp/m16-history/[wh]*/prosperity.db'))]:
    with ProsperityStore(path, read_only=True) as store:
        for w in weeks:
            if w not in boards:
                stored = store.get_board(BoardKey('weekly', w, H, CV))
                if stored is not None:
                    boards[w] = stored
_m = MarketStore(db_path=MARKET, read_only=True)
BOOK = PriceBook.load(_m, {s for b in boards.values() for s in b.meta.members})
_m.close()
_market = None


def one(w):
    global _market
    stored = boards.get(w)
    if stored is None:
        return {'as_of': w, 'error': 'board_missing'}
    if _market is None:
        _market = MarketStore(db_path=MARKET, read_only=True)
    members = sorted(stored.meta.members)
    row = {'as_of': w, 'members': len(members)}
    try:
        live = replace(stored, meta=replace(stored.meta, mode='live'))       # in memory only: reuse the pool rule
        pool = [m['symbol'] for m in build_pool(live, liquidity_ranks(_market, members, w), generated_at=w + 'T09:00:00Z')['members']]
        cal = BOOK.calendar
        end_i = bisect.bisect_right(cal, w) - 1
        if (date.fromisoformat(w) - date.fromisoformat(cal[end_i])).days > mb.MAX_LAG_DAYS:
            raise ValueError('prices_stale')
        row.update(pool=pool, price_date=cal[end_i], picks={}, eligible={}, scored={})
        inpool = set(pool)
        for L in WINDOWS:
            if end_i < L:
                row['picks'][str(L)] = None; continue
            closes = {}
            for s in members:
                series, why = BOOK.window_closes(s, end_i - L, end_i)
                if series is not None:
                    closes[s] = series
            vol = mb.load_dollar_volumes(_market, list(closes), cal[end_i - L + 1:end_i + 1])
            closes = {s: v for s, v in closes.items() if s in vol}
            if len(closes) < mb.COVERAGE_MIN * len(members):
                row['picks'][str(L)] = None; row['eligible'][str(L)] = f'coverage {len(closes)}/{len(members)}'; continue
            rows = mb.score_window(closes, {s: vol[s] for s in closes})
            longs = [r for r in rows if r['eligible']]
            row['scored'][str(L)] = len(rows); row['eligible'][str(L)] = len(longs)
            row['picks'][str(L)] = [[r['symbol'], r['rank']] for r in longs if r['symbol'] in inpool]
    except Exception as exc:
        row['error'] = f'{type(exc).__name__}: {exc}'
    return row


if __name__ == '__main__':
    if procs > 1:
        with mp.get_context('fork').Pool(procs) as pool:
            out = pool.map(one, weeks, chunksize=2)
    else:
        out = [one(w) for w in weeks]
    json.dump({'windows': WINDOWS, 'weeks': out}, open(out_path, 'w'))
    bad = [(r['as_of'], r['error']) for r in out if 'error' in r]
    print('weeks', len(out), 'errors', bad[:8], len(bad))
