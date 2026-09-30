"""Quality boundaries use immutable public-data fixtures and temporary inputs."""
import copy
import json
from pathlib import Path

import pytest

from src.data.prosperity_quality import (
    resolve_eps_quarters, split_basis_audit, split_ratio_eligible, statement_availability, statement_known_on,
)
from src.data.prosperity_history import street_eps_depth

CASES = json.loads((Path(__file__).parent / 'fixtures/prosperity_quality_cases.json').read_text())['cases']


@pytest.mark.parametrize('accepted,filing,expected', [
    ('2024-03-31 00:00:00', '2024-03-31', None),
    ('2024-03-30 20:00:00', '2024-03-31', None),
    ('bad', '2024-05-02', '2024-05-02'),
    ('2024-05-01 16:00:00', '2024-05-02', '2024-05-01'),
    ('', '', None),
])
def test_public_statement_date(accepted, filing, expected):
    r = statement_availability({'date': '2024-03-31', 'accepted_date': accepted, 'filing_date': filing})
    assert r['public_available_at'] == expected


def test_observation_proves_now_but_not_past_publication():
    row = {'date': '2024-03-31', 'filing_date': '2024-03-31'}
    assert statement_known_on(row) is None
    assert statement_known_on(row, observed_at='2026-09-29') == '2026-09-29'
    assert statement_availability(row)['public_available_at'] is None
    assert statement_known_on({'date': '2027-03-31'}, observed_at='2026-09-29') is None


def test_conflict_does_not_leak_back_before_second_announcement():
    rows = [{'fiscal_date': '2024-12-31', 'announce_date': '2025-02-13', 'eps_actual': 3.39},
            {'fiscal_date': '2024-12-31', 'announce_date': '2025-02-14', 'eps_actual': 4.66}]
    original = copy.deepcopy(rows)
    assert resolve_eps_quarters(rows, '2025-02-13')['quarters'][0]['eps_actual'] == 3.39
    got = resolve_eps_quarters(rows, '2025-02-14')
    assert got['quarters'][0]['eps_actual'] is None
    assert got['issues'][0]['reason'] == 'eps_conflicting_quarter'
    assert rows == original


def test_equal_duplicate_zero_and_no_transitive_quarter_merging():
    rows = [{'fiscal_date': f, 'announce_date': a, 'eps_actual': 0.0}
            for f, a in [('2025-03-31', '2025-05-01'), ('2025-03-29', '2025-05-02'),
                         ('2025-03-10', '2025-05-03')]]
    got = resolve_eps_quarters(rows, '2025-06-30')
    assert len(got['quarters']) == 2
    assert got['quarters'][0]['eps_actual'] == 0.0
    assert got['quarters'][0]['announce_date'] == '2025-05-01'


@pytest.mark.parametrize('symbol,boundary', [
    ('KLAC', '2024-09-30'), ('ANET', '2024-09-30'), ('ORLY', '2024-12-31')])
def test_real_split_adjustment_boundary_far_from_event(symbol, boundary):
    case = CASES[symbol]
    quarters = resolve_eps_quarters(case['earnings'], '2026-09-26')['quarters']
    result = split_basis_audit(quarters, case['income'], case['splits'])
    assert result['status'] == 'suspect'
    assert boundary in {i['boundary_fiscal'] for i in result['issues']}
    depth = street_eps_depth(case['earnings'], '2026-09-26', '2026-06-30',
                             income_rows=case['income'], splits=case['splits'])
    assert not depth['sue_ok'] and not depth['dsue_ok']
    assert depth['sue_missing'] == 'eps_split_basis_suspect'


@pytest.mark.parametrize('symbol', ['BHP', 'FER', 'COIN'])
def test_real_conflicting_quarters(symbol):
    result = resolve_eps_quarters(CASES[symbol]['earnings'], '2026-09-26')
    assert any(i['reason'] == 'eps_conflicting_quarter' for i in result['issues'])


