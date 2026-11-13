"""M16 momentum board: V4 long-only scoring over daily windows, payload, CLI."""
import numpy as np
import pytest

from backtest.research import momentum_board as mb
from scripts.crypto_trend_rankings import V4_WEIGHTS


def _path(start, end, n=36):
    return np.linspace(start, end, n)


def _longs(rows):
    return sorted((r for r in rows if r['eligible']), key=lambda r: r['rank'])


def test_v4_weights_are_the_frozen_ones():
    assert V4_WEIGHTS == {'absolute_return': .45, 'er': .125, 'r_squared': .125,
                          'drawdown': .05, 'quote_volume': .25}
    assert mb.WINDOWS == {'7w': 35, '14w': 70}


def test_score_window_scores_everyone_ranks_longs_only():
    closes = {'UP': _path(100, 140), 'UP2': _path(100, 110), 'DOWN': _path(100, 60), 'FLAT': np.full(36, 50.0)}
    rows = mb.score_window(closes, {s: 5e8 for s in closes})
    assert {r['symbol'] for r in rows} == set(closes)             # decliners stay: they are percentile inputs
    top = _longs(rows)
    assert [r['symbol'] for r in top] == ['UP', 'UP2']             # DOWN and FLAT fail the long gate
    assert [r['rank'] for r in top] == [1, 2] and top[0]['score'] > top[1]['score']
    assert top[0]['return'] == pytest.approx(0.40)
    assert all(r['rank'] is None and not r['eligible'] for r in rows if r['symbol'] in ('DOWN', 'FLAT'))
    assert set(rows[0]) == {'symbol', 'score', 'return', 'er', 'r_squared', 'slope', 'drawdown',
                            'dollar_volume', 'eligible', 'rank'}


def test_score_window_percentiles_use_the_whole_pool():
    base = {'UP': _path(100, 140), 'UP2': _path(100, 110)}
    alone = _longs(mb.score_window(base, {s: 5e8 for s in base}))
    wide = {**base, 'DOWN': _path(100, 30)}
    crowded = _longs(mb.score_window(wide, {s: 5e8 for s in wide}))
    assert crowded[0]['score'] != alone[0]['score']                # DOWN changes UP's percentile


def test_score_window_drawdown_gate_and_tie_order():
    peak = np.concatenate([_path(100, 300, 30), _path(290, 150, 6)])    # +50% but 50% off the high
    closes = {'B': _path(100, 120), 'A': _path(100, 120), 'PEAK': peak}
    rows = mb.score_window(closes, {s: 1e9 for s in closes})
    assert [r['symbol'] for r in _longs(rows)] == ['A', 'B']     # tie broken by symbol; PEAK gated out
    assert len(rows) == 3


def test_score_window_turnover_term_is_the_crypto_formula():
    closes = {'BIG': _path(100, 120), 'SMALL': _path(100, 120)}
    rows = {r['symbol']: r for r in mb.score_window(closes, {'BIG': 3e9, 'SMALL': 1e9})}
    assert rows['BIG']['score'] - rows['SMALL']['score'] == pytest.approx(25 * (0.75 - 0.5))   # 100V/(V+1e9)


# --- payload ---------------------------------------------------------------------------------

import json

from backtest.research.prosperity_health import PriceBook
from tests.prosperity_fixtures import seed_market, storage_batch, weekdays

CAL = weekdays('2026-11-06', 80)
SYMS = [f'S{i:02d}' for i in range(1, 11)]


def _risers():
    return {s: [(d, 100.0 + k * (i + 1)) for i, d in enumerate(CAL)] for k, s in enumerate(SYMS, 1)}


def test_build_payload_windows_and_unavailable():
    closes = _risers()
    closes['SHORT'] = closes['S01'][-20:]                           # not enough history
    symbols = [*SYMS, 'SHORT']
    out = mb.build_payload(book=PriceBook(closes=closes, splits={}, calendar=CAL),
                           dollar_volumes=lambda days: {s: 1e9 for s in symbols},
                           symbols=symbols, as_of='2026-11-07', generated_at='2026-11-07T09:00:00Z')
    b7, b14 = out['boards']['7w'], out['boards']['14w']
    assert out['price_date'] == CAL[-1] and out['universe'] == {'count': 11, 'symbols': sorted(symbols)}
    assert b7['window'] == [CAL[-36], CAL[-1]] and b14['window'] == [CAL[-71], CAL[-1]]
    assert b7['unavailable'] == {'SHORT': 'price_gap'} and len(b7['scored']) == 10
    assert len(_longs(b7['scored'])) == 10 and b7['adjustments'] == []
    for board in (b7, b14):                                         # every member is accounted for, once
        assert sorted([r['symbol'] for r in board['scored']] + list(board['unavailable'])) == sorted(symbols)
    assert not ({'valid', 'long_eligible', 'longs'} & set(b7))     # no self-reported counts
    json.dumps(out, allow_nan=False)                                # plain JSON types only


def test_build_payload_volume_window_is_the_signal_window():
    seen = []
    mb.build_payload(book=PriceBook(closes=_risers(), splits={}, calendar=CAL),
                     dollar_volumes=lambda days: seen.append(list(days)) or {s: 1e9 for s in SYMS},
                     symbols=SYMS, as_of='2026-11-07', generated_at='x')
    assert seen == [CAL[-35:], CAL[-70:]]                           # the L days after the base close


