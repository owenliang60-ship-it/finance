"""Numeric kernel shared with assets/kernel.js (same names, same argument order, same results).

Missing values are always None; no function produces NaN or Inf. Sums use explicit
left-to-right loops: Python 3.12+ sum() compensates float error and would drift from JS.
"""
from __future__ import annotations

import math
from typing import Optional


def fin(x) -> Optional[float]:
    if isinstance(x, bool) or not isinstance(x, (int, float)):
        return None
    try:
        x = float(x)
    except OverflowError:
        return None
    return x if math.isfinite(x) else None


def _fins(values):
    """Every element finite → list of floats; otherwise None."""
    if not isinstance(values, (list, tuple)):
        return None
    out = [fin(v) for v in values]
    return None if any(v is None for v in out) else out


def zone(rev, rev_cons, half_width) -> Optional[str]:
    rev, rev_cons, half_width = fin(rev), fin(rev_cons), fin(half_width)
    if rev is None or rev_cons is None or half_width is None or rev_cons <= 0 or half_width <= 0:
        return None
    return "mild" if abs(rev / rev_cons - 1) <= half_width else "tail"


def to_usd(local, fx) -> Optional[float]:
    local, fx = fin(local), fin(fx)
    if local is None or fx is None or fx <= 0:
        return None
    return fin(local / fx)


def implied_pe(price, eps_usd) -> Optional[float]:
    price, eps_usd = fin(price), fin(eps_usd)
    if price is None or eps_usd is None or price <= 0 or eps_usd <= 0:
        return None
    return fin(price / eps_usd)


def scenario_value(pe, eps_usd) -> Optional[float]:
    pe, eps_usd = fin(pe), fin(eps_usd)
    if pe is None or eps_usd is None or pe <= 0 or eps_usd <= 0:
        return None
    return fin(pe * eps_usd)


def pct_diff(a, b) -> Optional[float]:
    a, b = fin(a), fin(b)
    if a is None or b is None or b <= 0:
        return None
    return fin(a / b - 1)


def pct_rank(sorted_values, x) -> Optional[float]:
    values, x = _fins(sorted_values), fin(x)
    if not values or x is None:
        return None
    count = 0
    for v in values:
        if v <= x:
            count += 1
    return count / len(values)


def weighted_value(values, weights) -> Optional[float]:
    if not isinstance(values, (list, tuple)):
        return None
    w = _fins(weights)
    if w is None or len(w) < 1 or len(w) != len(values):
        return None
    total_w = 0.0
    for x in w:
        if x < 0:
            return None
        total_w += x
    if abs(total_w - 100) > 0.01:
        return None
    total = 0.0
    for v, x in zip(values, w):
        if x > 0:
            v = fin(v)
            if v is None:
                return None
            total += v * x / 100
    return fin(total)


def seg_revenue(bases, adjs, driver_delta) -> Optional[float]:
    """Σ bases[i] × (1 + adjs[i]) + driver_delta, summed left to right."""
    bases, adjs, delta = _fins(bases), _fins(adjs), fin(driver_delta)
    if not bases or adjs is None or delta is None or len(bases) != len(adjs):
        return None
    total = 0.0
    for b, a in zip(bases, adjs):
        total += b * (1 + a)
    return fin(total + delta)


def implied_margin(eps, shares, rev) -> Optional[float]:
    eps, shares, rev = fin(eps), fin(shares), fin(rev)
    if eps is None or shares is None or rev is None or shares <= 0 or rev <= 0:
        return None
    return fin(eps * shares / rev)


def margin_eps(rev, margin, shares) -> Optional[float]:
    rev, margin, shares = fin(rev), fin(margin), fin(shares)
    if rev is None or margin is None or shares is None or shares <= 0:
        return None
    return fin(rev * margin / shares)


def quarter_path(shape, total):
    values, total = _fins(shape), fin(total)
    if not values or total is None:
        return None
    s = 0.0
    for v in values:
        if v < 0:
            return None
        s += v
    if s <= 0:
        return None
    out = [fin(v * total / s) for v in values]
    return None if any(v is None for v in out) else out


def grid_pos(levels, x) -> Optional[float]:
    """Fractional index of x on a strictly increasing grid, clamped to [0, n-1]."""
    values, x = _fins(levels), fin(x)
    if values is None or len(values) < 2 or x is None:
        return None
    for a, b in zip(values, values[1:]):
        if not b > a:
            return None
    if x <= values[0]:
        return 0.0
    if x >= values[-1]:
        return float(len(values) - 1)
    i = 0
    while x > values[i + 1]:
        i += 1
    return fin(i + (x - values[i]) / (values[i + 1] - values[i]))


def live_state(s: dict) -> dict:
    """Net-margin method: revenue → net income → EPS → USD → P/E band. Never NaN; missing is None."""
    rev = seg_revenue(s.get("bases"), s.get("adjs"), s.get("driver_delta"))
    mode = s.get("eps_mode")
    net_income, eps_local = None, None
    if mode == "margin":
        margin = fin(s.get("margin"))
        net_income = fin(rev * margin) if rev is not None and margin is not None else None
        eps_local = margin_eps(rev, margin, s.get("shares"))
    elif mode == "direct":
        eps_local = fin(s.get("direct_eps"))
    eps_usd = to_usd(eps_local, s.get("fx"))
    eps_state = "missing" if eps_usd is None else "pos" if eps_usd > 0 else "nonpos"
    pe, half = fin(s.get("pe")), fin(s.get("pe_half"))
    pe_lo = fin(pe - half) if pe is not None and half is not None else None
    pe_hi = fin(pe + half) if pe is not None and half is not None else None
    value = scenario_value(pe, eps_usd)
    price = s.get("price")
    return {"rev": rev, "net_income": net_income, "eps_local": eps_local, "eps_usd": eps_usd,
            "eps_state": eps_state, "implied_pe": implied_pe(price, eps_usd), "value": value,
            "value_lo": scenario_value(pe_lo, eps_usd), "value_hi": scenario_value(pe_hi, eps_usd),
            "value_vs_price": pct_diff(value, price), "zone": zone(rev, s.get("rev_cons"), s.get("half_width"))}
