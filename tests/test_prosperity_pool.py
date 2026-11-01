"""M10 prosperity pool: liquidity ranks, pool file contract, live publish hook."""
import pytest

from src.data.market_store import MarketStore
from tests.prosperity_fixtures import seed_db, seed_prices, weekdays

DAYS = weekdays('2026-10-30', 35)
SYMBOLS = [f'S{i:04d}' for i in range(20)]


def market(tmp_path, rows):
    return MarketStore(db_path=seed_prices(seed_db(tmp_path / 'market.db'), rows), read_only=True)


def flat(volume_of):
    return {s: [(d, 10.0, volume_of(i)) for d in DAYS] for i, s in enumerate(SYMBOLS)}


def test_window_ignores_dates_few_members_traded(tmp_path):
    from src.data.prosperity_pool import liquidity_ranks
    rows = flat(lambda i: 1000 * (i + 1))
    rows['S0000'] = rows['S0000'] + [('2026-10-25', 10.0, 5)]          # stray Sunday row
    out = liquidity_ranks(market(tmp_path, rows), SYMBOLS, '2026-10-31')
    assert out.window == tuple(DAYS[-30:]) and out.as_of == '2026-10-31'


def test_rank_by_average_ties_by_symbol_and_old_spike_ignored(tmp_path):
    from src.data.prosperity_pool import liquidity_ranks
    rows = flat(lambda i: 1000 * (i + 1))
    rows['S0003'] = [(d, 10.0, 10**9) for d in DAYS[:5]] + [(d, 10.0, 1) for d in DAYS[5:]]
    rows['S0018'] = [(d, 10.0, 20000) for d in DAYS]                   # ties S0019
    out = liquidity_ranks(market(tmp_path, rows), SYMBOLS, '2026-10-31')
    assert [out.ranks['S0018'], out.ranks['S0019']] == [1, 2]
    assert out.ranks['S0003'] == 20


def test_fewer_than_five_days_is_unranked(tmp_path):
    from src.data.prosperity_pool import liquidity_ranks
    rows = flat(lambda i: 1000)
    rows['S0005'] = [(d, 10.0, 10**9) for d in DAYS[-4:]]
    out = liquidity_ranks(market(tmp_path, rows), SYMBOLS, '2026-10-31')
    assert 'S0005' not in out.ranks and len(out.ranks) == 19


def test_unadjusted_split_keeps_dollar_volume(tmp_path):
    from src.data.prosperity_pool import liquidity_ranks
    rows = flat(lambda i: 1000)
    rows['S0007'] = [(d, 100.0 if d < DAYS[-10] else 10.0, 100 if d < DAYS[-10] else 1000) for d in DAYS]
    out = liquidity_ranks(market(tmp_path, rows), SYMBOLS, '2026-10-31')
    assert out.averages['S0007'] == pytest.approx(10000.0)


@pytest.mark.parametrize('as_of,days,reason', [
    ('2026-10-31', DAYS[-20:], 'liquidity_window_short'),
    ('2026-11-14', DAYS, 'liquidity_prices_stale'),
])
def test_window_fails_closed(tmp_path, as_of, days, reason):
    from src.data.prosperity_pool import liquidity_ranks
    rows = {s: [(d, 10.0, 1000) for d in days] for s in SYMBOLS}
    with pytest.raises(ValueError, match=reason):
        liquidity_ranks(market(tmp_path, rows), SYMBOLS, as_of)


# ---- Task 2: pool file -------------------------------------------------------------
from datetime import datetime, timezone
from dataclasses import replace
import json

NOW = datetime(2026, 10, 31, 9, tzinfo=timezone.utc)


def f1_board(as_of='2026-10-31'):
    from tests.prosperity_fixtures import storage_batch
    return next(b for b in storage_batch(count=20, as_of=as_of) if b.board.scheme_id == 'F1')


