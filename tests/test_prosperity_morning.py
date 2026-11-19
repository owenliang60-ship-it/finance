"""Morning section 0d: bound prosperity pool reader and the weekly M16 momentum picks."""
import copy
import json
from datetime import datetime, timezone

import pytest

from src.data import prosperity_morning as pm
from src.data.prosperity_pool import publish_prosperity_pool
from src.data.prosperity_store import ProsperityStore
from terminal.prosperity.schemes import SCHEMES, scheme_hash
from tests.test_prosperity_web import boards, pool_for

F1 = scheme_hash(SCHEMES['F1'])

NOW = datetime(2026, 10, 3, 9, 30, tzinfo=timezone.utc)


@pytest.fixture()
def env(tmp_path):
    """Two weekly F1 boards (2026-09-26, 2026-10-03) and this week's published pool."""
    from tests.prosperity_fixtures import storage_batch
    db = tmp_path / 'prosperity.db'
    with ProsperityStore(db, read_only=False) as s:
        s.save_batch(storage_batch(count=20))
        s.save_batch(storage_batch(as_of='2026-10-03', count=20))
    with ProsperityStore(db) as s:
        old, stored = boards(s)
    pool = tmp_path / 'pool.json'
    payload = pool_for(stored)
    publish_prosperity_pool(payload, pool, now=NOW)
    return {'db': db, 'pool': pool, 'payload': payload, 'old': old, 'stored': stored,
            'members': sorted(m['symbol'] for m in payload['members'])}


def load(env, **kw):
    return pm.load_bound_prosperity_pool(kw.get('pool', env['pool']), kw.get('db', env['db']), now=NOW)


# --- B1: reading and binding ------------------------------------------------------------------

def test_valid_pool_is_available_with_all_members(env):
    out = load(env)
    assert out['available'] is True and out['reason'] is None
    assert sorted(m['symbol'] for m in out['members']) == env['members'] and len(env['members']) == 4


@pytest.mark.parametrize('mutate,reason', [
    (lambda p: p.unlink(), 'prosperity_pool_missing'),
    (lambda p: p.write_text('{', encoding='utf-8'), 'prosperity_pool_invalid'),
])
def test_pool_file_problems_pass_through(env, mutate, reason):
    mutate(env['pool'])
    assert load(env) == {'available': False, 'reason': reason, 'members': []}


def test_stale_and_criteria_mismatch_pass_through(env):
    late = pm.load_bound_prosperity_pool(env['pool'], env['db'], now=datetime(2026, 10, 20, tzinfo=timezone.utc))
    assert late['reason'] == 'prosperity_pool_stale'
    payload = copy.deepcopy(env['payload'])
    payload['criteria']['liquidity_top'] = 50
    env['pool'].write_text(json.dumps(payload), encoding='utf-8')
    assert load(env)['reason'] == 'prosperity_pool_criteria_mismatch'


def test_missing_db_is_unavailable(env, tmp_path):
    out = load(env, db=tmp_path / 'nope.db')
    assert out['available'] is False and out['reason'] == 'prosperity_db_unavailable' and out['members'] == []


def test_unreadable_db_is_unavailable(env, tmp_path):
    bad = tmp_path / 'bad.db'
    bad.write_bytes(b'not a database' * 100)
    out = load(env, db=bad)
    assert out['reason'] == 'prosperity_db_unavailable' and 'error' in out


def test_member_grade_tampered_is_board_mismatch(env):
    payload = copy.deepcopy(env['payload'])
    first = payload['members'][0]
    first['grade'] = 'FULL' if first['grade'] == 'STRICT' else 'STRICT'
    env['pool'].write_text(json.dumps(payload), encoding='utf-8')
    out = load(env)
    assert out['available'] is False and out['reason'] == 'prosperity_pool_board_mismatch'
    assert out['binding_note'].startswith('未标入池') and out['members'] == []


def test_board_missing_is_its_own_reason(env, tmp_path):
    db = tmp_path / 'other.db'
    from tests.prosperity_fixtures import storage_batch
    with ProsperityStore(db, read_only=False) as s:
        s.save_batch(storage_batch(count=20))                              # only 2026-09-26
    out = load(env, db=db)
    assert out['available'] is False and out['reason'] == 'prosperity_board_missing'


