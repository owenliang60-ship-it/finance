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
    freeze_now = datetime(2026, 12, 26, 12, 30, tzinfo=timezone.utc)
    (env[2] / 'fundamentals-now.json').write_text(json.dumps({'after': {'as_of': '2026-12-26T07:00:00Z',
        'coverage': {'fundamental_ready': {'ratio': .96}}}}))
    opts.update(saturday='2026-12-26', now=lambda: freeze_now,
                packet_builder=builder_with(calls, {'2026-12-26': '2026Q3'}))
    out = run_week(**opts)
    assert out.exit_code == 0 and len(out.keys) == 8
    for k in out.keys[4:]:
        assert env[1].get_board(k).meta.frozen_at == '2026-12-26T12:30:00Z'
    later = run_week(**{**opts, 'saturday': '2027-01-02', 'mode': 'replay',
                        'now': lambda: datetime(2027, 1, 9, 9, tzinfo=timezone.utc)})
    assert later.exit_code == 0
    assert all(env[1].get_board(k).board.frozen_as_of == '2026-12-26' for k in later.keys)


def test_candidate_receipt_prevents_rejudging_and_detects_pending_drift(env, monkeypatch):
    from src.data.prosperity_run import rebuild_freeze_chain, state_path
    calls = []
    # One candidate Saturday per quarter: 2026Q2 freezes on 09-26, 2026Q3 fails on 12-26 before commit
    # (fixture packets end at 2026-06-30 / EPS 07-30, so a 2026Q1 freeze on 06-27 would be a packet leak)
    build = builder_with(calls, {'2026-09-26': '2026Q2', '2026-12-26': '2026Q3'})
    opts = dict(market=env[0], store=env[1], through='2026-12-26', start_quarter='2026Q2',
                now=lambda: datetime(2027, 1, 9, 9, tzinfo=timezone.utc), version='test-m9', packet_builder=build)
    save = env[1].save_batch
    saves = []
    def broken(boards):
        saves.append(boards)
        if len(saves) == 2:
            raise RuntimeError('injected before commit')
        return save(boards)
    monkeypatch.setattr(env[1], 'save_batch', broken)
    assert rebuild_freeze_chain(**opts).exit_code == 6
    assert calls == ['2026-09-26', '2026-12-26']
    pending = next((state_path(env[1]) / 'candidates').glob('2026-12-26-*.json')).read_bytes()
    monkeypatch.setattr(env[1], 'save_batch', save)
    def changed(market, as_of, **kw):
        packets, meta = build(market, as_of, **kw)
        return [replace(p, price_asof=p.price_asof + 1) for p in packets], meta
    bad = rebuild_freeze_chain(**{**opts, 'packet_builder': changed})
    assert bad.exit_code == 6 and 'recovery_input_changed' in bad.error
    assert calls[-1] == '2026-12-26' and calls.count('2026-09-26') == 1
    recovered = rebuild_freeze_chain(**opts)
    assert recovered.exit_code == 0 and len(recovered.keys) == 8
    assert all(k.as_of == '2026-12-26' for k in recovered.keys)
    assert calls.count('2026-09-26') == 1                                     # the frozen quarter is not re-judged
    candidates = state_path(env[1]) / 'candidates'
    assert not list(candidates.glob('*.live-abandoned'))
    assert next(candidates.glob('2026-12-26-*.json')).read_bytes() == pending


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


def test_candidate_corruption_fails_closed(env, monkeypatch):
    from src.data.prosperity_run import rebuild_freeze_chain, state_path
    calls = []
    opts = dict(market=env[0], store=env[1], through='2026-09-26', start_quarter='2026Q2',
                now=lambda: NOW, version='test-m9',
                packet_builder=builder_with(calls, {'2026-09-26': '2026Q2'}))
    save = env[1].save_batch
    monkeypatch.setattr(env[1], 'save_batch', lambda boards: (_ for _ in ()).throw(RuntimeError('injected')))
    assert rebuild_freeze_chain(**opts).exit_code == 6                       # pending receipt, no boards
    monkeypatch.setattr(env[1], 'save_batch', save)
    receipt = next((state_path(env[1]) / 'candidates').glob('*.json'))
    receipt.write_text('{}')
    assert rebuild_freeze_chain(**opts).exit_code == 6
    assert calls == ['2026-09-26']