def liq(board, illiquid=(), *, as_of=None, window=None):
    from src.data.prosperity_pool import LiquidityResult
    symbols = sorted(board.meta.members)
    ranks = {s: (150 + i if s in illiquid else i + 1) for i, s in enumerate(symbols)}
    return LiquidityResult(as_of or board.board.as_of, tuple(window or weekdays('2026-10-30', 30)),
                           {s: 1e9 / r for s, r in ranks.items()}, ranks)


def eligible(board):
    return [r for r in board.board.rows if r.status == 'ranked' and r.grade in ('STRICT', 'FULL')]


def test_pool_keeps_strict_full_inside_top_100_in_rank_order():
    from src.data.prosperity_pool import build_pool, validate_prosperity_pool
    board = f1_board()
    best = eligible(board)[0].symbol
    pool = build_pool(board, liq(board, illiquid={best}), generated_at='2026-10-31T08:50:00Z')
    expected = [r.symbol for r in eligible(board) if r.symbol != best]
    assert [m['symbol'] for m in pool['members']] == expected
    assert pool['coverage']['eligible'] == len(eligible(board)) and pool['coverage']['selected'] == len(expected)
    validate_prosperity_pool(pool, now=NOW)


def test_zero_eligible_is_a_valid_empty_pool():
    from src.data.prosperity_pool import build_pool, validate_prosperity_pool
    board = f1_board()
    pool = build_pool(board, liq(board, illiquid=set(board.meta.members)), generated_at='2026-10-31T08:50:00Z')
    assert pool['members'] == [] and pool['coverage']['selected'] == 0
    validate_prosperity_pool(pool, now=NOW)


def test_build_rejects_non_f1_board():
    from tests.prosperity_fixtures import storage_batch
    from src.data.prosperity_pool import build_pool
    board = next(b for b in storage_batch(count=20, as_of='2026-10-31') if b.board.scheme_id == 'F0')
    with pytest.raises(ValueError, match='pool_source_invalid'):
        build_pool(board, liq(board), generated_at='2026-10-31T08:50:00Z')


@pytest.mark.parametrize('kwargs', [
    {'as_of': '2026-10-24'},                                         # ranks computed for another board date
    {'window': weekdays('2026-10-23', 30)},                          # last price day 8 days before as_of
    {'window': weekdays('2026-11-02', 30)},                          # window ends after as_of
    {'window': weekdays('2026-10-30', 29)},                          # not a full 30-day window
])
def test_build_rejects_liquidity_not_bound_to_board(kwargs):
    from src.data.prosperity_pool import build_pool
    board = f1_board()
    with pytest.raises(ValueError, match='pool_liquidity_mismatch'):
        build_pool(board, liq(board, **kwargs), generated_at='2026-10-31T08:50:00Z')


def valid_pool():
    from src.data.prosperity_pool import build_pool
    board = f1_board()
    return build_pool(board, liq(board), generated_at='2026-10-31T08:50:00Z')


@pytest.mark.parametrize('mutate,reason', [
    (lambda p: p.update(name='Wrong'), 'prosperity_pool_invalid'),
    (lambda p: p['criteria'].update(scheme_hash='0' * 16), 'prosperity_pool_criteria_mismatch'),
    (lambda p: p['criteria']['liquidity'].update(top=40), 'prosperity_pool_criteria_mismatch'),
    (lambda p: p['members'][0].update(grade='BELOW'), 'prosperity_pool_invalid'),
    (lambda p: p['members'][0].update(dollar_volume_rank=101), 'prosperity_pool_invalid'),
    (lambda p: p['members'].append(dict(p['members'][0])), 'prosperity_pool_invalid'),
    (lambda p: p['members'].reverse(), 'prosperity_pool_invalid'),
    (lambda p: p['coverage'].update(selected=p['coverage']['selected'] + 1), 'prosperity_pool_invalid'),
    (lambda p: p['coverage']['ranked'].update(covered=1, ratio=1 / p['universe']['count']), 'prosperity_pool_invalid'),
    (lambda p: p.update(generated_at='2026-10-22T08:00:00Z'), 'prosperity_pool_stale'),
    (lambda p: p.update(as_of='2026-10-17'), 'prosperity_pool_stale'),
    (lambda p: p['coverage'].update(trading_window=['2026-09-14', '2026-10-23']), 'prosperity_pool_invalid'),
    (lambda p: p['coverage'].update(trading_window=['2026-09-21', '2026-11-02']), 'prosperity_pool_invalid'),
])
def test_loader_rejects(tmp_path, mutate, reason):
    from src.data.prosperity_pool import load_prosperity_pool
    payload = valid_pool()
    assert len(payload['members']) >= 2
    mutate(payload)
    path = tmp_path / 'prosperity_pool.json'
    path.write_text(json.dumps(payload, ensure_ascii=False))
    assert load_prosperity_pool(path, now=NOW)['reason'] == reason


