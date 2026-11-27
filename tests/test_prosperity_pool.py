"""M10 prosperity pool: shared calendar/top-days operators, 90d >=31 pool contract, live publish hook."""
import copy
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
import json

import pytest

from src.data.market_store import MarketStore
from src.data.prosperity_store import ProsperityStore
from tests.prosperity_fixtures import seed_db, seed_prices, weekdays


# ---- Task 1: shared calendar, daily dollar volumes, and top-N day counts ------------
def test_count_top_days_ties_by_symbol_and_skips_missing_day():
    from src.data.prosperity_pool import count_top_days
    vols = {'A': {'d1': 3.0, 'd2': 1.0}, 'B': {'d1': 3.0, 'd2': 2.0}, 'C': {'d2': 5.0}}
    assert count_top_days(vols, ['d1', 'd2'], 1) == {'A': 1, 'B': 0, 'C': 1}
    assert count_top_days(vols, ['d1', 'd2'], 2) == {'A': 1, 'B': 2, 'C': 1}


def test_load_calendar_uses_spy_positive_closes_and_matches_price_book(tmp_path):
    from backtest.research import momentum_board as mb
    from backtest.research import prosperity_health as health
    from src.data import prosperity_pool as pool
    db = seed_prices(seed_db(tmp_path / 'market.db'), {
        'SPY': [('2026-10-20', 400.0, 1000), ('2026-10-21', 0.0, 1000), ('2026-10-22', 402.0, 1000)],
        'AAA': [('2026-10-19', 50.0, 1000), ('2026-10-20', 50.0, 1000), ('2026-10-22', 51.0, 1000)],
    })
    m = MarketStore(db_path=db, read_only=True)
    try:
        cal = pool.load_calendar(m)
        book = health.PriceBook.load(m, ['AAA'])
        dvs = pool.load_daily_dollar_volumes(m, ['AAA'], cal)
    finally:
        m.close()
    assert cal == ['2026-10-20', '2026-10-22']
    assert book.calendar == tuple(cal)
    assert dvs == {'AAA': {'2026-10-20': 50000.0, '2026-10-22': 51000.0}}
    assert mb.load_daily_dollar_volumes is pool.load_daily_dollar_volumes
    assert mb.TOP_VOLUME_RANK == pool.LIQUIDITY_TOP == 100
    assert health.CALENDAR_SYMBOL == pool.CALENDAR_SYMBOL == 'SPY'


# ---- Task 2: 90-day >=31 top-100 liquidity & subset invariant -----------------------
DAYS = weekdays('2026-10-30', 110)
SYMBOLS = [f'S{i:04d}' for i in range(20)]


def market(tmp_path, rows):
    full = {'SPY': [(d, 400.0, 10**7) for d in DAYS], **rows}
    return MarketStore(db_path=seed_prices(seed_db(tmp_path / 'market.db'), full), read_only=True)


def test_liquidity_top_days_counts_window_daily_min_and_unpriced_members(tmp_path, monkeypatch):
    from src.data import prosperity_pool as pool
    monkeypatch.setattr(pool, 'LIQUIDITY_TOP', 2)
    win = DAYS[-90:]
    rows = {s: [(d, 10.0, 100 + i) for d in DAYS] for i, s in enumerate(SYMBOLS)}
    rows['S0000'] = [(d, 10.0, 10**9) for d in DAYS]                                     # #1 on all 90 days
    rows['S0001'] = ([(d, 10.0, 10) for d in DAYS[:-90]]
                     + [(d, 10.0, 5 * 10**8) for d in win[:31]]
                     + [(d, 10.0, 10) for d in win[31:]])                                 # #2 on 31 days
    rows['S0002'] = ([(d, 10.0, 10) for d in DAYS[:-90]]
                     + [(d, 10.0, 10) for d in win[:31]]
                     + [(d, 10.0, 5 * 10**8) for d in win[31:]])                          # #2 on 59 days
    # On win[10], drop 4 non-top members (zero/negative/missing); S0019 has no prices at all -> daily_min = 15
    rows.pop('S0019')
    rows['S0015'] = [(d, 0.0 if d == win[10] else 10.0, 100) for d in DAYS]
    rows['S0016'] = [(d, 10.0, -5 if d == win[10] else 100) for d in DAYS]
    rows['S0017'] = [(d, 10.0, 100) for d in DAYS if d != win[10]]
    rows['S0018'] = [(d, 10.0, 100) for d in DAYS if d != win[10]]
    rows['S0000'] = rows['S0000'] + [('2026-10-25', 10.0, 10**9)]                         # stray Sunday ignored (not on SPY)
    m = market(tmp_path, rows)
    try:
        out = pool.liquidity_top_days(m, SYMBOLS, '2026-10-31')
    finally:
        m.close()
    assert out.as_of == '2026-10-31' and out.window == tuple(win) and len(out.window) == 90
    assert out.top_days['S0000'] == 90
    assert out.top_days['S0001'] == 31
    assert out.top_days['S0002'] == 59
    assert out.top_days['S0019'] == 0
    assert all(out.top_days[s] == 0 for s in SYMBOLS[3:])
    assert set(out.top_days) == set(SYMBOLS)
    assert out.daily_min == 15


