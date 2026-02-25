"""
Black-Scholes IV Solver — 从期权链 bid/ask 反推 Implied Volatility

纯 Python 实现，零外部依赖（math.erf for norm CDF），兼容云端 Python 3.10。

用途: MarketData.app Starter 版历史 EOD 的 iv/Greeks 字段全是 None，
但 bid/ask/strike/dte/underlyingPrice 都有 → 可用 BS 反推 IV。
"""
import math
import logging
from typing import Optional, List

logger = logging.getLogger(__name__)

# ── Constants ──
_SQRT_2 = math.sqrt(2.0)
_SQRT_2PI = math.sqrt(2.0 * math.pi)

# Solver bounds
MIN_IV = 0.01   # 1%
MAX_IV = 5.00   # 500%
NEWTON_TOL = 1e-6
NEWTON_MAX_ITER = 50
BISECT_TOL = 1e-6
BISECT_MAX_ITER = 100

# Filter thresholds
MIN_BID = 0.10          # skip options with bid < $0.10
MAX_SPREAD_RATIO = 0.50  # skip if spread / mid > 50%
MIN_DTE_YEARS = 1 / 365  # skip if T < 1 day


def _norm_cdf(x: float) -> float:
    """Standard normal CDF using math.erf."""
    return 0.5 * (1.0 + math.erf(x / _SQRT_2))


def _norm_pdf(x: float) -> float:
    """Standard normal PDF."""
    return math.exp(-0.5 * x * x) / _SQRT_2PI


def bs_price(
    S: float, K: float, T: float, r: float, sigma: float, option_type: str
) -> float:
    """Black-Scholes European option price.

    Args:
        S: underlying price
        K: strike price
        T: time to expiry in years
        r: risk-free rate (annualized, e.g. 0.045)
        sigma: volatility (annualized, e.g. 0.30)
        option_type: 'call' or 'put'

    Returns:
        Option price
    """
    if T <= 0 or sigma <= 0:
        # Intrinsic value at expiry
        if option_type == "call":
            return max(S - K, 0.0)
        return max(K - S, 0.0)

    sqrt_T = math.sqrt(T)
    d1 = (math.log(S / K) + (r + 0.5 * sigma * sigma) * T) / (sigma * sqrt_T)
    d2 = d1 - sigma * sqrt_T

    if option_type == "call":
        return S * _norm_cdf(d1) - K * math.exp(-r * T) * _norm_cdf(d2)
    else:
        return K * math.exp(-r * T) * _norm_cdf(-d2) - S * _norm_cdf(-d1)


def bs_vega(S: float, K: float, T: float, r: float, sigma: float) -> float:
    """BS Vega (dPrice/dSigma). Same for call and put.

    Returns:
        Vega (price sensitivity per 1.0 change in sigma)
    """
    if T <= 0 or sigma <= 0:
        return 0.0
    sqrt_T = math.sqrt(T)
    d1 = (math.log(S / K) + (r + 0.5 * sigma * sigma) * T) / (sigma * sqrt_T)
    return S * _norm_pdf(d1) * sqrt_T


def implied_volatility(
    market_price: float,
    S: float,
    K: float,
    T: float,
    r: float,
    option_type: str,
) -> Optional[float]:
    """Solve for implied volatility using Newton-Raphson + bisection fallback.

    Args:
        market_price: observed option price (mid of bid/ask)
        S: underlying price
        K: strike price
        T: time to expiry in years
        r: risk-free rate
        option_type: 'call' or 'put'

    Returns:
        IV as decimal (e.g. 0.30 for 30%), or None if cannot solve
    """
    if market_price <= 0 or S <= 0 or K <= 0 or T < MIN_DTE_YEARS:
        return None

    # Intrinsic value check
    if option_type == "call":
        intrinsic = max(S - K * math.exp(-r * T), 0.0)
    else:
        intrinsic = max(K * math.exp(-r * T) - S, 0.0)

    if market_price < intrinsic - 0.01:
        return None  # price below intrinsic (bad data)

    # Newton-Raphson
    sigma = 0.3  # initial guess
    for _ in range(NEWTON_MAX_ITER):
        price = bs_price(S, K, T, r, sigma, option_type)
        vega = bs_vega(S, K, T, r, sigma)

        diff = price - market_price
        if abs(diff) < NEWTON_TOL:
            if MIN_IV <= sigma <= MAX_IV:
                return round(sigma, 6)
            return None

        if vega < 1e-10:
            break  # vega too small, switch to bisection

        sigma -= diff / vega
        if sigma <= 0:
            break  # went negative, switch to bisection

    # Bisection fallback
    lo, hi = MIN_IV, MAX_IV
    for _ in range(BISECT_MAX_ITER):
        mid = (lo + hi) / 2.0
        price = bs_price(S, K, T, r, mid, option_type)
        diff = price - market_price

        if abs(diff) < BISECT_TOL:
            return round(mid, 6)

        if diff > 0:
            hi = mid
        else:
            lo = mid

    # Did not converge
    return None


def compute_atm_iv_from_chain(
    chain_data: dict,
    risk_free_rate: float = 0.045,
) -> Optional[float]:
    """Compute ATM IV from a MarketData.app chain response using BS solver.

    Takes the chain arrays (bid, ask, strike, dte, underlyingPrice, side),
    filters for valid ATM options, solves IV for each, returns average.

    Args:
        chain_data: MarketData.app chain response dict with array fields
        risk_free_rate: annualized risk-free rate

    Returns:
        Average ATM IV as decimal (e.g. 0.28), or None if insufficient data
    """
    if not chain_data or chain_data.get("s") != "ok":
        return None

    bids = chain_data.get("bid", [])
    asks = chain_data.get("ask", [])
    strikes = chain_data.get("strike", [])
    dtes = chain_data.get("dte", [])
    sides = chain_data.get("side", [])
    underlying_prices = chain_data.get("underlyingPrice", [])

    n = len(strikes)
    if n == 0:
        return None

    iv_results: List[float] = []

    for i in range(n):
        try:
            bid = bids[i] if i < len(bids) else None
            ask = asks[i] if i < len(asks) else None
            strike = strikes[i] if i < len(strikes) else None
            dte = dtes[i] if i < len(dtes) else None
            side = sides[i] if i < len(sides) else None
            underlying = underlying_prices[i] if i < len(underlying_prices) else None

            # Validate required fields
            if any(v is None for v in (bid, ask, strike, dte, side, underlying)):
                continue
            if underlying <= 0 or strike <= 0 or dte <= 0:
                continue

            # Filter: bid too low
            if bid < MIN_BID:
                continue

            # Mid price
            mid_price = (bid + ask) / 2.0
            if mid_price <= 0:
                continue

            # Filter: spread too wide
            if mid_price > 0 and (ask - bid) / mid_price > MAX_SPREAD_RATIO:
                continue

            # Convert DTE to years
            T = dte / 365.0

            option_type = "call" if side == "call" else "put"

            iv = implied_volatility(mid_price, underlying, strike, T, risk_free_rate, option_type)
            if iv is not None and MIN_IV <= iv <= MAX_IV:
                iv_results.append(iv)

        except (TypeError, ValueError, ZeroDivisionError):
            continue

    if not iv_results:
        return None

    avg_iv = sum(iv_results) / len(iv_results)
    return round(avg_iv, 4)