def test_unknown_binding_note_fails_closed(env, monkeypatch):
    """§11 #4: only a note starting with 入池 counts as bound."""
    monkeypatch.setattr(pm, 'pool_binding', lambda pool, stored: ({}, 'something new'))
    assert load(env)['reason'] == 'prosperity_pool_board_mismatch'


def test_empty_pool_is_available_not_unavailable(env, tmp_path):
    """North star §4.1: zero qualifying stocks is a valid result. Real build/publish/bind path, no mocks
    (review #7): a board whose ranked rows are all below FULL publishes an empty pool."""
    from dataclasses import replace
    from src.data.prosperity_pool import build_pool
    from tests.prosperity_fixtures import storage_batch
    from tests.test_prosperity_web import fake_liquidity
    batch = storage_batch(as_of='2026-10-03', count=20)
    batch = tuple(replace(b, board=replace(b.board, rows=tuple(
        replace(r, grade='BELOW') if r.grade in ('STRICT', 'FULL') else r for r in b.board.rows)))
        for b in batch)
    db = tmp_path / 'empty.db'
    with ProsperityStore(db, read_only=False) as s:
        s.save_batch(batch)
    with ProsperityStore(db) as s:
        (stored,) = [s.get_board(k) for k in s.list_boards(scheme_hash=F1, code_version='test-v1',
                                                           board_type='weekly')]
    payload = build_pool(stored, fake_liquidity(stored), generated_at='2026-10-03T09:00:00Z')
    assert payload['members'] == [] and payload['coverage']['eligible'] == 0
    pool = tmp_path / 'empty-pool.json'
    publish_prosperity_pool(payload, pool, now=NOW)
    out = pm.load_bound_prosperity_pool(pool, db, now=NOW)
    assert out['available'] is True and out['reason'] is None and out['members'] == []


def test_db_is_not_modified(env):
    before = env['db'].read_bytes()
    load(env)
    assert env['db'].read_bytes() == before


# --- 2026-11-19: weekly M16 momentum picks (pool ∩ 14-week momentum top 30) -----------------------

from pathlib import Path  # noqa: E402

from src.data.prosperity_pool import _universe  # noqa: E402

AS_OF = '2026-11-14'
UNIVERSE = ['U{:02d}'.format(i) for i in range(1, 41)]          # U01 has the best score … U38
INELIGIBLE = {'U03', 'U10'}                                       # scored, not long-eligible
UNAVAILABLE = {'U39': 'split_unresolved', 'U40': 'price_gap'}
# long ranks: U01 1, U02 2, U04 3, U05 4, … U09 8, U11 9, … Uk k-2 → U22 #20, U32 #30, U33 #31


def momentum_payload(universe=UNIVERSE):
    scored, rank = [], 0
    for i, s in enumerate(u for u in universe if u not in UNAVAILABLE):
        eligible = s not in INELIGIBLE
        rank += eligible
        scored.append({'symbol': s, 'score': 90.0 - i, 'return': 0.5 - i * 0.01 if eligible else -0.1,
                       'eligible': eligible, 'rank': rank if eligible else None, 'er': 0.2,
                       'r_squared': 0.5, 'slope': 0.01, 'drawdown': 0.1, 'dollar_volume': 1e9})
    return {'schema_version': 1, 'as_of': AS_OF, 'generated_at': '2026-11-14T08:58:30Z',
            'price_date': '2026-11-13', 'scoring': 'v4', 'weights': {},
            'universe': {'count': len(universe), 'symbols': list(universe)},
            'boards': {'14w': {'days': 70, 'window': ['2026-08-04', '2026-11-13'], 'scored': scored,
                               'unavailable': {s: w for s, w in sorted(UNAVAILABLE.items()) if s in universe},
                               'adjustments': []}}}


def momentum_pool(symbols=('U22', 'U05', 'U33', 'U03', 'U40'), universe=UNIVERSE, as_of=AS_OF):
    """A bound pool as load_bound_prosperity_pool returns it (only the fields the momentum reader uses)."""
    return {'available': True, 'reason': None, 'as_of': as_of, 'universe': _universe(universe),
            'members': [{'symbol': s, 'rank': i + 1} for i, s in enumerate(symbols)]}


