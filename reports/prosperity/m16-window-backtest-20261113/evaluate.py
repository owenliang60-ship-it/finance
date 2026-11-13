"""One-off spike: weekly-rebalanced returns of pool ∩ V4-long under each single window. Usage: evaluate.py OUT.json SWEEP.json..."""
import bisect, json, sys
import numpy as np

sys.path.insert(0, '/Users/owen/CC workspace/Finance/.worktrees/prosperity-m16')
from backtest.research.prosperity_health import PriceBook
from src.data.market_store import MarketStore

out_path, srcs = sys.argv[1], sys.argv[2:]
rows, WINDOWS = {}, None
for src in srcs:
    doc = json.load(open(src)); WINDOWS = doc['windows']
    for r in doc['weeks']:
        rows[r['as_of']] = r
bad = sorted(d for d, r in rows.items() if 'error' in r)
weeks = [rows[d] for d in sorted(rows) if 'error' not in rows[d]]
DEV_END = '2024-07-01'                                   # pre-registered split: dev before, holdout from here

def picks(w, L):
    p = w['picks'].get(str(L))
    return None if p is None else [s for s, _ in p]
def both(w):
    a, b = picks(w, 35), picks(w, 70)
    return None if a is None or b is None else [s for s in a if s in set(b)]
strategies = {f'{L}d': (lambda w, L=L: picks(w, L)) for L in WINDOWS}
strategies['7周∩14周'] = both
strategies['池全体'] = lambda w: w['pool']
def rest(w):
    b = both(w)
    return None if b is None else [s for s in w['pool'] if s not in set(b)]
strategies['池内其余'] = rest
strategies['SPY'] = lambda w: ['SPY']; strategies['QQQ'] = lambda w: ['QQQ']

market = MarketStore(db_path='/Users/owen/CC workspace/Finance/data/market.db', read_only=True)
book = PriceBook.load(market, {s for w in weeks for s in w['pool']} | {'SPY', 'QQQ'})
market.close()
cal = book.calendar
entry = [bisect.bisect_right(cal, w['as_of']) for w in weeks]
series = {name: [] for name in strategies}; sizes = {name: [] for name in strategies}; held = {name: [] for name in strategies}
usable = []
for k in range(len(weeks) - 1):
    lists = {name: f(weeks[k]) for name, f in strategies.items()}
    if any(v is None for v in lists.values()):
        continue                                          # a window without enough history: skip the week for everyone
    usable.append(weeks[k]['as_of'])
    for name, syms in lists.items():
        vals = [r for r in (book.window_return(s, entry[k], entry[k + 1])[0] for s in syms) if r is not None]
        series[name].append(float(np.mean(vals)) if vals else 0.0)     # empty list = cash
        sizes[name].append(len(syms)); held[name].append(set(syms))
dates = np.array(usable)
R = {k: np.array(v) for k, v in series.items()}

def stats(name, mask):
    r, spy, pool = R[name][mask], R['SPY'][mask], R['池全体'][mask]
    n = len(r)
    if n < 8: return None
    nav = np.cumprod(1 + r); peak = np.maximum.accumulate(np.concatenate([[1.0], nav]))
    beta = float(np.cov(r, spy, ddof=1)[0, 1] / spy.var(ddof=1)); resid = r - beta * spy
    alpha_w = float(resid.mean()); rs = float(resid.std(ddof=1))
    dp = r - pool; ds = r - spy
    idx = np.flatnonzero(mask); h = [held[name][i] for i in idx]
    turn = float(np.mean([len(h[i + 1] - h[i]) / max(1, len(h[i + 1])) for i in range(len(h) - 1)])) if len(h) > 1 else 0.0
    return {'weeks': n, 'cum': float(nav[-1] - 1), 'ann': float(nav[-1] ** (52 / n) - 1), 'vol': float(r.std(ddof=1) * 52 ** 0.5),
            'mdd': float((np.concatenate([[1.0], nav]) / peak - 1).min()), 'beta': beta, 'alpha_ann': alpha_w * 52,
            'alpha_ir': alpha_w / rs * 52 ** 0.5 if rs else 0.0, 'alpha_t': alpha_w / (rs / n ** 0.5) if rs else 0.0,
            'ex_spy_ann': float(ds.mean() * 52), 'won_spy': float((ds > 0).mean()),
            'ex_pool_ann': float(dp.mean() * 52), 'ex_pool_ir': float(dp.mean() / dp.std(ddof=1) * 52 ** 0.5) if dp.std(ddof=1) else 0.0,
            'won_pool': float((dp > 0).mean()), 'names': float(np.mean([sizes[name][i] for i in idx])),
            'cash': int(sum(sizes[name][i] == 0 for i in idx)), 'turnover': turn}

# SPY drawdown episodes ≥10% (peak → trough), found from the data
spy_nav = np.concatenate([[1.0], np.cumprod(1 + R['SPY'])]); episodes = []
peak_i, i = 0, 1
while i < len(spy_nav):
    if spy_nav[i] >= spy_nav[peak_i]: peak_i = i; i += 1; continue
    j = trough = i
    while j < len(spy_nav) and spy_nav[j] < spy_nav[peak_i]:
        if spy_nav[j] < spy_nav[trough]: trough = j
        j += 1
    if spy_nav[trough] / spy_nav[peak_i] - 1 <= -0.10: episodes.append((peak_i, trough))
    peak_i = j if j < len(spy_nav) else peak_i; i = j + 1
periods = {'全程': np.ones(len(dates), bool), '开发段': dates < DEV_END, '验证段': dates >= DEV_END}
for a, b in episodes:
    m = np.zeros(len(dates), bool); m[a:b] = True
    periods[f'下跌段 {dates[a]}→{dates[b - 1]}'] = m
result = {'windows': WINDOWS, 'weeks': len(dates), 'first': str(dates[0]), 'last': str(dates[-1]), 'skipped_boards': bad, 'dev_end': DEV_END,
          'stats': {p: {name: stats(name, m) for name in strategies} for p, m in periods.items()},
          'returns': {k: [float(x) for x in v] for k, v in R.items()}, 'dates': usable}
json.dump(result, open(out_path, 'w'), ensure_ascii=False)
print(f"weeks {len(dates)} {dates[0]} → {dates[-1]} | boards skipped {len(bad)} | dev/holdout {int(periods['开发段'].sum())}/{int(periods['验证段'].sum())}")
for p in periods:
    if result['stats'][p]['SPY'] is None: continue
    print(f"\n== {p} ({int(periods[p].sum())} 周) ==")
    print(f"{'策略':10s} {'累计':>8s} {'年化':>7s} {'回撤':>7s} {'beta':>5s} {'alpha年化':>9s} {'alphaIR':>7s} {'t':>5s} {'超池年化':>8s} {'超池IR':>6s} {'赢SPY':>6s} {'赢池':>5s} {'只数':>5s} {'空仓':>4s} {'换手':>5s}")
    for name in strategies:
        s = result['stats'][p][name]
        print(f"{name:10s} {s['cum']:+8.1%} {s['ann']:+7.1%} {s['mdd']:+7.1%} {s['beta']:5.2f} {s['alpha_ann']:+9.1%} {s['alpha_ir']:7.2f} {s['alpha_t']:5.1f} {s['ex_pool_ann']:+8.1%} {s['ex_pool_ir']:6.2f} {s['won_spy']:6.0%} {s['won_pool']:5.0%} {s['names']:5.1f} {s['cash']:4d} {s['turnover']:5.0%}")
