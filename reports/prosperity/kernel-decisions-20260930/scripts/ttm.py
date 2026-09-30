import sys
from pathlib import Path
from src.data.market_store import MarketStore
from terminal.prosperity.inputs import build_packets
from terminal.prosperity.consensus import _contiguous
from terminal.prosperity.loader import load_history
store = MarketStore(db_path=Path(sys.argv[1]), read_only=True)
for as_of, mode in (("2026-09-29", "live"), ("2024-08-17", "replay")):
    kw = {"observed_at": as_of} if mode == "live" else {}
    packets, _ = build_packets(store, as_of, mode=mode, with_beta=False, **kw)
    none = [p for p in packets if p.consensus.ttm_eps is None]
    print(f"{mode} {as_of}: ttm missing {len(none)} of {len(packets)}")
    # For those, would the paired dates of the same latest-4 announced quarters be contiguous?
    from terminal.prosperity.street_eps import announced_eps, pair_statement_dates
    from terminal.prosperity.statements import visible_statements, build_quarters
    hits = []
    for p in none:
        h = load_history(store, p.symbol, with_vintage=(mode == "replay" and as_of >= "2026-09-29"))
        vis = visible_statements(h, as_of, mode, as_of if mode == "live" else None)
        qb = build_quarters(vis, as_of)
        ann = [q for q in pair_statement_dates(announced_eps(h.earnings, h.income, h.splits, as_of), [q.fiscal_date for q in qb.quarters]) if q.announce_date[:10] <= as_of]
        last4 = sorted(ann, key=lambda q: q.fiscal_date)[-4:]
        if len(last4) == 4 and all(q.eps_actual is not None for q in last4) \
                and not _contiguous(tuple(q.fiscal_date[:10] for q in last4)) and _contiguous(tuple(q.period_end[:10] for q in last4)):
            hits.append((p.symbol, [(q.fiscal_date, q.statement_fiscal) for q in last4]))
    print("   contiguous only on paired dates:", len(hits)); [print("    ", x) for x in hits]
