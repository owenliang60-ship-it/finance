"""S2 cohorts use constituent counts, independently of the selection scan."""
import sqlite3

import pandas as pd
import pytest

from scripts import morning_report as mr


@pytest.fixture
def cohort_db(tmp_path, monkeypatch):
    monkeypatch.setattr(mr, 'DATA_DIR', tmp_path)
    dates = pd.bdate_range('2026-01-01', periods=90)
    as_of = dates[-1].date().isoformat()
    conn = sqlite3.connect(tmp_path / 'market.db')
    conn.executescript('''
        CREATE TABLE daily_price(symbol TEXT, date TEXT, close REAL);
        CREATE TABLE fmp_forward_runs(snapshot_date TEXT, run_kind TEXT, status TEXT);
        CREATE TABLE fmp_etf_holdings_snapshot(basket TEXT, snapshot_date TEXT,
            raw_asset TEXT, symbol TEXT, included INTEGER, filter_reason TEXT);
        CREATE TABLE extended_membership(symbol TEXT, effective_to TEXT);
        CREATE TABLE security_master(symbol TEXT, eligible INTEGER);
    ''')
    conn.execute('INSERT INTO fmp_forward_runs VALUES (?, ?, ?)', (as_of, 'weekly', 'complete'))
    for basket, n_above in [('SPY', 2), ('QQQ', 6), ('SOXX', 8), ('Extended', 4)]:
        for i in range(10):
            symbol = f'{basket}{i}'.upper()
            if basket == 'Extended':
                conn.execute('INSERT INTO extended_membership VALUES (?, NULL)', (symbol,))
                conn.execute('INSERT INTO security_master VALUES (?, 1)', (symbol,))
            else:
                conn.execute('INSERT INTO fmp_etf_holdings_snapshot VALUES (?, ?, ?, ?, 1, NULL)',
                             ('SOX' if basket == 'SOXX' else basket, as_of, symbol, symbol))
            close = [100.] * 20 + [90.] * 68 + [89., 130. if i < n_above else 80.]
            conn.executemany('INSERT INTO daily_price VALUES (?, ?, ?)',
                             [(symbol, d.date().isoformat(), c) for d, c in zip(dates, close)])
    for symbol in mr.MARKET_TIMING_TARGETS:
        conn.executemany('INSERT INTO daily_price VALUES (?, ?, ?)',
                         [(symbol, d.date().isoformat(), 100.) for d in dates])
    conn.commit()
    yield conn, as_of
    conn.close()


def test_four_independent_cohorts_and_unchanged_signal_parameters(cohort_db):
    report = mr.build_market_timing_factor_report()
    rows = {row['symbol']: row for row in report['rows']}
    assert set(rows) == {'SPY', 'QQQ', 'SOXX', 'Extended'}
    for symbol, value in [('SPY', .2), ('QQQ', .6), ('SOXX', .8), ('Extended', .4)]:
        assert rows[symbol]['breadth_s2_current'] == pytest.approx(value)
        assert rows[symbol]['breadth_s2_previous'] == 0
        assert rows[symbol]['breadth_s2_upcross'] is (value >= .3)
        breadth = report['breadth_s2_by_universe'][symbol]
        assert breadth['threshold'] == .30
        assert breadth['cooldown_days'] == 60
        assert breadth['symbols_expected'] == 10
    assert rows['Extended']['pmarp_current'] is None
    assert [a['universe'] for a in report['alerts']] == ['QQQ', 'SOXX', 'Extended']


def test_missing_etf_membership_does_not_fall_back_to_broad(cohort_db):
    conn, _ = cohort_db
    conn.execute("DELETE FROM fmp_etf_holdings_snapshot WHERE basket='QQQ'")
    conn.commit()
    rows = {r['symbol']: r for r in mr.build_market_timing_factor_report()['rows']}
    assert rows['QQQ']['breadth_s2_current'] is None
    assert rows['QQQ']['breadth_s2_upcross'] is False
    assert rows['SOXX']['breadth_s2_current'] == .8


def test_incomplete_latest_prices_do_not_masquerade_as_current(cohort_db):
    conn, as_of = cohort_db
    conn.execute("DELETE FROM daily_price WHERE symbol='SOXX0' AND date=?", (as_of,))
    conn.commit()
    report = mr.build_market_timing_factor_report()
    soxx = report['breadth_s2_by_universe']['SOXX']
    assert soxx['current'] is None  # 9/10 is below the data-coverage guard
    assert soxx['upcross'] is False
    assert soxx['error']


