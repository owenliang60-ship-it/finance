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
