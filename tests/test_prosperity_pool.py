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
