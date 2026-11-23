"""Tests for the dual-axis Extremity Z+S indicator (Python twin of extremity_v5_1.pine)."""
from __future__ import annotations

import ast
import inspect
import math
import re
from pathlib import Path

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


@pytest.mark.parametrize("market", ["equity", "crypto"])
def test_sub_eps_micro_tick_during_halt_stays_not_live(market):
    """A 2e-15 relative tick gives rail deviations in (0, STD_EPS): still a dead rail."""
    px = np.full(320, 268.77)
    px[300:] = 268.77 * (1.0 + 2e-15)
    out = compute_extremity(px, px, px, px, market=market)
    tail = slice(301, None)
    assert (out["std_up"][tail] > 0.0).all() and (out["std_up"][tail] < STD_EPS).all()
    assert (out["std_s"][tail] > 0.0).all() and (out["std_s"][tail] < STD_EPS).all()
    assert not out["live"][WARM_FROM + 200:].any()
    for key in GATED_KEYS:
        assert not out[key][WARM_FROM + 200:].any(), key


# --------------------------------------------------------------------------- #
# Task 2: hysteresis edges, causal prefix invariance, Pine scalar parity, .pine sync
# --------------------------------------------------------------------------- #

PINE_PATH = Path(__file__).resolve().parents[1] / "src" / "indicators" / "extremity" / "extremity_v5_1.pine"
SRC_PATH = PINE_PATH.with_name("__init__.py")


def _regime_ohlc(n: int = 900, seed: int = 20261123):
    """Deterministic path: chop, slow grind, blow-off, sharp dip, crash, recovery, chop."""
    rng = np.random.default_rng(seed)
    segs = [  # (bars, daily drift, daily vol)
        (200, 0.0003, 0.012),   # normal chop / warm-up
        (130, 0.0025, 0.006),   # slow grind up
        (25, 0.012, 0.02),      # blow-off top
        (6, -0.03, 0.02),       # sharp dip near the highs
        (40, 0.002, 0.012),     # rebound
        (110, -0.006, 0.025),   # bear crash
        (20, -0.02, 0.03),      # capitulation
        (90, 0.004, 0.018),     # recovery
        (279, 0.0004, 0.013),   # normal; the 120-bar window slides old regimes out
    ]
    drift = np.concatenate([np.full(b, d) for b, d, _ in segs])[:n]
    vol = np.concatenate([np.full(b, v) for b, _, v in segs])[:n]
    closes = 100.0 * np.exp(np.cumsum(drift + vol * rng.standard_normal(n)))
    prev = np.concatenate(([100.0], closes[:-1]))
    opens = prev * np.exp(0.3 * vol * rng.standard_normal(n))
    wick = np.abs(rng.standard_normal((2, n))) * 0.5 * vol
    highs = np.maximum(opens, closes) * np.exp(wick[0])
    lows = np.minimum(opens, closes) * np.exp(-wick[1])
    return opens, highs, lows, closes


def test_hysteresis_enter_exit_boundaries():
    enter, exit_ = hysteresis_signals([1.4, 1.5, 1.3, 1.26, 1.25, 1.6], 1.5, 0.25)
    assert enter.dtype == np.bool_ and exit_.dtype == np.bool_
    assert enter.tolist() == [False, True, False, False, False, True]
    assert exit_.tolist() == [False, False, False, False, True, False]


def test_hysteresis_zero_buffer_exits_at_the_entry_level():
    enter, exit_ = hysteresis_signals([1.5, 1.5, 1.49, 1.5], 1.5, 0.0)
    assert enter.tolist() == [True, False, False, True]
    assert exit_.tolist() == [False, True, False, False]


def test_hysteresis_non_finite_gaps_hold_armed_state():
    nan, inf = float("nan"), float("inf")
    enter, exit_ = hysteresis_signals([inf, 1.6, nan, -inf, 1.3, 1.2, nan, 1.6], 1.5, 0.25)
    assert enter.tolist() == [False, True, False, False, False, False, False, True]
    assert exit_.tolist() == [False, False, False, False, False, True, False, False]