def test_version_rebuild_preserves_old_rows(env):
    from src.data.prosperity_run import rebuild_freeze_chain
    opts = dict(market=env[0], store=env[1], through='2026-09-26', start_quarter='2026Q2',
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
    clock = [datetime(2026, 12, 26, 8, 30, tzinfo=timezone.utc)]
    cutoffs = []
    base = builder_with([], {'2026-12-26': '2026Q3'})
    def timed(market, as_of, **kw):
        cutoffs.append(kw['cutoff'].hour)
        packets, meta = base(market, as_of, **kw)
        return [replace(p, price_asof=100 + kw['cutoff'].hour) for p in packets], meta
    (env[2] / 'fundamentals-now.json').write_text(json.dumps({'after': {'as_of': '2026-12-26T07:00:00Z',
        'coverage': {'fundamental_ready': {'ratio': .96}}}}))
    opts = {**options(env, []), 'saturday': '2026-12-26', 'now': lambda: clock[0], 'packet_builder': timed}
    save = env[1].save_batch
    monkeypatch.setattr(env[1], 'save_batch', lambda boards: (_ for _ in ()).throw(RuntimeError('injected')))
    assert run_week(**opts).exit_code == 6
    monkeypatch.setattr(env[1], 'save_batch', save)
    clock[0] = datetime(2026, 12, 26, 12, 30, tzinfo=timezone.utc)
    out = run_week(**opts)
    assert out.exit_code == 0 and cutoffs[-1] == 8
    assert all(env[1].get_board(k).meta.frozen_at == '2026-12-26T08:30:00Z' for k in out.keys[4:])


def test_stale_live_pending_receipt_is_retired_by_next_week_chain(env, monkeypatch):
    from src.data.prosperity_run import run_week, state_path
    clock = [datetime(2026, 12, 26, 8, 30, tzinfo=timezone.utc)]
    base = builder_with([], {'2026-12-26': '2026Q3'})
    def timed(market, as_of, **kw):
        packets, meta = base(market, as_of, **kw)
        return [replace(p, price_asof=100 + kw['cutoff'].hour) for p in packets], meta
    quality = env[2] / 'fundamentals-now.json'
    quality.write_text(json.dumps({'after': {'as_of': '2026-12-26T07:00:00Z',
        'coverage': {'fundamental_ready': {'ratio': .96}}}}))
    opts = {**options(env, []), 'saturday': '2026-12-26', 'now': lambda: clock[0], 'packet_builder': timed}
    save = env[1].save_batch
    monkeypatch.setattr(env[1], 'save_batch', lambda boards: (_ for _ in ()).throw(RuntimeError('injected')))
    assert run_week(**opts).exit_code == 6
    monkeypatch.setattr(env[1], 'save_batch', save)
    candidates = state_path(env[1]) / 'candidates'
    [live] = candidates.glob('2026-12-26-*.json')
    original = live.read_bytes()
    assert json.loads(original)['payload']['meta']['mode'] == 'live'
    clock[0] = datetime(2027, 1, 2, 8, 30, tzinfo=timezone.utc)
    quality.write_text(json.dumps({'after': {'as_of': '2027-01-02T07:00:00Z',
        'coverage': {'fundamental_ready': {'ratio': .96}}}}))
    out = run_week(**{**opts, 'saturday': '2027-01-02'})
    assert out.exit_code == 0, out.error
    chain = [k for k in out.detail['chain_keys'] if k['as_of'] == '2026-12-26']
    assert len(chain) == 8
    from src.data.prosperity_storage import BoardKey
    for k in chain:
        meta = env[1].get_board(BoardKey(**k)).meta
        assert meta.mode == 'replay'
        if k['board_type'] == 'quarter_final':
            assert meta.frozen_at == '2026-12-27T00:00:00Z'
    abandoned = list(candidates.glob('2026-12-26-*.live-abandoned'))
    assert len(abandoned) == 1 and abandoned[0].read_bytes() == original
    [replay] = candidates.glob('2026-12-26-*.json')
    assert json.loads(replay.read_text())['payload']['meta']['mode'] == 'replay'


def test_replay_week_before_the_freeze_date_does_not_block_chain(env):
    # was ..._with_negative_receipt_...: the calendar never judges a non-freeze week, so the weekly
    # replay of 09-19 leaves no receipt and the 09-26 freeze still finds its day free
    from src.data.prosperity_run import run_week, rebuild_freeze_chain
    calls = []
    build = builder_with(calls, {'2026-09-26': '2026Q2'})
    opts = {**options(env, calls), 'mode': 'replay', 'saturday': '2026-09-19',
            'start_quarter': '2026Q2', 'packet_builder': build}
    week = run_week(**opts)
    assert week.exit_code == 0 and len(week.keys) == 4
    result = rebuild_freeze_chain(market=env[0], store=env[1], through='2026-09-26',
        start_quarter='2026Q2', now=lambda: NOW, version='test-m9', packet_builder=build)
    assert result.exit_code == 0 and len(result.keys) == 8
    assert all(k.as_of == '2026-09-26' for k in result.keys)
    assert calls.count('2026-09-19') == 1 and calls.count('2026-09-26') == 1


def test_degenerate_freeze_input_health_returns_stale(env):
    from src.data.prosperity_run import run_week
    clock = datetime(2026,12,26,9,tzinfo=timezone.utc)
    base = builder_with([], {'2026-12-26':'2026Q3'})
    def identical(market, as_of, **kw):
        packets, meta = base(market, as_of, **kw)
        return [replace(packets[3], symbol=p.symbol) for p in packets], meta
    (env[2]/'fundamentals-now.json').write_text(json.dumps({'after':{'as_of':'2026-12-26T07:00:00Z',
        'coverage':{'fundamental_ready':{'ratio':.96}}}}))
    out = run_week(**{**options(env,[]), 'saturday':'2026-12-26', 'now':lambda:clock, 'packet_builder':identical})
    assert out.exit_code == 3 and not out.keys


def test_missed_freeze_week_is_replayed_on_its_date_next_week(env):
    from src.data.prosperity_run import run_week
    from src.data.prosperity_storage import BoardKey
    calls = []
    build = builder_with(calls, {'2026-12-26': '2026Q3', '2027-01-02': '2026Q3'})
    quality = env[2]
    (quality / 'fundamentals-now.json').unlink()                 # 12-26: fundamentals did not refresh
    missed = run_week(**{**options(env, calls), 'saturday': '2026-12-26', 'packet_builder': build,
                         'now': lambda: datetime(2026, 12, 26, 9, tzinfo=timezone.utc)})
    assert missed.exit_code == 3 and not missed.keys and not calls
    (quality / 'fundamentals-next.json').write_text(json.dumps({'after': {'as_of': '2027-01-02T07:00:00Z',
        'coverage': {'fundamental_ready': {'ratio': .96}}}}))
    out = run_week(**{**options(env, calls), 'saturday': '2027-01-02', 'packet_builder': build,
                      'now': lambda: datetime(2027, 1, 2, 9, tzinfo=timezone.utc)})
    assert out.exit_code == 0 and len(out.keys) == 4                         # this week: weekly boards only
    assert all(k.as_of == '2027-01-02' and k.board_type == 'weekly' for k in out.keys)
    chain = [BoardKey(**k) for k in out.detail['chain_keys']]
    assert len(chain) == 8 and {k.as_of for k in chain} == {'2026-12-26'}     # the planned date, not 01-02
    finals = [k for k in chain if k.board_type == 'quarter_final']
    assert len(finals) == 4
    assert all(env[1].get_board(k).meta.mode == 'replay'
               and env[1].get_board(k).meta.frozen_at == '2026-12-27T00:00:00Z' for k in finals)
    assert all(env[1].get_board(k).board.frozen_as_of == '2026-12-26' for k in out.keys)
    assert calls == ['2026-12-26', '2027-01-02']                             # replayed once, then this week