def write_board(root, payload, as_of=AS_OF):
    path = root / 'out' / as_of / 'momentum-{}.json'.format(as_of.replace('-', ''))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(payload if isinstance(payload, str) else json.dumps(payload), encoding='utf-8')
    return path


def picks(root, pool=None):
    return pm.load_prosperity_momentum_picks(momentum_pool() if pool is None else pool, root)


def test_board_path_follows_the_saturday_delivery_layout(tmp_path):
    assert pm.momentum_board_path(AS_OF, tmp_path) == tmp_path / 'out' / AS_OF / 'momentum-20261114.json'
    with pytest.raises(ValueError):
        pm.momentum_board_path('20261114', tmp_path)


def test_picks_are_pool_members_in_the_top_30_by_momentum_rank(tmp_path):
    write_board(tmp_path, momentum_payload())
    out = picks(tmp_path)
    assert out['available'] is True and out['reason'] is None
    assert (out['as_of'], out['price_date'], out['window'], out['days'], out['top_n']) == (
        AS_OF, '2026-11-13', '14w', 70, 30)
    # U22 comes first in pool order; U33 is #31; U03 is not long-eligible; non-pool top-30 rows never show
    assert [(p['symbol'], p['momentum_rank']) for p in out['picks']] == [('U05', 4), ('U22', 20)]
    first = out['picks'][0]
    assert set(first) == {'symbol', 'momentum_rank', 'momentum_score', 'momentum_return'}
    assert first['momentum_score'] == 86.0 and first['momentum_return'] == pytest.approx(0.46)
    # U40 is a pool member that could not be scored; U39 (not in the pool) is not listed
    assert out['unscored'] == [{'symbol': 'U40', 'reason': 'price_gap'}]


def test_rank_30_is_in_and_rank_31_is_out(tmp_path):
    write_board(tmp_path, momentum_payload())
    out = picks(tmp_path, momentum_pool(('U33', 'U32')))
    assert [(p['symbol'], p['momentum_rank']) for p in out['picks']] == [('U32', 30)]


@pytest.mark.parametrize('members', [('U33', 'U03'), ()])
def test_no_hit_and_an_empty_pool_are_a_valid_empty_result(tmp_path, members):
    write_board(tmp_path, momentum_payload())
    out = picks(tmp_path, momentum_pool(members))
    assert out['available'] is True and out['picks'] == [] and out['unscored'] == []


def test_missing_board_is_its_own_reason(tmp_path):
    write_board(tmp_path, momentum_payload(), as_of='2026-11-07')       # last week's file is never used
    assert picks(tmp_path) == {'available': False, 'reason': 'prosperity_momentum_missing',
                               'picks': [], 'unscored': []}


def test_unavailable_pool_passes_its_reason_through(tmp_path):
    write_board(tmp_path, momentum_payload())
    stale = {'available': False, 'reason': 'prosperity_pool_stale', 'members': []}
    assert picks(tmp_path, stale) == {'available': False, 'reason': 'prosperity_pool_stale',
                                      'picks': [], 'unscored': []}
    assert picks(tmp_path, {'available': False})['reason'] == 'prosperity_pool_invalid'


@pytest.mark.parametrize('mutate', [
    lambda p: p.pop('universe'),
    lambda p: p['universe'].pop('symbols_sha256'),
    lambda p: p.update(members={'U05': {}}),
    lambda p: p['members'].append('U06'),
    lambda p: p['members'].append({'rank': 9}),
    lambda p: p['members'].append({'symbol': 'U05'}),
    lambda p: p['members'].append({'symbol': 'BRK_B'}),
    lambda p: p.update(as_of='20261114'),
    lambda p: p.pop('as_of'),
], ids=['no-universe', 'no-hash', 'members-not-list', 'member-not-dict', 'member-no-symbol',
        'member-duplicate', 'member-underscore', 'compact-as-of', 'no-as-of'])
