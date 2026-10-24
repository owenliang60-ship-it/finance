import json
from dataclasses import replace
from datetime import datetime, timezone

import pytest

from src.data.market_store import MarketStore
from src.data.prosperity_store import ProsperityStore
from tests.prosperity_fixtures import seed_db, storage_inputs


NOW = datetime(2026, 10, 24, 9, tzinfo=timezone.utc)


def builder_with(calls, buckets=None):
    def build(market, as_of, **kwargs):
        calls.append(as_of)
        packets, _, _, _ = storage_inputs(as_of=as_of)
        label = (buckets or {}).get(as_of, '2026Q2')
        packets = [replace(p, quarter_bucket=label) for p in packets]
        return packets, {'members_resolved': len(packets), 'errors': {}}
    return build


@pytest.fixture
def env(tmp_path):
    market = MarketStore(db_path=seed_db(tmp_path / 'market.db'), read_only=True)
    quality = tmp_path / 'quality'
    quality.mkdir()
    (quality / 'fundamentals-now.json').write_text(json.dumps({'after': {
        'as_of': '2026-10-24T07:00:00Z', 'coverage': {'fundamental_ready': {'ratio': .96}}}}))
    with ProsperityStore(tmp_path / 'result.db', read_only=False) as store:
        yield market, store, quality
    market.close()


def options(env, calls, **kwargs):
    market, store, quality = env
    return dict(saturday='2026-10-24', mode='live', market=market, store=store,
                quality_dir=quality, publishers=(), now=lambda: NOW, version='test-m9',
                start_quarter='2026Q3', packet_builder=builder_with(calls), **kwargs)


def test_week_compute_once_and_empty_publishers_do_not_mark(env):
    from src.data.prosperity_run import run_week
    calls = []
    opts = options(env, calls)
    first = run_week(**opts)
    assert first.exit_code == 0 and len(first.keys) == 4
    assert all(env[1].publication_state(k).published_at is None for k in first.keys)
    assert run_week(**opts).exit_code == 0
    assert calls == ['2026-10-24']


def test_publish_failure_retries_only_publication(env):
    from src.data.prosperity_run import run_week
    calls, published = [], []
    def broken(keys):
        raise RuntimeError('publisher offline')
    opts = options(env, calls)
    first = run_week(**{**opts, 'publishers': (broken,)})
    assert first.exit_code == 5
    second = run_week(**{**opts, 'publishers': (published.append,)})
    assert second.exit_code == 0 and len(published) == 1
    assert calls == ['2026-10-24']
    assert all(env[1].publication_state(k).published_at for k in second.keys)


def test_hard_gate_and_replay_never_publish(env):
    from src.data.prosperity_run import run_week
    calls, publications = [], []
    opts = options(env, calls)
    for p in env[2].iterdir():
        p.unlink()
    assert run_week(**opts).exit_code == 3
    assert not calls
    replay = run_week(**{**opts, 'saturday': '2026-10-17', 'mode': 'replay', 'publishers': (publications.append,)})
    assert replay.exit_code == 0 and len(replay.keys) == 4 and not publications
    assert run_week(**{**opts, 'mode': 'replay'}).exit_code == 4


def test_freeze_eight_boards_and_next_week_uses_params(env):
    from src.data.prosperity_run import run_week
    calls = []
    opts = options(env, calls)
    freeze_now = datetime(2026, 12, 5, 12, 30, tzinfo=timezone.utc)
    (env[2] / 'fundamentals-now.json').write_text(json.dumps({'after': {'as_of': '2026-12-05T07:00:00Z',
        'coverage': {'fundamental_ready': {'ratio': .96}}}}))
    opts.update(saturday='2026-12-05', now=lambda: freeze_now,
                packet_builder=builder_with(calls, {'2026-12-05': '2026Q3'}))
    out = run_week(**opts)
    assert out.exit_code == 0 and len(out.keys) == 8
    for k in out.keys[4:]:
        assert env[1].get_board(k).meta.frozen_at == '2026-12-05T12:30:00Z'
    later = run_week(**{**opts, 'saturday': '2026-12-12', 'mode': 'replay',
                        'now': lambda: datetime(2026, 12, 19, 9, tzinfo=timezone.utc)})
    assert later.exit_code == 0
    assert all(env[1].get_board(k).board.frozen_as_of == '2026-12-05' for k in later.keys)


