"""M9 forward admission and its complete input-to-score path (synthetic DBs only)."""
from datetime import datetime, timedelta, timezone

import pytest
import importlib

from src.data.market_store import MarketStore
from terminal.prosperity.consensus import build_consensus, ntm_eps, pre_announcement_consensus, revision_inputs
from terminal.prosperity.factors import compute_factor_row
from terminal.prosperity.inputs import build_packets
from terminal.prosperity.kernel import score_board
from terminal.prosperity.loader import load_history
from terminal.prosperity.schemes import SCHEMES
from tests.prosperity_fixtures import eq, est, seed_db

AS_OF = '2026-10-24'
CUTOFF = datetime(2026, 10, 24, 8, 30, tzinfo=timezone.utc)
FISCALS = ('2026-09-30', '2026-12-31', '2027-03-31', '2027-06-30')
EPS = [eq('2026-06-30', '2026-07-30', 1.0)]


def estimates(snap, *, status='complete', completed=None, value=1.0):
    return [dict(est(snap, f, value), forward_run_kind='weekly', forward_run_status=status,
                 forward_completed_at=completed or snap + 'T06:00:00Z') for f in FISCALS]


@pytest.mark.parametrize('age,stale,present', [(6, False, True), (7, True, True),
                                             (14, True, True), (15, False, False)])
def test_snapshot_age_boundaries(age, stale, present):
    snap = (CUTOFF.date() - timedelta(days=age)).isoformat()
    got = build_consensus(estimates=estimates(snap), announced=EPS, current_eps=EPS[-1], closes=[],
                          as_of=AS_OF, unit_ok=True, cutoff=CUTOFF, forward_runs_available=True)
    assert (got.ntm.value is not None) is present
    assert got.forward_stale is stale
    if not present:
        assert got.ntm.missing_reason == 'snapshot_stale'


@pytest.mark.parametrize('status,completed', [('running', None), ('failed', None),
                                            ('complete', '2026-10-24T09:00:00Z'),
                                            ('complete', 'invalid')])
def test_unqualified_current_snapshot_cannot_replace_last_completed(status, completed):
    rows = estimates('2026-10-17') + estimates(AS_OF, status=status, completed=completed, value=1.1)
    got = ntm_eps(rows, EPS, AS_OF, cutoff=CUTOFF)
    assert got.snapshot_date == '2026-10-17' and got.value == 4.0


def test_revision_base_and_jump_history_also_exclude_failed_runs():
    rows = (estimates('2026-07-18') + estimates('2026-07-25', status='failed', value=2.0)
            + estimates('2026-09-12', status='failed', value=10.0) + estimates(AS_OF, value=1.1))
    got = revision_inputs(rows, EPS, AS_OF, cutoff=CUTOFF)
    assert got.base_snapshot == '2026-07-18'
    assert got.delta_eps == pytest.approx(0.4)


def test_unverified_estimates_are_not_implicitly_accepted():
    rows = [{k: v for k, v in est(AS_OF, f, 1.0).items() if not k.startswith('forward_')} for f in FISCALS]
    got = ntm_eps(rows, EPS, AS_OF, cutoff=CUTOFF)
    assert got.value is None


def test_pre_announcement_consensus_filters_failed_and_after_cutoff_runs():
    quarter = eq('2026-09-30', '2026-10-23', 1.2)
    rows = (estimates('2026-10-10', value=1.0)
            + estimates('2026-10-17', status='failed', value=1.1)
            + estimates('2026-10-22', completed='2026-10-24T09:00:00Z', value=1.2))
    got = pre_announcement_consensus(quarter, rows, [], cutoff=CUTOFF)
    assert (got.snapshot_date, got.value) == ('2026-10-10', 1.0)


@pytest.mark.parametrize('cutoff', [datetime(2026, 10, 24, 8, 30), '2026-10-24T08:30:00', '2026-10-24'])
def test_naive_cutoff_is_rejected(cutoff):
    with pytest.raises(ValueError, match='timezone'):
        ntm_eps(estimates(AS_OF), EPS, AS_OF, cutoff=cutoff)


def test_completion_at_cutoff_uses_instant_not_text_order():
    got = ntm_eps(estimates(AS_OF, completed='2026-10-24T16:30:00+08:00'), EPS, AS_OF, cutoff=CUTOFF)
    assert got.value == 4.0 and got.snapshot_date == AS_OF


