"""A4: forward returns of the production first-tier pool on the 21 F1 quarter-end boards,
compared per period with the read-only trial baseline (trial/q1_liq.json, 2026-11-02).

Pool members come from the production build_pool + liquidity_ranks on the same-day weekly board
(relabelled live only to pass build_pool's source check, as M10 acceptance does). Returns, excess
and intervals come from the M12 health-report functions. The baseline was computed directly from
the quarter-end board (rank tier 0 ∩ dollar-volume top 100), so a match also shows the production
selection and the trial selection are the same names. Exit 1 on any difference.
"""
import argparse
import json
import math
from dataclasses import replace
from pathlib import Path
import sys

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path.insert(0, str(ROOT))
from backtest.research.prosperity_health import (HORIZONS, PriceBook, _series, board_view,  # noqa: E402
                                                 evaluate_board, group_excess, universe)
from src.data.market_store import MarketStore  # noqa: E402
from src.data.prosperity_pool import build_pool, liquidity_ranks  # noqa: E402
from src.data.prosperity_store import ProsperityStore  # noqa: E402
from src.data.prosperity_storage import BoardKey  # noqa: E402
from terminal.prosperity.schemes import SCHEMES, scheme_hash  # noqa: E402
from terminal.prosperity.version import code_version  # noqa: E402

TOL = 1e-12


STATS = ('n', 'mean', 'lo', 'hi', 't', 'p', 'wins')
INTS = ('n', 'wins')


def _pct(x):
    return '   None  ' if x is None else f'{x:+.6f}'      # an empty pool period has no excess


def _same(base, got, *, integer=False) -> bool:
    """Equal within TOL. None only matches None (empty pool period); NaN/inf/bool/str never match."""
    if base is None or got is None:
        return base is None and got is None
    if integer:
        return type(base) is int and type(got) is int and base == got
    ok = all(type(v) in (int, float) and math.isfinite(v) for v in (base, got))
    return ok and abs(base - got) <= TOL


def compute(db, market_db):
    version, f1 = code_version(), scheme_hash(SCHEMES['F1'])
    market = MarketStore(db_path=market_db, read_only=True)
    boards = []
    try:
        with ProsperityStore(db, read_only=True) as store:
            for key in store.list_boards(scheme_hash=f1, code_version=version, board_type='quarter_final'):
                qf = store.get_board(key)
                wk = store.get_board(BoardKey('weekly', key.as_of, f1, version))
                liq = liquidity_ranks(market, wk.meta.members, key.as_of)
                pool = build_pool(replace(wk, meta=replace(wk.meta, mode='live')), liq,
                                  generated_at='2026-11-02T00:00:00Z')
                boards.append((board_view(qf), tuple(m['symbol'] for m in pool['members'])))
        prices = PriceBook.load(market, {r.symbol for v, _ in boards for r in universe(v)})
    finally:
        market.close()
    out = {}
    for h in HORIZONS:
        points, rows = [], {}
        for view, members in boards:
            r = evaluate_board(view, prices, horizon=h)
            if r.status != 'complete':
                continue
            ex = group_excess(r.returns, members, r.baseline)
            points.append((r, ex))
            rows[r.as_of] = {'n': len(members), 'excess': ex}
        s = _series(points)
        s['wins'] = sum(v > 0 for _, v in points if v is not None)
        out[str(h)] = {'stats': {k: s.get(k) for k in STATS}, 'rows': rows}
    return out


def compare(got, baseline):
    lines, diffs, checked = [], 0, 0
    for h in (str(x) for x in HORIZONS):
        b = baseline['stats'][h]
        brows = {r['as_of']: r for r in b['rows']}
        grows = got[h]['rows']
        if set(brows) != set(grows):
            diffs += 1
            lines.append(f'{h}Q as_of sets differ: only baseline {sorted(set(brows) - set(grows))}, '
                         f'only production {sorted(set(grows) - set(brows))}')
        for as_of in sorted(set(brows) & set(grows)):
            bn, gn = brows[as_of]['n_q1_liq'], grows[as_of]['n']
            bx, gx = brows[as_of]['q1_liq'], grows[as_of]['excess']
            checked += 2
            bad = not (_same(bn, gn, integer=True) and _same(bx, gx))
            if bad:
                diffs += 1
            lines.append(f'{h}Q {as_of}  n {gn:>3} (base {bn:>3})  excess {_pct(gx)} (base {_pct(bx)})'
                         + ('  DIFF' if bad else ''))
        bs, gs = b['stats']['q1_liq'], got[h]['stats']
        for k in STATS:
            checked += 1
            bv, gv = bs.get(k), gs.get(k)
            bad = not _same(bv, gv, integer=k in INTS)
            if bad:
                diffs += 1
            lines.append(f'{h}Q {k:<4} {gv!r} (base {bv!r})' + ('  DIFF' if bad else ''))
    lines.append(f'checked {checked} values; differences: {diffs}')
    return lines, diffs


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--market-db', type=Path, required=True)
    p.add_argument('--db', type=Path, required=True)
    p.add_argument('--baseline', type=Path, required=True)
    p.add_argument('--computed', type=Path, help='reuse a saved computation instead of recomputing')
    p.add_argument('--save', type=Path, help='save the computation for later --computed runs')
    a = p.parse_args()
    baseline = json.loads(a.baseline.read_text())
    got = json.loads(a.computed.read_text()) if a.computed else compute(a.db, a.market_db)
    if a.save:
        a.save.write_text(json.dumps(got, indent=1) + '\n')
    lines, diffs = compare(got, baseline)
    print('\n'.join(lines))
    return 1 if diffs else 0


if __name__ == '__main__':
    sys.exit(main())
