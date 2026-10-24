import sqlite3

import pytest

from src.data.prosperity_store import ProsperityStore


def test_readonly_missing_never_creates(tmp_path):
    path = tmp_path / 'missing' / 'prosperity.db'
    with pytest.raises(FileNotFoundError):
        ProsperityStore(path)
    assert not path.parent.exists()


def test_empty_database_can_reopen_readonly(tmp_path):
    path = tmp_path / 'research.db'
    with ProsperityStore(path, read_only=False):
        pass
    with ProsperityStore(path):
        pass
    with sqlite3.connect(path) as conn:
        assert conn.execute('PRAGMA user_version').fetchone()[0] == 1
        assert conn.execute('PRAGMA foreign_key_check').fetchall() == []


@pytest.mark.parametrize('ddl', [
    'PRAGMA user_version=9',
    'PRAGMA user_version=1',
    'CREATE TABLE other (id INTEGER)',
])
def test_reject_unknown_or_incomplete_schema_without_changing_it(tmp_path, ddl):
    path = tmp_path / 'bad.db'
    with sqlite3.connect(path) as conn:
        conn.executescript(ddl)
    before = path.read_bytes()
    for readonly in (True, False):
        with pytest.raises(ValueError, match='schema'):
            ProsperityStore(path, read_only=readonly)
        assert path.read_bytes() == before


def test_connection_closes_and_readonly_is_enforced(tmp_path):
    path = tmp_path / 'store.db'
    with ProsperityStore(path, read_only=False):
        pass
    with ProsperityStore(path) as store:
        conn = store._conn
        assert conn.execute('PRAGMA query_only').fetchone()[0] == 1
        assert conn.execute('PRAGMA foreign_keys').fetchone()[0] == 1
        with pytest.raises(sqlite3.OperationalError):
            conn.execute('DELETE FROM board')
    with pytest.raises(sqlite3.ProgrammingError):
        conn.execute('SELECT 1')


from dataclasses import replace
from src.data.prosperity_storage import key_for, normalize_board
from tests.prosperity_fixtures import storage_batch


def test_round_trip_after_close_and_retry(tmp_path):
    path, batch = tmp_path / 'research.db', storage_batch()
    with ProsperityStore(path, read_only=False) as store:
        keys = store.save_batch(batch)
        assert store.save_batch(batch) == keys
    with ProsperityStore(path) as store:
        assert tuple(store.get_board(k) for k in keys) == batch
        assert store.get_board(replace(keys[0], as_of='2026-09-19')) is None
    with sqlite3.connect(path) as conn:
        assert conn.execute('SELECT count(*) FROM board').fetchone()[0] == 4
        assert conn.execute('PRAGMA foreign_key_check').fetchall() == []


@pytest.mark.parametrize('zero', [0, 0.0, -0.0])
def test_sql_zero_roundtrip_and_retry(tmp_path, zero):
    b = storage_batch()[0]
    rows = (replace(b.board.rows[0], trend_adj=zero),) + b.board.rows[1:]
    b = replace(b, board=replace(b.board, rows=rows))
    path = tmp_path / 'zero.db'
    with ProsperityStore(path, read_only=False) as store:
        key = store.save_batch([b])[0]
    with ProsperityStore(path, read_only=False) as store:
        assert store.get_board(key) == normalize_board(b)
        rows = (replace(b.board.rows[0], trend_adj=0.0),) + b.board.rows[1:]
        assert store.save_batch([replace(b, board=replace(b.board, rows=rows))]) == (key,)