def test_publish_round_trip_and_pre_replace_failure_keeps_old_file(tmp_path, monkeypatch):
    import src.data.prosperity_pool as pool
    from src.data.prosperity_pool import load_prosperity_pool, publish_prosperity_pool
    path = tmp_path / 'pool' / 'prosperity_pool.json'
    assert load_prosperity_pool(path, now=NOW)['reason'] == 'prosperity_pool_missing'
    good = valid_pool()
    publish_prosperity_pool(good, path, now=NOW)
    before = path.read_bytes()
    assert load_prosperity_pool(path, now=NOW)['available'] is True
    with pytest.raises(ValueError):
        publish_prosperity_pool({**good, 'name': 'Wrong'}, path, now=NOW)       # rejected before writing
    def broken_replace(src, dst):
        raise OSError('replace failed')
    monkeypatch.setattr(pool.os, 'replace', broken_replace)
    with pytest.raises(OSError):
        publish_prosperity_pool({**good, 'generated_at': '2026-10-31T08:55:00Z'}, path, now=NOW)
    assert path.read_bytes() == before
    assert sorted(p.name for p in path.parent.iterdir()) == ['prosperity_pool.json']


def test_directory_fsync_failure_leaves_validated_new_file(tmp_path, monkeypatch):
    import src.data.prosperity_pool as pool
    from src.data.prosperity_pool import load_prosperity_pool, publish_prosperity_pool
    path = tmp_path / 'pool' / 'prosperity_pool.json'
    publish_prosperity_pool(valid_pool(), path, now=NOW)
    def broken_fsync(directory):
        raise OSError('fsync failed')
    monkeypatch.setattr(pool, '_fsync_dir', broken_fsync)
    newer = {**valid_pool(), 'generated_at': '2026-10-31T08:55:00Z'}
    with pytest.raises(OSError):
        publish_prosperity_pool(newer, path, now=NOW)
    loaded = load_prosperity_pool(path, now=NOW)
    assert loaded['available'] is True and loaded['generated_at'] == '2026-10-31T08:55:00Z'
    assert sorted(p.name for p in path.parent.iterdir()) == ['prosperity_pool.json']


# ---- Task 3: live publish hook ------------------------------------------------------
from src.data.prosperity_store import ProsperityStore

LIVE_NOW = datetime(2026, 10, 24, 9, tzinfo=timezone.utc)


def builder(market, as_of, **kwargs):
    from tests.prosperity_fixtures import storage_inputs
    packets, _, _, _ = storage_inputs(as_of=as_of, count=20)
    packets = [replace(p, quarter_bucket='2026Q2') for p in packets]
    return packets, {'members_resolved': len(packets), 'errors': {}}


@pytest.fixture
def live(tmp_path):
    quality = tmp_path / 'quality'
    quality.mkdir()
    (quality / 'fundamentals-now.json').write_text(json.dumps({'after': {
        'as_of': '2026-10-24T07:00:00Z', 'coverage': {'fundamental_ready': {'ratio': .96}}}}))
    db = seed_db(tmp_path / 'market.db')
    return tmp_path, db, quality


