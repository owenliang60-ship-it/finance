"""Per live packet: YoY pairs found in the SUE window (current + 8 prior) on FMP dates vs paired dates."""
import sys
from datetime import date
from pathlib import Path
from src.data.market_store import MarketStore
from src.data.prosperity_history import SAME_QUARTER_DAYS, YEAR_DAYS
from terminal.prosperity.inputs import build_packets

def pairs(dates):
    newest = list(reversed(dates)); n = 0
    for k in range(min(9, len(newest))):
        d = date.fromisoformat(newest[k][:10])
        for j in range(k + 1, len(newest)):
            gap = (d - date.fromisoformat(newest[j][:10])).days
            if abs(gap - YEAR_DAYS) <= SAME_QUARTER_DAYS: n += 1; break
            if gap > YEAR_DAYS + SAME_QUARTER_DAYS: break
    return n

db, as_of, mode = sys.argv[1], sys.argv[2], sys.argv[3]
store = MarketStore(db_path=Path(db), read_only=True)
kw = {"observed_at": as_of} if mode == "live" else {}
packets, _ = build_packets(store, as_of, mode=mode, with_beta=False, **kw)
better, worse, same, paired_any = [], [], 0, 0
for p in packets:
    if not p.eps: continue
    a, b = pairs([q.fiscal_date for q in p.eps]), pairs([q.period_end for q in p.eps])
    paired_any += any(q.statement_fiscal and q.statement_fiscal != q.fiscal_date[:10] for q in p.eps)
    if b > a: better.append((p.symbol, a, b))
    elif b < a: worse.append((p.symbol, a, b, [(q.fiscal_date, q.statement_fiscal) for q in p.eps
                                                if q.statement_fiscal and abs((date.fromisoformat(q.statement_fiscal) - date.fromisoformat(q.fiscal_date[:10])).days) > 10]))
    else: same += 1
unpaired = sum(1 for p in packets for q in p.eps if q.statement_fiscal is None)
total = sum(len(p.eps) for p in packets)
print(f"{mode} {as_of}: packets {len(packets)}, eps quarters {total}, unpaired {unpaired}, symbols with a moved date {paired_any}")
print("more pairs:", len(better), better[:30]); print("fewer pairs:", len(worse)); [print("  ", w) for w in worse]; print("same:", same)