def test_malformed_pool_is_pool_invalid(tmp_path, mutate):
    write_board(tmp_path, momentum_payload())
    pool = momentum_pool()
    mutate(pool)
    assert picks(tmp_path, pool)['reason'] == 'prosperity_pool_invalid'
    assert pm.load_prosperity_momentum_picks('not a pool', tmp_path)['reason'] == 'prosperity_pool_invalid'


@pytest.mark.parametrize('mutate', [
    lambda p: p.update(as_of='2026-11-07'),
    lambda p: p['universe'].update(count=41, symbols=UNIVERSE + ['U41']),
    lambda p: p['universe'].update(count=39, symbols=UNIVERSE[1:]),
], ids=['other-week', 'universe-plus-one', 'universe-minus-one'])
def test_board_of_another_week_or_universe_is_a_pool_mismatch(tmp_path, mutate):
    payload = momentum_payload()
    mutate(payload)
    write_board(tmp_path, payload)
    out = picks(tmp_path)
    assert out['available'] is False and out['reason'] == 'prosperity_momentum_pool_mismatch'
    assert out['picks'] == [] and out['unscored'] == []


def test_pool_member_outside_the_board_universe_is_a_pool_mismatch(tmp_path):
    """Equal hashes imply members ⊆ universe; a member outside it would vanish silently, so it fails closed."""
    write_board(tmp_path, momentum_payload())
    assert picks(tmp_path, momentum_pool(('U05', 'ZZZ')))['reason'] == 'prosperity_momentum_pool_mismatch'


def _board(p):
    return p['boards']['14w']


def _row(p, symbol):
    return next(r for r in _board(p)['scored'] if r['symbol'] == symbol)


def _move_to_unavailable(p, symbols):
    board = _board(p)
    board['scored'] = [r for r in board['scored'] if r['symbol'] not in symbols]
    board['unavailable'].update({s: 'no_price' for s in symbols})


def _swap_scores(p):
    a, b = _board(p)['scored'][:2]
    a['score'], b['score'] = b['score'], a['score']


def _swap_ranks(p):
    a, b = _board(p)['scored'][:2]
    a['rank'], b['rank'] = b['rank'], a['rank']


def _rename_u01(p):
    p['universe']['symbols'][0] = 'BRK_B'
    _row(p, 'U01')['symbol'] = 'BRK_B'


INVALID_TEXT = {                       # payload → file text
    'corrupt-json': lambda p: '{',
    'not-an-object': lambda p: '[]',
    'nan-score': lambda p: json.dumps(p).replace('"score": 90.0', '"score": NaN', 1),
    'infinity-return': lambda p: json.dumps(p).replace('"return": 0.5,', '"return": Infinity,', 1),
    'overflow-float': lambda p: json.dumps(p).replace('"score": 90.0', '"score": 1e400', 1),
    'deep-nesting': lambda p: '[' * 200000 + ']' * 200000,
    'not-utf8': lambda p: b'\xff\xfe{}',
}

