"""One-off spike (Boss 2026-11-13): weekly pool ∩ 14w momentum top 30 vs pool ∩ 14w eligible vs the whole pool.
Usage: evaluate_top30.py SWEEP.json MARKET_DB. Reads sweep_full.json (pool and 70-day picks with long ranks); market.db read-only.
Same rules as evaluate.py: Saturday board, equal weight Monday close to Monday close, price return, no costs."""
import bisect, json, sys
from pathlib import Path
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from backtest.research.prosperity_health import PriceBook
from src.data.market_store import MarketStore

sweep, market_db = sys.argv[1], sys.argv[2]
weeks = sorted((r for r in json.load(open(sweep))['weeks'] if 'error' not in r), key=lambda r: r['as_of'])
m = MarketStore(db_path=market_db, read_only=True)
book = PriceBook.load(m, {s for w in weeks for s in w['pool']} | {'SPY'})
m.close()
entry = [bisect.bisect_right(book.calendar, w['as_of']) for w in weeks]
LISTS = {'池∩前30': lambda w: [s for s, k in w['picks']['70'] if k <= 30],
         '池∩合格': lambda w: [s for s, _ in w['picks']['70']],
         '池全体': lambda w: w['pool'], 'SPY': lambda w: ['SPY']}
R, held, dates = {k: [] for k in LISTS}, {k: [] for k in LISTS}, []
for k in range(len(weeks) - 1):
    dates.append(weeks[k]['as_of'])
    for name, f in LISTS.items():
        syms = f(weeks[k])
        vals = [x for x in (book.window_return(s, entry[k], entry[k + 1])[0] for s in syms) if x is not None]
        R[name].append(float(np.mean(vals)) if vals else 0.0)            # empty list = cash
        held[name].append(set(syms))
dates, R = np.array(dates), {k: np.array(v) for k, v in R.items()}


def stats(name, mask):
    r, spy, pool = R[name][mask], R['SPY'][mask], R['池全体'][mask]
    n = len(r)
    nav = np.concatenate([[1.0], np.cumprod(1 + r)])
    beta = np.cov(r, spy, ddof=1)[0, 1] / spy.var(ddof=1)
    resid = r - beta * spy
    h = [held[name][i] for i in np.flatnonzero(mask)]
    turn = np.mean([len(h[i + 1] - h[i]) / len(h[i + 1]) for i in range(len(h) - 1) if h[i + 1]])
    return dict(ann=nav[-1] ** (52 / n) - 1, alpha=resid.mean() * 52, t=resid.mean() / (resid.std(ddof=1) / n ** .5),
                ex_pool=(r - pool).mean() * 52, mdd=float((nav / np.maximum.accumulate(nav) - 1).min()),
                names=np.mean([len(x) for x in h]), cash=sum(len(x) == 0 for x in h), turn=turn)


for period, mask in {'全程': np.ones(len(dates), bool), '开发段': dates < '2024-07-01',
                     '验证段': dates >= '2024-07-01'}.items():
    print(f'== {period} {mask.sum()} 周 ==  年化   alpha    t  比池多   回撤  只数 空仓 换手')
    for name in ('池∩前30', '池∩合格', '池全体'):
        s = stats(name, mask)
        print(f"  {name:6s} {s['ann']:+6.1%} {s['alpha']:+6.1%} {s['t']:4.1f} {s['ex_pool']:+6.1%} {s['mdd']:+6.1%}"
              f" {s['names']:5.1f} {s['cash']:3d} {s['turn']:4.0%}")