def test_unadjusted_split_keeps_daily_dollar_volume_top_days(tmp_path, monkeypatch):
    from src.data import prosperity_pool as pool
    monkeypatch.setattr(pool, 'LIQUIDITY_TOP', 1)
    rows = {s: [(d, 10.0, 500) for d in DAYS] for s in SYMBOLS}
    # S0007 splits 10:1 (100*100 == 10*1000 == 10000 > 5000 on every day)
    rows['S0007'] = [(d, 100.0 if d < DAYS[-20] else 10.0, 100 if d < DAYS[-20] else 1000) for d in DAYS]
    m = market(tmp_path, rows)
    try:
        out = pool.liquidity_top_days(m, SYMBOLS, '2026-10-31')
    finally:
        m.close()
    assert out.top_days['S0007'] == 90


@pytest.mark.parametrize('as_of,spy_days,symbols,reason', [
    ('2026-10-31', DAYS[-89:], SYMBOLS, 'liquidity_window_short'),
    ('2026-11-14', DAYS, SYMBOLS, 'liquidity_prices_stale'),
    ('2026-10-31', DAYS, [], 'liquidity_no_members'),
])
def test_liquidity_window_fails_closed(tmp_path, as_of, spy_days, symbols, reason):
    from src.data.prosperity_pool import liquidity_top_days
    rows = {'SPY': [(d, 400.0, 10**7) for d in spy_days],
            **{s: [(d, 10.0, 1000) for d in spy_days] for s in SYMBOLS}}
    m = MarketStore(db_path=seed_prices(seed_db(tmp_path / 'market.db'), rows), read_only=True)
    try:
        with pytest.raises(ValueError, match=reason):
            liquidity_top_days(m, symbols, as_of)
    finally:
        m.close()