def test_conflict_rolls_back_new_boards_and_preserves_first_audit(tmp_path):
    batch = storage_batch()
    with ProsperityStore(tmp_path / 'conflict.db', read_only=False) as store:
        key = store.save_batch(batch[:1])[0]
        new_audit = replace(batch[0], meta=replace(batch[0].meta, computed_at='2026-10-23T09:00:00Z'))
        store.save_batch([new_audit])
        assert store.get_board(key) == batch[0]
        changed = tuple(replace(b, stocks=({**b.stocks[0], 'name': 'Changed'},) + b.stocks[1:]) for b in batch)
        # boards 1–3 are new, board 0 conflicts with the stored one: nothing new may survive
        with pytest.raises(ValueError, match='conflict'):
            store.save_batch([*changed[1:], changed[0]])
        assert store._conn.execute('SELECT count(*) FROM board').fetchone()[0] == 1
        assert store.get_board(key) == batch[0]


def test_failure_on_third_board_rolls_back_every_new_table_row(tmp_path):
    path = tmp_path / 'atomic.db'
    with ProsperityStore(path, read_only=False) as store:
        old = storage_batch(as_of='2026-09-19')
        keys = store.save_batch(old)
        tables = ('board', 'input_set', 'stock_input', 'stock_period', 'factor_value', 'score', 'params', 'scheme')
        counts = {t: store._conn.execute('SELECT count(*) FROM '+t).fetchone()[0] for t in tables}
        store._conn.execute("CREATE TRIGGER fail_third BEFORE INSERT ON score WHEN NEW.board_id=7 "
                            "BEGIN SELECT RAISE(ABORT, 'injected'); END")
        with pytest.raises(sqlite3.IntegrityError, match='injected'):
            store.save_batch(storage_batch())
        with sqlite3.connect(path) as reader:
            assert {t: reader.execute('SELECT count(*) FROM '+t).fetchone()[0] for t in tables} == counts
        assert counts['input_set'] == 1 and counts['stock_input'] == 12
        assert tuple(store.get_board(k) for k in keys) == old


@pytest.mark.parametrize('sql', [
    'DELETE FROM score WHERE row_index=0',
    'DELETE FROM factor_value WHERE factor=\'revenue_yoy\'',
    'UPDATE factor_value SET raw_value=999 WHERE factor=\'revenue_yoy\'',
    'UPDATE factor_value SET raw_present=0 WHERE factor=\'revenue_yoy\'',
    'UPDATE factor_value SET in_scheme=0',
    'UPDATE params SET payload_json=\'{}\'',
    'UPDATE stock_period SET sector=\'other\'',
    'UPDATE score SET row_index=row_index+100',
])
def test_corrupt_detail_fails_read_and_same_content_retry(tmp_path, sql):
    path, batch = tmp_path / 'corrupt.db', storage_batch()
    with ProsperityStore(path, read_only=False) as store:
        keys = store.save_batch(batch)
        store._conn.execute(sql)
        with pytest.raises(ValueError):
            store.get_board(keys[0])
        with pytest.raises(ValueError):
            store.save_batch(batch)


def test_cross_version_coexists_and_readonly_cannot_save(tmp_path):
    path = tmp_path / 'versions.db'
    with ProsperityStore(path, read_only=False) as store:
        keys = store.save_batch(storage_batch())
        other = store.save_batch(storage_batch(code_version='v2'))
        assert set(keys).isdisjoint(other)
    with ProsperityStore(path) as store:
        with pytest.raises(PermissionError):
            store.save_batch(storage_batch())


def test_detached_board_is_revalidated_and_freeze_requires_pair(tmp_path):
    with ProsperityStore(tmp_path / 'invalid.db', read_only=False) as store:
        batch = storage_batch(freeze=True)
        with pytest.raises(ValueError, match='weekly'):
            store.save_batch(batch[4:])
        bad = replace(batch[0], meta=replace(batch[0].meta, errors={'lost': 'failure'}))
        with pytest.raises(ValueError):
            store.save_batch([bad])
        with pytest.raises(ValueError):
            store.save_batch([batch[0], batch[0]])
        with pytest.raises(ValueError):
            store.save_batch([])


