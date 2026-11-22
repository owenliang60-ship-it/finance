// Numeric kernel mirrored from terminal/valuation/kernel.py: same names, same argument order,
// same results. Every function passes its inputs through fin() first; null is never compared.
"use strict";

function fin(x) {
  return typeof x === "number" && Number.isFinite(x) ? x : null;
}

function _fins(values) {
  if (!Array.isArray(values)) return null;
  const out = values.map(fin);
  return out.some((v) => v === null) ? null : out;
}

function bridge_eps(rev, rev_cons, eps_cons, margin, shares) {
  rev = fin(rev); rev_cons = fin(rev_cons); eps_cons = fin(eps_cons); margin = fin(margin); shares = fin(shares);
  if (rev === null || rev_cons === null || eps_cons === null || margin === null || shares === null || shares <= 0) {
    return null;
  }
  return fin(eps_cons + (rev - rev_cons) * margin / shares);
}

function zone(rev, rev_cons, half_width) {
  rev = fin(rev); rev_cons = fin(rev_cons); half_width = fin(half_width);
  if (rev === null || rev_cons === null || half_width === null || rev_cons <= 0 || half_width <= 0) return null;
  return Math.abs(rev / rev_cons - 1) <= half_width ? "mild" : "tail";
}

function to_usd(local, fx) {
  local = fin(local); fx = fin(fx);
  if (local === null || fx === null || fx <= 0) return null;
  return fin(local / fx);
}

function implied_pe(price, eps_usd) {
  price = fin(price); eps_usd = fin(eps_usd);
  if (price === null || eps_usd === null || price <= 0 || eps_usd <= 0) return null;
  return fin(price / eps_usd);
}

function scenario_value(pe, eps_usd) {
  pe = fin(pe); eps_usd = fin(eps_usd);
  if (pe === null || eps_usd === null || pe <= 0 || eps_usd <= 0) return null;
  return fin(pe * eps_usd);
}

function pct_diff(a, b) {
  a = fin(a); b = fin(b);
  if (a === null || b === null || b <= 0) return null;
  return fin(a / b - 1);
}

function pct_rank(sorted_values, x) {
  const values = _fins(sorted_values);
  x = fin(x);
  if (values === null || values.length === 0 || x === null) return null;
  let count = 0;
  for (const v of values) {
    if (v <= x) count += 1;
  }
  return count / values.length;
}

function weighted_value(values, weights) {
  if (!Array.isArray(values)) return null;
  const w = _fins(weights);
  if (w === null || w.length < 1 || w.length !== values.length) return null;
  let total_w = 0.0;
  for (const x of w) {
    if (x < 0) return null;
    total_w += x;
  }
  if (Math.abs(total_w - 100) > 0.01) return null;
  let total = 0.0;
  for (let i = 0; i < w.length; i += 1) {
    if (w[i] > 0) {
      const v = fin(values[i]);
      if (v === null) return null;
      total += v * w[i] / 100;
    }
  }
  return fin(total);
}

function scenario_state(s) {
  const rev = fin(s.rev);
  const delta = fin(s.driver_delta);
  const rev_eff = rev !== null && delta !== null ? fin(rev + delta) : null;
  const where = zone(rev_eff, s.rev_cons, s.half_width);
  let eps_local;
  let source;
  if (s.eps_mode === "direct") {
    eps_local = fin(s.direct_eps);
    source = eps_local !== null ? "direct" : "blocked_input";
  } else if (where === "mild") {
    eps_local = bridge_eps(rev_eff, s.rev_cons, s.eps_cons, s.margin, s.shares);
    source = eps_local !== null ? "bridge" : "blocked_input";
  } else if (where === "tail") {
    eps_local = null;
    source = "blocked_tail";
  } else {
    eps_local = null;
    source = "blocked_zone_unknown";
  }
  const eps_usd = to_usd(eps_local, s.fx);
  const value = scenario_value(s.pe, eps_usd);
  return {
    rev_eff: rev_eff, zone: where, eps_local: eps_local, eps_source: source, eps_usd: eps_usd,
    implied_pe: implied_pe(s.price, eps_usd), value: value, value_vs_price: pct_diff(value, s.price),
  };
}

if (typeof module !== "undefined") {
  module.exports = {
    fin, bridge_eps, zone, to_usd, implied_pe, scenario_value, pct_diff, pct_rank, weighted_value,
    scenario_state,
  };
}
