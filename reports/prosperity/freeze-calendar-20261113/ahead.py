"""Spike: share of rankable members whose NEXT season has already arrived on the freeze date (old vs new rule)."""
import runpy, io, contextlib
from datetime import date, timedelta
import numpy as np
with contextlib.redirect_stdout(io.StringIO()):
    g = runpy.run_path('coverage.py')
rec, arrival = g['rec'], g['arrival']
END = {1: (3, 31), 2: (6, 30), 3: (9, 30), 4: (12, 31)}
def last_sat(d): return d - timedelta(days=(d.weekday() - 5) % 7)
old_all, new_all = [], []
for q in sorted(rec):
    y, k = int(q[:4]), int(q[-1]); ny, nk = (y + 1, 1) if k == 4 else (y, k + 1)
    qe, qe2 = date(y, *END[k]), date(ny, *END[nk])
    old = date.fromisoformat(max(rec[q])[0]); new = last_sat(qe2)
    members = min(rec[q])[2]
    arr2 = [arrival(s, qe2.isoformat()) for s in members]           # days after qe2 the next season arrived
    share = lambda f: sum(a is not None and a <= (f - qe2).days for a in arr2) / len(arr2)
    old_all.append(share(old)); new_all.append(share(new))
    print(q, old, f'{share(old):5.1%}', new, f'{share(new):5.1%}')
print(f'mean old {np.mean(old_all):.1%} new {np.mean(new_all):.1%} | max new {max(new_all):.1%}')
