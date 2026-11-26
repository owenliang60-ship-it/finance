"""M16 momentum board: U70 + UDV long-only scoring over daily windows (Boss 2026-11-26), payload, CLI."""
import json

import numpy as np
import pytest

from backtest.research import momentum_board as mb
from scripts.crypto_trend_rankings import V4_WEIGHTS

L = 70                                                              # the 14-week window: L days, L + 1 closes


def _path(start, end, n=L + 1):
    return np.linspace(start, end, n)


def _flat(v=5e8, n=L):
    return [v] * n


def _longs(rows):
    return sorted((r for r in rows if r['eligible']), key=lambda r: r['rank'])


def _score(closes, vols=None, **kw):
    return mb.score_window(closes, vols or {s: _flat() for s in closes}, **kw)


def _zigzag():
    """Up 4 on even steps, down 2 on odd steps; the last step is flat (no up, no down)."""
    closes = [100.0 + k + (3 if k % 2 else 0) for k in range(L + 1)]
    closes[L] = closes[L - 1]
    return np.array(closes)


def test_constants_are_the_boss_2026_11_26_rule():
    assert mb.SCORING == 'udv1' and '_' not in mb.SCORING          # Telegram legacy Markdown: no bare "_"
    assert mb.SCHEMA_VERSION == 1 and mb.WINDOWS == {'14w': 70} and mb.MAX_LAG_DAYS == 3
    assert mb.TOP_VOLUME_RANK == 100 and mb.LONG_DRAWDOWN_LIMIT == 0.30
    assert mb.UDV_WEIGHTS == {'return': 0.45, 'er': 0.125, 'r_squared': 0.125, 'drawdown': 0.05, 'udv': 0.25}
    v4 = [V4_WEIGHTS[k] for k in ('absolute_return', 'er', 'r_squared', 'drawdown', 'quote_volume')]
    assert list(mb.UDV_WEIGHTS.values()) == v4                     # the V4 ratio; the turnover slot is now UDV
    assert [mb.min_top_volume_days(n) for n in (70, 71, 2)] == [36, 36, 2]     # a strict majority of days


def test_score_window_ranks_longs_only_and_zeroes_the_rest():
    closes = {'UP': _path(100, 140), 'UP2': _path(100, 110), 'DOWN': _path(100, 60), 'FLAT': np.full(L + 1, 50.0)}
    rows = _score(closes)
    assert [r['symbol'] for r in rows] == ['UP', 'UP2', 'DOWN', 'FLAT']         # eligible first, then by symbol
    top = _longs(rows)
    assert [r['symbol'] for r in top] == ['UP', 'UP2'] and [r['rank'] for r in top] == [1, 2]
    assert 100.0 >= top[0]['score'] > top[1]['score'] > 0 and top[0]['return'] == pytest.approx(0.40)
    assert all(r['rank'] is None and r['score'] == 0.0 and r['eligible'] is False for r in rows[2:])
    assert set(rows[0]) == {'symbol', 'score', 'return', 'er', 'r_squared', 'slope', 'drawdown',
                            'dollar_volume', 'udv', 'top100_days', 'in_u70', 'eligible', 'rank'}
    assert all(r['top100_days'] == L and r['in_u70'] is True for r in rows)     # < 100 names: all in every top


def test_percentiles_are_taken_inside_the_eligible_set_only():
    base = {'UP': _path(100, 140), 'UP2': _path(100, 110)}
    alone = {r['symbol']: r['score'] for r in _score(base)}
    crowded = {r['symbol']: r['score'] for r in _score({**base, 'DOWN': _path(100, 30), 'DOWN2': _path(100, 95)})}
    assert crowded['UP'] == alone['UP'] and crowded['UP2'] == alone['UP2']      # decliners are not inputs
    assert crowded['DOWN'] == crowded['DOWN2'] == 0.0


def test_drawdown_gate_and_tie_order():
    peak = np.concatenate([_path(100, 300, 60), _path(290, 150, 11)])           # +50% but 50% off the high
    rows = _score({'B': _path(100, 120), 'A': _path(100, 120), 'PEAK': peak})
    assert [r['symbol'] for r in _longs(rows)] == ['A', 'B'] and rows[0]['score'] == rows[1]['score']
    assert rows[2]['symbol'] == 'PEAK' and rows[2]['eligible'] is False and rows[2]['return'] > 0
    assert rows[2]['drawdown'] > mb.LONG_DRAWDOWN_LIMIT


