import sqlite3, collections, sys
from datetime import date
c = sqlite3.connect(f"file:{sys.argv[1]}?mode=ro", uri=True)
d = lambda s: date.fromisoformat(s[:10])
inc = collections.defaultdict(list)
for s, dt in c.execute("select symbol, date from income_quarterly order by date"): inc[s].append(d(dt))
mem = {r[0] for r in c.execute("select symbol from extended_membership where effective_to is null")}
hits = []
for s, st in inc.items():
    for i, x in enumerate(st):
        before = [y for y in st if 300 <= (x - y).days <= 430]
        after = [y for y in st if 300 <= (y - x).days <= 430]
        if not before or not after: continue
        ok = lambda y: abs(abs((x - y).days) - 365) <= 20
        if any(map(ok, before)) or any(map(ok, after)): continue
        # the neighbours a year on either side are themselves two years apart: x is the outlier
        if any(abs((a - b).days - 730) <= 20 for b in before for a in after):
            hits.append((s, str(x), s in mem))
print(len(hits), "isolated statement dates;", sum(h[2] for h in hits), "in current members")
for h in hits: print("  ", h)
