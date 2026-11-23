"""Tests for the dual-axis Extremity Z+S indicator (Python twin of extremity_v5_1.pine)."""
from __future__ import annotations

import numpy as np
import pytest

from src.indicators.extremity import RAIL_BARS, STD_EPS, compute_extremity, hysteresis_signals

FLOAT_KEYS = (
    "sigma", "z", "s", "mean_z", "std_up", "std_dn", "std_s", "u_dn", "u_up",
    "z_up_warn", "z_up_ext", "z_dn_warn", "z_dn_ext", "s_ext", "s_dip",
)
BOOL_KEYS = (
    "warm", "live", "panic_bottom", "deep_oversold", "warn_oversold", "climax_top",
    "slow_grind", "fake_dip", "enter_dn_warn", "exit_dn_warn", "enter_dn_ext",
    "exit_dn_ext", "exit_up_ext", "exit_up_warn",
)
GATED_KEYS = BOOL_KEYS[2:]  # 6 regime classes + 6 hysteresis alarms
WARM_FROM = 84 + 5


def _random_ohlc(n: int, seed: int = 11, vol: float = 0.015):
    rng = np.random.default_rng(seed)
    closes = 100.0 * np.exp(np.cumsum(rng.normal(0.0, vol, n)))
    prev = np.concatenate(([closes[0]], closes[:-1]))
    opens = prev * np.exp(rng.normal(0.0, vol / 3.0, n))
    wick = np.abs(rng.normal(0.0, vol / 2.0, (2, n)))
    highs = np.maximum(opens, closes) * np.exp(wick[0])
    lows = np.minimum(opens, closes) * np.exp(-wick[1])
    return opens, highs, lows, closes


# --------------------------------------------------------------------------- #
# Task 1: input validation, output contract, short series, flat (halted) series
# --------------------------------------------------------------------------- #

def _bad_ohlc_cases():
    o, h, l, c = _random_ohlc(10)
    cases = {
        "two_dimensional": (o.reshape(2, 5), h.reshape(2, 5), l.reshape(2, 5), c.reshape(2, 5)),
        "length_mismatch": (o, h, l, c[:-1]),
        "empty": ([], [], [], []),
        "nan_close": (o, h, l, np.where(np.arange(10) == 3, np.nan, c)),
        "inf_high": (o, np.where(np.arange(10) == 4, np.inf, h), l, c),
        "zero_low": (o, h, np.where(np.arange(10) == 5, 0.0, l), c),
        "negative_open": (np.where(np.arange(10) == 6, -1.0, o), h, l, c),
        "high_below_low": (o, l * 0.99, l, c),
        "non_numeric": (["a"] * 10, h, l, c),
    }
    return list(cases.items())


@pytest.mark.parametrize("name, ohlc", _bad_ohlc_cases(), ids=[k for k, _ in _bad_ohlc_cases()])
def test_bad_ohlc_is_rejected(name, ohlc):
    with pytest.raises(ValueError):
        compute_extremity(*ohlc, market="equity")


@pytest.mark.parametrize("market", ["stock", "EQUITY", "", None, 1, b"equity"])
def test_bad_market_is_rejected(market):
    with pytest.raises(ValueError):
        compute_extremity(*_random_ohlc(10), market=market)


@pytest.mark.parametrize("kwargs", [
    # bool masquerading as a number
    {"ema_len": True}, {"speed_len": True}, {"rail_bars": True}, {"half_life": True},
    {"prior_weight": False}, {"k_warn": True}, {"k_ext": True}, {"k_dip": True},
    {"hyst_buf": False},
    # wrong type
    {"ema_len": 84.0}, {"speed_len": "5"}, {"rail_bars": 504.0}, {"k_warn": "1.5"},
    {"half_life": None},
    # out of range / non-finite
    {"ema_len": 1}, {"speed_len": 0}, {"rail_bars": 0},
    {"half_life": 0.0}, {"half_life": -1.0}, {"half_life": float("nan")},
    {"half_life": float("inf")}, {"half_life": 1e20},  # 1e20 -> alpha == 0.0
    {"prior_weight": -1.0}, {"prior_weight": float("nan")},
    {"k_warn": 0.0}, {"k_warn": 2.0}, {"k_ext": 1.5}, {"k_ext": float("inf")},
    {"k_dip": 0.0}, {"k_dip": -1.5},
    {"hyst_buf": -0.1}, {"hyst_buf": 1.5}, {"hyst_buf": float("nan")},
])
def test_bad_parameters_are_rejected(kwargs):
    with pytest.raises(ValueError):
        compute_extremity(*_random_ohlc(10), market="equity", **kwargs)