@pytest.mark.parametrize("market", ["equity", "crypto"])
def test_causal_prefix_invariance(market):
    ohlc = _regime_ohlc()
    full = compute_extremity(*ohlc, market=market, rail_bars=120)
    for k in (1, 50, WARM_FROM, WARM_FROM + 1, 150, 333, 600, 899):
        part = compute_extremity(*(arr[:k] for arr in ohlc), market=market, rail_bars=120)
        for key in FLOAT_KEYS + BOOL_KEYS:
            np.testing.assert_array_equal(part[key], full[key][:k], err_msg=f"{key} @ k={k}")


@pytest.mark.parametrize("market", ["equity", "crypto"])
def test_last_bar_price_cannot_move_its_own_rails(market):
    ohlc = _regime_ohlc()
    base = compute_extremity(*ohlc, market=market, rail_bars=120)
    bumped = [arr.copy() for arr in ohlc]
    for arr in bumped:
        arr[-1] *= 0.8
    moved = compute_extremity(*bumped, market=market, rail_bars=120)
    assert moved["z"][-1] != base["z"][-1]
    for key in ("mean_z", "std_up", "std_dn", "std_s", "z_up_warn", "z_up_ext",
                "z_dn_warn", "z_dn_ext", "s_ext", "s_dip"):
        assert moved[key][-1] == base[key][-1], key


