"""Pure-math tests for the Ehlers/TradingView Fisher transform helper."""
import numpy as np
import pytest

from src.indicators.fisher import fisher_transform, latest_crossing


def test_first_eight_missing_then_neutral_initialised_state():
    highs = np.arange(10.0, 21.0)
    lows = highs - 1.0
    out = fisher_transform(highs, lows, length=9)
    assert np.isnan(out['value'][:8]).all()
    assert np.isnan(out['fisher'][:8]).all()
    assert np.isfinite(out['value'][8])
    assert np.isfinite(out['fisher'][8])
    # trigger at the first valid bar is the neutral initial previous fisher.
    assert out['trigger'][8] == 0.0


def test_flat_rolling_range_uses_neutral_input_without_infinity():
    highs = np.full(40, 100.0)
    lows = np.full(40, 100.0)
    out = fisher_transform(highs, lows, length=9)
    assert not np.isinf(out['fisher']).any()
    assert not np.isinf(out['value']).any()
    # (hl2-low)/(high-low) is undefined: the neutral normalised input is 0.
    assert out['value'][8] == pytest.approx(0.0)
    # A flat series stays at the neutral state instead of diverging.
    assert out['value'][-1] == pytest.approx(0.0)
    assert out['fisher'][-1] == pytest.approx(0.0)
    assert np.isfinite(out['fisher'][-1])


def test_steep_uptrend_clamps_at_upper_bound():
    highs = 100.0 * np.exp(0.5 * np.arange(20))
    lows = highs * 0.5
    out = fisher_transform(highs, lows, length=9)
    assert out['value'][-1] == pytest.approx(0.999)
    assert np.isfinite(out['fisher'][-1])


@pytest.mark.parametrize('fisher, expected', [
    ([0.0, -0.5, 0.2], 'up'),          # new upcross
    ([0.0, 0.5, -0.2], 'down'),        # new downcross
    ([0.0, 0.0, 0.2], 'up'),           # equality on previous two still crosses up
    ([0.0, 0.0, -0.2], 'down'),        # equality on previous two still crosses down
    ([-0.5, 0.0, 0.5], None),          # continuing uptrend is not a new cross
    ([0.5, 0.0, -0.5], None),          # continuing downtrend is not a new cross
    ([0.2, 0.2, 0.2], None),           # perfectly flat is neither
    ([np.nan, np.nan, 0.1], None),     # warmup is unknown, not no-cross
    ([0.0, -0.5], None),               # too short to judge
])
def test_latest_crossing_semantics(fisher, expected):
    assert latest_crossing(fisher) == expected


@pytest.mark.parametrize('bad', [
    (np.array([1.0]), np.array([1.0])),               # too short
    (np.array([1.0, 2.0]), np.array([1.0])),          # mismatched
    (np.array([1.0, np.nan, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0, 9.0]),
     np.array([1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0, 9.0])),  # non-finite
    (np.array([1.0] * 9), np.array([2.0] * 9)),       # high < low
    (np.array([0.0] * 9), np.array([0.0] * 9)),       # non-positive
])
def test_bad_ohlc_is_rejected(bad):
    highs, lows = bad
    with pytest.raises(ValueError):
        fisher_transform(highs, lows, length=9)