def test_missing_frozen_reference_is_rejected(tmp_path):
    first = storage_batch(freeze=True)
    frozen = {b.board.scheme_id: b.board.params for b in first[4:]}
    next_batch = storage_batch(as_of='2026-10-03', frozen=frozen)
    with ProsperityStore(tmp_path / 'missing-reference.db', read_only=False) as store:
        with pytest.raises(ValueError, match='frozen'):
            store.save_batch(next_batch)
        assert store._conn.execute('SELECT count(*) FROM board').fetchone()[0] == 0


def test_weekly_board_is_not_a_frozen_parameter_source(tmp_path):
    with ProsperityStore(tmp_path / 'research.db', read_only=False) as store:
        key = store.save_batch(storage_batch())[0]
        assert store.get_frozen_params(scheme_hash=key.scheme_hash, code_version=key.code_version,
                                       as_of=key.as_of, effective_at='2026-09-26T08:30:00Z') is None


def test_freeze_restore_respects_version_date_and_effective_instant(tmp_path):
    batch = storage_batch(freeze=True)
    with ProsperityStore(tmp_path / 'frozen.db', read_only=False) as store:
        keys = store.save_batch(batch)
        opts = dict(scheme_hash=keys[0].scheme_hash, code_version=keys[0].code_version,
                    as_of='2026-10-03', effective_at='2026-09-26T08:30:00Z')
        assert store.get_frozen_params(**opts) == batch[4].board.params
        assert store.get_frozen_params(**{**opts, 'effective_at': '2026-09-26T16:30:00+08:00'}) == batch[4].board.params
        for changes in ({'effective_at': '2026-09-26T08:29:59Z'}, {'as_of': '2026-09-19'},
                        {'code_version': 'other'}, {'scheme_hash': 'other'}):
            assert store.get_frozen_params(**{**opts, **changes}) is None
        with pytest.raises(ValueError):
            store.get_frozen_params(**{**opts, 'effective_at': '2026-09-26T08:30:00'})
        store.save_batch(storage_batch(as_of='2026-10-03', freeze=True, quarter_label='2026Q3'))
        assert store.get_frozen_params(**opts) == batch[4].board.params
        latest = store.get_frozen_params(**{**opts, 'effective_at': '2026-10-03T08:30:00Z'})
        assert latest.as_of == '2026-10-03'
        weekly = store.list_boards(scheme_hash=keys[0].scheme_hash, code_version=keys[0].code_version,
                                    board_type='weekly')
        assert [k.as_of for k in weekly] == ['2026-09-26', '2026-10-03']
        assert store.list_boards(scheme_hash=keys[0].scheme_hash, code_version=keys[0].code_version,
                                  board_type='quarter_final', through='2026-09-26') == (keys[4],)
        store._conn.execute("UPDATE params SET payload_json='{}' WHERE as_of='2026-10-03'")
        with pytest.raises(ValueError):
            store.get_frozen_params(**{**opts, 'effective_at': '2026-10-03T08:30:00Z'})


def test_frozen_dependency_roundtrip_and_corruption(tmp_path):
    initial = storage_batch(freeze=True)
    with ProsperityStore(tmp_path / 'dependency.db', read_only=False) as store:
        store.save_batch(initial)
        frozen = {b.board.scheme_id: store.get_frozen_params(scheme_hash=b.board.scheme_hash,
                  code_version=b.board.code_version, as_of='2026-10-03', effective_at='2026-10-03T08:00:00Z')
                  for b in initial[:4]}
        following = storage_batch(as_of='2026-10-03', frozen=frozen)
        keys = store.save_batch(following)
        assert tuple(store.get_board(k) for k in keys) == following
        store._conn.execute("UPDATE factor_value SET raw_value=999 WHERE board_id=5")
        with pytest.raises(ValueError):
            store.get_board(keys[0])


from src.data.prosperity_storage import PublicationState