def _pine_scalar_ref(opens, highs, lows, closes, *, market, rail_bars, ema_len=84, speed_len=5,
                     half_life=25.0, prior_weight=200.0, k_warn=1.5, k_ext=2.0, k_dip=1.5,
                     hyst_buf=0.25):
    """Bar-by-bar transliteration of extremity_v5_1.pine using only ``math`` scalars."""
    nan = float("nan")
    alpha = 1.0 - math.pow(0.5, 1.0 / half_life)
    n_eff = 2.0 / alpha - 1.0
    k_yz = 0.34 / (1.34 + (n_eff + 1.0) / max(n_eff - 1.0, 1.0))
    c_n = (ema_len - 1.0) / (2.0 * math.sqrt(ema_len))
    use_gk = market == "crypto"
    state = {}  # one Pine `var float acc` per ewma() call site

    def ewma(site, src, a):
        acc = state.get(site, nan)
        if not math.isnan(src):
            acc = src if math.isnan(acc) else acc + a * (src - acc)
        state[site] = acc
        return acc

    keys = FLOAT_KEYS + BOOL_KEYS
    out = {key: [] for key in keys}
    xs, sigmas, z_buf, s_buf = [], [], [], []
    armed = {"dn_warn": False, "dn_ext": False, "up_ext": False, "up_warn": False}
    for t in range(len(closes)):
        x = math.log(closes[t])
        o = math.log(opens[t])
        h = math.log(highs[t])
        l = math.log(lows[t])
        d_hl = h - l
        d_xo = x - o
        gk_raw = max(0.0, 0.5 * (d_hl * d_hl) - (2.0 * math.log(2.0) - 1.0) * (d_xo * d_xo))
        var_gk = ewma("gk", gk_raw, alpha)
        r_o = o - xs[t - 1] if t >= 1 else nan
        r_c = x - o
        mu_o = ewma("mu_o", r_o, alpha)
        mu_c = ewma("mu_c", r_c, alpha)
        d_o = r_o - mu_o
        d_c = r_c - mu_c
        var_o = ewma("var_o", d_o * d_o, alpha)
        var_c = ewma("var_c", d_c * d_c, alpha)
        rs_raw = max(0.0, (h - x) * (h - o) + (l - x) * (l - o))
        var_rs = ewma("rs", rs_raw, alpha)
        var_yz = var_o + k_yz * var_c + (1.0 - k_yz) * var_rs
        var_sel = var_gk if use_gk else var_yz
        sigma_m = nan if math.isnan(var_sel) else math.sqrt(max(var_sel, 1e-12))
        xs.append(x)
        sigmas.append(sigma_m)
        sigma_lag = sigmas[t - speed_len] if t >= speed_len else nan
        ema_x = ewma("ema", x, 2.0 / (ema_len + 1.0))
        if not math.isnan(ema_x) and not math.isnan(sigma_lag) and sigma_lag > 0:
            z_val = (x - ema_x) / (c_n * sigma_lag)
            s_val = (x - xs[t - speed_len]) / (sigma_lag * math.sqrt(speed_len))
        else:
            z_val = s_val = nan

        w_len = len(z_buf)
        m_len = float(w_len)
        eff_pri = max(0.0, prior_weight - m_len)
        sum_z = 0.0
        ss_s = 0.0
        for i in range(w_len):
            sum_z += z_buf[i]
            sv = s_buf[i]
            ss_s += sv * sv
        mean_z = sum_z / max(1.0, m_len + eff_pri)
        n_up = ss_up = n_dn = ss_dn = 0.0
        for i in range(w_len):
            dz = z_buf[i] - mean_z
            if dz >= 0.0:
                n_up += 1.0
                ss_up += dz * dz
            else:
                n_dn += 1.0
                ss_dn += dz * dz
        std_up = math.sqrt(max(0.0, ss_up + eff_pri * 0.5) / max(1.0, n_up + eff_pri * 0.5))
        std_dn = math.sqrt(max(0.0, ss_dn + eff_pri * 0.5) / max(1.0, n_dn + eff_pri * 0.5))
        std_s = math.sqrt(max(0.0, ss_s + eff_pri) / max(1.0, m_len + eff_pri))
        z_up_warn = mean_z + k_warn * std_up
        z_up_ext = mean_z + k_ext * std_up
        z_dn_warn = mean_z - k_warn * std_dn
        z_dn_ext = mean_z - k_ext * std_dn
        s_ext_dyn = k_ext * std_s
        s_dip_dyn = k_dip * std_s

        is_warm = t >= ema_len + speed_len and not math.isnan(z_val) and not math.isnan(s_val)
        rail_ok = std_up > 1e-9 and std_dn > 1e-9 and std_s > 1e-9
        is_live = is_warm and rail_ok
        u_dn = (mean_z - z_val) / std_dn if is_live else nan
        u_up = (z_val - mean_z) / std_up if is_live else nan

        is_panic = is_live and z_val <= z_dn_ext and s_val <= -s_ext_dyn
        is_deep = is_live and z_val <= z_dn_ext and s_val > -s_ext_dyn
        is_warn = is_live and z_val > z_dn_ext and z_val <= z_dn_warn
        is_climax = is_live and (z_val >= z_up_ext or (z_val >= z_up_warn and s_val >= s_ext_dyn))
        is_grind = is_live and z_val >= z_up_warn and not is_climax
        is_fake = is_live and z_val > (mean_z - 1.2 * std_dn) and s_val <= -s_dip_dyn

        sig = dict.fromkeys(("enter_dn_warn", "exit_dn_warn", "enter_dn_ext", "exit_dn_ext",
                             "exit_up_ext", "exit_up_warn"), False)
        if is_live:
            if not armed["dn_warn"] and u_dn >= k_warn:
                armed["dn_warn"] = True
                sig["enter_dn_warn"] = True
            elif armed["dn_warn"] and u_dn <= (k_warn - hyst_buf):
                armed["dn_warn"] = False
                sig["exit_dn_warn"] = True
            if not armed["dn_ext"] and u_dn >= k_ext:
                armed["dn_ext"] = True
                sig["enter_dn_ext"] = True
            elif armed["dn_ext"] and u_dn <= (k_ext - hyst_buf):
                armed["dn_ext"] = False
                sig["exit_dn_ext"] = True
            if not armed["up_ext"] and u_up >= k_ext:
                armed["up_ext"] = True
            elif armed["up_ext"] and u_up <= (k_ext - hyst_buf):
                armed["up_ext"] = False
                sig["exit_up_ext"] = True
            if not armed["up_warn"] and u_up >= k_warn:
                armed["up_warn"] = True
            elif armed["up_warn"] and u_up <= (k_warn - hyst_buf):
                armed["up_warn"] = False
                sig["exit_up_warn"] = True

        if is_warm:
            z_buf.append(max(-6.0, min(6.0, z_val)))
            s_buf.append(max(-6.0, min(6.0, s_val)))
            while len(z_buf) > rail_bars:
                z_buf.pop(0)
                s_buf.pop(0)

        plotted = {  # Pine plots rails / Z only while warm
            "z": z_val, "s": s_val, "mean_z": mean_z, "std_up": std_up, "std_dn": std_dn,
            "std_s": std_s, "z_up_warn": z_up_warn, "z_up_ext": z_up_ext,
            "z_dn_warn": z_dn_warn, "z_dn_ext": z_dn_ext, "s_ext": s_ext_dyn, "s_dip": s_dip_dyn,
        }
        row = {key: (val if is_warm else nan) for key, val in plotted.items()}
        row.update(sigma=sigma_m, u_dn=u_dn, u_up=u_up, warm=is_warm, live=is_live,
                   panic_bottom=is_panic, deep_oversold=is_deep, warn_oversold=is_warn,
                   climax_top=is_climax, slow_grind=is_grind, fake_dip=is_fake, **sig)
        for key in keys:
            out[key].append(row[key])
    return {key: np.array(out[key], dtype=bool if key in BOOL_KEYS else np.float64) for key in keys}