def test_candidate_receipt_prevents_rejudging_and_detects_pending_drift(env, monkeypatch):
    from src.data.prosperity_run import rebuild_freeze_chain
    calls = []
    build = builder_with(calls, {'2026-08-29': '2026Q1', '2026-09-05': '2026Q2'})
    opts = dict(market=env[0], store=env[1], through='2026-09-05', start_quarter='2026Q2',
                now=lambda: NOW, version='test-m9', packet_builder=build)
    save = env[1].save_batch
    def broken(boards):
        raise RuntimeError('injected before commit')
    monkeypatch.setattr(env[1], 'save_batch', broken)
    assert rebuild_freeze_chain(**opts).exit_code == 6
    assert calls == ['2026-08-29', '2026-09-05']
    monkeypatch.setattr(env[1], 'save_batch', save)
    def changed(market, as_of, **kw):
        packets, meta = build(market, as_of, **kw)
        return [replace(p, price_asof=p.price_asof + 1) for p in packets], meta
    bad = rebuild_freeze_chain(**{**opts, 'packet_builder': changed})
    assert bad.exit_code == 6 and 'recovery_input_changed' in bad.error
    assert calls[-1] == '2026-09-05' and calls.count('2026-08-29') == 1
    recovered = rebuild_freeze_chain(**opts)
    assert recovered.exit_code == 0 and len(recovered.keys) == 8
    assert all(k.as_of == '2026-09-05' for k in recovered.keys)


def test_rebuild_timeout_and_invalid_future(env):
    from src.data.prosperity_run import rebuild_freeze_chain
    opts = dict(market=env[0], store=env[1], through='2026-09-19', start_quarter='2026Q2',
                now=lambda: NOW, version='test-m9', packet_builder=builder_with([]))
    assert rebuild_freeze_chain(**opts, timeout_seconds=0).exit_code == 6
    assert rebuild_freeze_chain(**{**opts, 'through': '2026-10-24'}).exit_code == 4


def test_partial_freeze_is_not_published(env):
    from src.data.prosperity_run import run_week
    from tests.prosperity_fixtures import storage_batch
    batch = storage_batch(as_of='2026-10-24', code_version='test-m9', freeze=True)
    env[1].save_batch(batch[:5])
    published = []
    out = run_week(**{**options(env, []), 'publishers': (published.append,)})
    assert out.exit_code == 6 and 'partial_freeze' in out.error
    assert not published


def test_candidate_corruption_fails_closed(env):
    from src.data.prosperity_run import rebuild_freeze_chain, state_path
    calls = []
    opts = dict(market=env[0], store=env[1], through='2026-08-29', start_quarter='2026Q2',
                now=lambda: NOW, version='test-m9',
                packet_builder=builder_with(calls, {'2026-08-29': '2026Q1'}))
    assert rebuild_freeze_chain(**opts).exit_code == 0
    receipt = next((state_path(env[1]) / 'candidates').glob('*.json'))
    receipt.write_text('{}')
    assert rebuild_freeze_chain(**opts).exit_code == 6
    assert calls == ['2026-08-29']


def test_version_rebuild_preserves_old_rows(env):
    from src.data.prosperity_run import rebuild_freeze_chain
    opts = dict(market=env[0], store=env[1], through='2026-09-19', start_quarter='2026Q2',
                now=lambda: NOW, packet_builder=builder_with([]))
    old = rebuild_freeze_chain(**opts, version='old')
    frozen = [env[1].get_board(k) for k in old.keys]
    new = rebuild_freeze_chain(**opts, version='new')
    assert old.exit_code == new.exit_code == 0 and len(new.keys) == 8
    assert [env[1].get_board(k) for k in old.keys] == frozen