def test_publish_retry_preserves_first_receipt(tmp_path):
    batch = storage_batch()
    path = tmp_path / 'publication.db'
    with ProsperityStore(path, read_only=False) as store:
        key = key_for(batch[0])
        assert store.publication_state(key) == PublicationState(False, None)
        keys = store.save_batch(batch)
        assert store.publication_state(keys[0]) == PublicationState(True, None)
        store.mark_published(keys, published_at='2026-10-23T09:00:00Z')
        store.mark_published(keys, published_at='2026-10-23T10:00:00Z')
        store.save_batch(batch)
        assert store.publication_state(keys[0]).published_at == '2026-10-23T09:00:00Z'
    with ProsperityStore(path) as store:
        assert store.publication_state(keys[0]) == PublicationState(True, '2026-10-23T09:00:00Z')
        with pytest.raises(PermissionError):
            store.mark_published(keys, published_at='2026-10-23T11:00:00Z')


@pytest.mark.parametrize('case', ['missing', 'mixed', 'corrupt', 'early', 'naive'])
def test_publication_failure_is_atomic(tmp_path, case):
    with ProsperityStore(tmp_path / 'failed-publish.db', read_only=False) as store:
        keys = store.save_batch(storage_batch())
        at = '2026-10-23T09:00:00Z'
        if case == 'missing':
            keys = [replace(keys[0], as_of='2026-09-19')]
        elif case == 'mixed':
            keys = [keys[0], replace(keys[1], as_of='2026-09-19')]
        elif case == 'corrupt':
            store._conn.execute('DELETE FROM score WHERE board_id=2 AND row_index=0')
        elif case == 'early':
            at = '2026-09-26T08:00:00Z'
        elif case == 'naive':
            at = '2026-10-23T09:00:00'
        with pytest.raises(ValueError):
            store.mark_published(keys, published_at=at)
        assert store._conn.execute('SELECT count(*) FROM publication').fetchone()[0] == 0
        assert store._conn.execute('SELECT count(*) FROM board').fetchone()[0] == 4


def test_state_validates_board_and_receipt(tmp_path):
    with ProsperityStore(tmp_path / 'state.db', read_only=False) as store:
        keys = store.save_batch(storage_batch())
        store.mark_published(keys, published_at='2026-10-23T09:00:00Z')
        store._conn.execute("UPDATE publication SET published_at='2026-01-01T00:00:00Z' WHERE board_id=1")
        with pytest.raises(ValueError):
            store.publication_state(keys[0])
        store._conn.execute('DELETE FROM score WHERE board_id=2 AND row_index=0')
        with pytest.raises(ValueError):
            store.publication_state(keys[1])


def test_two_databases_and_recomputed_scores_across_weeks(tmp_path):
    from terminal.prosperity.kernel import score_board
    initial = storage_batch(freeze=True)
    research, production = tmp_path / 'research.db', tmp_path / 'production.db'
    with ProsperityStore(research, read_only=False) as store:
        store.save_batch(initial)
        frozen = {b.board.scheme_id: store.get_frozen_params(scheme_hash=b.board.scheme_hash,
                  code_version=b.board.code_version, as_of='2026-10-03', effective_at='2026-10-03T08:00:00Z')
                  for b in initial[:4]}
        following = storage_batch(as_of='2026-10-03', frozen=frozen)
        for key, expected in zip(store.save_batch(following), following):
            restored = store.get_board(key)
            rerun = score_board(restored.factors, restored.scheme, frozen=frozen[restored.board.scheme_id],
                                as_of=restored.board.as_of, code_version=restored.board.code_version)
            assert rerun == expected.board
        research_count = store._conn.execute('SELECT count(*) FROM board').fetchone()[0]
    with ProsperityStore(production, read_only=False) as store:
        assert store._conn.execute('SELECT count(*) FROM board').fetchone()[0] == 0
        store.save_batch(storage_batch(as_of='2026-10-10'))
    with ProsperityStore(research) as store:
        assert store._conn.execute('SELECT count(*) FROM board').fetchone()[0] == research_count == 12
    assert not (tmp_path / 'market.db').exists()


