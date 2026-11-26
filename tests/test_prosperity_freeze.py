import pytest


def test_calendar_freeze_is_last_saturday_of_next_quarter():
    from terminal.prosperity.freeze import candidate_saturdays, freeze_date, freeze_decision, quarter_of
    assert tuple(str(x) for x in quarter_of('2026Q2')) == ('2026-04-01', '2026-06-30')
    assert freeze_date('2026Q3') == '2026-12-26' and freeze_date('2026Q4') == '2027-03-27'   # Q4 crosses the year
    assert freeze_date('2021Q2') == '2021-09-25' and freeze_date('2022Q3') == '2022-12-31'
    assert freeze_date('2028Q2') == '2028-09-30'                         # quarter ends on a Saturday: that day
    assert candidate_saturdays('2026Q3') == ('2026-12-26',)
    assert freeze_decision('2026-12-26', '2026Q3', .10) == 'calendar'   # coverage is recorded, never a gate
    assert freeze_decision('2026-12-26', '2026Q3', 1.0) == 'calendar'
    assert freeze_decision('2026-12-19', '2026Q3', 1.0) is None
    assert freeze_decision('2027-01-02', '2026Q3', 1.0) is None          # a missed date is replayed, not shifted


def test_new_chain_dates_match_the_north_star_list():
    from terminal.prosperity.freeze import freeze_date, quarter_labels
    assert [freeze_date(q) for q in quarter_labels('2021Q2', '2026Q2')] == [
        '2021-09-25', '2021-12-25', '2022-03-26', '2022-06-25', '2022-09-24', '2022-12-31', '2023-03-25',
        '2023-06-24', '2023-09-30', '2023-12-30', '2024-03-30', '2024-06-29', '2024-09-28', '2024-12-28',
        '2025-03-29', '2025-06-28', '2025-09-27', '2025-12-27', '2026-03-28', '2026-06-27', '2026-09-26']


def test_due_quarter_has_explicit_origin_and_skips_frozen():
    from terminal.prosperity.freeze import due_quarter, quarter_labels
    frozen = set(quarter_labels('2021Q2', '2026Q2'))
    assert due_quarter('2026-10-24', frozen) is None
    assert due_quarter('2026-12-19', frozen) is None
    assert due_quarter('2026-12-26', frozen) == '2026Q3'
    assert due_quarter('2026-12-26', frozen | {'2026Q3'}) is None
    assert due_quarter('2026-12-26', frozen - {'2022Q1'}) == '2022Q1'


def test_coverage_counts_missing_bucket_only_in_denominator():
    from terminal.prosperity.freeze import freeze_coverage
    from tests.prosperity_fixtures import frow, full_values
    from terminal.prosperity.schemes import SCHEMES
    from types import SimpleNamespace
    rows = [frow('A', full_values(1)), frow('B', full_values(2)),
            frow('C', full_values(3), flags=('identity_unverified',))]
    packets = {'A': SimpleNamespace(quarter_bucket='2026Q3'),
               'B': SimpleNamespace(quarter_bucket=None), 'C': SimpleNamespace(quarter_bucket='2026Q3')}
    assert freeze_coverage(rows, packets, SCHEMES['F1'], '2026Q3') == (.5, 1, 2)
    with pytest.raises(ValueError, match='denominator'):
        freeze_coverage([], {}, SCHEMES['F1'], '2026Q3')


@pytest.mark.parametrize('label', ['2026Q0', '2026Q5', '26Q2', '2026q2'])
def test_bad_quarter(label):
    from terminal.prosperity.freeze import quarter_of
    with pytest.raises(ValueError):
        quarter_of(label)


def test_invalid_decision_inputs():
    from terminal.prosperity.freeze import freeze_decision
    for coverage in (float('nan'), -.1, 1.1):
        with pytest.raises(ValueError):
            freeze_decision('2026-12-26', '2026Q3', coverage)
    with pytest.raises(ValueError):
        freeze_decision('2026-12-25', '2026Q3', .95)


VERIFIER = 'reports/prosperity/freeze-calendar-20261113/verify_freezes.py'


def _envelope(body):
    import hashlib, json
    text = json.dumps(body, sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False)
    return {'payload': body, 'sha256': hashlib.sha256(text.encode()).hexdigest()}


