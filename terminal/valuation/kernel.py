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


def bridge_eps(rev, rev_cons, eps_cons, margin, shares) -> Optional[float]:
    rev, rev_cons, eps_cons, margin, shares = fin(rev), fin(rev_cons), fin(eps_cons), fin(margin), fin(shares)
    if rev is None or rev_cons is None or eps_cons is None or margin is None or shares is None or shares <= 0:
        return None
    return fin(eps_cons + (rev - rev_cons) * margin / shares)


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


def scenario_state(s: dict) -> dict:
    rev, delta = fin(s.get("rev")), fin(s.get("driver_delta"))
    rev_eff = fin(rev + delta) if rev is not None and delta is not None else None
    where = zone(rev_eff, s.get("rev_cons"), s.get("half_width"))
    if s.get("eps_mode") == "direct":
        eps_local = fin(s.get("direct_eps"))
        source = "direct" if eps_local is not None else "blocked_input"
    elif where == "mild":
        eps_local = bridge_eps(rev_eff, s.get("rev_cons"), s.get("eps_cons"), s.get("margin"), s.get("shares"))
        source = "bridge" if eps_local is not None else "blocked_input"
    elif where == "tail":
        eps_local, source = None, "blocked_tail"
    else:
        eps_local, source = None, "blocked_zone_unknown"
    eps_usd = to_usd(eps_local, s.get("fx"))
    value = scenario_value(s.get("pe"), eps_usd)
    return {"rev_eff": rev_eff, "zone": where, "eps_local": eps_local, "eps_source": source,
            "eps_usd": eps_usd, "implied_pe": implied_pe(s.get("price"), eps_usd), "value": value,
            "value_vs_price": pct_diff(value, s.get("price"))}
