import sqlite3

import pytest

from trading.momentum_ls01.store import LockBusy, Store, TerminalActionError, process_lock


@pytest.fixture
def store(tmp_path):
    now = [1000.0]
    s = Store(tmp_path / 'state', clock=lambda: now[0])
    s.now = now
    return s


def plan(store, client_id='mls1-26111604-B-0001-1', **overrides):
    fields = dict(client_id=client_id, kind='order', purpose='wave_b_open', anchor='2026-11-16 04:00:00+00:00',
                  symbol='AAAUSDT', side='SELL', qty='10', lane='risk', request={'symbol': 'AAAUSDT'})
    fields.update(overrides)
    return store.plan_action(**fields)


def test_database_is_wal_versioned_and_has_exactly_the_four_tables(store, tmp_path):
    db = sqlite3.connect(tmp_path / 'state' / 'state.db')
    assert db.execute('PRAGMA journal_mode').fetchone()[0] == 'wal'
    assert db.execute('PRAGMA user_version').fetchone()[0] == 1
    tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")}
    assert tables == {'weeks', 'actions', 'state', 'evidence'}
    assert (tmp_path / 'state').stat().st_mode & 0o777 == 0o700


def test_week_fields_merge_and_json_columns_round_trip(store):
    anchor = '2026-11-16 04:00:00+00:00'
    assert store.week(anchor) is None
    store.save_week(anchor, prep_status='ready', regime=1, short_ranked=['A', 'B'], status='prepared')
    store.save_week(anchor, status='running', targets={'A': '-10'})
    store.add_deviation(anchor, 'leverage_failed', symbol='A', code_from_exchange=-4005)
    week = store.week(anchor)
    assert week['prep_status'] == 'ready' and week['status'] == 'running' and week['short_ranked'] == ['A', 'B']
    assert week['targets'] == {'A': '-10'}
    assert week['deviations'] == [{'code': 'leverage_failed', 'symbol': 'A', 'code_from_exchange': -4005}]


def test_week_rejects_unknown_columns(store):
    with pytest.raises(ValueError):
        store.save_week('a', surprise=1)


def test_action_is_planned_then_sending_with_timestamps(store):
    action_id = plan(store)
    assert store.action(action_id)['state'] == 'PLANNED' and store.action(action_id)['sent_at'] is None
    store.now[0] = 1005.0
    store.mark_sending(action_id)
    action = store.action(action_id)
    assert action['state'] == 'SENDING' and action['sent_at'] == 1005.0 and action['request'] == {'symbol': 'AAAUSDT'}


def test_client_id_is_unique(store):
    plan(store)
    with pytest.raises(sqlite3.IntegrityError):
        plan(store)


def test_only_a_planned_action_can_start_sending(store):
    action_id = plan(store)
    store.mark_sending(action_id)
    with pytest.raises(ValueError):
        store.mark_sending(action_id)


def test_terminal_action_is_never_rewritten(store):
    action_id = plan(store)
    store.mark_sending(action_id)
    store.update_action(action_id, state='FILLED', executed_qty='10')
    assert store.action(action_id)['terminal_at'] == 1000.0
    with pytest.raises(TerminalActionError):
        store.update_action(action_id, state='UNKNOWN')
    assert store.action(action_id)['state'] == 'FILLED'


def test_actions_filter_by_state_and_kind(store):
    a = plan(store)
    plan(store, client_id='mls1-26111604-P-0002-1', kind='algo', purpose='protect_new', lane='protect')
    store.mark_sending(a)
    assert [x['id'] for x in store.actions(state='SENDING')] == [a]
    assert [x['kind'] for x in store.actions(kind='algo')] == ['algo']
    assert len(store.actions(state=('SENDING', 'PLANNED'))) == 2


def test_sequence_numbers_increase_per_tag(store):
    assert [store.next_sequence('26111604'), store.next_sequence('26111604'), store.next_sequence('26112304')] == [1, 2, 1]


def test_state_keys_set_get_delete_and_prefix_listing(store):
    assert store.get('pause') is None and store.get('missing', 5) == 5
    store.set('cooldown:AAAUSDT', {'at': 1.5})
    store.set('cooldown:BBBUSDT', {'at': 2.5})
    assert store.get('cooldown:AAAUSDT') == {'at': 1.5}
    assert store.keys('cooldown:') == ['cooldown:AAAUSDT', 'cooldown:BBBUSDT']
    store.delete('cooldown:AAAUSDT')
    assert store.keys('cooldown:') == ['cooldown:BBBUSDT']


def test_evidence_is_deduplicated_by_key(store):
    assert store.add_evidence('income', 'FUNDING_FEE:AAAUSDT:7:1', {'income': '-1.5'}, symbol='AAAUSDT', time_ms=1) is True
    assert store.add_evidence('income', 'FUNDING_FEE:AAAUSDT:7:1', {'income': '-1.5'}) is False
    rows = store.evidence('income')
    assert len(rows) == 1 and rows[0]['payload'] == {'income': '-1.5'} and rows[0]['symbol'] == 'AAAUSDT'


def test_notifications_stay_pending_until_marked_sent(store):
    store.add_evidence('notification', 'n:1', {'text': 'hello'}, status='pending')
    pending = store.pending_notifications()
    assert [n['payload']['text'] for n in pending] == ['hello']
    store.mark_notification_sent(pending[0]['id'])
    assert store.pending_notifications() == []


def test_transaction_rolls_back_every_write_on_error(store):
    with pytest.raises(RuntimeError):
        with store.tx():
            store.set('a', 1)
            plan(store)
            raise RuntimeError('boom')
    assert store.get('a') is None and store.actions() == []


def test_process_lock_is_exclusive_and_released(tmp_path):
    with process_lock(tmp_path):
        with pytest.raises(LockBusy):
            with process_lock(tmp_path):
                pass
    with process_lock(tmp_path):
        pass


def test_backup_is_restorable_and_old_copies_are_pruned(store, tmp_path):
    plan(store)
    store.set('pause', {'reason': 'x'})
    for i in range(4):
        store.now[0] = 2000.0 + i
        path = store.backup(tmp_path / 'backups', keep=3)
    assert len(list((tmp_path / 'backups').glob('state-*.db'))) == 3
    restored = sqlite3.connect(path)
    assert restored.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
    assert restored.execute('SELECT count(*) FROM actions').fetchone()[0] == 1
    assert restored.execute('SELECT count(*) FROM state').fetchone()[0] == 1
