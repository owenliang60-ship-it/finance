"""
Options Scenario Analyzer — BS 概率加权场景定价引擎

给定多腿策略，在不同 (股价 × 时间 × IV) 情景下用 BS 重定价，
并用对数正态分布（含 Cornish-Fisher 偏度调整）做概率加权。

纯 Python + math，零外部依赖，兼容云端 Python 3.10。
"""
import math
import logging
from typing import Any, Dict, List, Optional

from terminal.options.iv_solver import bs_price, _norm_cdf, _norm_pdf

logger = logging.getLogger(__name__)

# ── Constants ──
_SQRT_2PI = math.sqrt(2.0 * math.pi)
_GRID_POINTS = 50       # probability integration grid
_SIGMA_RANGE = 3.0      # integrate out to ±3σ


def build_strategy(legs: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Validate and normalize a multi-leg strategy definition.

    Each leg dict must contain:
        side: 'call' or 'put'
        direction: 'long' or 'short'
        strike: float
        expiry_dte: int (DTE at entry)
        iv: float (annualized, e.g. 0.35)
        entry_price: float (mid price at entry)
        contracts: int (default 1)

    Returns:
        Normalized list of legs with direction_sign added.

    Raises:
        ValueError: if any leg has invalid fields.
    """
    if not legs:
        raise ValueError("Strategy must have at least one leg")

    normalized = []
    for i, leg in enumerate(legs):
        side = leg.get("side", "").lower()
        direction = leg.get("direction", "").lower()

        if side not in ("call", "put"):
            raise ValueError("Leg {}: side must be 'call' or 'put', got '{}'".format(i, side))
        if direction not in ("long", "short"):
            raise ValueError("Leg {}: direction must be 'long' or 'short', got '{}'".format(i, direction))

        strike = float(leg["strike"])
        expiry_dte = int(leg["expiry_dte"])
        iv = float(leg["iv"])
        entry_price = float(leg["entry_price"])
        contracts = int(leg.get("contracts", 1))

        if strike <= 0 or expiry_dte <= 0 or iv <= 0 or entry_price < 0 or contracts <= 0:
            raise ValueError("Leg {}: invalid numeric values (strike, dte, iv, contracts must be > 0)".format(i))

        normalized.append({
            "side": side,
            "direction": direction,
            "direction_sign": 1 if direction == "long" else -1,
            "strike": strike,
            "expiry_dte": expiry_dte,
            "iv": iv,
            "entry_price": entry_price,
            "contracts": contracts,
        })

    return normalized


def price_strategy(
    strategy: List[Dict[str, Any]],
    S: float,
    days_forward: int,
    r: float,
    iv_shift: float = 0.0,
) -> float:
    """Reprice an entire strategy at a given (S, T, IV) point using BS.

    Args:
        strategy: Normalized legs from build_strategy()
        S: hypothetical underlying price
        days_forward: days elapsed since entry (0 = entry day)
        r: risk-free rate
        iv_shift: multiplicative IV shift (e.g. 0.1 = +10% IV)

    Returns:
        Net strategy P&L per unit (in dollars, × 100 for per-contract).
    """
    total_pnl = 0.0

    for leg in strategy:
        remaining_dte = leg["expiry_dte"] - days_forward
        T = max(remaining_dte / 365.0, 0.0)
        sigma = leg["iv"] * (1.0 + iv_shift)
        sigma = max(sigma, 0.001)  # floor

        new_price = bs_price(S, leg["strike"], T, r, sigma, leg["side"])
        leg_pnl = leg["direction_sign"] * (new_price - leg["entry_price"])
        total_pnl += leg_pnl * leg["contracts"]

    return total_pnl * 100.0  # per-contract multiplier


def _compute_sigma_move(iv: float, dte: int) -> float:
    """Compute 1-sigma price move magnitude as fraction of S.

    1σ = IV × √(DTE/252) using trading days.
    """
    return iv * math.sqrt(max(dte, 1) / 252.0)


def _adaptive_price_points(S: float, sigma_move: float, n_points: int = 7) -> List[float]:
    """Generate IV-adaptive price grid points.

    Returns n_points symmetric around S, spaced by 0.5σ increments.
    """
    if sigma_move <= 0:
        sigma_move = 0.01  # fallback

    half = n_points // 2
    points = []
    for i in range(-half, half + 1):
        offset = i * 0.5 * sigma_move
        points.append(round(S * (1.0 + offset), 2))

    return points


def _adaptive_time_points(dte: int) -> List[int]:
    """Generate time axis points (days forward from entry).

    Adapts to DTE: shorter DTE = fewer points.
    Always includes 0 (entry) and dte (expiry).
    """
    if dte <= 7:
        return [0, dte]
    elif dte <= 14:
        return [0, 7, dte]
    elif dte <= 21:
        return [0, 7, 14, dte]
    else:
        points = [0, 7, 14, dte]
        # Add 21d if DTE is long enough
        if dte > 28:
            points.insert(3, 21)
        return points


def _lognormal_pdf(S_t: float, S_0: float, mu: float, sigma: float, T: float) -> float:
    """Lognormal PDF for stock price S_t given initial price S_0.

    mu: drift = r - 0.5 * sigma^2
    """
    if S_t <= 0 or T <= 0 or sigma <= 0:
        return 0.0

    log_ratio = math.log(S_t / S_0)
    mean = mu * T
    std = sigma * math.sqrt(T)

    z = (log_ratio - mean) / std
    return math.exp(-0.5 * z * z) / (S_t * std * _SQRT_2PI)


def _adjusted_pdf(
    S_t: float, S_0: float, mu: float, sigma: float, T: float, skew_alpha: float
) -> float:
    """Lognormal PDF with Cornish-Fisher skewness adjustment.

    adjusted_pdf = lognormal_pdf × (1 + α × (z³ - 3z) / 6)

    α < 0 means left-skewed (heavier downside tail).
    """
    base_pdf = _lognormal_pdf(S_t, S_0, mu, sigma, T)
    if base_pdf <= 0 or skew_alpha == 0.0:
        return base_pdf

    log_ratio = math.log(S_t / S_0)
    mean = mu * T
    std = sigma * math.sqrt(T)
    z = (log_ratio - mean) / std

    # Cornish-Fisher first-order skewness correction
    adjustment = 1.0 + skew_alpha * (z * z * z - 3.0 * z) / 6.0
    # Clamp to prevent negative densities
    adjustment = max(adjustment, 0.0)

    return base_pdf * adjustment


def compute_probability_summary(
    strategy: List[Dict[str, Any]],
    S: float,
    r: float,
    sigma: float,
    days_forward: int,
    skew_alpha: float = 0.0,
) -> Dict[str, Any]:
    """Probability-weighted P&L summary using numerical integration.

    Integrates P&L × adjusted_pdf over a grid of future stock prices.

    Args:
        strategy: Normalized legs
        S: current underlying price
        r: risk-free rate
        sigma: annualized volatility for the probability distribution
        days_forward: time horizon for P&L evaluation
        skew_alpha: Cornish-Fisher skewness parameter

    Returns:
        Dict with: expected_pnl, win_probability, median_pnl,
                   pctl_25, pctl_75, price_range
    """
    T = days_forward / 365.0
    # Clamp skew_alpha to prevent extreme Cornish-Fisher distortion
    skew_alpha = max(-0.5, min(0.5, skew_alpha))

    if T <= 0:
        # At expiry: just compute at current price
        pnl = price_strategy(strategy, S, days_forward, r)
        return {
            "expected_pnl": round(pnl, 2),
            "win_probability": 1.0 if pnl > 0 else 0.0,
            "median_pnl": round(pnl, 2),
            "pctl_25": round(pnl, 2),
            "pctl_75": round(pnl, 2),
        }

    mu = r - 0.5 * sigma * sigma
    std_log = sigma * math.sqrt(T)
    mean_log = math.log(S) + mu * T

    # Integration grid: ±3σ in log space
    log_lo = mean_log - _SIGMA_RANGE * std_log
    log_hi = mean_log + _SIGMA_RANGE * std_log
    S_lo = math.exp(log_lo)
    S_hi = math.exp(log_hi)
    dS = (S_hi - S_lo) / _GRID_POINTS

    # Collect (pnl, probability_weight) pairs
    pnl_weights = []
    total_weight = 0.0

    for i in range(_GRID_POINTS):
        S_i = S_lo + (i + 0.5) * dS  # midpoint rule
        pdf_i = _adjusted_pdf(S_i, S, mu, sigma, T, skew_alpha)
        weight = pdf_i * dS
        pnl_i = price_strategy(strategy, S_i, days_forward, r)
        pnl_weights.append((pnl_i, weight))
        total_weight += weight

    if total_weight <= 0:
        return {
            "expected_pnl": 0.0,
            "win_probability": 0.0,
            "median_pnl": 0.0,
            "pctl_25": 0.0,
            "pctl_75": 0.0,
        }

    # Normalize weights (should sum to ~1.0 but Cornish-Fisher distorts)
    norm = 1.0 / total_weight

    # Expected P&L
    expected_pnl = sum(pnl * w for pnl, w in pnl_weights) * norm

    # Win probability
    win_prob = sum(w for pnl, w in pnl_weights if pnl > 0) * norm

    # Percentiles via sorted cumulative weights
    # Capture the P&L at the FIRST bucket where cumulative weight crosses the threshold
    sorted_pw = sorted(pnl_weights, key=lambda x: x[0])
    cum = 0.0
    pctl_25 = sorted_pw[-1][0]
    median = sorted_pw[-1][0]
    pctl_75 = sorted_pw[-1][0]
    found_25 = found_50 = found_75 = False

    for pnl, w in sorted_pw:
        cum += w * norm
        if not found_25 and cum >= 0.25:
            pctl_25 = pnl
            found_25 = True
        if not found_50 and cum >= 0.50:
            median = pnl
            found_50 = True
        if not found_75 and cum >= 0.75:
            pctl_75 = pnl
            found_75 = True

    return {
        "expected_pnl": round(expected_pnl, 2),
        "win_probability": round(win_prob, 4),
        "median_pnl": round(median, 2),
        "pctl_25": round(pctl_25, 2),
        "pctl_75": round(pctl_75, 2),
    }


def generate_scenario_matrix(
    strategy: List[Dict[str, Any]],
    S: float,
    r: float,
    skew_alpha: float = 0.0,
) -> Dict[str, Any]:
    """Generate complete scenario analysis: P&L matrix + IV sensitivity + probability summary.

    Args:
        strategy: Normalized legs from build_strategy()
        S: current underlying price
        r: risk-free rate
        skew_alpha: Cornish-Fisher skewness parameter

    Returns:
        Dict with:
            price_points: list of price levels
            time_points: list of days forward
            pnl_matrix: {price: {days: pnl}} nested dict
            iv_sensitivity: {shift_label: pnl} at +7d, current price
            probability_summary: output of compute_probability_summary()
            sigma_labels: {price: sigma_label} for display
    """
    # Use max DTE across legs for time axis
    max_dte = max(leg["expiry_dte"] for leg in strategy)

    # Use average IV weighted by contracts for sigma move
    total_contracts = sum(leg["contracts"] for leg in strategy)
    avg_iv = sum(leg["iv"] * leg["contracts"] for leg in strategy) / max(total_contracts, 1)

    # Adaptive axes
    sigma_move = _compute_sigma_move(avg_iv, max_dte)
    price_points = _adaptive_price_points(S, sigma_move)
    time_points = _adaptive_time_points(max_dte)

    # Build sigma labels for each price point
    sigma_labels = {}
    for p in price_points:
        pct_move = (p - S) / S
        if abs(pct_move) < 0.001:
            sigma_labels[p] = "now"
        else:
            n_sigma = pct_move / sigma_move if sigma_move > 0 else 0
            if abs(n_sigma) < 0.01:
                sigma_labels[p] = "now"
            else:
                sigma_labels[p] = "{:+.1f}\u03c3".format(n_sigma)

    # P&L matrix
    pnl_matrix = {}
    for price in price_points:
        pnl_matrix[price] = {}
        for days in time_points:
            pnl_matrix[price][days] = round(
                price_strategy(strategy, price, days, r), 2
            )

    # IV sensitivity at +7d (or first non-zero time point), current price
    eval_day = 7 if max_dte > 7 else max(1, max_dte // 2)
    iv_shifts = [
        ("-20%", -0.20),
        ("-10%", -0.10),
        ("+10%", 0.10),
        ("+20%", 0.20),
    ]
    iv_sensitivity = {}
    for label, shift in iv_shifts:
        iv_sensitivity[label] = round(
            price_strategy(strategy, S, eval_day, r, iv_shift=shift), 2
        )

    # Probability summary at the evaluation time horizon
    # Use midpoint of strategy life for probability assessment
    eval_horizon = min(max_dte, max(7, max_dte // 2))
    prob_summary = compute_probability_summary(
        strategy, S, r, avg_iv, eval_horizon, skew_alpha
    )

    return {
        "underlying_price": S,
        "avg_iv": round(avg_iv, 4),
        "max_dte": max_dte,
        "sigma_move": round(sigma_move, 4),
        "skew_alpha": skew_alpha,
        "price_points": price_points,
        "time_points": time_points,
        "pnl_matrix": pnl_matrix,
        "sigma_labels": sigma_labels,
        "iv_sensitivity": iv_sensitivity,
        "iv_eval_day": eval_day,
        "probability_summary": prob_summary,
        "probability_horizon_days": eval_horizon,
    }


def skew_to_alpha(put_iv_25d: float, call_iv_25d: float, atm_iv: float) -> float:
    """Convert 25-delta put/call IV skew to Cornish-Fisher alpha.

    α = -(put_iv - call_iv) / atm_iv

    Negative α = left skew (heavier downside). Typical equity skew: α ≈ -0.1 to -0.3.
    """
    if atm_iv <= 0:
        return 0.0
    return -(put_iv_25d - call_iv_25d) / atm_iv