@pytest.mark.parametrize("args", [
    ([], 1.5, 0.25),
    ([[1.0, 2.0]], 1.5, 0.25),
    (["x"], 1.5, 0.25),
    ([1.0], True, 0.25),
    ([1.0], 1.5, False),
    ([1.0], 0.0, 0.0),
    ([1.0], float("inf"), 0.25),
    ([1.0], 1.5, -0.1),
    ([1.0], 1.5, 1.5),
    ([1.0], 1.5, float("nan")),
    ([1.0], "1.5", 0.25),
])
def test_hysteresis_bad_arguments_are_rejected(args):
    with pytest.raises(ValueError):
        hysteresis_signals(*args)


def test_numpy_integer_parameters_are_accepted():
    out = compute_extremity(*_random_ohlc(120), market="crypto", ema_len=np.int64(20),
                            speed_len=np.int32(3), rail_bars=np.int64(30))
    assert out["warm"].any()


def test_open_slightly_outside_high_low_is_accepted():
    o, h, l, c = _random_ohlc(150)
    o = o.copy()
    o[50] = h[50] * 1.001
    o[60] = l[60] * 0.999
    out = compute_extremity(o, h, l, c, market="equity")
    assert np.isfinite(out["sigma"][1:]).all()


@pytest.mark.parametrize("market", ["equity", "crypto"])
def test_output_contract(market):
    n = 200
    out = compute_extremity(*_random_ohlc(n), market=market)
    assert set(out) == set(FLOAT_KEYS) | set(BOOL_KEYS)
    assert len(out) == 29
    for key in FLOAT_KEYS:
        assert out[key].dtype == np.float64 and out[key].shape == (n,), key
    for key in BOOL_KEYS:
        assert out[key].dtype == np.bool_ and out[key].shape == (n,), key


def test_default_rail_bars_and_eps_constants():
    assert RAIL_BARS == {"equity": 504, "crypto": 730}
    assert STD_EPS == 1e-9


@pytest.mark.parametrize("market", ["equity", "crypto"])
@pytest.mark.parametrize("n", [1, 2, WARM_FROM])
def test_short_series_is_all_warmup(market, n):
    out = compute_extremity(*_random_ohlc(n), market=market)
    for key in FLOAT_KEYS:
        if key != "sigma":
            assert np.isnan(out[key]).all(), key
    for key in BOOL_KEYS:
        assert not out[key].any(), key
    # The volatility ruler itself is not warm-gated: YZ needs one prior close, GK does not.
    if market == "equity":
        assert np.isnan(out["sigma"][0]) and np.isfinite(out["sigma"][1:]).all()
    else:
        assert np.isfinite(out["sigma"]).all()


@pytest.mark.parametrize("market", ["equity", "crypto"])
def test_flat_series_goes_warm_but_not_live_then_recovers(market):
    n_flat = 300
    flat = np.full(n_flat, 268.77)
    out = compute_extremity(flat, flat, flat, flat, market=market)

    assert not out["warm"][:WARM_FROM].any()
    assert out["warm"][WARM_FROM:].all()
    assert (out["z"][WARM_FROM:] == 0.0).all()
    assert (out["s"][WARM_FROM:] == 0.0).all()
    # The 200-bar prior keeps the rails open until the buffer holds 200 zeros.
    dead_from = WARM_FROM + 200
    assert out["live"][WARM_FROM:dead_from].all()
    assert not out["live"][dead_from:].any()
    assert np.isnan(out["u_dn"][dead_from:]).all()
    assert np.isnan(out["u_up"][dead_from:]).all()
    assert (out["std_dn"][dead_from:] == 0.0).all()
    for key in GATED_KEYS:
        assert not out[key].any(), key

    # Splice a volatile segment: the buffer keeps rolling while warm, so live recovers.
    o, h, l, c = _random_ohlc(150, seed=3)
    scale = 268.77 / o[0]
    spliced = [np.concatenate((flat, arr * scale)) for arr in (o, h, l, c)]
    out2 = compute_extremity(*spliced, market=market)
    assert not out2["live"][dead_from:n_flat + 1].any()
    assert out2["live"][n_flat + 1:].all()
    assert np.isfinite(out2["u_dn"][n_flat + 1:]).all()
    assert np.isfinite(out2["u_up"][n_flat + 1:]).all()