def test_split_heuristic_does_not_flag_business_drop_with_constant_basis():
    rows = [{'fiscal_date': f'2024-{m:02}-28', 'eps_actual': value}
            for m, value in [(1, 10), (3, 12), (5, 11), (7, 1), (9, 1.2), (11, 1.1)]]
    income = [{'date': r['fiscal_date'], 'eps_diluted': r['eps_actual'] * .9} for r in rows]
    assert split_basis_audit(rows, income, [{'date': '2024-08-01', 'numerator': 10, 'denominator': 1}])['issues'] == []
    assert split_basis_audit(rows, income, [])['status'] == 'split_metadata_unknown'
    assert split_basis_audit(rows[:2], income, [{'date': '2024-08-01', 'numerator': 10, 'denominator': 1}])['status'] == 'insufficient_basis_pairs'


def test_conflict_outside_sue_window_does_not_permanently_block_symbol():
    from tests.test_prosperity_history import FISCALS, _eps_rows
    rows = _eps_rows(FISCALS)
    rows.append(dict(rows[0], announce_date='2022-05-20', eps_actual=99.0))
    assert street_eps_depth(rows, '2026-06-30', '2026-03-31')['sue_ok']


def test_report_distinguishes_historical_dates_from_observed_snapshot(tmp_path):
    from src.data.market_store import MarketStore
    from scripts.verify_prosperity_history import build_report
    from tests.test_prosperity_history import _seed_statements_filed, _eps_rows, FISCALS
    s = MarketStore(tmp_path / 'm.db')
    _seed_statements_filed(s, 'X', FISCALS[-13:], 0)
    s.replace_fmp_earnings('X', _eps_rows(FISCALS[-13:]))
    t = {'by_quarter_end': {'2026-06-30': ['X']}}
    # Placeholder filing dates: each quarter's results release now dates it (Boss 2026-09-30)
    q = build_report(s, t, [])['quarter_ends'][0]
    assert (q['raw_three_table_ok'], q['three_table_ok'], q['sue_ok']) == (1, 1, 1)
    # Releases without an actual are no evidence: the quarter still has no trusted date
    bare = MarketStore(tmp_path / 'bare.db')
    _seed_statements_filed(bare, 'X', FISCALS[-13:], 0)
    bare.replace_fmp_earnings('X', [dict(r, eps_actual=None) for r in _eps_rows(FISCALS[-13:])])
    q = build_report(bare, t, [])['quarter_ends'][0]
    assert q['raw_three_table_ok'] == 1 and q['three_table_ok'] == 0
    assert q['sue_ok'] == 0  # no trusted current-quarter anchor
    assert q['street_eps_gap_reasons'] == {'statement_availability_unknown': 1}
    with pytest.raises(ValueError, match='observation'):
        build_report(s, t, [], observed_at='2026-09-29')
    live = build_report(s, {'by_quarter_end': {'2026-09-29': ['X']}}, [], observed_at='2026-09-29')
    assert live['quarter_ends'][0]['three_table_ok'] == 1
    assert live['quarter_ends'][0]['sue_ok'] == 1
    assert live['input_mode'] == 'observed_snapshot'
    assert live['quarter_ends'][0]['freeze']['cov_d60'] is None
    s.close()


def test_current_cli_requires_matching_archived_snapshot(tmp_path):
    import hashlib
    from src.data.market_store import MarketStore
    from scripts.verify_prosperity_history import main
    path = tmp_path / 'db.sqlite'
    store = MarketStore(path)
    store.close()
    manifest = tmp_path / 'manifest.json'
    manifest.write_text(json.dumps({'created_at': '20260929T040937Z',
                                   'snapshot_sha256': '0' * 64}))
    args = ['report', '--db-path', str(path), '--snapshot-manifest', str(manifest),
            '--as-of', '2026-09-29', '--out-dir', str(tmp_path / 'out')]
    with pytest.raises(ValueError, match='SHA'):
        main(args)
    manifest.write_text(json.dumps({'created_at': '20260929T040937Z',
                                   'snapshot_sha256': hashlib.sha256(path.read_bytes()).hexdigest()}))
    with pytest.raises(ValueError, match='observation'):
        main([a if a != '2026-09-29' else '2026-09-26' for a in args])
    assert not (tmp_path / 'out').exists()


