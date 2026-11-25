import json
import sqlite3
from datetime import datetime, timezone


CUTOFF = datetime(2026, 10, 24, 8, 30, tzinfo=timezone.utc)


def report(path, at='2026-10-24T07:00:00Z', ratio=.96):
    path.write_text(json.dumps({'exit_code': 1, 'after': {'as_of': at,
        'coverage': {'fundamental_ready': {'ratio': ratio}}}}))


def test_fundamental_gate_uses_evidence_not_exit_code(tmp_path):
    from src.data.prosperity_gates import fundamental_refreshed
    p = tmp_path / 'fundamentals-now.json'
    report(p)
    assert fundamental_refreshed(tmp_path, '2026-10-24', CUTOFF).status == 'pass'
    report(p, at='2026-10-24T09:00:00Z')
    g = fundamental_refreshed(tmp_path, '2026-10-24', CUTOFF)
    assert g.status == 'fail' and g.detail['reason'] == 'report_after_snapshot'
    report(p, at='2026-10-17T09:00:00Z')
    assert fundamental_refreshed(tmp_path, '2026-10-24', CUTOFF).status == 'fail'
    report(p, ratio=.89)
    assert fundamental_refreshed(tmp_path, '2026-10-24', CUTOFF).status == 'fail'


def test_fundamental_invalid_or_missing_is_unknown(tmp_path):
    from src.data.prosperity_gates import fundamental_refreshed
    assert fundamental_refreshed(tmp_path, '2026-10-24', CUTOFF).status == 'unknown'
    p = tmp_path / 'fundamentals-bad.json'
    for text in ('{', '{}', '{"after": null}'):
        p.write_text(text)
        assert fundamental_refreshed(tmp_path, '2026-10-24', CUTOFF).status == 'unknown'
    report(p, ratio=float('nan'))
    assert fundamental_refreshed(tmp_path, '2026-10-24', CUTOFF).status == 'unknown'


def test_integrity_never_creates_missing_db(tmp_path):
    from src.data.prosperity_gates import integrity
    path = tmp_path / 'db'
    assert integrity(path).status == 'unknown'
    assert not path.exists()
    with sqlite3.connect(path) as conn:
        conn.execute('create table sample (x)')
    assert integrity(path).status == 'pass'
    path.write_text('broken')
    assert integrity(path).status == 'unknown'


def test_member_floor_and_input_errors():
    from src.data.prosperity_gates import member_floor, input_health
    from tests.prosperity_fixtures import frow, full_values
    rows = [frow(str(i), full_values(i)) for i in range(100)]
    assert member_floor(800, 900).status == 'fail'
    assert member_floor(810, 900).status == 'pass'
    assert member_floor(100, None).detail['reason'] == 'no_previous'
    meta = {'members_resolved': 100, 'errors': {'x': 'bad'}}
    assert input_health(meta, rows, 100, 100).status == 'pass'
    assert input_health({**meta, 'errors': {'x': 'bad', 'y': 'bad'}}, rows, 100, 100).status == 'fail'
    assert input_health(meta, rows, 0, 100).status == 'fail'
    assert input_health({'members_resolved': 0, 'errors': {}}, [], 0, None).status == 'fail'


def _named(tmp_path, hhmm, ratio=None, text=None):
    p = tmp_path / f'fundamentals-20261024T{hhmm}00Z.json'
    if text is not None:
        p.write_text(text)
    else:
        report(p, at=f'2026-10-24T{hhmm[:2]}:{hhmm[2:]}:00Z', ratio=ratio)
    return p


def test_fundamental_latest_pre_snapshot_failure_overrides_earlier_pass(tmp_path):
    from src.data.prosperity_gates import fundamental_refreshed
    _named(tmp_path, '0600', .93)
    latest = _named(tmp_path, '0700', .84)
    g = fundamental_refreshed(tmp_path, '2026-10-24', CUTOFF)
    assert g.status == 'fail' and g.detail['reason'] == 'coverage_below_floor'
    assert g.detail['path'] == str(latest)


def test_fundamental_latest_pre_snapshot_pass_overrides_earlier_failure(tmp_path):
    from src.data.prosperity_gates import fundamental_refreshed
    _named(tmp_path, '0600', .84)
    latest = _named(tmp_path, '0700', .93)
    g = fundamental_refreshed(tmp_path, '2026-10-24', CUTOFF)
    assert g.status == 'pass' and g.detail['path'] == str(latest)


def test_fundamental_corrupt_latest_report_is_unknown(tmp_path):
    from src.data.prosperity_gates import fundamental_refreshed
    _named(tmp_path, '0600', .93)
    _named(tmp_path, '0700', text='{')
    g = fundamental_refreshed(tmp_path, '2026-10-24', CUTOFF)
    assert g.status == 'unknown' and g.detail['reason'] == 'report_invalid'


def test_fundamental_corrupt_older_report_is_ignored(tmp_path):
    from src.data.prosperity_gates import fundamental_refreshed
    _named(tmp_path, '0600', text='{')
    latest = _named(tmp_path, '0700', .93)
    g = fundamental_refreshed(tmp_path, '2026-10-24', CUTOFF)
    assert g.status == 'pass' and g.detail['path'] == str(latest)


def test_fundamental_report_after_cutoff_is_skipped(tmp_path):
    from src.data.prosperity_gates import fundamental_refreshed
    before = _named(tmp_path, '0700', .93)
    _named(tmp_path, '0900', .50)
    g = fundamental_refreshed(tmp_path, '2026-10-24', CUTOFF)
    assert g.status == 'pass' and g.detail['path'] == str(before)
