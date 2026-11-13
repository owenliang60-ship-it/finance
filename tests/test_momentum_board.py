"""M16 momentum board: V4 long-only scoring over daily windows, payload, CLI."""
import numpy as np
import pytest

from backtest.research import momentum_board as mb
from scripts.crypto_trend_rankings import V4_WEIGHTS


def _path(start, end, n=36):
    return np.linspace(start, end, n)


def test_v4_weights_are_the_frozen_ones():
    assert V4_WEIGHTS == {'absolute_return': .45, 'er': .125, 'r_squared': .125,
                          'drawdown': .05, 'quote_volume': .25}
    assert mb.WINDOWS == {'7w': 35, '14w': 70} and mb.TOP_N == 30


def test_score_window_scores_everyone_ranks_longs_only():
    closes = {'UP': _path(100, 140), 'UP2': _path(100, 110), 'DOWN': _path(100, 60), 'FLAT': np.full(36, 50.0)}
    rows = mb.score_window(closes, {s: 5e8 for s in closes})
    assert {r['symbol'] for r in rows} == set(closes)             # decliners stay: they are percentile inputs
    top = mb.longs(rows)
    assert [r['symbol'] for r in top] == ['UP', 'UP2']             # DOWN and FLAT fail the long gate
    assert [r['rank'] for r in top] == [1, 2] and top[0]['score'] > top[1]['score']
    assert top[0]['return'] == pytest.approx(0.40)
    assert all(r['rank'] is None and not r['eligible'] for r in rows if r['symbol'] in ('DOWN', 'FLAT'))
    assert set(rows[0]) == {'symbol', 'score', 'return', 'er', 'r_squared', 'slope', 'drawdown',
                            'dollar_volume', 'eligible', 'rank'}


def test_score_window_percentiles_use_the_whole_pool():
    base = {'UP': _path(100, 140), 'UP2': _path(100, 110)}
    alone = mb.longs(mb.score_window(base, {s: 5e8 for s in base}))
    wide = {**base, 'DOWN': _path(100, 30)}
    crowded = mb.longs(mb.score_window(wide, {s: 5e8 for s in wide}))
    assert crowded[0]['score'] != alone[0]['score']                # DOWN changes UP's percentile


def test_score_window_drawdown_gate_and_tie_order():
    peak = np.concatenate([_path(100, 300, 30), _path(290, 150, 6)])    # +50% but 50% off the high
    closes = {'B': _path(100, 120), 'A': _path(100, 120), 'PEAK': peak}
    rows = mb.score_window(closes, {s: 1e9 for s in closes})
    assert [r['symbol'] for r in mb.longs(rows)] == ['A', 'B']     # tie broken by symbol; PEAK gated out
    assert len(rows) == 3


def test_score_window_turnover_term_is_the_crypto_formula():
    closes = {'BIG': _path(100, 120), 'SMALL': _path(100, 120)}
    rows = {r['symbol']: r for r in mb.score_window(closes, {'BIG': 3e9, 'SMALL': 1e9})}
    assert rows['BIG']['score'] - rows['SMALL']['score'] == pytest.approx(25 * (0.75 - 0.5))   # 100V/(V+1e9)
