"""M14 morning trial section: bound prosperity pool reader and Premium comparison."""
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


# --- B2: comparison ---------------------------------------------------------------------------

def _pool(symbols, available=True):
    return {'available': available, 'reason': None if available else 'x',
            'members': [{'symbol': s} for s in symbols]}


def test_comparison_sorted_by_symbol():
    out = pm.pool_comparison(_pool(['ZZ', 'AA', 'MM']), _pool(['MM', 'BB', 'AA', 'CC']))
    assert out == {'status': 'ok', 'both': ['AA', 'MM'], 'prosperity_only': ['ZZ'],
                   'premium_only': ['BB', 'CC']}


def test_comparison_unavailable_sides():
    assert pm.pool_comparison(_pool(['A'], available=False), _pool(['A'])) == {'status': 'prosperity_unavailable'}
    assert pm.pool_comparison(_pool(['A']), _pool([], available=False)) == {'status': 'premium_unavailable'}


def test_comparison_empty_prosperity_pool_is_ok():
    assert pm.pool_comparison(_pool([]), _pool(['A'])) == {
        'status': 'ok', 'both': [], 'prosperity_only': [], 'premium_only': ['A']}