def _run(store, snap, status):
    store._get_conn().execute(
        'INSERT INTO fmp_forward_runs (snapshot_date,run_kind,status,target_universe_json,'
        'target_count,started_at,completed_at) VALUES (?,?,?,?,?,?,?)',
        (snap, 'weekly', status, '["AAA"]', 1, snap + 'T01:00:00Z',
         snap + 'T06:00:00Z' if status == 'complete' else None))


def _ready_statements(store):
    meta = {'date': '2026-06-30', 'reportedCurrency': 'USD', 'fiscalYear': '2026',
            'period': 'Q2', 'filingDate': '2026-07-30', 'acceptedDate': '2026-07-30 16:00:00'}
    store.upsert_income('AAA', [dict(meta, revenue=100, costOfRevenue=40, grossProfit=60, netIncome=10)])
    store.upsert_balance_sheet('AAA', [dict(meta, totalAssets=500, goodwillAndIntangibleAssets=50)])
    store.upsert_cash_flow('AAA', [dict(meta, operatingCashFlow=20, capitalExpenditure=-5, freeCashFlow=15)])
    store._get_conn().execute("INSERT INTO fmp_earnings (symbol,fiscal_date,announce_date,eps_actual,"
                             "eps_estimated,match_method) VALUES ('AAA','2026-06-30','2026-07-30',1.0,0.9,'statement_window')")
    store._get_conn().commit()


@pytest.mark.parametrize('broken', ['empty', 'missing_table'])
def test_unavailable_manifest_degrades_forward_without_dropping_member(tmp_path, broken):
    store = MarketStore(seed_db(tmp_path / 'm.db'))
    _ready_statements(store)
    store._get_conn().execute('DELETE FROM fmp_forward_runs')
    if broken == 'missing_table':
        store._get_conn().execute('DROP TABLE fmp_forward_runs')
    history = load_history(store, 'AAA', with_vintage=False)
    assert history.forward_runs_available is False
    packets, meta = build_packets(store, AS_OF, mode='live', observed_at=CUTOFF.isoformat(),
                                  cutoff=CUTOFF, symbols=['AAA'], with_beta=False)
    assert len(packets) == 1 and not meta['errors']
    assert packets[0].consensus.ntm.missing_reason == 'forward_runs_unavailable'
    store.close()


@pytest.mark.parametrize('status', ['running', 'failed'])
def test_real_packet_to_scored_row_preserves_stale_badge(tmp_path, status):
    store = MarketStore(seed_db(tmp_path / 'm.db'))
    _ready_statements(store)
    for snap, run_status in [('2026-10-17', 'complete'), (AS_OF, status)]:
        _run(store, snap, run_status)
        store.upsert_fmp_estimates('AAA', [est(snap, f, 1.0) for f in FISCALS])
    store._get_conn().commit()
    h = load_history(store, 'AAA', with_vintage=False)
    assert {r['forward_run_status'] for r in h.estimates if r['snapshot_date'] >= '2026-10-17'} == {'complete', status}
    packets, meta = build_packets(store, AS_OF, mode='live', observed_at=CUTOFF.isoformat(),
                                  cutoff=CUTOFF, symbols=['AAA'], with_beta=False)
    assert not meta['errors'] and len(packets) == 1
    packet = packets[0]
    assert packet.consensus.ntm.snapshot_date == '2026-10-17'
    assert packet.consensus.ntm.value == 4.0
    assert 'forward_stale' in packet.flags
    row = compute_factor_row(packet)
    assert 'forward_stale' in row.packet_flags
    board = score_board([row], SCHEMES['F1'], frozen=None, as_of=AS_OF, code_version='synthetic')
    assert 'forward_stale' in board.rows[0].badges
    store.close()


@pytest.mark.parametrize('module_name', ['scripts.prosperity_inputs', 'scripts.prosperity_score'])
def test_acceptance_clis_forward_an_aware_observation_cutoff(tmp_path, monkeypatch, module_name):
    cli = importlib.import_module(module_name)
    real = cli.build_packets
    seen = {}

    def record(*args, **kwargs):
        seen.update(kwargs)
        return real(*args, **kwargs)

    monkeypatch.setattr(cli, 'build_packets', record)
    args = ['--as-of', '2026-09-26', '--mode', 'live', '--observed-at', '2026-09-26T16:30:00+08:00',
            '--db', str(seed_db(tmp_path / 'm.db')), '--out-dir', str(tmp_path / 'out'), '--no-beta']
    assert cli.main(args) == 0
    assert seen['cutoff'] == datetime(2026, 9, 26, 8, 30, tzinfo=timezone.utc)
    assert seen['observed_at'] == '2026-09-26T08:30:00+00:00'
