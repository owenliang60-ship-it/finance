"""
Tests for Black-Scholes Pricing, Greeks & IV Solver.
"""
import math
import pytest
from terminal.options.iv_solver import (
    bs_price,
    bs_delta,
    bs_gamma,
    bs_theta,
    bs_vega,
    bs_rho,
    implied_volatility,
    compute_atm_iv_from_chain,
    _norm_cdf,
)


class TestNormCDF:
    def test_zero(self):
        assert abs(_norm_cdf(0.0) - 0.5) < 1e-10

    def test_large_positive(self):
        assert abs(_norm_cdf(10.0) - 1.0) < 1e-10

    def test_large_negative(self):
        assert abs(_norm_cdf(-10.0)) < 1e-10


class TestBSPrice:
    """BS pricing sanity checks."""

    def test_atm_call_positive(self):
        """ATM call should have positive value."""
        price = bs_price(100, 100, 0.25, 0.05, 0.30, "call")
        assert price > 0

    def test_atm_put_positive(self):
        """ATM put should have positive value."""
        price = bs_price(100, 100, 0.25, 0.05, 0.30, "put")
        assert price > 0

    def test_put_call_parity(self):
        """C - P = S - K*exp(-rT) (put-call parity)."""
        import math
        S, K, T, r, sigma = 100, 100, 0.25, 0.05, 0.30
        call = bs_price(S, K, T, r, sigma, "call")
        put = bs_price(S, K, T, r, sigma, "put")
        parity = S - K * math.exp(-r * T)
        assert abs((call - put) - parity) < 1e-8

    def test_deep_itm_call(self):
        """Deep ITM call ≈ S - K*exp(-rT)."""
        import math
        S, K, T, r = 200, 100, 0.25, 0.05
        price = bs_price(S, K, T, r, 0.30, "call")
        intrinsic = S - K * math.exp(-r * T)
        assert price >= intrinsic - 0.01

    def test_zero_time_returns_intrinsic(self):
        """T=0 should return intrinsic value."""
        assert abs(bs_price(110, 100, 0, 0.05, 0.30, "call") - 10.0) < 1e-10
        assert abs(bs_price(90, 100, 0, 0.05, 0.30, "put") - 10.0) < 1e-10
        assert abs(bs_price(90, 100, 0, 0.05, 0.30, "call")) < 1e-10


class TestBSVega:
    def test_atm_vega_positive(self):
        vega = bs_vega(100, 100, 0.25, 0.05, 0.30)
        assert vega > 0

    def test_vega_zero_at_expiry(self):
        vega = bs_vega(100, 100, 0, 0.05, 0.30)
        assert vega == 0.0


class TestBSDelta:
    """Delta sanity checks."""

    def test_atm_call_near_half(self):
        """ATM call delta ≈ 0.5."""
        d = bs_delta(100, 100, 0.25, 0.05, 0.30, "call")
        assert 0.45 < d < 0.65

    def test_atm_put_near_neg_half(self):
        """ATM put delta ≈ -0.5 (positive rates shift it toward -0.4)."""
        d = bs_delta(100, 100, 0.25, 0.05, 0.30, "put")
        assert -0.65 < d < -0.35

    def test_deep_itm_call_near_one(self):
        d = bs_delta(200, 100, 0.25, 0.05, 0.30, "call")
        assert d > 0.99

    def test_deep_otm_call_near_zero(self):
        d = bs_delta(50, 100, 0.25, 0.05, 0.30, "call")
        assert d < 0.01

    def test_call_put_delta_relationship(self):
        """Call delta - Put delta = 1 (approximately, for same inputs)."""
        dc = bs_delta(100, 100, 0.25, 0.05, 0.30, "call")
        dp = bs_delta(100, 100, 0.25, 0.05, 0.30, "put")
        assert abs((dc - dp) - 1.0) < 1e-8

    def test_expiry_call_itm(self):
        assert bs_delta(110, 100, 0, 0.05, 0.30, "call") == 1.0

    def test_expiry_call_otm(self):
        assert bs_delta(90, 100, 0, 0.05, 0.30, "call") == 0.0

    def test_expiry_put_itm(self):
        assert bs_delta(90, 100, 0, 0.05, 0.30, "put") == -1.0

    def test_expiry_put_otm(self):
        assert bs_delta(110, 100, 0, 0.05, 0.30, "put") == 0.0