def test_u70_is_strict_subset_of_pool_liquidity(tmp_path, monkeypatch):
    """End-to-end subset invariant: U70 (>=36/70) is guaranteed inside pool liquidity (>=31/90)."""
    import bisect
    from backtest.research import momentum_board as mb
    from backtest.research.prosperity_health import PriceBook
    from src.data import prosperity_pool as pool

    assert pool.LIQUIDITY_DAYS >= max(mb.WINDOWS.values()) == 70
    assert pool.LIQUIDITY_MIN_TOP_DAYS <= mb.min_top_volume_days(70) == 36
    assert mb.MAX_LAG_DAYS <= pool.LIQUIDITY_MAX_LAG_DAYS

    monkeypatch.setattr(pool, 'LIQUIDITY_TOP', 5)
    monkeypatch.setattr(mb, 'TOP_VOLUME_RANK', 5)

    cal = weekdays('2026-10-30', 100)
    win90 = cal[-90:]
    first20, last70 = set(win90[:20]), win90[20:]
    early_70 = set(last70[:11])
    syms = [f'M{i:02d}' for i in range(30)]
    rows = {'SPY': [(d, 400.0 + k * 0.1, 10**7) for k, d in enumerate(cal)]}
    for i, s in enumerate(syms):
        pts = []
        for k, d in enumerate(cal):
            close = 20.0 + k * 0.1
            if i < 4:
                vol = 10**7 - i * 10**5                           # M00..M03: always top 4 (90/90, 70/70)
            elif s == 'M04':
                # 20/20 in first20 + 11/70 in last70 = 31/90 (in pool) but 11/70 (NOT in U70)
                vol = 5 * 10**6 if (d in first20 or d in early_70) else 100
            elif s == 'M05':
                # 0/20 in first20 + 59/70 in last70 = 59/90 (in pool) and 59/70 (in U70)
                vol = 5 * 10**6 if (d not in first20 and d not in early_70) else 100
            else:
                vol = 1000 + i
            pts.append((d, close, vol))
        rows[s] = pts
    # Make M03 unscorable in momentum (missing one day's volume in last70) while still >=36/70 & >=31/90
    rows['M03'] = [p for p in rows['M03'] if p[0] != last70[-1]]

    db = seed_prices(seed_db(tmp_path / 'market.db'), rows)
    m = MarketStore(db_path=db, read_only=True)
    try:
        res = pool.liquidity_top_days(m, syms, '2026-10-31')
        book = PriceBook.load(m, syms)
        end_i = bisect.bisect_right(book.calendar, '2026-10-31') - 1
        payload = mb.build_payload(
            book=book,
            daily_volumes=lambda ds: pool.load_daily_dollar_volumes(m, syms, ds),
            symbols=syms, as_of='2026-10-31', generated_at='2026-10-31T09:00:00Z',
        )
    finally:
        m.close()

    board = payload['boards']['14w']
    assert list(book.calendar[end_i - 69:end_i + 1]) == list(res.window[-70:])
    assert board['window'][1] == res.window[-1]
    mb_days = {r['symbol']: r['top100_days'] for r in board['scored']}
    mb_days.update(board['unavailable_top100_days'])
    u70_set = {s for s, c in mb_days.items() if c >= mb.min_top_volume_days(70)}
    pool_set = {s for s, c in res.top_days.items() if c >= pool.LIQUIDITY_MIN_TOP_DAYS}
    assert u70_set == {'M00', 'M01', 'M02', 'M03', 'M05'}
    assert pool_set == {'M00', 'M01', 'M02', 'M03', 'M04', 'M05'}
    assert u70_set < pool_set and 'M04' in (pool_set - u70_set)


# ---- Pool file contract & validator -------------------------------------------------
NOW = datetime(2026, 10, 31, 9, 30, tzinfo=timezone.utc)


def f1_board(as_of='2026-10-31'):
    from tests.prosperity_fixtures import storage_batch
    return next(b for b in storage_batch(count=20, as_of=as_of) if b.board.scheme_id == 'F1')


def liq(board, illiquid=(), *, as_of=None, window=None, daily_min=None, days_override=None):
    from src.data.prosperity_pool import LiquidityResult
    symbols = sorted(board.meta.members)
    top_days = {s: (30 if s in illiquid else 90 - i) for i, s in enumerate(symbols)}
    if days_override:
        top_days.update(days_override)
    return LiquidityResult(
        as_of or board.board.as_of,
        tuple(window or weekdays('2026-10-30', 90)),
        top_days,
        len(symbols) if daily_min is None else daily_min,
    )


def eligible(board):
    return [r for r in board.board.rows if r.status == 'ranked' and r.grade in ('STRICT', 'FULL')]


def first_tier(board):
    from src.data.prosperity_pool import tier_cut
    cut = tier_cut(sum(r.status == 'ranked' for r in board.board.rows))
    return [r for r in eligible(board) if r.rank <= cut]


def test_criteria_payload_and_pool_selection_boundary():
    from src.data.prosperity_pool import POOL_SCHEMA_VERSION, build_pool, criteria_payload, validate_prosperity_pool
    assert POOL_SCHEMA_VERSION == 1
    assert criteria_payload()['liquidity'] == {
        'metric': 'top_dollar_volume_days', 'calendar': 'SPY',
        'trading_days': 90, 'min_top_days': 31, 'top': 100,
    }
    board = f1_board()
    tier = first_tier(board)
    assert 0 < len(tier) < len(eligible(board))                 # fixture: 4 of 9 STRICT/FULL in first tier
    out_30, in_31 = tier[0].symbol, tier[1].symbol
    pool = build_pool(board, liq(board, days_override={out_30: 30, in_31: 31}, daily_min=19),
                      generated_at='2026-10-31T09:10:00Z')
    expected = [r.symbol for r in tier if r.symbol != out_30]
    assert [m['symbol'] for m in pool['members']] == expected
    by_sym = {m['symbol']: m for m in pool['members']}
    assert by_sym[in_31]['top100_days'] == 31
    assert all('top100_days' in m and 'dollar_volume_rank' not in m and 'avg_dollar_volume' not in m
               for m in pool['members'])
    assert set(pool['coverage']) == {'ranked', 'liquidity_daily', 'tier_cut', 'eligible', 'selected', 'trading_window'}
    assert pool['coverage']['liquidity_daily']['covered'] == 19
    assert pool['coverage']['eligible'] == len(tier) and pool['coverage']['selected'] == len(expected)
    assert pool['coverage']['tier_cut'] == 4
    validate_prosperity_pool(pool, now=NOW)