def test_excessive_packet_errors_prevent_any_board(env):
    from src.data.prosperity_run import run_week
    build = builder_with([])
    def faulty(market, as_of, **kw):
        packets, meta = build(market, as_of, **kw)
        return packets[:-1], {**meta, 'errors': {packets[-1].symbol: 'injected'}}
    out = run_week(**{**options(env, []), 'packet_builder': faulty})
    assert out.exit_code == 3 and not out.keys
    assert env[1]._conn.execute('SELECT COUNT(*) FROM board').fetchone()[0] == 0


def test_live_pending_retry_rebuilds_at_original_cutoff(env, monkeypatch):
    from src.data.prosperity_run import run_week
    clock = [datetime(2026, 12, 5, 8, 30, tzinfo=timezone.utc)]
    cutoffs = []
    base = builder_with([], {'2026-12-05': '2026Q3'})
    def timed(market, as_of, **kw):
        cutoffs.append(kw['cutoff'].hour)
        packets, meta = base(market, as_of, **kw)
        return [replace(p, price_asof=100 + kw['cutoff'].hour) for p in packets], meta
    (env[2] / 'fundamentals-now.json').write_text(json.dumps({'after': {'as_of': '2026-12-05T07:00:00Z',
        'coverage': {'fundamental_ready': {'ratio': .96}}}}))
    opts = {**options(env, []), 'saturday': '2026-12-05', 'now': lambda: clock[0], 'packet_builder': timed}
    save = env[1].save_batch
    monkeypatch.setattr(env[1], 'save_batch', lambda boards: (_ for _ in ()).throw(RuntimeError('injected')))
    assert run_week(**opts).exit_code == 6
    monkeypatch.setattr(env[1], 'save_batch', save)
    clock[0] = datetime(2026, 12, 5, 12, 30, tzinfo=timezone.utc)
    out = run_week(**opts)
    assert out.exit_code == 0 and cutoffs[-1] == 8
    assert all(env[1].get_board(k).meta.frozen_at == '2026-12-05T08:30:00Z' for k in out.keys[4:])


def test_replay_week_with_negative_receipt_does_not_block_chain(env):
    from src.data.prosperity_run import run_week, rebuild_freeze_chain
    calls = []
    build = builder_with(calls, {'2026-08-29': '2026Q1', '2026-09-05': '2026Q2'})
    opts = {**options(env, calls), 'mode': 'replay', 'saturday': '2026-08-29',
            'start_quarter': '2026Q2', 'packet_builder': build}
    assert run_week(**opts).exit_code == 0
    result = rebuild_freeze_chain(market=env[0], store=env[1], through='2026-09-05',
        start_quarter='2026Q2', now=lambda: NOW, version='test-m9', packet_builder=build)
    assert result.exit_code == 0 and len(result.keys) == 8
    assert calls.count('2026-08-29') == 1


def test_degenerate_freeze_input_health_returns_stale(env):
    from src.data.prosperity_run import run_week
    clock = datetime(2026,12,5,9,tzinfo=timezone.utc)
    base = builder_with([], {'2026-12-05':'2026Q3'})
    def identical(market, as_of, **kw):
        packets, meta = base(market, as_of, **kw)
        return [replace(packets[3], symbol=p.symbol) for p in packets], meta
    (env[2]/'fundamentals-now.json').write_text(json.dumps({'after':{'as_of':'2026-12-05T07:00:00Z',
        'coverage':{'fundamental_ready':{'ratio':.96}}}}))
    out = run_week(**{**options(env,[]), 'saturday':'2026-12-05', 'now':lambda:clock, 'packet_builder':identical})
    assert out.exit_code == 3 and not out.keys