def test_independent_sql_accounting(tmp_path):
    """No store decoder/hash helper used in this check of actual persisted values."""
    import json
    import math
    path = tmp_path / 'independent.db'
    batch = storage_batch()
    with ProsperityStore(path, read_only=False) as store:
        store.save_batch(batch)
    with sqlite3.connect(path) as conn:
        conn.row_factory = sqlite3.Row
        for h, source in zip(conn.execute('SELECT * FROM board ORDER BY board_id'), batch):
            bid = h['board_id']
            members = set(json.loads(h['members_json']))
            scores = conn.execute('SELECT * FROM score WHERE board_id=? ORDER BY row_index', (bid,)).fetchall()
            assert {r['symbol'] for r in scores} | set(json.loads(h['errors_json'])) == members
            assert {r[0] for r in conn.execute('SELECT symbol FROM stock_period WHERE board_id=?', (bid,))} == members
            assert {r[0] for r in conn.execute('SELECT DISTINCT symbol FROM factor_value WHERE board_id=?', (bid,))} == members
            assert json.loads(h['counts_json']) == {k: sum(r['status'] == k for r in scores)
                                                    for k in ('ranked', 'observe', 'excluded')}
            for r, expected in zip(scores, source.board.rows):
                assert r['symbol'] == expected.symbol
                assert r['rank'] == expected.rank
                assert r['score'] == expected.score
                fs = conn.execute('SELECT * FROM factor_value WHERE board_id=? AND symbol=?',
                                  (bid, r['symbol'])).fetchall()
                for f in fs:
                    if f['z'] is None:
                        assert f['weight'] is None and f['contribution'] is None
                    else:
                        assert math.isclose(f['z'] * f['weight'], f['contribution'], abs_tol=1e-12)
                if r['status'] == 'ranked':
                    total = sum(f['contribution'] for f in fs if f['contribution'] is not None)
                    assert math.isclose(total, r['composite'], abs_tol=1e-12)
                    assert math.isclose((max(-2, min(2, total))+2)*25, r['base'], abs_tol=1e-12)
                    assert math.isclose(r['base']+r['trend_adj'], r['score'], abs_tol=1e-12)
            assert conn.execute('SELECT count(*) FROM factor_value WHERE board_id=? AND '
                                'scheme_missing=\'industry_exempt\' AND scheme_value IS NOT NULL', (bid,)).fetchone()[0] == 0
        assert conn.execute('PRAGMA foreign_key_check').fetchall() == []


def test_failure_on_frozen_half_rolls_back_weeklies(tmp_path):
    with ProsperityStore(tmp_path / 'freeze-atomic.db', read_only=False) as store:
        store._conn.execute("CREATE TRIGGER fail_freeze BEFORE INSERT ON board "
                            "WHEN NEW.board_type='quarter_final' BEGIN SELECT RAISE(ABORT, 'freeze failure'); END")
        with pytest.raises(sqlite3.IntegrityError):
            store.save_batch(storage_batch(freeze=True))
        for table in ('scheme', 'params', 'input_set', 'stock_input', 'board', 'score', 'factor_value', 'stock_period'):
            assert store._conn.execute('SELECT count(*) FROM '+table).fetchone()[0] == 0


def test_keyboard_interrupt_rolls_back_and_releases_transaction(tmp_path, monkeypatch):
    with ProsperityStore(tmp_path / 'interrupt.db', read_only=False) as store:
        original = store._insert_board
        def interrupted(board):
            original(board)
            raise KeyboardInterrupt()
        monkeypatch.setattr(store, '_insert_board', interrupted)
        with pytest.raises(KeyboardInterrupt):
            store.save_batch(storage_batch())
        assert not store._conn.in_transaction
        assert store._conn.execute('SELECT count(*) FROM board').fetchone()[0] == 0
        monkeypatch.setattr(store, '_insert_board', original)
        assert len(store.save_batch(storage_batch())) == 4