INVALID = {                            # in-place mutation of the payload
    'missing-as-of': lambda p: p.pop('as_of'),
    'compact-as-of': lambda p: p.update(as_of='20261114'),
    'schema-version': lambda p: p.update(schema_version=2),
    'schema-version-bool': lambda p: p.update(schema_version=True),
    'scoring': lambda p: p.update(scoring='v3'),
    'boards-not-dict': lambda p: p.update(boards=[]),
    'extra-7w-window': lambda p: p['boards'].update({'7w': copy.deepcopy(_board(p))}),
    'days-35': lambda p: _board(p).update(days=35),
    'days-float': lambda p: _board(p).update(days=70.0),
    'price-date-lag-4': lambda p: (p.update(price_date='2026-11-10'), _board(p)['window'].__setitem__(1, '2026-11-10')),
    'price-date-future': lambda p: (p.update(price_date='2026-11-15'), _board(p)['window'].__setitem__(1, '2026-11-15')),
    'window-end-not-price-date': lambda p: _board(p)['window'].__setitem__(1, '2026-11-12'),
    'window-not-ascending': lambda p: _board(p).update(window=['2026-11-13', '2026-11-13']),
    'window-one-date': lambda p: _board(p).update(window=['2026-11-13']),
    'universe-count-inconsistent': lambda p: p['universe'].update(count=41),
    'universe-empty': lambda p: p['universe'].update(count=0, symbols=[]),
    'universe-duplicate': lambda p: p['universe'].update(count=41, symbols=UNIVERSE + ['U01']),
    'underscore-symbol': _rename_u01,
    'scored-not-list': lambda p: _board(p).update(scored={}),
    'unavailable-not-dict': lambda p: _board(p).update(unavailable=[]),
    'unavailable-empty-reason': lambda p: _board(p)['unavailable'].update(U40=''),
    'row-not-dict': lambda p: _board(p)['scored'].append('U39'),
    'score-string': lambda p: _row(p, 'U01').update(score='90'),
    'eligible-not-bool': lambda p: _row(p, 'U01').update(eligible=1),
    'partition-missing-member': lambda p: _board(p).update(scored=_board(p)['scored'][:-1]),
    'partition-overlap': lambda p: _board(p)['scored'].append(
        {'symbol': 'U39', 'score': 0.0, 'return': -0.1, 'eligible': False, 'rank': None}),
    'duplicate-scored-row': lambda p: _board(p)['scored'].append(copy.deepcopy(_board(p)['scored'][-1])),
    'coverage-below-90': lambda p: _move_to_unavailable(p, {'U36', 'U37', 'U38'}),
    'rank-gap': lambda p: _row(p, 'U38').update(rank=37),
    'rank-duplicate': lambda p: _row(p, 'U02').update(rank=1),
    'rank-bool': lambda p: _row(p, 'U01').update(rank=True),
    'eligible-return-zero': lambda p: _row(p, 'U01').update(**{'return': 0.0}),
    'ineligible-with-rank': lambda p: _row(p, 'U03').update(rank=3),
    'scored-out-of-score-order': _swap_scores,
    'ranks-out-of-score-order': _swap_ranks,
}


def _mutation_case(mutate):
    def apply(payload):
        mutate(payload)
        return json.dumps(payload)
    return apply


CASES = {**INVALID_TEXT, **{k: _mutation_case(f) for k, f in INVALID.items()}}


@pytest.mark.parametrize('case', CASES.values(), ids=list(CASES))
def test_malformed_board_is_invalid(tmp_path, case):
    content = case(momentum_payload())
    path = write_board(tmp_path, '')
    if isinstance(content, bytes):
        path.write_bytes(content)
    else:
        path.write_text(content, encoding='utf-8')
    out = picks(tmp_path)
    assert out['available'] is False and out['reason'] == 'prosperity_momentum_invalid'
    assert out['picks'] == [] and out['unscored'] == []


def test_fixture_coverage_and_lag_edges_are_valid(tmp_path):
    """The INVALID cases fail for their own reason: 36/40 = 90% and a 3-day lag still pass."""
    payload = momentum_payload()
    _move_to_unavailable(payload, {'U37', 'U38'})
    payload['price_date'] = _board(payload)['window'][1] = '2026-11-11'
    write_board(tmp_path, payload)
    assert picks(tmp_path)['available'] is True


def test_constants_match_the_saturday_producer():
    from backtest.research import momentum_board
    from config.settings import DATA_DIR
    from src.data.prosperity_delivery import MOMENTUM_WINDOW
    script = (Path(__file__).resolve().parents[1] / 'scripts' / 'run_prosperity.sh').read_text(encoding='utf-8')
    assert '--delivery-dir "$PROJECT_DIR/data/{}"'.format(
        pm.PROSPERITY_DELIVERY_DIR.relative_to(DATA_DIR).as_posix()) in script
    assert pm.MOMENTUM_WINDOW_DAYS == momentum_board.WINDOWS[MOMENTUM_WINDOW]
    assert pm.MOMENTUM_MAX_LAG_DAYS == momentum_board.MAX_LAG_DAYS
    assert pm._SCORING == momentum_board.SCORING


def test_board_file_is_not_modified(tmp_path):
    path = write_board(tmp_path, momentum_payload())
    before = path.read_bytes()
    picks(tmp_path)
    assert path.read_bytes() == before