def test_liquid_strict_full_outside_first_tier_never_enter():
    from src.data.prosperity_pool import build_pool, validate_prosperity_pool
    board = f1_board()
    tier = {r.symbol for r in first_tier(board)}
    outside = {r.symbol for r in eligible(board)} - tier
    assert outside
    pool = build_pool(board, liq(board, illiquid=tier), generated_at='2026-10-31T09:10:00Z')
    assert pool['members'] == [] and pool['coverage']['eligible'] == len(tier)
    validate_prosperity_pool(pool, now=NOW)


@pytest.mark.parametrize('n,cut', [(1, 1), (4, 1), (5, 1), (6, 2), (18, 4), (848, 170), (871, 175)])
def test_tier_cut_matches_health_report_first_tier(n, cut):
    from src.data.prosperity_pool import tier_cut
    assert tier_cut(n) == cut


def test_tier_cut_equals_health_report_tier_zero_count():
    pytest.importorskip('scipy')
    from backtest.research.prosperity_health import TIERS, assign_tiers
    from src.data.prosperity_pool import POOL_TIERS, tier_cut
    assert POOL_TIERS == TIERS
    for n in range(1, 1001):
        tiers = assign_tiers([str(i) for i in range(n)])
        assert tier_cut(n) == sum(t == 0 for t in tiers.values()), n


def test_zero_eligible_is_a_valid_empty_pool():
    from src.data.prosperity_pool import build_pool, validate_prosperity_pool
    board = f1_board()
    pool = build_pool(board, liq(board, illiquid=set(board.meta.members)), generated_at='2026-10-31T09:10:00Z')
    assert pool['members'] == [] and pool['coverage']['selected'] == 0
    validate_prosperity_pool(pool, now=NOW)


def test_build_rejects_non_f1_board():
    from tests.prosperity_fixtures import storage_batch
    from src.data.prosperity_pool import build_pool
    board = next(b for b in storage_batch(count=20, as_of='2026-10-31') if b.board.scheme_id == 'F0')
    with pytest.raises(ValueError, match='pool_source_invalid'):
        build_pool(board, liq(board), generated_at='2026-10-31T09:10:00Z')


@pytest.mark.parametrize('kwargs', [
    {'as_of': '2026-10-24'},                                         # computed for another board date
    {'window': weekdays('2026-10-23', 90)},                          # last price day 8 days before as_of
    {'window': weekdays('2026-11-02', 90)},                          # window ends after as_of
    {'window': weekdays('2026-10-30', 89)},                          # not a full 90-day window
    {'window': list(reversed(weekdays('2026-10-30', 90)))},          # descending window
    {'days_override': {'OUTSIDER': 50}},                             # non-member in top_days
    {'daily_min': 21},                                               # daily_min > member count
])
def test_build_rejects_liquidity_not_bound_to_board(kwargs):
    from src.data.prosperity_pool import build_pool
    board = f1_board()
    with pytest.raises(ValueError, match='pool_liquidity_mismatch'):
        build_pool(board, liq(board, **kwargs), generated_at='2026-10-31T09:10:00Z')


def valid_pool():
    from src.data.prosperity_pool import build_pool
    board = f1_board()
    return build_pool(board, liq(board), generated_at='2026-10-31T09:10:00Z')