class TestBSGamma:
    """Gamma sanity checks."""

    def test_atm_gamma_positive(self):
        g = bs_gamma(100, 100, 0.25, 0.05, 0.30)
        assert g > 0

    def test_atm_gamma_highest(self):
        """ATM gamma > OTM gamma."""
        g_atm = bs_gamma(100, 100, 0.25, 0.05, 0.30)
        g_otm = bs_gamma(80, 100, 0.25, 0.05, 0.30)
        assert g_atm > g_otm

    def test_gamma_increases_near_expiry(self):
        """ATM gamma increases as T decreases."""
        g_far = bs_gamma(100, 100, 1.0, 0.05, 0.30)
        g_near = bs_gamma(100, 100, 0.01, 0.05, 0.30)
        assert g_near > g_far

    def test_gamma_zero_at_expiry(self):
        assert bs_gamma(100, 100, 0, 0.05, 0.30) == 0.0

    def test_gamma_numerical_check(self):
        """Gamma ≈ (delta(S+h) - delta(S-h)) / (2h)."""
        S, K, T, r, sigma = 100, 100, 0.25, 0.05, 0.30
        h = 0.01
        d_up = bs_delta(S + h, K, T, r, sigma, "call")
        d_dn = bs_delta(S - h, K, T, r, sigma, "call")
        numerical_gamma = (d_up - d_dn) / (2 * h)
        analytical_gamma = bs_gamma(S, K, T, r, sigma)
        assert abs(numerical_gamma - analytical_gamma) < 1e-4


class TestBSTheta:
    """Theta sanity checks."""

    def test_long_call_theta_negative(self):
        """Long call theta is negative (time decay)."""
        t = bs_theta(100, 100, 0.25, 0.05, 0.30, "call")
        assert t < 0

    def test_long_put_theta_negative(self):
        """Long put theta is negative."""
        t = bs_theta(100, 100, 0.25, 0.05, 0.30, "put")
        assert t < 0

    def test_atm_theta_largest(self):
        """ATM theta magnitude > OTM theta magnitude."""
        t_atm = abs(bs_theta(100, 100, 0.25, 0.05, 0.30, "call"))
        t_otm = abs(bs_theta(80, 100, 0.25, 0.05, 0.30, "call"))
        assert t_atm > t_otm

    def test_theta_zero_at_expiry(self):
        assert bs_theta(100, 100, 0, 0.05, 0.30, "call") == 0.0

    def test_theta_numerical_check(self):
        """Theta ≈ (price(T-h) - price(T)) / h (per day)."""
        S, K, T, r, sigma = 100, 100, 0.25, 0.05, 0.30
        h = 1 / 365.0  # 1 day
        p1 = bs_price(S, K, T, r, sigma, "call")
        p2 = bs_price(S, K, T - h, r, sigma, "call")
        numerical_theta = (p2 - p1) / 1.0  # per day (h = 1 day)
        analytical_theta = bs_theta(S, K, T, r, sigma, "call")
        assert abs(numerical_theta - analytical_theta) < 0.01


class TestBSRho:
    """Rho sanity checks."""

    def test_call_rho_positive(self):
        """Call rho is positive (higher rates → higher call value)."""
        rho = bs_rho(100, 100, 0.25, 0.05, 0.30, "call")
        assert rho > 0

    def test_put_rho_negative(self):
        """Put rho is negative."""
        rho = bs_rho(100, 100, 0.25, 0.05, 0.30, "put")
        assert rho < 0

    def test_rho_zero_at_expiry(self):
        assert bs_rho(100, 100, 0, 0.05, 0.30, "call") == 0.0

    def test_rho_increases_with_dte(self):
        """Longer DTE → larger rho magnitude."""
        rho_short = abs(bs_rho(100, 100, 0.1, 0.05, 0.30, "call"))
        rho_long = abs(bs_rho(100, 100, 1.0, 0.05, 0.30, "call"))
        assert rho_long > rho_short


