"""Numeric kernel v2: finite-number guards, segment revenue and the net-margin live state (Task 1)."""
import pytest

from terminal.valuation import kernel as k


def test_fin_rejects_non_numbers_and_non_finite():
    assert k.fin(True) is None
    assert k.fin(False) is None
    assert k.fin("3") is None
    assert k.fin(None) is None
    assert k.fin(float("nan")) is None
    assert k.fin(float("inf")) is None
    assert k.fin(float("-inf")) is None
    assert k.fin(0) == 0.0
    assert k.fin(-2) == -2.0


def test_zone():
    assert k.zone(6400e6 * 1.2, 6400e6, 0.104) == "tail"
    assert k.zone(6400e6 * 1.05, 6400e6, 0.104) == "mild"
    assert k.zone(6400e6, 6400e6, None) is None
    assert k.zone(6400e6, 0, 0.1) is None
    assert k.zone(6400e6, 6400e6, 0) is None


def test_pe_and_value_reject_non_positive_eps():
    assert k.implied_pe(80, 0) is None
    assert k.implied_pe(80, -1.0) is None
    assert k.implied_pe(0, 10.0) is None
    assert k.implied_pe(80, 10.0) == 8.0
    assert k.scenario_value(8, -1.0) is None
    assert k.scenario_value(0, 10.0) is None
    assert k.scenario_value(8, 10.0) == 80.0


def test_to_usd_and_pct_diff():
    assert k.to_usd(64.0, 32.0) == 2.0
    assert k.to_usd(64.0, 0) is None
    assert k.to_usd(None, 32.0) is None
    assert k.pct_diff(88.0, 80.0) == 88.0 / 80.0 - 1
    assert k.pct_diff(88.0, 0) is None


def test_pct_rank():
    assert k.pct_rank([], 1.0) is None
    assert k.pct_rank([1, 2, 3, 4], 2) == 0.5
    assert k.pct_rank([1, 2, 3, 4], float("nan")) is None
    assert k.pct_rank([1, None, 3], 2) is None


def test_weighted_value():
    assert k.weighted_value([100, 50], [60, 40]) == 80.0
    assert k.weighted_value([100, 50], [60, 30]) is None
    assert k.weighted_value([None, 50], [0, 100]) == 50.0
    assert k.weighted_value([None, 50], [10, 90]) is None
    assert k.weighted_value([], []) is None
    assert k.weighted_value([100], [100, 0]) is None
    assert k.weighted_value([100, 50], [-10, 110]) is None


def test_seg_revenue():
    assert k.seg_revenue([3840e6, 2560e6], [0.10, 0.05], 0.0) == 6912e6
    assert k.seg_revenue([3840e6, 2560e6], [0.0, 0.0], 150e6) == 6400e6 + 150e6
    assert k.seg_revenue([3840e6, 2560e6], [0.10], 0.0) is None
    assert k.seg_revenue([], [], 0.0) is None
    assert k.seg_revenue([3840e6, float("nan")], [0.1, 0.0], 0.0) is None
    assert k.seg_revenue([3840e6], [0.1], None) is None
    assert k.seg_revenue(None, [0.1], 0.0) is None


def test_implied_margin():
    assert k.implied_margin(10.0, 100e6, 6400e6) == 0.15625
    assert k.implied_margin(10.0, 100e6, 0) is None
    assert k.implied_margin(10.0, 0, 6400e6) is None
    assert k.implied_margin(None, 100e6, 6400e6) is None


def test_margin_eps():
    assert k.margin_eps(6912e6, 0.18, 100e6) == pytest.approx(12.4416, rel=1e-15)
    neg = k.margin_eps(4e9, -0.5642, 1e9)
    assert neg is not None and neg < 0
    assert k.margin_eps(4e9, 0.2, 0) is None
    assert k.margin_eps(4e9, float("inf"), 1e9) is None


def test_quarter_path():
    assert k.quarter_path([1, 1, 2], 8.0) == [2.0, 2.0, 4.0]
    assert k.quarter_path([0, 0, 0], 8.0) is None
    assert k.quarter_path([1, -1, 2], 8.0) is None
    assert k.quarter_path([1, 1], None) is None
    assert k.quarter_path([], 8.0) is None


def test_grid_pos():
    levels = [6, 7, 8, 9, 10]
    assert k.grid_pos(levels, 8.5) == 2.5
    assert k.grid_pos(levels, 3) == 0.0
    assert k.grid_pos(levels, 99) == 4.0
    assert k.grid_pos(levels, 10) == 4.0
    assert k.grid_pos([6], 6) is None
    assert k.grid_pos([6, 6, 7], 6) is None
    assert k.grid_pos([7, 6], 6.5) is None
    assert k.grid_pos(levels, None) is None


LIVE = {"bases": [3840e6, 2560e6], "adjs": [0.0, 0.0], "driver_delta": 0.0, "margin": 0.15625,
        "shares": 100e6, "fx": 1.0, "pe": 8.0, "pe_half": 0.0, "price": 80.0, "rev_cons": 6400e6,
        "half_width": 0.1, "eps_mode": "margin", "direct_eps": None}
LIVE_KEYS = {"rev", "net_income", "eps_local", "eps_usd", "eps_state", "implied_pe", "value", "value_lo",
             "value_hi", "value_vs_price", "zone"}


def test_live_state_initial_equals_price():
    out = k.live_state(LIVE)
    assert set(out) == LIVE_KEYS
    assert out["rev"] == 6400e6 and out["net_income"] == 1000e6 and out["eps_local"] == 10.0
    assert abs(out["value"] / 80.0 - 1) <= 1e-12
    assert abs(out["value_vs_price"]) <= 1e-12
    assert out["eps_state"] == "pos" and out["zone"] == "mild"
    assert out["value_lo"] == out["value_hi"] == out["value"]


def test_live_state_band_and_tail_keep_numbers():
    out = k.live_state({**LIVE, "adjs": [-0.6, -0.6], "pe_half": 1.0})
    assert out["zone"] == "tail"
    assert out["value"] == pytest.approx(8.0 * 4.0) and out["value_lo"] == pytest.approx(7.0 * 4.0)
    assert out["value_hi"] == pytest.approx(9.0 * 4.0)


def test_live_state_negative_margin():
    out = k.live_state({**LIVE, "margin": -0.5642})
    assert out["eps_state"] == "nonpos"
    assert out["value"] is None and out["value_lo"] is None and out["implied_pe"] is None
    for key in ("rev", "net_income", "eps_local", "eps_usd"):
        assert k.fin(out[key]) is not None, key


def test_live_state_direct_mode():
    out = k.live_state({**LIVE, "eps_mode": "direct", "direct_eps": 12.25, "margin": None})
    assert out["net_income"] is None
    assert out["eps_local"] == 12.25 and out["eps_usd"] == 12.25
    assert out["value"] == 8.0 * 12.25


def test_live_state_missing_inputs():
    no_fx = k.live_state({**LIVE, "fx": None})
    assert no_fx["eps_usd"] is None and no_fx["eps_state"] == "missing" and no_fx["value"] is None
    wide = k.live_state({**LIVE, "pe_half": 9.0})
    assert wide["value_lo"] is None and wide["value_hi"] == pytest.approx(17.0 * 10.0)
    bad = k.live_state({**LIVE, "adjs": [0.0]})
    assert bad["rev"] is None and bad["eps_local"] is None and bad["eps_state"] == "missing"