def test_old_rule_pool_file_triggers_criteria_mismatch(tmp_path):
    from src.data.prosperity_pool import load_prosperity_pool
    old = valid_pool()
    old['criteria']['liquidity'] = {'metric': 'avg_dollar_volume', 'trading_days': 30, 'min_days': 5, 'top': 100}
    old['coverage']['liquidity_ranked'] = old['coverage'].pop('liquidity_daily')
    for i, m in enumerate(old['members'], 1):
        m.pop('top100_days', None)
        m['dollar_volume_rank'] = i
        m['avg_dollar_volume'] = 1e9
    path = tmp_path / 'old_pool.json'
    path.write_text(json.dumps(old, ensure_ascii=False))
    assert load_prosperity_pool(path, now=NOW) == {
        'available': False, 'reason': 'prosperity_pool_criteria_mismatch', 'members': []
    }


def test_trading_window_span_bound_allows_159_and_rejects_160():
    from src.data.prosperity_pool import validate_prosperity_pool
    as_of = date(2026, 10, 31)
    ok = valid_pool()
    ok['coverage']['trading_window'] = [(as_of - timedelta(days=159)).isoformat(), '2026-10-30']
    validate_prosperity_pool(ok, now=NOW)
    too_wide = valid_pool()
    too_wide['coverage']['trading_window'] = [(as_of - timedelta(days=160)).isoformat(), '2026-10-30']
    with pytest.raises(ValueError, match='pool trading window not bound to as_of'):
        validate_prosperity_pool(too_wide, now=NOW)


@pytest.mark.parametrize('mutate,reason', [
    (lambda p: p.update(name='Wrong'), 'prosperity_pool_invalid'),
    (lambda p: p['criteria'].update(scheme_hash='0' * 16), 'prosperity_pool_criteria_mismatch'),
    (lambda p: p['criteria']['liquidity'].update(min_top_days=30), 'prosperity_pool_criteria_mismatch'),
    (lambda p: p['criteria']['liquidity'].update(top=40), 'prosperity_pool_criteria_mismatch'),
    (lambda p: p['members'][0].update(grade='BELOW'), 'prosperity_pool_invalid'),
    (lambda p: p['members'][0].update(top100_days=30), 'prosperity_pool_invalid'),
    (lambda p: p['members'][0].update(top100_days=91), 'prosperity_pool_invalid'),
    (lambda p: p['members'][0].update(top100_days=31.0), 'prosperity_pool_invalid'),
    (lambda p: p['members'][0].update(top100_days=True), 'prosperity_pool_invalid'),
    (lambda p: p['members'][0].pop('top100_days'), 'prosperity_pool_invalid'),
    (lambda p: p['coverage'].pop('liquidity_daily'), 'prosperity_pool_invalid'),
    (lambda p: p['members'].append(dict(p['members'][0])), 'prosperity_pool_invalid'),
    (lambda p: p['members'].reverse(), 'prosperity_pool_invalid'),
    (lambda p: p['coverage'].update(selected=p['coverage']['selected'] + 1), 'prosperity_pool_invalid'),
    (lambda p: p['coverage']['ranked'].update(covered=1, ratio=1 / p['universe']['count']), 'prosperity_pool_invalid'),
    (lambda p: p.update(generated_at='2026-10-22T08:00:00Z'), 'prosperity_pool_stale'),
    (lambda p: p.update(as_of='2026-10-17'), 'prosperity_pool_stale'),
    (lambda p: p['coverage'].update(trading_window=['2026-06-15', '2026-10-23']), 'prosperity_pool_invalid'),
    (lambda p: p['coverage'].update(trading_window=['2026-06-15', '2026-11-02']), 'prosperity_pool_invalid'),
    (lambda p: p['criteria']['tier'].update(tiers=4), 'prosperity_pool_criteria_mismatch'),
    (lambda p: p['coverage'].update(tier_cut=p['coverage']['tier_cut'] + 1), 'prosperity_pool_invalid'),
    (lambda p: p['coverage'].update(eligible=p['coverage']['tier_cut'] + 1), 'prosperity_pool_invalid'),
    (lambda p: p['members'][-1].update(rank=p['coverage']['tier_cut'] + 1), 'prosperity_pool_invalid'),
])
def test_loader_rejects(tmp_path, mutate, reason):
    from src.data.prosperity_pool import load_prosperity_pool
    payload = valid_pool()
    assert len(payload['members']) >= 2
    mutate(payload)
    path = tmp_path / 'prosperity_pool.json'
    path.write_text(json.dumps(payload, ensure_ascii=False))
    assert load_prosperity_pool(path, now=NOW)['reason'] == reason