@pytest.mark.parametrize("cfg", [
    {"market": "equity", "rail_bars": 120},
    {"market": "crypto", "rail_bars": 120},
    {"market": "equity", "rail_bars": 30, "prior_weight": 0.0},
    {"market": "crypto", "rail_bars": 30, "prior_weight": 0.0},
], ids=["equity-120", "crypto-120", "equity-30-noprior", "crypto-30-noprior"])
def test_matches_pine_scalar_reference(cfg):
    ohlc = _regime_ohlc()
    got = compute_extremity(*ohlc, **cfg)
    ref = _pine_scalar_ref(*(arr.tolist() for arr in ohlc), **cfg)
    for key in ("z", "s", "u_dn", "u_up"):
        np.testing.assert_allclose(got[key], ref[key], rtol=1e-12, atol=1e-12, equal_nan=True,
                                   err_msg=key)
    for key in set(FLOAT_KEYS) - {"z", "s", "u_dn", "u_up"}:
        np.testing.assert_allclose(got[key], ref[key], rtol=0, atol=1e-12, equal_nan=True,
                                   err_msg=key)
    for key in BOOL_KEYS:
        np.testing.assert_array_equal(got[key], ref[key], err_msg=key)
    for key in GATED_KEYS:
        assert got[key].sum() >= 1, f"{key} never fires on the synthetic path"


@pytest.mark.parametrize("market", ["equity", "crypto"])
def test_halt_then_resume_matches_pine_scalar_reference(market):
    """Flat run (D2 dead rails) then a restart whose first |Z| is in the thousands (clamp path)."""
    flat = np.full(300, 268.77)
    o, h, l, c = _random_ohlc(150, seed=3)
    scale = 268.77 / o[0]
    ohlc = [np.concatenate((flat, arr * scale)) for arr in (o, h, l, c)]
    got = compute_extremity(*ohlc, market=market)
    ref = _pine_scalar_ref(*(arr.tolist() for arr in ohlc), market=market,
                           rail_bars=RAIL_BARS[market])
    assert np.nanmax(np.abs(got["z"])) > 6.0  # the buffer clamp is exercised
    for key in FLOAT_KEYS:
        np.testing.assert_allclose(got[key], ref[key], rtol=1e-12, atol=1e-12, equal_nan=True,
                                   err_msg=key)
    for key in BOOL_KEYS:
        np.testing.assert_array_equal(got[key], ref[key], err_msg=key)


def test_pine_defaults_match_python_defaults():
    pine = PINE_PATH.read_text(encoding="utf-8")

    def pine_default(name):
        m = re.search(rf"^{name}\s*=\s*input\.(?:int|float)\(\s*([0-9.]+)", pine, re.MULTILINE)
        assert m, f"{name} input not found in {PINE_PATH.name}"
        return float(m.group(1))

    params = inspect.signature(compute_extremity).parameters
    for pine_name, py_name in (("emaLen", "ema_len"), ("speedLen", "speed_len"),
                               ("halfLife", "half_life"), ("priorWeight", "prior_weight"),
                               ("kWarn", "k_warn"), ("kExt", "k_ext"), ("kDip", "k_dip"),
                               ("hystBuf", "hyst_buf")):
        assert pine_default(pine_name) == params[py_name].default, pine_name
    rail_years = pine_default("railYears")
    assert params["rail_bars"].default is None
    assert RAIL_BARS == {"equity": round(rail_years * 252), "crypto": round(rail_years * 365)}
    # D1 / D2 hardening is present in the Pine twin.
    assert "ta.ema(" not in pine and "math.pow(h" not in pine
    assert "railOk = stdUp > 1e-9 and stdDn > 1e-9 and stdS > 1e-9" in pine


def test_python_source_parses_as_python_3_10():
    ast.parse(SRC_PATH.read_text(encoding="utf-8"), feature_version=(3, 10))
