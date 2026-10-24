import pytest


def test_calendar_and_forced_week():
    from terminal.prosperity.freeze import candidate_saturdays, freeze_decision, quarter_of
    assert tuple(str(x) for x in quarter_of('2026Q2')) == ('2026-04-01', '2026-06-30')
    assert candidate_saturdays('2026Q2') == ('2026-08-29', '2026-09-05', '2026-09-12', '2026-09-19')
    assert candidate_saturdays('2026Q3') == ('2026-12-05', '2026-12-12', '2026-12-19')
    assert freeze_decision('2026-11-28', '2026Q3', .99) is None
    assert freeze_decision('2026-12-05', '2026Q3', .94) is None
    assert freeze_decision('2026-12-05', '2026Q3', .95) == 'coverage'
    assert freeze_decision('2026-09-19', '2026Q2', .40) == 'forced'
    assert candidate_saturdays('2025Q4')[-1] == '2026-03-21'


def test_due_quarter_has_explicit_origin_and_skips_frozen():
    from terminal.prosperity.freeze import due_quarter, quarter_labels
    frozen = set(quarter_labels('2021Q2', '2026Q2'))
    assert due_quarter('2026-10-24', frozen) is None
    assert due_quarter('2026-12-05', frozen) == '2026Q3'
    assert due_quarter('2026-12-05', frozen | {'2026Q3'}) is None
    assert due_quarter('2026-12-05', frozen - {'2022Q1'}) == '2022Q1'


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
            freeze_decision('2026-12-05', '2026Q3', coverage)
    with pytest.raises(ValueError):
        freeze_decision('2026-12-04', '2026Q3', .95)