def test_board_mismatch_catches_what_the_file_alone_cannot():
    from src.data.prosperity_pool import board_mismatch, build_pool, validate_prosperity_pool
    board = f1_board()
    pool = valid_pool()
    assert board_mismatch(pool, board) is None

    bad = copy.deepcopy(pool)
    bad['as_of'] = '2026-10-24'
    assert board_mismatch(bad, board) == 'as_of'

    bad = copy.deepcopy(pool)
    bad['source']['content_hash'] = '0' * 64
    assert board_mismatch(bad, board) == 'content_hash'

    bad = copy.deepcopy(pool)
    stat = bad['coverage']['ranked']
    stat.update(covered=stat['total'], ratio=1.0)
    validate_prosperity_pool(bad, now=NOW)
    assert board_mismatch(bad, board) == 'ranked'

    bad = copy.deepcopy(pool)
    bad['universe']['symbols_sha256'] = '0' * 64
    assert board_mismatch(bad, board) == 'universe'

    bad = copy.deepcopy(pool)
    bad['coverage']['tier_cut'] = 5
    assert board_mismatch(bad, board) == 'tier_cut'

    thin = build_pool(board, liq(board, illiquid={first_tier(board)[0].symbol}),
                      generated_at='2026-10-31T09:10:00Z')
    bad = copy.deepcopy(thin)
    bad['coverage']['eligible'] = 3
    validate_prosperity_pool(bad, now=NOW)
    assert board_mismatch(bad, board) == 'eligible'


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
        publish_prosperity_pool({**good, 'name': 'Wrong'}, path, now=NOW)
    def broken_replace(src, dst):
        raise OSError('replace failed')
    monkeypatch.setattr(pool.os, 'replace', broken_replace)
    with pytest.raises(OSError):
        publish_prosperity_pool({**good, 'generated_at': '2026-10-31T09:15:00Z'}, path, now=NOW)
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
    newer = {**valid_pool(), 'generated_at': '2026-10-31T09:15:00Z'}
    with pytest.raises(OSError):
        publish_prosperity_pool(newer, path, now=NOW)
    loaded = load_prosperity_pool(path, now=NOW)
    assert loaded['available'] is True and loaded['generated_at'] == '2026-10-31T09:15:00Z'
    assert sorted(p.name for p in path.parent.iterdir()) == ['prosperity_pool.json']


# ---- Task 3 (M10): live publish hook ------------------------------------------------
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


def seed_live_prices(db, end_day='2026-10-23', n_days=100, symbol_range=range(20)):
    days = weekdays(end_day, n_days)
    rows = {'SPY': [(d, 400.0, 10**7) for d in days]}
    rows.update({f'S{i:04d}': [(d, 10.0, 1000 * (i + 1)) for d in days] for i in symbol_range})
    seed_prices(db, rows)


def run(tmp_path, db, quality, mode='live', saturday='2026-10-24'):
    from src.data.prosperity_pool import pool_publisher
    from src.data.prosperity_run import run_week
    path = tmp_path / 'pool' / 'prosperity_pool.json'
    m = MarketStore(db_path=db, read_only=True)
    try:
        with ProsperityStore(tmp_path / 'result.db', read_only=False) as store:
            out = run_week(saturday=saturday, mode=mode, market=m, store=store, quality_dir=quality,
                           publishers=(pool_publisher(store=store, market=m, path=path, now=lambda: LIVE_NOW),),
                           now=lambda: LIVE_NOW, version='test-m10', start_quarter='2026Q3',
                           packet_builder=builder)
            published = [store.publication_state(k).published_at for k in out.keys]
    finally:
        m.close()
    return out, path, published


def test_live_week_publishes_pool_then_marks(live):
    from src.data.prosperity_pool import load_prosperity_pool
    tmp_path, db, quality = live
    seed_live_prices(db)
    out, path, published = run(tmp_path, db, quality)
    assert out.exit_code == 0 and out.detail['publication'] == 'published'
    assert all(published)
    assert load_prosperity_pool(path, now=LIVE_NOW)['available'] is True


def test_missing_prices_exit_5_then_retry_publishes_only(live):
    tmp_path, db, quality = live
    out, path, published = run(tmp_path, db, quality)
    assert out.exit_code == 5 and 'liquidity_window_short' in out.error
    assert not path.exists() and not any(published)
    seed_live_prices(db)
    again, path, published = run(tmp_path, db, quality)
    assert again.exit_code == 0 and set(again.keys) == set(out.keys) and all(published) and path.exists()