def test_udv_is_median_up_day_over_median_down_day_dollar_volume():
    dvs = [6e8 if k % 2 == 0 else 2e8 for k in range(L)]
    dvs[0] = 1e12                                                   # median, not mean
    dvs[L - 1] = 5e12                                               # the flat last step counts on neither side
    row = _score({'X': _zigzag()}, {'X': dvs})[0]
    assert row['udv'] == pytest.approx(3.0) and row['dollar_volume'] == pytest.approx(sum(dvs) / L)
    assert row['eligible'] and row['rank'] == 1


def test_udv_without_a_priced_down_day_is_none_and_tops_the_udv_percentile():
    zero_down = [5e8 if k % 2 == 0 else 0.0 for k in range(L)]      # every down day traded zero dollars
    finite = [1e12 if k % 2 == 0 else 1.0 for k in range(L)]
    rows = {r['symbol']: r for r in _score({'P': _zigzag(), 'Q': _zigzag()}, {'P': zero_down, 'Q': finite})}
    assert rows['P']['udv'] is None and rows['Q']['udv'] == pytest.approx(1e12)
    # identical closes tie on the other four dimensions; None ranks above any finite UDV
    assert rows['P']['score'] == 100.0 and rows['Q']['score'] == 100.0 - 25 * 0.5
    mono = _score({'M': _path(100, 120)})[0]
    assert mono['udv'] is None and mono['eligible'] is True        # no down day at all
    json.dumps(list(rows.values()), allow_nan=False)                # never Infinity in the JSON


def test_u70_needs_a_strict_majority_of_days_in_the_daily_top(monkeypatch):
    monkeypatch.setattr(mb, 'TOP_VOLUME_RANK', 1)
    closes = {'A': _path(100, 120), 'B': _path(100, 130)}

    def rows(a_days, a_low=0.5):
        vols = {'A': [2.0] * a_days + [a_low] * (L - a_days), 'B': _flat(1.0)}
        return {r['symbol']: r for r in mb.score_window(closes, vols)}
    r36 = rows(36)
    assert [(r36[s]['top100_days'], r36[s]['in_u70']) for s in 'AB'] == [(36, True), (34, False)]
    assert r36['B']['eligible'] is False and r36['B']['rank'] is None and r36['B']['score'] == 0.0
    assert r36['A']['rank'] == 1
    r34 = rows(34)                                                  # 34 / 36: B now holds the majority
    assert (r34['A']['top100_days'], r34['A']['in_u70'], r34['B']['in_u70']) == (34, False, True)
    tie = rows(0, a_low=1.0)                                        # equal dollar volume: the symbol breaks the tie
    assert (tie['A']['top100_days'], tie['B']['top100_days']) == (L, 0)


def test_the_daily_top_is_one_hundred_names():
    names = [f'S{i:03d}' for i in range(101)]
    vols = {s: _flat(1e9 - i) for i, s in enumerate(names)}        # S100 trades least on every day
    rows = {r['symbol']: r for r in mb.score_window({s: _path(100, 120) for s in names}, vols)}
    assert rows['S099']['top100_days'] == L and rows['S100']['top100_days'] == 0
    assert rows['S100']['in_u70'] is False and sum(r['eligible'] for r in rows.values()) == 100


def test_explicit_top100_days_override_the_local_count():
    row = mb.score_window({'A': _path(100, 120)}, {'A': _flat()}, top100_days={'A': 35})[0]
    assert (row['top100_days'], row['in_u70'], row['eligible'], row['rank'], row['score']) == (35, False, False, None, 0.0)


@pytest.mark.parametrize('vols', [_flat(n=L - 1), [float('nan')] + _flat(n=L - 1), [-1.0] + _flat(n=L - 1)],
                         ids=['short', 'nan', 'negative'])
def test_score_window_refuses_bad_volume_input(vols):
    with pytest.raises(ValueError):
        mb.score_window({'A': _path(100, 120)}, {'A': vols})


def _cut_board(u, e, unavailable_days=35):
    return {'days': L, 'scored': [{'in_u70': i < u, 'eligible': i < e} for i in range(60)],
            'unavailable': {'Z': 'price_gap'}, 'unavailable_top100_days': {'Z': unavailable_days}}