def test_etf_share_classes_count_as_constituents_and_cash_is_excluded(cohort_db):
    conn, as_of = cohort_db
    conn.execute("UPDATE fmp_etf_holdings_snapshot SET symbol=NULL, included=0, "
                 "filter_reason='dual_class_secondary' WHERE raw_asset='QQQ0'")
    conn.execute("INSERT INTO fmp_etf_holdings_snapshot VALUES ('QQQ', ?, 'CASH', NULL, 0, 'cash_or_fund')", (as_of,))
    conn.commit()
    qqq = mr.build_market_timing_factor_report()['breadth_s2_by_universe']['QQQ']
    assert qqq['symbols_expected'] == 10
    assert qqq['current'] == .6


def test_extended_is_base_only_not_overlay(cohort_db):
    conn, _ = cohort_db
    conn.execute("INSERT INTO extended_membership VALUES ('SOXX0', NULL)")
    conn.execute("INSERT INTO security_master VALUES ('SOXX0', 0)")
    conn.execute("INSERT INTO extended_membership VALUES ('SOXX1', '2026-01-01')")
    conn.execute("INSERT INTO security_master VALUES ('SOXX1', 1)")
    conn.commit()
    ext = mr.build_market_timing_factor_report()['breadth_s2_by_universe']['Extended']
    assert ext['symbols_expected'] == 10
    assert ext['current'] == .4


def test_all_report_surfaces_show_four_cohorts(cohort_db):
    _, as_of = cohort_db
    signals = {'as_of': as_of, 'market_timing_factor': mr.build_market_timing_factor_report()}
    text = mr.format_section_market_timing_factor(signals)
    assert 'Extended' in text
    assert 'S2参与度(broad)' not in text
    visual = mr.build_morning_visual_sections(signals, {})[0]
    assert len(visual['blocks'][0]['rows']) == 4
    assert 'S2参与度(broad)' not in visual['blocks'][0]['columns']
    html = mr.build_html_payload(signals, {}, as_of)['blocks']
    table = next(b for b in html if b.get('columns') and 'PMARP' in b['columns'])
    assert len(table['rows']) == 4
    assert 'Extended' in str(table)


def test_cooldown_is_independent_for_each_cohort(cohort_db):
    conn, _ = cohort_db
    prior_day = pd.bdate_range('2026-01-01', periods=90)[70].date().isoformat()
    for i in range(4):
        conn.execute('UPDATE daily_price SET close=130 WHERE symbol=? AND date=?',
                     (f'QQQ{i}', prior_day))
    conn.commit()
    result = mr.build_market_timing_factor_report()['breadth_s2_by_universe']
    assert result['QQQ']['last_event_date'] == prior_day
    assert result['QQQ']['upcross'] is False
    assert result['SOXX']['upcross'] is True
    assert result['Extended']['upcross'] is True


def test_warmup_and_missing_days_cannot_create_artificial_upcross():
    dates = pd.bdate_range('2026-01-01', periods=90)
    rising = pd.DataFrame({'close': range(100, 190)}, index=dates)
    result = mr._compute_breadth_s2_status_from_price_frames(
        {'A': rising}, min_symbols=1, allow_market_db_fallback=False)
    assert result['current'] == 1
    assert result['last_event_date'] is None
    daily = pd.DataFrame({'date': dates[:3], 'breadth_20': [.2, None, .4]})
    assert mr._compute_breadth_s2_status(daily)['upcross'] is False


@pytest.mark.parametrize('state', ['stale', 'future', 'incomplete'])
def test_unusable_member_snapshots_are_unavailable(cohort_db, state):
    conn, as_of = cohort_db
    if state == 'incomplete':
        conn.execute("UPDATE fmp_forward_runs SET status='failed'")
    else:
        day = (pd.Timestamp(as_of) + pd.Timedelta(days=1 if state == 'future' else -15)).date().isoformat()
        conn.execute('UPDATE fmp_forward_runs SET snapshot_date=?', (day,))
        conn.execute('UPDATE fmp_etf_holdings_snapshot SET snapshot_date=?', (day,))
    conn.commit()
    report = mr.build_market_timing_factor_report()
    for symbol in mr.MARKET_TIMING_TARGETS:
        assert report['breadth_s2_by_universe'][symbol]['current'] is None
    assert report['breadth_s2_by_universe']['Extended']['current'] == .4
    text = mr.format_section_market_timing_factor({'market_timing_factor': report})
    assert next(line for line in text.splitlines() if line.startswith('QQQ |')).endswith('| N/A')


def test_calculation_is_read_only(cohort_db, tmp_path):
    before = (tmp_path / 'market.db').read_bytes()
    mr.build_market_timing_factor_report()
    assert (tmp_path / 'market.db').read_bytes() == before