def test_directory_fsync_failure_exits_5_then_retry_marks(live, monkeypatch):
    import src.data.prosperity_pool as pool
    from src.data.prosperity_pool import load_prosperity_pool
    tmp_path, db, quality = live
    seed_live_prices(db)
    real = pool._fsync_dir
    def broken(directory):
        raise OSError('fsync failed')
    monkeypatch.setattr(pool, '_fsync_dir', broken)
    out, path, published = run(tmp_path, db, quality)
    assert out.exit_code == 5 and 'fsync failed' in out.error and not any(published)
    assert load_prosperity_pool(path, now=LIVE_NOW)['available'] is True
    monkeypatch.setattr(pool, '_fsync_dir', real)
    again, _, published = run(tmp_path, db, quality)
    assert again.exit_code == 0 and again.detail['publication'] == 'published' and all(published)


def test_mark_published_failure_exits_5_then_retry_marks(live, monkeypatch):
    tmp_path, db, quality = live
    seed_live_prices(db)
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
    seed_live_prices(db, end_day='2026-10-16')
    out, path, _ = run(tmp_path, db, quality, mode='replay', saturday='2026-10-17')
    assert out.exit_code == 0 and not path.exists()


def huge_score():
    payload = valid_pool()
    payload['members'][0]['score'] = 10**400
    return json.dumps(payload)


@pytest.mark.parametrize('raw', [
    huge_score,
    lambda: json.dumps({**valid_pool(), 'generated_at': '9999-12-31T23:59:59-01:00'}),
    lambda: '[' * 100000 + ']' * 100000,
])
def test_loader_fails_closed_on_overflow_and_recursion(tmp_path, raw):
    from src.data.prosperity_pool import load_prosperity_pool
    path = tmp_path / 'prosperity_pool.json'
    path.write_text(raw())
    assert load_prosperity_pool(path, now=NOW)['reason'] == 'prosperity_pool_invalid'


@pytest.mark.parametrize('mutate', [
    lambda p: p.pop('source'),
    lambda p: p['source'].update(board_type='quarter_final'),
    lambda p: p['source'].update(code_version=''),
    lambda p: p['source'].update(content_hash='xyz'),
    lambda p: p['source'].update(computed_at='2026-10-31T09:00:00'),
    lambda p: p['members'][-1].update(rank=p['coverage']['ranked']['covered'] + 1),
    lambda p: p['members'][0].update(name=7),
    lambda p: p['members'][0]['family_scores'].update(accel='high'),
    lambda p: p['coverage'].update(trading_window=['20260701', '20261030']),
])
def test_validator_checks_source_members_and_strict_dates(tmp_path, mutate):
    from src.data.prosperity_pool import load_prosperity_pool
    payload = valid_pool()
    mutate(payload)
    path = tmp_path / 'prosperity_pool.json'
    path.write_text(json.dumps(payload, ensure_ascii=False))
    assert load_prosperity_pool(path, now=NOW)['reason'] == 'prosperity_pool_invalid'


@pytest.mark.parametrize('key,reason', [
    ('ranked', 'pool_ranked_coverage_below_floor'),
    ('liquidity_daily', 'pool_liquidity_coverage_below_floor'),
])
def test_coverage_floor_names_which_side_failed(key, reason):
    from src.data.prosperity_pool import validate_prosperity_pool
    payload = valid_pool()
    stat = payload['coverage'][key]
    stat.update(covered=stat['total'] - 3, ratio=(stat['total'] - 3) / stat['total'])
    with pytest.raises(ValueError, match=reason):
        validate_prosperity_pool(payload, now=NOW)


def test_liquidity_coverage_failure_recovers_after_prices_arrive(live):
    tmp_path, db, quality = live
    seed_live_prices(db, symbol_range=range(17))
    out, path, published = run(tmp_path, db, quality)
    assert out.exit_code == 5 and 'pool_liquidity_coverage_below_floor' in out.error and not path.exists()
    seed_live_prices(db, symbol_range=range(17, 20))
    again, path, published = run(tmp_path, db, quality)
    assert again.exit_code == 0 and set(again.keys) == set(out.keys) and all(published) and path.exists()