def test_weekly_expectations_change_while_quarterly_parameters_stay_frozen(tmp_path):
    from dataclasses import asdict
    from tests.prosperity_fixtures import storage_inputs
    from terminal.prosperity.factors import compute_factor_row
    from terminal.prosperity.kernel import score_board
    from terminal.prosperity.schemes import SCHEMES
    from src.data.prosperity_storage import prepare_board
    initial = storage_batch(freeze=True)
    with ProsperityStore(tmp_path / 'weekly-params.db', read_only=False) as store:
        store.save_batch(initial)
        packets, _, _, meta = storage_inputs(as_of='2026-10-03')
        for i, p in enumerate(packets):
            revision = replace(p.consensus.revision, delta_eps=p.consensus.revision.delta_eps*(2+i%3))
            packets[i] = replace(p, consensus=replace(p.consensus, revision=revision),
                                 archive={**p.archive, 'revision': asdict(revision)})
        factors = [compute_factor_row(p) for p in packets]
        output = []
        for first in initial[:4]:
            scheme = first.scheme
            frozen = store.get_frozen_params(scheme_hash=first.board.scheme_hash, code_version='test-v1',
                                             as_of='2026-10-03', effective_at='2026-10-03T08:30:00Z')
            result = score_board(factors, scheme, as_of='2026-10-03', code_version='test-v1', frozen=frozen)
            mapping = 'reference' if scheme.transform == 'rank_z' else 'winsor'
            used, prior = getattr(result.params, mapping), getattr(frozen, mapping)
            assert used['revenue_yoy'] == prior['revenue_yoy']
            if scheme.scheme_id != 'F0':
                assert used['revision'] != prior['revision']
            output.append(prepare_board(meta, result, SCHEMES[scheme.scheme_id], packets, factors,
                                        names={p.symbol: None for p in packets}))
        keys = store.save_batch(output)
        assert tuple(store.get_board(k) for k in keys) == tuple(output)


def test_raw_key_absence_is_distinct_from_null_and_raw_missing_presence(tmp_path):
    b = storage_batch()[0]
    f = b.factors[0]
    values = dict(f.values)
    values.pop('eps_sue')
    values['eps_accel'] = None
    missing = dict(f.missing)
    missing.pop('eps_sue', None)
    missing['eps_accel'] = 'fixture_gap'
    b = replace(b, factors=(replace(f, values=values, missing=missing),) + b.factors[1:])
    with ProsperityStore(tmp_path / 'presence.db', read_only=False) as store:
        key = store.save_batch([b])[0]
        loaded = store.get_board(key)
        assert loaded == b
        assert 'eps_sue' not in loaded.factors[0].values
        assert loaded.factors[0].values['eps_accel'] is None
        assert loaded.factors[0].missing['eps_accel'] == 'fixture_gap'


def test_old_scheme_reads_without_current_registry(tmp_path, monkeypatch):
    from terminal.prosperity import schemes
    path = tmp_path / 'old-scheme.db'
    with ProsperityStore(path, read_only=False) as store:
        batch = storage_batch()
        keys = store.save_batch(batch)
    monkeypatch.setattr(schemes, 'SCHEMES', {})
    monkeypatch.setitem(schemes.FACTOR_SPECS, 'revenue_yoy', replace(schemes.FACTOR_SPECS['revenue_yoy'], family='changed'))
    with ProsperityStore(path) as store:
        assert tuple(store.get_board(k) for k in keys) == batch


def test_current_freeze_can_reference_its_newly_estimated_package(tmp_path):
    from terminal.prosperity.kernel import score_board, estimate_params
    from src.data.prosperity_storage import prepare_board
    from tests.prosperity_fixtures import storage_inputs
    from terminal.prosperity.schemes import SCHEMES
    packets, factors, _, meta = storage_inputs()
    batch = []
    for scheme in SCHEMES.values():
        params = estimate_params(factors, scheme, as_of='2026-09-26', code_version='test-v1')
        result = score_board(factors, scheme, as_of='2026-09-26', code_version='test-v1', frozen=params)
        weekly = prepare_board(meta, result, scheme, packets, factors, names={p.symbol: None for p in packets})
        batch.extend([weekly, replace(weekly, meta=replace(meta, board_type='quarter_final', quarter_label='2026Q2',
                                                          frozen_at='2026-09-26T08:30:00Z'))])
    with ProsperityStore(tmp_path / 'same-week.db', read_only=False) as store:
        keys = store.save_batch(batch)
        assert tuple(store.get_board(k) for k in keys) == tuple(batch)


