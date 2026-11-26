"""Spike: share of F1-rankable members whose just-ended quarter is in all three statements, by day after quarter end. Read-only."""
import glob, json, sys
from datetime import date, timedelta
import numpy as np
sys.path.insert(0, '/Users/owen/CC workspace/Finance')
from src.data.market_store import MarketStore
from src.data.prosperity_history import arrival_day
from scripts.backfill_extended_fundamentals import ASOF_WINDOW_TABLES

rec = {}
for p in glob.glob('/Users/owen/CC workspace/Finance/data/prosperity.db.state/candidates/*.json'):
    pl = json.load(open(p))['payload']; i = pl['identity']
    if i['code_version'] == '13e5f9d5183f1b8e':
        rec.setdefault(i['quarter_label'], []).append((i['as_of'], pl['coverage'], [s for s, _ in pl['evidence']]))
QE = {'1': (3, 31), '2': (6, 30), '3': (9, 30), '4': (12, 31)}
m = MarketStore(db_path='/Users/owen/CC workspace/Finance/data/market.db', read_only=True); conn = m._get_conn()
cache = {}
def arrival(sym, qe):
    if sym not in cache:
        cache[sym] = ({t: [dict(r) for r in conn.execute(f'SELECT * FROM {t} WHERE symbol=? ORDER BY date', (sym,))]
                       for t in ASOF_WINDOW_TABLES}, m.get_fmp_earnings(sym))
    tables, earn = cache[sym]
    return arrival_day(tables, qe, earnings_rows=earn)
def first_sat(y, mo, min_day=1):
    d = date(y, mo, min_day); return d + timedelta(days=(5 - d.weekday()) % 7)
REL = {'1': 5, '2': 8, '3': 11, '4': 2}           # Boss: Q1→May, Q2→Aug, Q3→Nov, Q4→Feb
DAYS = [30, 35, 40, 45, 50, 60, 70, 80]
print(f"{'quarter':8s} {'n':>4s} " + ' '.join(f'd{d:<4d}' for d in DAYS) + "  | 1st Sat (day, cov) | 2nd Sat (day, cov) | check d60 receipt")
by_type = {'Q1-Q3': [], 'Q4': []}
for q in sorted(rec):
    first = min(rec[q]); members = first[2]
    y, k = int(q[:4]), q[-1]; qe = date(y, *QE[k])
    arr = [arrival(s, qe.isoformat()) for s in members]
    cov = lambda d: sum(a is not None and a <= d for a in arr) / len(arr)
    ry = y + 1 if k == '4' else y
    s1 = first_sat(ry, REL[k]); s2 = first_sat(ry, REL[k], 8)
    d1, d2, d0 = (s1 - qe).days, (s2 - qe).days, (date.fromisoformat(first[0]) - qe).days
    row = [cov(d) for d in DAYS]
    by_type['Q4' if k == '4' else 'Q1-Q3'].append((row, cov(d1), cov(d2), d1, d2))
    print(f"{q:8s} {len(arr):4d} " + ' '.join(f'{c:5.0%}' for c in row)
          + f"  | {s1} d{d1} {cov(d1):4.0%} | {s2} d{d2} {cov(d2):4.0%} | d{d0} mine {cov(d0):.0%} vs engine {first[1]:.0%}")
print()
for t, v in by_type.items():
    a = np.array([r[0] for r in v])
    print(f"{t:6s} mean " + ' '.join(f'{c:5.0%}' for c in a.mean(0)) + f"  | 1st Sat {np.mean([r[1] for r in v]):.0%} (d{min(r[3] for r in v)}–{max(r[3] for r in v)})"
          f" | 2nd Sat {np.mean([r[2] for r in v]):.0%} (d{min(r[4] for r in v)}–{max(r[4] for r in v)})")
m.close()