@pytest.mark.parametrize('u, e, cut', [(38, 36, 8), (40, 40, 8), (50, 5, 3), (10, 3, 2), (12, 11, 3),
                                       (1, 1, 1), (20, 0, 0), (0, 0, 0)])
def test_board_top_cut_is_min_of_a_fifth_of_u_and_half_of_e(u, e, cut):
    assert mb.board_top_cut(_cut_board(u, e)) == cut               # Z: 35 of 70 days, not in U70


def test_u70_counts_unavailable_members_from_their_days():
    """Review P1: U70 is over the whole universe; an unscorable member on 36+ days counts (north star §4.4)."""
    assert mb.board_u70_count(_cut_board(10, 10)) == 10 and mb.board_top_cut(_cut_board(10, 10)) == 2
    assert mb.board_u70_count(_cut_board(10, 10, 36)) == 11 and mb.board_top_cut(_cut_board(10, 10, 36)) == 3


# --- payload ---------------------------------------------------------------------------------

from backtest.research.prosperity_health import PriceBook  # noqa: E402
from tests.prosperity_fixtures import seed_db, seed_market, seed_prices, storage_batch, weekdays  # noqa: E402

CAL = weekdays('2026-11-06', 80)
SYMS = [f'S{i:02d}' for i in range(1, 11)]


def _risers():
    return {s: [(d, 100.0 + k * (i + 1)) for i, d in enumerate(CAL)] for k, s in enumerate(SYMS, 1)}


def _vols(symbols, value=1e9):
    return lambda days: {s: {d: value for d in days} for s in symbols}


def test_build_payload_windows_and_unavailable():
    closes = _risers()
    closes['SHORT'] = closes['S01'][-20:]                           # not enough history
    symbols = [*SYMS, 'SHORT']
    out = mb.build_payload(book=PriceBook(closes=closes, splits={}, calendar=CAL), daily_volumes=_vols(symbols),
                           symbols=symbols, as_of='2026-11-07', generated_at='2026-11-07T09:00:00Z')
    assert list(out['boards']) == ['14w'] and out['scoring'] == 'udv1' and out['weights'] == mb.UDV_WEIGHTS
    b14 = out['boards']['14w']
    assert out['price_date'] == CAL[-1] and out['universe'] == {'count': 11, 'symbols': sorted(symbols)}
    assert b14['days'] == 70 and b14['window'] == [CAL[-71], CAL[-1]]
    assert b14['unavailable'] == {'SHORT': 'price_gap'} and len(b14['scored']) == 10
    assert b14['unavailable_top100_days'] == {'SHORT': 70}          # per member, same keys as unavailable
    assert len(_longs(b14['scored'])) == 10 and b14['adjustments'] == []
    assert all(r['top100_days'] == 70 and r['in_u70'] is True for r in b14['scored'])
    assert sorted([r['symbol'] for r in b14['scored']] + list(b14['unavailable'])) == sorted(symbols)   # everyone, once
    assert not ({'valid', 'long_eligible', 'longs', 'u_count', 'e_count', 'top_n', 'cut'} & set(b14))   # no self-reported counts
    json.dumps(out, allow_nan=False)                                # plain JSON types only


def test_build_payload_volume_window_is_the_signal_window():
    seen = []
    mb.build_payload(book=PriceBook(closes=_risers(), splits={}, calendar=CAL),
                     daily_volumes=lambda days: seen.append(list(days)) or _vols(SYMS)(days),
                     symbols=SYMS, as_of='2026-11-07', generated_at='x')
    assert seen == [CAL[-70:]]                                      # the L days after the base close


def test_build_payload_needs_volume_on_every_day():
    def volumes(days):
        out = _vols(SYMS)(days)
        del out['S01'][days[5]]                                     # one missing day
        return out
    out = mb.build_payload(book=PriceBook(closes=_risers(), splits={}, calendar=CAL), daily_volumes=volumes,
                           symbols=SYMS, as_of='2026-11-07', generated_at='x')
    assert out['boards']['14w']['unavailable'] == {'S01': 'volume_missing'}
    assert out['boards']['14w']['unavailable_top100_days'] == {'S01': 69}  # 69 days with volume, all in the top
    assert 'S01' not in {r['symbol'] for r in out['boards']['14w']['scored']}


