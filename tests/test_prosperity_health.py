"""M12 health report: prices, per-board evaluation, structure, controls."""
import pytest

from tests.prosperity_fixtures import weekdays

CAL = weekdays('2027-12-31', 600)


def book(closes, splits=None):
    from backtest.research.prosperity_health import PriceBook
    return PriceBook(closes=closes, splits=splits or {}, calendar=CAL)


def rising(base=10.0):
    return [(d, base + k) for k, d in enumerate(CAL)]


def test_start_is_first_trading_day_after_the_freeze_date():
    b = book({'A': rising()})
    assert CAL[b.start_index('2026-09-27T00:00:00Z')] == '2026-09-28'
    assert CAL[b.start_index('2026-09-26T08:30:00Z')] == '2026-09-28'
    assert b.start_index(CAL[-1] + 'T08:30:00Z') is None


def test_window_return_and_unknown_reasons():
    s = CAL.index('2026-09-28')
    e = s + 63
    full = rising()
    gap = [p for p in full if not (CAL[e - 10] <= p[0] <= CAL[e])]
    b = book({'A': full, 'B': full[:e - 3], 'C': gap, 'D': full[s + 1:], 'E': []})
    ret, why = b.window_return('A', s, e)
    assert why == 'ok' and ret == pytest.approx(full[e][1] / full[s][1] - 1)
    assert b.window_return('B', s, e) == (None, 'ended_early')
    assert b.window_return('C', s, e) == (None, 'price_gap')
    assert b.window_return('D', s, e) == (None, 'no_start_price')
    assert b.window_return('E', s, e) == (None, 'no_price')
    assert b.window_return('ZZZ', s, e) == (None, 'no_price')


def test_recorded_split_matched_within_five_days_small_factor_left_alone():
    k = [(d, 100.0) for d in CAL[:250]] + [(d, 10.0) for d in CAL[250:]]
    g = [(d, 50.0) for d in CAL]
    b = book({'K': k, 'G': g}, {'K': [(CAL[254], 10.0)], 'G': [(CAL[100], 1.253)]})
    assert b.window_return('K', 240, 260) == (pytest.approx(0.0), 'ok')
    assert b.window_return('G', 90, 110) == (pytest.approx(0.0), 'ok')
    assert b.adjustments == (('K', CAL[250], 10.0),)
    assert [e[3] for e in b.split_events] == ['already_adjusted', 'adjusted']


def test_reverse_split_and_already_adjusted_series():
    r = [(d, 10.0) for d in CAL[:250]] + [(d, 30.0) for d in CAL[250:]]
    flat = [(d, 10.0) for d in CAL]
    b = book({'R': r, 'N': flat}, {'R': [(CAL[247], 1 / 3)], 'N': [(CAL[200], 10.0)]})
    assert b.window_return('R', 240, 260) == (pytest.approx(0.0), 'ok')
    assert b.window_return('N', 190, 210) == (pytest.approx(0.0), 'ok')
    assert b.adjustments == (('R', CAL[250], pytest.approx(1 / 3)),)


def test_five_for_four_split_is_adjusted():
    """Review P1 case 2: a 1.25 record with a matching 100→80 step; true price return is 0."""
    p = [(d, 100.0) for d in CAL[:250]] + [(d, 80.0) for d in CAL[250:]]
    b = book({'P': p}, {'P': [(CAL[250], 1.25)]})
    assert b.window_return('P', 240, 260) == (pytest.approx(0.0), 'ok')
    assert b.adjustments == (('P', CAL[250], 1.25),)


def test_unmatched_or_ambiguous_records_are_unresolved_not_assumed_adjusted():
    """Review P1 case 1: a 2:1 record with 100→40 is a real break, never 'already adjusted'."""
    two = [(d, 100.0) for d in CAL[:250]] + [(d, 40.0) for d in CAL[250:]]
    multi = ([(d, 100.0) for d in CAL[:248]] + [(d, 50.0) for d in CAL[248:249]]
             + [(d, 100.0) for d in CAL[249:251]] + [(d, 50.0) for d in CAL[251:]])
    small = [(d, 100.0) for d in CAL[:250]] + [(d, 90.0) for d in CAL[250:]]
    b = book({'T': two, 'M': multi, 'S': small},
             {'T': [(CAL[250], 2.0)], 'M': [(CAL[250], 2.0)], 'S': [(CAL[250], 1.05)]})
    assert b.adjustments == ()
    assert [e[3] for e in b.split_events] == ['unresolved'] * 3
    for s in ('T', 'M', 'S'):
        assert b.window_return(s, 240, 260) == (None, 'split_unresolved')
    assert b.window_return('T', 200, 240)[1] == 'ok'            # window ends before the event window
    assert b.window_return('T', 256, 300) == (pytest.approx(0.0), 'ok')   # starts after it


def test_unexplained_split_like_jump_is_unknown_and_large_moves_are_listed():
    q = [(d, 60.0) for d in CAL[:250]] + [(d, 12.0) for d in CAL[250:]]
    v = [(d, 100.0) for d in CAL[:250]] + [(d, 40.0) for d in CAL[250:]]
    b = book({'Q': q, 'V': v})
    assert b.window_return('Q', 240, 260) == (None, 'split_suspect')
    assert b.window_return('Q', 251, 270)[1] == 'ok'
    assert b.window_return('V', 240, 260) == (pytest.approx(-0.6), 'ok')
    assert b.jumps('V', 240, 260) == [{'date': CAL[250], 'ratio': pytest.approx(0.4), 'kind': 'large_move'}]
