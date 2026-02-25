"""
Tests for Black-Scholes IV Solver.
"""
import pytest
from terminal.options.iv_solver import (
    bs_price,
    bs_vega,
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
