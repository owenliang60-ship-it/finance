"""
Tests for Options Scenario Analyzer.
"""
import math
import pytest
from terminal.options.scenario_analyzer import (
    build_strategy,
    price_strategy,
    generate_scenario_matrix,
    compute_probability_summary,
    skew_to_alpha,
    _compute_sigma_move,
    _adaptive_price_points,
    _adaptive_time_points,
    _lognormal_pdf,
    _adjusted_pdf,
)
from terminal.options.iv_solver import bs_price


# ── Fixtures ──

def _long_call(strike=200, dte=30, iv=0.35, entry=5.0, contracts=1):
    return {
        "side": "call", "direction": "long",
        "strike": strike, "expiry_dte": dte, "iv": iv,
        "entry_price": entry, "contracts": contracts,
    }


def _short_call(strike=210, dte=30, iv=0.35, entry=2.0, contracts=1):
    return {
        "side": "call", "direction": "short",
        "strike": strike, "expiry_dte": dte, "iv": iv,
        "entry_price": entry, "contracts": contracts,
    }


def _bull_call_spread():
    """Bull call spread: buy $200C, sell $210C, 30 DTE."""
    return build_strategy([
        _long_call(200, 30, 0.35, 5.0),
        _short_call(210, 30, 0.35, 2.0),
    ])


class TestBuildStrategy:
    def test_valid_two_legs(self):
        legs = _bull_call_spread()
        assert len(legs) == 2
        assert legs[0]["direction_sign"] == 1
        assert legs[1]["direction_sign"] == -1

    def test_invalid_side(self):
        with pytest.raises(ValueError, match="side"):
            build_strategy([{"side": "xxx", "direction": "long",
                             "strike": 100, "expiry_dte": 30, "iv": 0.3,
                             "entry_price": 5}])

    def test_invalid_direction(self):
        with pytest.raises(ValueError, match="direction"):
            build_strategy([{"side": "call", "direction": "xxx",
                             "strike": 100, "expiry_dte": 30, "iv": 0.3,
                             "entry_price": 5}])

    def test_negative_strike(self):
        with pytest.raises(ValueError, match="invalid"):
            build_strategy([{"side": "call", "direction": "long",
                             "strike": -100, "expiry_dte": 30, "iv": 0.3,
                             "entry_price": 5}])

    def test_default_contracts(self):
        legs = build_strategy([_long_call()])
        assert legs[0]["contracts"] == 1

    def test_empty_strategy_rejected(self):
        with pytest.raises(ValueError, match="at least one leg"):
            build_strategy([])

    def test_zero_contracts_rejected(self):
        with pytest.raises(ValueError, match="invalid"):
            build_strategy([{"side": "call", "direction": "long",
                             "strike": 100, "expiry_dte": 30, "iv": 0.3,
                             "entry_price": 5, "contracts": 0}])

    def test_negative_contracts_rejected(self):
        with pytest.raises(ValueError, match="invalid"):
            build_strategy([{"side": "call", "direction": "long",
                             "strike": 100, "expiry_dte": 30, "iv": 0.3,
                             "entry_price": 5, "contracts": -1}])


class TestPriceStrategy:
    """Test strategy repricing at various scenarios."""

    def test_entry_day_pnl_zero(self):
        """At entry (day 0), P&L should be approximately 0."""
        # Use BS to generate consistent entry prices
        S = 200.0
        r = 0.045
        K1, K2 = 200.0, 210.0
        T = 30 / 365.0
        sigma = 0.35

        p1 = bs_price(S, K1, T, r, sigma, "call")
        p2 = bs_price(S, K2, T, r, sigma, "call")

        legs = build_strategy([
            _long_call(K1, 30, sigma, p1),
            _short_call(K2, 30, sigma, p2),
        ])

        pnl = price_strategy(legs, S, 0, r)
        assert abs(pnl) < 0.50  # near zero (rounding)

    def test_stock_up_at_expiry_profit(self):
        """Bull call spread should profit if stock above upper strike at expiry."""
        legs = _bull_call_spread()
        # At expiry (day 30), stock at $220 (above both strikes)
        pnl = price_strategy(legs, 220.0, 30, 0.045)
        # Max profit = (210 - 200 - net_debit) × 100 = (10 - 3) × 100 = $700
        assert abs(pnl - 700.0) < 1.0

    def test_stock_down_at_expiry_loss(self):
        """Bull call spread max loss = net debit if stock below lower strike."""
        legs = _bull_call_spread()
        pnl = price_strategy(legs, 180.0, 30, 0.045)
        # Max loss = net debit × 100 = 3.0 × 100 = -$300
        assert abs(pnl - (-300.0)) < 1.0

    def test_mid_life_has_time_value(self):
        """At +7d, P&L should be between max profit and max loss."""
        legs = _bull_call_spread()
        pnl_up = price_strategy(legs, 215.0, 7, 0.045)
        pnl_dn = price_strategy(legs, 185.0, 7, 0.045)
        # Not at extremes yet
        assert pnl_up < 700.0
        assert pnl_dn > -300.0

    def test_iv_shift_affects_price(self):
        """IV shift should change mid-life P&L."""
        legs = _bull_call_spread()
        pnl_base = price_strategy(legs, 200.0, 7, 0.045, iv_shift=0.0)
        pnl_up_iv = price_strategy(legs, 200.0, 7, 0.045, iv_shift=0.20)
        # Bull call spread has reduced vega, but some difference expected
        assert pnl_base != pnl_up_iv

    def test_single_long_call(self):
        """Single long call: profit if stock rises significantly."""
        legs = build_strategy([_long_call(200, 30, 0.35, 5.0)])
        pnl = price_strategy(legs, 220.0, 30, 0.045)
        # At expiry: (220 - 200 - 5) × 100 = $1500
        assert abs(pnl - 1500.0) < 1.0