@pytest.mark.parametrize('ddl', [
    'CREATE VIEW other_app AS SELECT 42 AS answer',
    'CREATE TABLE sqliteXuser_data(x INTEGER); INSERT INTO sqliteXuser_data VALUES(42)',
])
def test_existing_user_objects_are_not_an_empty_new_database(tmp_path, ddl):
    path = tmp_path / 'other-app.db'
    with sqlite3.connect(path) as conn:
        conn.executescript(ddl)
    before = path.read_bytes()
    with pytest.raises(ValueError, match='schema'):
        ProsperityStore(path, read_only=False)
    assert path.read_bytes() == before


# --- 2026-10-23 acceptance fixes: point-in-time freezes, one freeze per quarter, shared inputs ---

def _replay(batch, computed_at):
    return tuple(replace(b, meta=replace(b.meta, mode='replay', computed_at=computed_at,
                                         source_evidence={**b.meta.source_evidence, 'observed_at': None}))
                 for b in batch)


def test_replay_cannot_cite_a_freeze_that_was_not_effective_on_its_date(tmp_path):
    """Boss 2026-10-23 #1: write path must apply the same as-of rule as get_frozen_params."""
    late = _replay(storage_batch(freeze=True, frozen_at='2026-10-20T08:30:00Z'), '2026-10-20T09:00:00Z')
    frozen = {b.board.scheme_id: b.board.params for b in late[4:]}
    following = _replay(storage_batch(as_of='2026-10-03', frozen=frozen), '2026-10-25T09:00:00Z')
    with ProsperityStore(tmp_path / 'pit.db', read_only=False) as store:
        store.save_batch(late)
        with pytest.raises(ValueError, match='frozen_not_effective'):
            store.save_batch(following)
        assert store._conn.execute("SELECT count(*) FROM board WHERE as_of='2026-10-03'").fetchone()[0] == 0


def test_newest_quarter_freeze_wins_over_a_later_recomputed_older_one(tmp_path):
    """Boss 2026-10-23 #2: choose by quarter date, not by when the freeze was written."""
    older = _replay(storage_batch(freeze=True, frozen_at='2026-10-20T08:30:00Z'), '2026-10-20T09:00:00Z')
    newer = storage_batch(as_of='2026-10-03', freeze=True, quarter_label='2026Q3')
    with ProsperityStore(tmp_path / 'latest.db', read_only=False) as store:
        store.save_batch(older)
        store.save_batch(newer)
        key = key_for(older[0])
        chosen = store.get_frozen_params(scheme_hash=key.scheme_hash, code_version=key.code_version,
                                         as_of='2026-10-10', effective_at='2026-10-23T00:00:00Z')
        assert chosen.as_of == '2026-10-03'


def test_one_quarter_final_board_per_quarter_label(tmp_path):
    """Boss 2026-10-23 #3: a quarter freeze is unique, enforced by the database."""
    with ProsperityStore(tmp_path / 'quarter.db', read_only=False) as store:
        store.save_batch(storage_batch(freeze=True, quarter_label='2026Q2'))
        with pytest.raises((ValueError, sqlite3.IntegrityError)):
            store.save_batch(storage_batch(as_of='2026-10-03', freeze=True, quarter_label='2026Q2'))
        assert store._conn.execute("SELECT count(*) FROM board WHERE board_type='quarter_final'").fetchone()[0] == 4
        assert store._conn.execute('SELECT count(*) FROM board').fetchone()[0] == 8