def test_unscorable_members_still_compete_for_the_daily_top(monkeypatch):
    """A price_gap member with raw volume takes its place in each day's top, as in the backtest."""
    monkeypatch.setattr(mb, 'TOP_VOLUME_RANK', 2)
    closes = _risers()
    closes['SHORT'] = closes['S01'][-20:]
    symbols = [*SYMS, 'SHORT']
    big = {'SHORT': 1e12, 'S01': 1e11, 'S02': 1e10}

    def volumes(days):
        return {s: {d: big.get(s, 1e9) for d in days} for s in symbols}
    out = mb.build_payload(book=PriceBook(closes=closes, splits={}, calendar=CAL), daily_volumes=volumes,
                           symbols=symbols, as_of='2026-11-07', generated_at='x')
    rows = {r['symbol']: r for r in out['boards']['14w']['scored']}
    assert out['boards']['14w']['unavailable'] == {'SHORT': 'price_gap'}
    assert out['boards']['14w']['unavailable_top100_days'] == {'SHORT': 70}
    assert (rows['S01']['top100_days'], rows['S02']['top100_days']) == (70, 0)
    assert mb.board_u70_count(out['boards']['14w']) == 2                   # S01 and SHORT
    assert [r['symbol'] for r in _longs(rows.values())] == ['S01']


def test_build_payload_reports_split_adjustments():
    split = [(d, 200.0 if i < 60 else 100.0 + (i - 60)) for i, d in enumerate(CAL)]    # 2:1 on CAL[60]
    book = PriceBook(closes={'S': split, 'T': [(d, 50.0 + i) for i, d in enumerate(CAL)]},
                     splits={'S': [(CAL[60], 2.0)]}, calendar=CAL)
    out = mb.build_payload(book=book, daily_volumes=_vols(['S', 'T']), symbols=['S', 'T'],
                           as_of='2026-11-07', generated_at='x')
    assert out['boards']['14w']['adjustments'] == [{'symbol': 'S', 'date': CAL[60], 'factor': 2.0}]
    assert next(r for r in out['boards']['14w']['scored'] if r['symbol'] == 'S')['return'] > 0


def test_build_payload_fails_closed():
    book = PriceBook(closes={'A': [(d, 10.0 + i) for i, d in enumerate(CAL)]}, splits={}, calendar=CAL)
    with pytest.raises(ValueError, match='momentum_coverage_below_floor'):
        mb.build_payload(book=book, daily_volumes=lambda days: {}, symbols=['A', 'B', 'C'],
                         as_of='2026-11-07', generated_at='x')
    with pytest.raises(ValueError, match='momentum_prices_stale'):
        mb.build_payload(book=book, daily_volumes=_vols(['A'], 1.0), symbols=['A'],
                         as_of='2026-12-01', generated_at='x')
    with pytest.raises(ValueError, match='momentum_window_short'):
        mb.build_payload(book=PriceBook(closes={'A': [(d, 10.0) for d in CAL[:30]]}, splits={}, calendar=CAL[:30]),
                         daily_volumes=_vols(['A'], 1.0), symbols=['A'], as_of=CAL[29], generated_at='x')
    with pytest.raises(ValueError, match='momentum_no_members'):
        mb.build_payload(book=book, daily_volumes=lambda days: {}, symbols=[], as_of='2026-11-07', generated_at='x')


def test_load_daily_dollar_volumes_reads_close_times_volume_per_day(tmp_path):
    from src.data.market_store import MarketStore
    days = CAL[-3:]
    path = seed_prices(seed_db(tmp_path / 'market.db'), {
        'A': [(days[0], 10.0, 100), (days[1], 11.0, 0), (days[2], 12.0, 50)],
        'B': [(days[0], 5.0, 10), (days[2], 6.0, 10)],              # a missing day stays missing
        'C': [(CAL[0], 1.0, 1)],                                    # outside the days
    })
    market = MarketStore(db_path=path, read_only=True)
    try:
        out = mb.load_daily_dollar_volumes(market, ['A', 'B', 'C', 'Z'], days)
    finally:
        market.close()
    assert out == {'A': {days[0]: 1000.0, days[1]: 0.0, days[2]: 600.0}, 'B': {days[0]: 50.0, days[2]: 60.0}}


# --- CLI (real subprocess) -------------------------------------------------------------------

import subprocess
import sys
from pathlib import Path

from src.data.prosperity_store import ProsperityStore

ROOT = Path(__file__).resolve().parents[1]
WEEK = '2026-10-03'


