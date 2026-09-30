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
drift = lambda q: abs((date.fromisoformat(q.statement_fiscal) - date.fromisoformat(q.fiscal_date[:10])).days)
store = MarketStore(db_path=Path(sys.argv[1]), read_only=True)
for as_of, mode in (("2026-09-29", "live"), ("2026-09-29", "replay"), ("2024-08-17", "replay")):
    kw = {"observed_at": as_of} if mode == "live" else {}
    packets, _ = build_packets(store, as_of, mode=mode, with_beta=False, **kw)
    mixed, rows, ttm_diff = 0, [], []
    for p in packets:
        e = p.eps
        if not e: continue
        un = [q for q in e if q.statement_fiscal is None]
        big = [q for q in e if q.statement_fiscal and drift(q) > 20]
        if un and big:
            mixed += 1
            fmp, cur = pairs([q.fiscal_date for q in e]), pairs([q.period_end for q in e])
            uni = pairs([q.statement_fiscal if not un else q.fiscal_date for q in e])
            rows.append((p.symbol, len(e), len(un), "fmp", fmp, "mixed(now)", cur, "uniform", uni))
    print(f"{mode} {as_of}: packets {len(packets)}; windows mixing unpaired quarters with >20-day moves: {mixed}")
    for r in rows: print("   ", r)
