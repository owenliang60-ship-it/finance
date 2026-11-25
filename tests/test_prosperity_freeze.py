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