def run(tmp_path, db, quality, mode='live', saturday='2026-10-24'):
    from src.data.prosperity_pool import pool_publisher
    from src.data.prosperity_run import run_week
    path = tmp_path / 'pool' / 'prosperity_pool.json'
    market = MarketStore(db_path=db, read_only=True)
    try:
        with ProsperityStore(tmp_path / 'result.db', read_only=False) as store:
            out = run_week(saturday=saturday, mode=mode, market=market, store=store, quality_dir=quality,
                           publishers=(pool_publisher(store=store, market=market, path=path, now=lambda: LIVE_NOW),),
                           now=lambda: LIVE_NOW, version='test-m10', start_quarter='2026Q3',
                           packet_builder=builder)
            published = [store.publication_state(k).published_at for k in out.keys]
    finally:
        market.close()
    return out, path, published


def test_live_week_publishes_pool_then_marks(live):
    from src.data.prosperity_pool import load_prosperity_pool
    tmp_path, db, quality = live
    seed_prices(db, {f'S{i:04d}': [(d, 10.0, 1000 * (i + 1)) for d in weekdays('2026-10-23', 35)]
                     for i in range(20)})
    out, path, published = run(tmp_path, db, quality)
    assert out.exit_code == 0 and out.detail['publication'] == 'published'
    assert all(published)
    assert load_prosperity_pool(path, now=LIVE_NOW)['available'] is True


def test_missing_prices_exit_5_then_retry_publishes_only(live):
    tmp_path, db, quality = live
    out, path, published = run(tmp_path, db, quality)
    assert out.exit_code == 5 and 'liquidity_window_short' in out.error
    assert not path.exists() and not any(published)
    seed_prices(db, {f'S{i:04d}': [(d, 10.0, 1000) for d in weekdays('2026-10-23', 35)] for i in range(20)})
    again, path, published = run(tmp_path, db, quality)
    assert again.exit_code == 0 and set(again.keys) == set(out.keys) and all(published) and path.exists()


def test_directory_fsync_failure_exits_5_then_retry_marks(live, monkeypatch):
    import src.data.prosperity_pool as pool
    from src.data.prosperity_pool import load_prosperity_pool
    tmp_path, db, quality = live
    seed_prices(db, {f'S{i:04d}': [(d, 10.0, 1000) for d in weekdays('2026-10-23', 35)] for i in range(20)})
    real = pool._fsync_dir
    def broken(directory):
        raise OSError('fsync failed')
    monkeypatch.setattr(pool, '_fsync_dir', broken)
    out, path, published = run(tmp_path, db, quality)
    assert out.exit_code == 5 and 'fsync failed' in out.error and not any(published)
    assert load_prosperity_pool(path, now=LIVE_NOW)['available'] is True     # validated new file is visible
    monkeypatch.setattr(pool, '_fsync_dir', real)
    again, _, published = run(tmp_path, db, quality)
    assert again.exit_code == 0 and again.detail['publication'] == 'published' and all(published)


def test_mark_published_failure_exits_5_then_retry_marks(live, monkeypatch):
    tmp_path, db, quality = live
    seed_prices(db, {f'S{i:04d}': [(d, 10.0, 1000) for d in weekdays('2026-10-23', 35)] for i in range(20)})
    real = ProsperityStore.mark_published
    def broken(self, keys, *, published_at):
        raise RuntimeError('mark failed')
    monkeypatch.setattr(ProsperityStore, 'mark_published', broken)
    out, path, published = run(tmp_path, db, quality)
    assert out.exit_code == 5 and 'mark failed' in out.error and not any(published) and path.exists()
    monkeypatch.setattr(ProsperityStore, 'mark_published', real)
    again, _, published = run(tmp_path, db, quality)
    assert again.exit_code == 0 and all(published)


def test_replay_week_never_writes_pool(live):
    tmp_path, db, quality = live
    seed_prices(db, {f'S{i:04d}': [(d, 10.0, 1000) for d in weekdays('2026-10-16', 35)] for i in range(20)})
    out, path, _ = run(tmp_path, db, quality, mode='replay', saturday='2026-10-17')
    assert out.exit_code == 0 and not path.exists()
