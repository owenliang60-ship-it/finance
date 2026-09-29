"""Quality boundaries use immutable public-data fixtures and temporary inputs."""
import copy
import json
from pathlib import Path

import pytest

from src.data.prosperity_quality import (
    resolve_eps_quarters, split_basis_audit, statement_availability, statement_known_on,
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
    historical = build_report(s, t, [])
    q = historical['quarter_ends'][0]
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