def test_build_payload_missing_volume_is_unavailable():
    out = mb.build_payload(book=PriceBook(closes=_risers(), splits={}, calendar=CAL),
                           dollar_volumes=lambda days: {s: 1e9 for s in SYMS[1:]},
                           symbols=SYMS, as_of='2026-11-07', generated_at='x')
    assert out['boards']['7w']['unavailable'] == {'S01': 'volume_missing'}
    assert 'S01' not in {r['symbol'] for r in out['boards']['7w']['scored']}


def test_build_payload_reports_split_adjustments():
    split = [(d, 200.0 if i < 60 else 100.0 + (i - 60)) for i, d in enumerate(CAL)]    # 2:1 on CAL[60]
    book = PriceBook(closes={'S': split, 'T': [(d, 50.0 + i) for i, d in enumerate(CAL)]},
                     splits={'S': [(CAL[60], 2.0)]}, calendar=CAL)
    out = mb.build_payload(book=book, dollar_volumes=lambda days: {'S': 1e9, 'T': 1e9}, symbols=['S', 'T'],
                           as_of='2026-11-07', generated_at='x')
    assert out['boards']['7w']['adjustments'] == [{'symbol': 'S', 'date': CAL[60], 'factor': 2.0}]
    assert next(r for r in out['boards']['7w']['scored'] if r['symbol'] == 'S')['return'] > 0


def test_build_payload_fails_closed():
    book = PriceBook(closes={'A': [(d, 10.0 + i) for i, d in enumerate(CAL)]}, splits={}, calendar=CAL)
    with pytest.raises(ValueError, match='momentum_coverage_below_floor'):
        mb.build_payload(book=book, dollar_volumes=lambda days: {}, symbols=['A', 'B', 'C'],
                         as_of='2026-11-07', generated_at='x')
    with pytest.raises(ValueError, match='momentum_prices_stale'):
        mb.build_payload(book=book, dollar_volumes=lambda days: {'A': 1.0}, symbols=['A'],
                         as_of='2026-12-01', generated_at='x')
    with pytest.raises(ValueError, match='momentum_window_short'):
        mb.build_payload(book=PriceBook(closes={'A': [(d, 10.0) for d in CAL[:30]]}, splits={}, calendar=CAL[:30]),
                         dollar_volumes=lambda days: {'A': 1.0}, symbols=['A'], as_of=CAL[29], generated_at='x')
    with pytest.raises(ValueError, match='momentum_no_members'):
        mb.build_payload(book=book, dollar_volumes=lambda days: {}, symbols=[], as_of='2026-11-07', generated_at='x')


# --- CLI (real subprocess) -------------------------------------------------------------------

import subprocess
import sys
from pathlib import Path

from src.data.prosperity_store import ProsperityStore

ROOT = Path(__file__).resolve().parents[1]
WEEK = '2026-10-03'


@pytest.fixture()
def dbs(tmp_path):
    db = tmp_path / 'prosperity.db'
    with ProsperityStore(db, read_only=False) as store:
        batch = storage_batch(as_of=WEEK, count=20)
        store.save_batch(batch)
    members = sorted(batch[0].meta.members)
    return db, seed_market(tmp_path / 'market.db', members, '2026-10-02'), members


def _cli(db, market, out, as_of=WEEK):
    return subprocess.run([sys.executable, str(ROOT / 'scripts' / 'momentum_board.py'), '--db', str(db),
                           '--market-db', str(market), '--code-version', 'test-v1', '--as-of', as_of,
                           '--out', str(out)], capture_output=True, text=True, timeout=120)


def test_cli_writes_the_payload_for_the_board_members(dbs, tmp_path):
    db, market, members = dbs
    out = tmp_path / 'momentum.json'
    done = _cli(db, market, out)
    assert done.returncode == 0, done.stderr
    payload = json.loads(out.read_text(encoding='utf-8'))
    assert payload['as_of'] == WEEK and payload['price_date'] == '2026-10-02'
    assert payload['universe']['symbols'] == members
    assert len(payload['boards']['14w']['scored']) == len(members) and not payload['boards']['14w']['unavailable']
    assert [p.name for p in tmp_path.iterdir() if p.name.startswith('.momentum')] == []     # no temp left


def test_cli_fails_closed(dbs, tmp_path):
    db, market, _ = dbs
    out = tmp_path / 'momentum.json'
    missing_week = _cli(db, market, out, as_of='2026-10-10')
    assert missing_week.returncode == 6 and 'weekly_board_missing' in missing_week.stderr and not out.exists()
    empty = tmp_path / 'empty.db'
    empty.write_bytes(b'')
    assert _cli(db, empty, out).returncode == 6 and not out.exists()


def test_prices_must_reach_the_week_before_the_board_date():
    """Code review #2: a Wed–Fri price outage must not produce this week's board."""
    def payload(last_day, as_of):
        cal = weekdays(last_day, 80)
        closes = {'A': [(d, 10.0 + i) for i, d in enumerate(cal)]}
        return mb.build_payload(book=PriceBook(closes=closes, splits={}, calendar=cal),
                                dollar_volumes=lambda days: {'A': 1e9}, symbols=['A'], as_of=as_of, generated_at='x')
    assert payload('2026-11-06', '2026-11-07')['price_date'] == '2026-11-06'      # Friday close, normal
    assert payload('2026-11-05', '2026-11-07')['price_date'] == '2026-11-05'      # Friday holiday
    assert payload('2026-11-04', '2026-11-07')['price_date'] == '2026-11-04'      # Thu + Fri closed
    with pytest.raises(ValueError, match='momentum_prices_stale'):
        payload('2026-11-03', '2026-11-07')                                       # prices stop on Tuesday
    from src.data import prosperity_pool
    assert mb.COVERAGE_MIN is prosperity_pool.COVERAGE_MIN and mb.MAX_LAG_DAYS == 3