@pytest.mark.parametrize('ratio', [1.01, 1.1, 1.25, 0.8, 2.0])
def test_flat_basis_cannot_be_a_split_discontinuity(ratio):
    from tests.test_prosperity_history import FISCALS, _eps_rows
    rows = _eps_rows(FISCALS)
    quarters = resolve_eps_quarters(rows, '2026-06-30')['quarters']
    income = [{'date': r['fiscal_date'], 'eps_diluted': r['eps_actual']} for r in rows]
    assert split_basis_audit(quarters, income, [
        {'date': '2024-01-01', 'numerator': ratio, 'denominator': 1}])['issues'] == []


def test_report_split_summary_uses_new_detector(tmp_path):
    from src.data.market_store import MarketStore
    from scripts.verify_prosperity_history import build_report
    s = MarketStore(tmp_path / 'm.db')
    case = CASES['KLAC']
    s.replace_fmp_earnings('KLAC', case['earnings'])
    s.upsert_income('KLAC', [{'date': r['date'], 'epsDiluted': r['eps_diluted']} for r in case['income']])
    with s._get_conn() as c:
        for r in case['splits']:
            c.execute('INSERT INTO fmp_stock_splits (symbol,date,numerator,denominator,split_type,source,fetched_at) VALUES (?,?,?,?,?,?,?)',
                      ('KLAC', r['date'], r['numerator'], r['denominator'], r['split_type'], r['source'], r['fetched_at']))
    d = build_report(s, {'by_quarter_end': {'2026-06-30': ['KLAC']}}, [])
    assert any(x.get('boundary_fiscal') == '2024-09-30' for x in d['split_suspects'])
    s.close()


def test_current_archive_ignores_unhashed_wal(tmp_path):
    import hashlib
    import sqlite3
    from src.data.market_store import MarketStore
    from scripts.verify_prosperity_history import main
    path = tmp_path / 'db.sqlite'
    s = MarketStore(path)
    with s._get_conn() as c:
        c.execute("INSERT INTO security_master(symbol,eligible,reason,updated_at) VALUES ('OLD',1,'test','2026-09-29')")
        c.execute("INSERT INTO extended_membership(symbol,effective_from) VALUES ('OLD','2026-01-01')")
    s.close()
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    manifest = tmp_path / 'manifest.json'
    manifest.write_text(json.dumps({'created_at': '20260929T040937Z', 'snapshot_sha256': digest}))
    writer = sqlite3.connect(path)
    writer.execute('PRAGMA wal_autocheckpoint=0')
    with writer:
        writer.execute("INSERT INTO security_master(symbol,eligible,reason,updated_at) VALUES ('NEW',1,'test','2026-09-29')")
        writer.execute("INSERT INTO extended_membership(symbol,effective_from) VALUES ('NEW','2026-01-01')")
    assert hashlib.sha256(path.read_bytes()).hexdigest() == digest
    try:
        main(['report', '--db-path', str(path), '--snapshot-manifest', str(manifest),
              '--as-of', '2026-09-29', '--out-dir', str(tmp_path / 'out'), '--date', 'wal'])
        doc = json.loads((tmp_path / 'out/d9-coverage-wal.json').read_text())
        assert doc['quarter_ends'][0]['members'] == 1
    finally:
        writer.close()


def test_split_only_blocks_the_factor_that_crosses_boundary():
    from tests.test_prosperity_history import FISCALS, _eps_rows
    rows = _eps_rows(FISCALS)
    income = [{'date': r['fiscal_date'], 'eps_diluted': r['eps_actual']} for r in rows]
    for r in rows[:4]:
        r['eps_actual'] *= 10
    result = street_eps_depth(rows, '2026-06-30', '2026-03-31', income_rows=income,
        splits=[{'date': '2023-06-01', 'numerator': 10, 'denominator': 1}])
    assert result['sue_ok'] and not result['dsue_ok']
    assert result['dsue_missing'] == 'eps_split_basis_suspect'


@pytest.mark.parametrize('symbol', ['DELL', 'FTV', 'LH'])
def test_spinoff_adjustment_is_not_split_evidence(symbol):
    case = CASES[symbol]
    quarters = resolve_eps_quarters(case['earnings'], '2026-09-29')['quarters']
    assert split_basis_audit(quarters, case['income'], case['splits'])['issues'] == []