def _synthetic_chain(tmp_path, version='v-new'):
    """Minimal prosperity.db + state + market.db for two quarters on the new calendar."""
    import json, sqlite3
    db, market = tmp_path / 'prosperity.db', tmp_path / 'market.db'
    conn = sqlite3.connect(db)
    conn.execute('CREATE TABLE board (board_type TEXT, as_of TEXT, scheme_id TEXT, code_version TEXT, '
                 'quarter_label TEXT, frozen_at TEXT, input_set_id INTEGER)')
    conn.execute('CREATE TABLE stock_input (input_set_id INTEGER, symbol TEXT, input_json TEXT)')
    m = sqlite3.connect(market)
    m.execute('CREATE TABLE income_quarterly (symbol TEXT, date TEXT)')
    state = tmp_path / 'prosperity.db.state' / 'candidates'
    state.mkdir(parents=True)
    for n, (label, day, fiscal) in enumerate((('2026Q1', '2026-06-27', '2026-03-31'),
                                              ('2026Q2', '2026-09-26', '2026-06-30'))):
        bucket = label
        evidence = [['AAA', bucket], ['BBB', bucket], ['CCC', None]]
        cutoff = f'{day[:8]}{int(day[8:]) + 1:02d}T00:00:00Z'
        body = {'schema_version': 1, 'identity': {'as_of': day, 'quarter_label': label, 'code_version': version,
                                                  'schemes': {}},
                'cutoff': cutoff, 'meta': {'mode': 'replay'}, 'health': {'status': 'pass'},
                'coverage': 2 / 3, 'decision': 'calendar', 'evidence': evidence, 'hashes': {'k': 'h'}}
        (state / f'{day}-{n}.json').write_text(json.dumps(_envelope(body)))
        for scheme in ('F1', 'F1-rank', 'F1-exfin', 'F0'):
            conn.execute('INSERT INTO board VALUES (?,?,?,?,?,?,?)', ('quarter_final', day, scheme, version, label,
                                                                    cutoff, n))
            conn.execute('INSERT INTO board VALUES (?,?,?,?,?,?,?)', ('weekly', day, scheme, version, None, None, n))
        for sym, b in evidence:
            conn.execute('INSERT INTO stock_input VALUES (?,?,?)',
                         (n, sym, json.dumps({'current_fiscal': fiscal if b else None, 'quarter_bucket': b})))
            if b:
                m.execute('INSERT INTO income_quarterly VALUES (?,?)', (sym, fiscal))
    conn.commit(); m.commit(); conn.close(); m.close()
    return db, market, state


def _verify(db, market, out, *extra):
    import subprocess, sys
    return subprocess.run([sys.executable, '-I', VERIFIER, '--db', str(db), '--market-db', str(market),
                           '--version', 'v-new', '--first', '2026Q1', '--last', '2026Q2', '--out', str(out),
                           '--sample-quarters', '2026Q2', *extra], capture_output=True, text=True)


def test_new_calendar_verifier_is_stdlib_only_and_passes_a_valid_chain(tmp_path):
    import json, re
    source = open(VERIFIER).read()
    assert not re.search(r'^\s*(from|import)\s+(terminal|src|backtest|scripts)\b', source, re.M)
    db, market, _ = _synthetic_chain(tmp_path)
    out = tmp_path / 'verify.json'
    run = _verify(db, market, out)
    assert run.returncode == 0, run.stdout + run.stderr
    report = json.loads(out.read_text())
    assert report['status'] == 'PASS' and [q['as_of'] for q in report['quarters']] == ['2026-06-27', '2026-09-26']
    assert len(report['source_bucket_sample']) == 2


def _rewrite(state, day, change):
    import json
    (path,) = state.glob(f'{day}-*.json')
    body = json.loads(path.read_text())['payload']
    change(body)
    path.write_text(json.dumps(_envelope(body)))                 # self-consistent checksum: only content checks catch it


def _bad_checksum(state):
    import json
    (path,) = state.glob('2026-06-27-*.json')
    envelope = json.loads(path.read_text())
    envelope['sha256'] = '0' * 64
    path.write_text(json.dumps(envelope))


def test_new_calendar_verifier_fails_on_tampered_records(tmp_path):
    import json, shutil
    cases = {
        'as_of': lambda s: _rewrite(s, '2026-09-26', lambda b: b['identity'].update(as_of='2026-10-03')),
        'coverage': lambda s: _rewrite(s, '2026-09-26', lambda b: b.update(coverage=1.0)),
        'decision': lambda s: _rewrite(s, '2026-06-27', lambda b: b.update(decision='coverage')),
        'missing': lambda s: next(s.glob('2026-06-27-*.json')).unlink(),
        'duplicate': lambda s: shutil.copy(next(s.glob('2026-06-27-*.json')), s / '2026-06-27-copy.json'),
        'checksum': _bad_checksum,
    }
    for name, tamper in cases.items():
        root = tmp_path / name
        root.mkdir()
        db, market, state = _synthetic_chain(root)
        tamper(state)
        run = _verify(db, market, root / 'verify.json')
        assert run.returncode == 1, (name, run.stdout, run.stderr)
        assert 'FAIL' in run.stdout and not (root / 'verify.json').exists(), name