def test_quarter_final_unique_index_is_part_of_the_schema(tmp_path):
    with ProsperityStore(tmp_path / 'index.db', read_only=False) as store:
        sql = store._conn.execute("SELECT sql FROM sqlite_master WHERE type='index' AND tbl_name='board' "
                                  "AND sql LIKE '%quarter_label%'").fetchone()
    assert sql is not None and 'UNIQUE' in sql[0] and "board_type='quarter_final'" in sql[0]


def test_one_input_archive_per_week_shared_by_every_board(tmp_path):
    """Boss 2026-10-23 capacity decision: the week's inputs are stored once for all schemes and the freeze."""
    batch = storage_batch(freeze=True)
    path = tmp_path / 'shared.db'
    with ProsperityStore(path, read_only=False) as store:
        keys = store.save_batch(batch)
        assert store.save_batch(batch) == keys
    with sqlite3.connect(path) as conn:
        assert conn.execute('SELECT count(*) FROM input_set').fetchone()[0] == 1
        assert conn.execute('SELECT count(*) FROM stock_input').fetchone()[0] == len(batch[0].stocks)
        assert conn.execute('SELECT count(DISTINCT input_set_id) FROM board').fetchone()[0] == 1
        columns = {r[1] for r in conn.execute('PRAGMA table_info(stock_period)')}
        assert not {'input_json', 'factor_meta_json'} & columns
    with ProsperityStore(path) as store:
        assert tuple(store.get_board(k) for k in keys) == batch


def test_boards_in_one_batch_must_share_identical_inputs(tmp_path):
    batch = storage_batch()
    stock = {**batch[1].stocks[3], 'price_asof': 123.456}
    drifted = replace(batch[1], stocks=batch[1].stocks[:3] + (stock,) + batch[1].stocks[4:])
    with ProsperityStore(tmp_path / 'drift.db', read_only=False) as store:
        with pytest.raises(ValueError, match='batch_inputs_mismatch'):
            store.save_batch([batch[0], drifted, *batch[2:]])
        assert store._conn.execute('SELECT count(*) FROM board').fetchone()[0] == 0


def test_corrupt_shared_input_fails_every_board_that_uses_it(tmp_path):
    with ProsperityStore(tmp_path / 'shared-corrupt.db', read_only=False) as store:
        keys = store.save_batch(storage_batch())
        store._conn.execute("UPDATE stock_input SET input_json=replace(input_json,'Synthetic','Edited') "
                            "WHERE symbol='S0003'")
        for key in keys:
            with pytest.raises(ValueError):
                store.get_board(key)


def test_new_week_or_version_gets_its_own_input_archive(tmp_path):
    with ProsperityStore(tmp_path / 'weeks.db', read_only=False) as store:
        store.save_batch(storage_batch())
        store.save_batch(storage_batch(as_of='2026-10-03'))
        store.save_batch(storage_batch(code_version='v2'))
        assert store._conn.execute('SELECT count(*) FROM input_set').fetchone()[0] == 3
        assert store._conn.execute('SELECT count(*) FROM board').fetchone()[0] == 12


@pytest.mark.parametrize('frozen_at', ['2026-09-26T08:45:00Z', '2026-09-27T08:30:00Z'])
def test_corrupt_freeze_time_is_an_error_not_a_cold_start(tmp_path, frozen_at):
    """Codex 2026-10-23 P2: a stored freeze must be verified before its frozen_at is trusted for filtering."""
    batch = storage_batch(freeze=True)
    with ProsperityStore(tmp_path / 'freeze-time.db', read_only=False) as store:
        key = store.save_batch(batch)[4]
        store._conn.execute("UPDATE board SET frozen_at=? WHERE board_type='quarter_final'", (frozen_at,))
        with pytest.raises(ValueError):
            store.get_board(key)
        with pytest.raises(ValueError):
            store.get_frozen_params(scheme_hash=key.scheme_hash, code_version=key.code_version,
                                    as_of='2026-09-26', effective_at='2026-09-26T08:35:00Z')