class TestImpliedVolatility:
    """Round-trip tests: price → IV → should match original sigma."""

    @pytest.mark.parametrize("sigma", [0.15, 0.30, 0.50, 1.00, 2.00])
    def test_call_round_trip(self, sigma):
        S, K, T, r = 100, 100, 0.25, 0.05
        price = bs_price(S, K, T, r, sigma, "call")
        iv = implied_volatility(price, S, K, T, r, "call")
        assert iv is not None
        assert abs(iv - sigma) < 0.001

    @pytest.mark.parametrize("sigma", [0.15, 0.30, 0.50, 1.00])
    def test_put_round_trip(self, sigma):
        S, K, T, r = 100, 100, 0.25, 0.05
        price = bs_price(S, K, T, r, sigma, "put")
        iv = implied_volatility(price, S, K, T, r, "put")
        assert iv is not None
        assert abs(iv - sigma) < 0.001

    def test_call_put_iv_similar_atm(self):
        """ATM call and put IV should be very close."""
        S, K, T, r, sigma = 100, 100, 0.25, 0.05, 0.30
        call_price = bs_price(S, K, T, r, sigma, "call")
        put_price = bs_price(S, K, T, r, sigma, "put")
        iv_call = implied_volatility(call_price, S, K, T, r, "call")
        iv_put = implied_volatility(put_price, S, K, T, r, "put")
        assert iv_call is not None and iv_put is not None
        assert abs(iv_call - iv_put) < 0.001

    def test_zero_price_returns_none(self):
        assert implied_volatility(0, 100, 100, 0.25, 0.05, "call") is None

    def test_zero_time_returns_none(self):
        assert implied_volatility(5, 100, 100, 0, 0.05, "call") is None

    def test_negative_price_returns_none(self):
        assert implied_volatility(-1, 100, 100, 0.25, 0.05, "call") is None

    def test_otm_option(self):
        """OTM call should still solve."""
        S, K, T, r, sigma = 100, 120, 0.25, 0.05, 0.30
        price = bs_price(S, K, T, r, sigma, "call")
        iv = implied_volatility(price, S, K, T, r, "call")
        assert iv is not None
        assert abs(iv - sigma) < 0.001


class TestComputeATMIVFromChain:
    """Tests for the high-level chain → IV function."""

    def _make_chain(self, bids, asks, strikes, dtes, sides, prices):
        """Helper to build a MarketData.app-style chain dict."""
        return {
            "s": "ok",
            "bid": bids,
            "ask": asks,
            "strike": strikes,
            "dte": dtes,
            "side": sides,
            "underlyingPrice": prices,
        }

    def test_basic_chain(self):
        """Simple 2-option chain should produce reasonable IV."""
        # Use BS to generate consistent prices
        from terminal.options.iv_solver import bs_price as bsp
        S, K, T_days, r, sigma = 150.0, 150.0, 30, 0.045, 0.30
        T = T_days / 365.0
        call_price = bsp(S, K, T, r, sigma, "call")
        put_price = bsp(S, K, T, r, sigma, "put")

        chain = self._make_chain(
            bids=[call_price - 0.10, put_price - 0.10],
            asks=[call_price + 0.10, put_price + 0.10],
            strikes=[K, K],
            dtes=[T_days, T_days],
            sides=["call", "put"],
            prices=[S, S],
        )
        iv = compute_atm_iv_from_chain(chain, risk_free_rate=r)
        assert iv is not None
        assert abs(iv - sigma) < 0.02  # within 2% of true IV

    def test_filters_low_bid(self):
        """Options with bid < $0.10 should be skipped."""
        chain = self._make_chain(
            bids=[0.05],
            asks=[0.15],
            strikes=[200.0],
            dtes=[30],
            sides=["call"],
            prices=[100.0],
        )
        iv = compute_atm_iv_from_chain(chain)
        assert iv is None  # filtered out

    def test_filters_wide_spread(self):
        """Options with spread/mid > 50% should be skipped."""
        chain = self._make_chain(
            bids=[1.0],
            asks=[5.0],  # spread=4, mid=3, ratio=133%
            strikes=[100.0],
            dtes=[30],
            sides=["call"],
            prices=[100.0],
        )
        iv = compute_atm_iv_from_chain(chain)
        assert iv is None

    def test_empty_chain(self):
        assert compute_atm_iv_from_chain({"s": "ok", "strike": []}) is None

    def test_bad_status(self):
        assert compute_atm_iv_from_chain({"s": "error"}) is None

    def test_none_input(self):
        assert compute_atm_iv_from_chain(None) is None

    def test_iv_in_reasonable_range(self):
        """IV result should be within 1%-500%."""
        from terminal.options.iv_solver import bs_price as bsp
        S, K, T_days, r, sigma = 100.0, 100.0, 30, 0.045, 0.25
        T = T_days / 365.0
        call_price = bsp(S, K, T, r, sigma, "call")

        chain = self._make_chain(
            bids=[call_price - 0.05],
            asks=[call_price + 0.05],
            strikes=[K],
            dtes=[T_days],
            sides=["call"],
            prices=[S],
        )
        iv = compute_atm_iv_from_chain(chain, risk_free_rate=r)
        assert iv is not None
        assert 0.01 <= iv <= 5.0
