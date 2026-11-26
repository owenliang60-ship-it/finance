import runpy, io, contextlib
from datetime import date, timedelta
import numpy as np
with contextlib.redirect_stdout(io.StringIO()):
    g = runpy.run_path('coverage.py')
rec, QE, arrival = g['rec'], g['QE'], g['arrival']
END = {1: (3, 31), 2: (6, 30), 3: (9, 30), 4: (12, 31)}
def last_sat(d): return d - timedelta(days=(d.weekday() - 5) % 7)
q4, oth, days = [], [], []
for q in sorted(rec):
    y, k = int(q[:4]), int(q[-1]); qe = date(y, *END[k])
    ny, nk = (y + 1, 1) if k == 4 else (y, k + 1)
    f = last_sat(date(ny, *END[nk])); d = (f - qe).days; days.append(d)
    members = min(rec[q])[2]; arr = [arrival(s, qe.isoformat()) for s in members]
    c = sum(a is not None and a <= d for a in arr) / len(arr); (q4 if k == 4 else oth).append(c)
print(f'last Saturday of next quarter: d{min(days)}–{max(days)} | Q1–Q3 mean {np.mean(oth):.1%} min {min(oth):.1%} | Q4 mean {np.mean(q4):.1%} min {min(q4):.1%}')
nxt = [last_sat(date(y, *END[k])) for y, k in [(2026, 4), (2027, 1), (2027, 2), (2027, 3), (2027, 4)]]
print([d.isoformat() for d in nxt], [(b - a).days // 7 for a, b in zip(nxt, nxt[1:])])