class TestAdaptiveAxes:
    def test_sigma_move_scales_with_iv(self):
        """Higher IV → larger sigma move."""
        low = _compute_sigma_move(0.20, 30)
        high = _compute_sigma_move(0.60, 30)
        assert high > low

    def test_sigma_move_scales_with_dte(self):
        """Longer DTE → larger sigma move."""
        short = _compute_sigma_move(0.30, 14)
        long = _compute_sigma_move(0.30, 60)
        assert long > short

    def test_price_points_centered(self):
        """Price points should be symmetric around S."""
        points = _adaptive_price_points(100.0, 0.10)
        assert len(points) == 7
        # Middle point should be S
        assert abs(points[3] - 100.0) < 0.01

    def test_price_points_wider_for_high_iv(self):
        points_low = _adaptive_price_points(100.0, 0.05)
        points_high = _adaptive_price_points(100.0, 0.20)
        range_low = points_low[-1] - points_low[0]
        range_high = points_high[-1] - points_high[0]
        assert range_high > range_low

    def test_time_points_short_dte(self):
        """Short DTE should have fewer time points."""
        points = _adaptive_time_points(7)
        assert points == [0, 7]

    def test_time_points_medium_dte(self):
        points = _adaptive_time_points(30)
        assert 0 in points
        assert 30 in points
        assert 7 in points

    def test_time_points_long_dte(self):
        points = _adaptive_time_points(60)
        assert 0 in points
        assert 60 in points
        assert 21 in points


class TestProbabilityDistribution:
    def test_lognormal_pdf_integrates_to_one(self):
        """Lognormal PDF should integrate to ~1 over wide range."""
        S, sigma, T, r = 100.0, 0.30, 0.25, 0.05
        mu = r - 0.5 * sigma * sigma

        total = 0.0
        lo, hi = 20.0, 300.0
        n = 1000
        dS = (hi - lo) / n
        for i in range(n):
            S_i = lo + (i + 0.5) * dS
            total += _lognormal_pdf(S_i, S, mu, sigma, T) * dS

        assert abs(total - 1.0) < 0.02

    def test_adjusted_pdf_no_skew_equals_base(self):
        """With α=0, adjusted PDF = base PDF."""
        S, sigma, T, r = 100.0, 0.30, 0.25, 0.05
        mu = r - 0.5 * sigma * sigma
        S_t = 105.0

        base = _lognormal_pdf(S_t, S, mu, sigma, T)
        adjusted = _adjusted_pdf(S_t, S, mu, sigma, T, 0.0)
        assert abs(base - adjusted) < 1e-10

    def test_left_skew_increases_extreme_downside(self):
        """Negative α increases PDF weight in extreme downside (|z| > √3)."""
        S, sigma, T, r = 100.0, 0.30, 0.25, 0.05
        mu = r - 0.5 * sigma * sigma
        S_down = 70.0  # z ≈ -2.4, past √3 crossover

        base = _lognormal_pdf(S_down, S, mu, sigma, T)
        skewed = _adjusted_pdf(S_down, S, mu, sigma, T, -0.3)
        assert skewed > base

    def test_left_skew_decreases_extreme_upside(self):
        """Negative α decreases PDF weight in extreme upside (|z| > √3)."""
        S, sigma, T, r = 100.0, 0.30, 0.25, 0.05
        mu = r - 0.5 * sigma * sigma
        S_up = 135.0  # z ≈ 2.0, past √3 crossover

        base = _lognormal_pdf(S_up, S, mu, sigma, T)
        skewed = _adjusted_pdf(S_up, S, mu, sigma, T, -0.3)
        assert skewed < base