@pytest.fixture()
def dbs(tmp_path):
    db = tmp_path / 'prosperity.db'
    with ProsperityStore(db, read_only=False) as store:
        batch = storage_batch(as_of=WEEK, count=20)
        store.save_batch(batch)
    members = sorted(batch[0].meta.members)
    return db, seed_market(tmp_path / 'market.db', members, '2026-10-02'), members


def _cli(db, market, out, as_of=WEEK):
    return subprocess.run([sys.executable, str(ROOT / 'scripts' / 'momentum_board.py'), '--db', str(db),
                           '--market-db', str(market), '--code-version', 'test-v1', '--as-of', as_of,
                           '--out', str(out)], capture_output=True, text=True, timeout=120)


def test_cli_writes_the_payload_for_the_board_members(dbs, tmp_path):
    db, market, members = dbs
    out = tmp_path / 'momentum.json'
    done = _cli(db, market, out)
    assert done.returncode == 0, done.stderr
    payload = json.loads(out.read_text(encoding='utf-8'))
    assert payload['as_of'] == WEEK and payload['price_date'] == '2026-10-02'
    assert payload['universe']['symbols'] == members
    assert len(payload['boards']['14w']['scored']) == len(members) and not payload['boards']['14w']['unavailable']
    assert payload['scoring'] == 'udv1' and all(r['in_u70'] and r['top100_days'] == 70 and r['eligible']
                                                for r in payload['boards']['14w']['scored'])
    assert [p.name for p in tmp_path.iterdir() if p.name.startswith('.momentum')] == []     # no temp left


def test_cli_fails_closed(dbs, tmp_path):
    db, market, _ = dbs
    out = tmp_path / 'momentum.json'
    missing_week = _cli(db, market, out, as_of='2026-10-10')
    assert missing_week.returncode == 6 and 'weekly_board_missing' in missing_week.stderr and not out.exists()
    empty = tmp_path / 'empty.db'
    empty.write_bytes(b'')
    assert _cli(db, empty, out).returncode == 6 and not out.exists()


def test_prices_must_reach_the_week_before_the_board_date():
    """Code review #2: a Wed–Fri price outage must not produce this week's board."""
    def payload(last_day, as_of):
        cal = weekdays(last_day, 80)
        closes = {'A': [(d, 10.0 + i) for i, d in enumerate(cal)]}
        return mb.build_payload(book=PriceBook(closes=closes, splits={}, calendar=cal),
                                daily_volumes=_vols(['A']), symbols=['A'], as_of=as_of, generated_at='x')
    assert payload('2026-11-06', '2026-11-07')['price_date'] == '2026-11-06'      # Friday close, normal
    assert payload('2026-11-05', '2026-11-07')['price_date'] == '2026-11-05'      # Friday holiday
    assert payload('2026-11-04', '2026-11-07')['price_date'] == '2026-11-04'      # Thu + Fri closed
    with pytest.raises(ValueError, match='momentum_prices_stale'):
        payload('2026-11-03', '2026-11-07')                                       # prices stop on Tuesday
    from src.data import prosperity_pool
    assert mb.COVERAGE_MIN is prosperity_pool.COVERAGE_MIN and mb.MAX_LAG_DAYS == 3


def _is_sqlite(path):
    return path.read_bytes()[:15] == b'SQLite format 3'


@pytest.mark.parametrize('target', ['db', 'market', 'db-wal', 'market-shm', 'db.state', 'db-upper'])
def test_cli_refuses_an_out_path_that_is_an_input(dbs, tmp_path, target):
    """Review P2: os.replace onto --db or --market-db (or a sidecar) would destroy an input."""
    db, market, _ = dbs
    out = {'db': db, 'market': market, 'db-wal': Path(str(db) + '-wal'), 'market-shm': Path(str(market) + '-shm'),
           'db.state': Path(str(db) + '.state'), 'db-upper': db.with_name(db.name.upper())}[target]
    done = _cli(db, market, out)
    assert done.returncode == 4 and 'would overwrite an input' in done.stderr
    assert _is_sqlite(db) and _is_sqlite(market)


def test_cli_refuses_a_directory_or_missing_parent_as_out(dbs, tmp_path):
    db, market, _ = dbs
    assert _cli(db, market, tmp_path).returncode == 4
    assert _cli(db, market, tmp_path / 'nope' / 'momentum.json').returncode == 4