@pytest.mark.parametrize('symbol,boundary', [('APH', '2025-03-31'), ('MNST', '2024-12-31')])
def test_additional_real_stock_splits_still_detected(symbol, boundary):
    case = CASES[symbol]
    quarters = resolve_eps_quarters(case['earnings'], '2026-09-29')['quarters']
    assert any(i['boundary_fiscal'] == boundary for i in
               split_basis_audit(quarters, case['income'], case['splits'])['issues'])


@pytest.mark.parametrize('symbol,fiscal,expected', [
    ('ASML', '2022-06-30', '2022-07-20'), ('HALO', '2025-09-30', '2025-11-03')])
def test_public_date_cannot_precede_same_quarter_report(symbol, fiscal, expected):
    case = CASES[symbol]
    row = next(r for r in case['income'] if r['date'] == fiscal)
    result = statement_availability(row, earnings_rows=case['earnings'])
    assert result['public_available_at'] == expected
    assert 'statement_date_before_earnings' in result['issues']


def test_earnings_announcement_fills_placeholder_dates_only_on_the_historical_path():
    eps = [{'fiscal_date': '2025-09-30', 'announce_date': '2025-11-03', 'eps_actual': 1.0},
           {'fiscal_date': '2025-09-30', 'announce_date': '2025-11-04', 'eps_actual': 2.0},
           {'fiscal_date': '2025-09-30', 'announce_date': '2025-10-01', 'eps_actual': None}]
    unknown = {'date': '2025-09-30', 'accepted_date': '2025-09-30'}
    filled = statement_availability(unknown, earnings_rows=eps)
    assert (filled['public_available_at'], filled['source']) == ('2025-11-03', 'earnings_announcement')
    assert filled['issues'] == ['accepted_date_invalid_or_placeholder', 'statement_date_from_earnings']
    assert statement_availability(unknown)['public_available_at'] is None           # no earnings evidence given
    # an observed archive never takes the stored earnings date (current and strict reads)
    assert statement_known_on(unknown, earnings_rows=eps, observed_at='2025-12-01') == '2025-12-01'
    valid = dict(unknown, accepted_date='2025-10-03')
    assert statement_known_on(valid, earnings_rows=eps) == '2025-11-03'
    assert statement_known_on(valid, earnings_rows=eps, observed_at='2025-10-20') == '2025-10-03'
    later = dict(valid, accepted_date='2025-11-06')
    assert statement_known_on(later, earnings_rows=eps) == '2025-11-06'


@pytest.mark.parametrize('row,eps,expected', [
    # market.db 2026-09-30: FMP stamps both dates on the fiscal day; the results release is real
    ({'date': '2019-06-30', 'filing_date': '2019-06-30', 'accepted_date': '2019-06-30 00:00:00'},
     {'fiscal_date': '2019-06-29', 'announce_date': '2019-08-01', 'eps_actual': 0.01}, '2019-08-01'),      # SHOP
    ({'date': '2022-07-31', 'filing_date': '2022-07-31', 'accepted_date': '2022-07-31 00:00:00'},
     {'fiscal_date': '2022-08-01', 'announce_date': '2022-09-01', 'eps_actual': 0.97}, '2022-09-01'),      # AVGO
])
def test_placeholder_dates_fall_back_to_the_results_release(row, eps, expected):
    assert statement_availability(row, earnings_rows=[eps])['public_available_at'] == expected
    assert statement_known_on(row, earnings_rows=[eps]) == expected


def test_placeholder_dates_without_a_same_quarter_release_stay_unknown():
    row = {'date': '2022-07-31', 'filing_date': '2022-07-31'}
    eps = [{'fiscal_date': '2022-05-01', 'announce_date': '2022-06-09', 'eps_actual': 9.07},     # prior quarter
           {'fiscal_date': '2022-07-31', 'announce_date': '2022-09-01', 'eps_actual': None}]     # no actual
    result = statement_availability(row, earnings_rows=eps)
    assert result['public_available_at'] is None
    assert result['issues'] == ['filing_date_invalid_or_placeholder', 'statement_availability_unknown']