class TestProbabilitySummary:
    def test_long_call_has_positive_expected(self):
        """Long ATM call in risk-neutral world: E[P&L] ≈ 0 (before cost of carry)."""
        S = 100.0
        r = 0.045
        sigma = 0.30
        T = 30 / 365.0
        entry = bs_price(S, 100, T, r, sigma, "call")

        legs = build_strategy([_long_call(100, 30, sigma, entry)])
        result = compute_probability_summary(legs, S, r, sigma, 30)

        # In risk-neutral world, E[P&L] should be near 0
        assert abs(result["expected_pnl"]) < 50.0  # within $50

    def test_win_probability_between_0_and_1(self):
        legs = _bull_call_spread()
        result = compute_probability_summary(legs, 200.0, 0.045, 0.35, 15)
        assert 0.0 <= result["win_probability"] <= 1.0

    def test_percentiles_ordered(self):
        legs = _bull_call_spread()
        result = compute_probability_summary(legs, 200.0, 0.045, 0.35, 15)
        assert result["pctl_25"] <= result["median_pnl"] <= result["pctl_75"]

    def test_pctl_75_not_collapsed_to_median(self):
        """Regression: pctl_75 must capture FIRST crossing, not last-below."""
        S = 200.0
        r = 0.045
        sigma = 0.35
        T = 30 / 365.0
        entry = bs_price(S, 200, T, r, sigma, "call")
        legs = build_strategy([_long_call(200, 30, sigma, entry)])
        result = compute_probability_summary(legs, S, r, sigma, 15)
        # 75th percentile should be strictly above median for a long call
        assert result["pctl_75"] > result["median_pnl"]

    def test_skew_changes_expected_for_long_call(self):
        """Left skew should change expected P&L for long call (vs no skew)."""
        S = 200.0
        r = 0.045
        sigma = 0.35
        legs = build_strategy([_long_call(200, 30, sigma, 5.0)])

        base = compute_probability_summary(legs, S, r, sigma, 15, skew_alpha=0.0)
        skewed = compute_probability_summary(legs, S, r, sigma, 15, skew_alpha=-0.3)
        # Skew adjustment should produce a different expected P&L
        assert base["expected_pnl"] != skewed["expected_pnl"]


class TestGenerateScenarioMatrix:
    def test_matrix_structure(self):
        legs = _bull_call_spread()
        result = generate_scenario_matrix(legs, 200.0, 0.045)

        assert "price_points" in result
        assert "time_points" in result
        assert "pnl_matrix" in result
        assert "iv_sensitivity" in result
        assert "probability_summary" in result
        assert "sigma_labels" in result

    def test_matrix_dimensions(self):
        legs = _bull_call_spread()
        result = generate_scenario_matrix(legs, 200.0, 0.045)

        n_prices = len(result["price_points"])
        n_times = len(result["time_points"])

        assert n_prices == 7
        assert n_times >= 2

        # Each price has all time points
        for price in result["price_points"]:
            assert price in result["pnl_matrix"]
            assert len(result["pnl_matrix"][price]) == n_times

    def test_iv_sensitivity_has_four_shifts(self):
        legs = _bull_call_spread()
        result = generate_scenario_matrix(legs, 200.0, 0.045)
        assert len(result["iv_sensitivity"]) == 4

    def test_sigma_labels_present(self):
        legs = _bull_call_spread()
        result = generate_scenario_matrix(legs, 200.0, 0.045)
        labels = result["sigma_labels"]
        # Should have "now" for the center point
        has_now = any(v == "now" for v in labels.values())
        assert has_now

    def test_skew_alpha_passed_through(self):
        legs = _bull_call_spread()
        result = generate_scenario_matrix(legs, 200.0, 0.045, skew_alpha=-0.2)
        assert result["skew_alpha"] == -0.2


class TestSkewToAlpha:
    def test_typical_equity_skew(self):
        """Typical: put IV > call IV → negative α."""
        alpha = skew_to_alpha(0.38, 0.30, 0.32)
        assert alpha < 0
        assert abs(alpha - (-0.25)) < 0.01

    def test_no_skew(self):
        alpha = skew_to_alpha(0.30, 0.30, 0.30)
        assert alpha == 0.0

    def test_zero_atm_iv(self):
        alpha = skew_to_alpha(0.30, 0.25, 0.0)
        assert alpha == 0.0

    def test_reverse_skew(self):
        """Call IV > put IV → positive α."""
        alpha = skew_to_alpha(0.25, 0.35, 0.30)
        assert alpha > 0
