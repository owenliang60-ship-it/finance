"""Numeric kernel: finite-number guards and the scenario state (Task 1)."""
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


def test_bridge_eps():
    assert k.bridge_eps(6500e6, 6400e6, 10.0, 0.5, 100e6) == 10.5
    assert k.bridge_eps(6500e6, 6400e6, 10.0, 0.5, 0) is None
    assert k.bridge_eps(6500e6, 6400e6, 10.0, 0.5, -1.0) is None
    assert k.bridge_eps(None, 6400e6, 10.0, 0.5, 100e6) is None


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


BASE = {"rev": 6400e6, "rev_cons": 6400e6, "eps_cons": 10.0, "margin": 0.5, "shares": 100e6,
        "half_width": 0.1, "fx": 1.0, "price": 80.0, "pe": 8.0, "driver_delta": 0.0,
        "eps_mode": "bridge", "direct_eps": None}


def test_scenario_state_initial_equals_price():
    out = k.scenario_state(BASE)
    assert out["value"] == 80.0
    assert out["value_vs_price"] == 0.0
    assert out["eps_source"] == "bridge"
    assert out["zone"] == "mild"
    assert set(out) == {"rev_eff", "zone", "eps_local", "eps_source", "eps_usd",
                        "implied_pe", "value", "value_vs_price"}


def test_scenario_state_direct_negative_eps():
    out = k.scenario_state({**BASE, "eps_mode": "direct", "direct_eps": -129.0})
    assert out["eps_usd"] == -129.0
    assert out["eps_source"] == "direct"
    assert out["implied_pe"] is None
    assert out["value"] is None
    assert out["value_vs_price"] is None


def test_scenario_state_blocked_paths():
    tail = k.scenario_state({**BASE, "rev": 6400e6 * 1.2})
    assert tail["eps_source"] == "blocked_tail" and tail["eps_local"] is None and tail["value"] is None
    unknown = k.scenario_state({**BASE, "half_width": None})
    assert unknown["eps_source"] == "blocked_zone_unknown" and unknown["value"] is None
    no_shares = k.scenario_state({**BASE, "shares": 0})
    assert no_shares["eps_source"] == "blocked_input" and no_shares["value"] is None
    no_direct = k.scenario_state({**BASE, "eps_mode": "direct", "direct_eps": float("nan")})
    assert no_direct["eps_source"] == "blocked_input" and no_direct["eps_local"] is None
    no_delta = k.scenario_state({**BASE, "driver_delta": None})
    assert no_delta["rev_eff"] is None and no_delta["eps_source"] == "blocked_zone_unknown"