def test_earlier_of_two_valid_dates_wins_over_a_later_amendment():
    # market.db 2026-09-30, CRM: the stored acceptance is an amendment a year after the 10-K filing
    row = {'date': '2021-01-31', 'filing_date': '2021-03-17', 'accepted_date': '2022-02-24 16:13:45'}
    result = statement_availability(row)
    assert (result['public_available_at'], result['source']) == ('2021-03-17', 'filing_date')
    eps = [{'fiscal_date': '2021-01-31', 'announce_date': '2021-02-25', 'eps_actual': 1.04}]
    assert statement_known_on(row, earnings_rows=eps) == '2021-03-17'           # release floor is earlier


def test_freeze_arrival_uses_earnings_floor():
    from src.data.prosperity_history import arrival_day
    row = {'date': '2025-09-30', 'accepted_date': '2025-10-03'}
    eps = [{'fiscal_date': '2025-09-30', 'announce_date': '2025-11-03', 'eps_actual': 1.0}]
    assert arrival_day({'i': [row], 'b': [row], 'c': [row]}, '2025-09-30', earnings_rows=eps) == 34


@pytest.mark.parametrize('num,den,eligible', [
    (2.0, 1.0, 1), (3, 2, 1), (5, 4, 1), (4, 5, 1), (20, 1, 1), (1, 20, 1),
    (25, 1, 1), (50, 1, 1), (1, 50, 1), (21, 1, 1),          # one leg is 1: BKNG 25:1, CMG 50:1, reverse 1:50
    (20, 20, 0), (21, 2, 0), (903, 500, 0), (239, 200, 0), (239, 100, 0), (1.25, 1, 0),
    (True, 2, 0), (float('nan'), 1, 0), (2, 0, 0), (float('inf'), 1, 0), (1, float('inf'), 0),
])
def test_only_small_integer_split_legs_are_eligible(num, den, eligible):
    result = split_basis_audit([], [], [{'date': '2025-01-01', 'numerator': num, 'denominator': den}])
    assert result['eligible_split_events'] == eligible
    assert split_ratio_eligible(num, den) is bool(eligible)     # the M4 price layer uses this same predicate


def test_earliest_valid_same_quarter_earnings_floor_and_zero_actual():
    row = {'date': '2025-09-30', 'filing_date': '2025-10-01'}
    eps = [{'fiscal_date': '2025-09-29', 'announce_date': '2025-11-03', 'eps_actual': 0.0},
           {'fiscal_date': '2025-09-30', 'announce_date': '2025-11-10', 'eps_actual': 1.0},
           {'fiscal_date': '2025-06-30', 'announce_date': '2025-07-20', 'eps_actual': 1.0},
           {'fiscal_date': '2025-09-30', 'announce_date': 'bad', 'eps_actual': 1.0},
           {'fiscal_date': '2025-09-30', 'announce_date': '2025-10-03', 'eps_actual': float('nan')}]
    assert statement_availability(row, earnings_rows=eps)['public_available_at'] == '2025-11-03'


def test_report_anchor_uses_same_earnings_floor(tmp_path):
    from src.data.market_store import MarketStore
    from scripts.verify_prosperity_history import _SymbolData
    s = MarketStore(tmp_path / 'm.db')
    row = {'date': '2022-06-30', 'acceptedDate': '2022-07-02', 'filingDate': '2022-07-02'}
    for write in (s.upsert_income, s.upsert_balance_sheet, s.upsert_cash_flow):
        write('ASML', [row])
    s.replace_fmp_earnings('ASML', [{'fiscal_date': '2022-06-30', 'announce_date': '2022-07-20',
                                  'eps_actual': 1.0, 'match_method': 'statement_window'}])
    historical = _SymbolData(s, 'ASML')
    assert historical.current_fiscal('2022-07-10') is None
    assert historical.current_fiscal('2022-07-20') == '2022-06-30'
    observed = _SymbolData(s, 'ASML', observed_at='2022-07-10')
    assert observed.current_fiscal('2022-07-10') == '2022-06-30'
    s.close()
