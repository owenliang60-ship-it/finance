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

function seg_revenue(bases, adjs, driver_delta) {
  bases = _fins(bases);
  adjs = _fins(adjs);
  const delta = fin(driver_delta);
  if (bases === null || bases.length === 0 || adjs === null || delta === null || bases.length !== adjs.length) {
    return null;
  }
  let total = 0.0;
  for (let i = 0; i < bases.length; i += 1) total += bases[i] * (1 + adjs[i]);
  return fin(total + delta);
}

function implied_margin(eps, shares, rev) {
  eps = fin(eps); shares = fin(shares); rev = fin(rev);
  if (eps === null || shares === null || rev === null || shares <= 0 || rev <= 0) return null;
  return fin(eps * shares / rev);
}

function margin_eps(rev, margin, shares) {
  rev = fin(rev); margin = fin(margin); shares = fin(shares);
  if (rev === null || margin === null || shares === null || shares <= 0) return null;
  return fin(rev * margin / shares);
}

function quarter_path(shape, total) {
  const values = _fins(shape);
  total = fin(total);
  if (values === null || values.length === 0 || total === null) return null;
  let s = 0.0;
  for (const v of values) {
    if (v < 0) return null;
    s += v;
  }
  if (s <= 0) return null;
  const out = values.map((v) => fin(v * total / s));
  return out.some((v) => v === null) ? null : out;
}

function grid_pos(levels, x) {
  const values = _fins(levels);
  x = fin(x);
  if (values === null || values.length < 2 || x === null) return null;
  for (let i = 0; i + 1 < values.length; i += 1) {
    if (!(values[i + 1] > values[i])) return null;
  }
  if (x <= values[0]) return 0.0;
  if (x >= values[values.length - 1]) return values.length - 1;
  let i = 0;
  while (x > values[i + 1]) i += 1;
  return fin(i + (x - values[i]) / (values[i + 1] - values[i]));
}

function live_state(s) {
  const rev = seg_revenue(s.bases, s.adjs, s.driver_delta);
  let net_income = null;
  let eps_local = null;
  if (s.eps_mode === "margin") {
    const margin = fin(s.margin);
    net_income = rev !== null && margin !== null ? fin(rev * margin) : null;
    eps_local = margin_eps(rev, margin, s.shares);
  } else if (s.eps_mode === "direct") {
    eps_local = fin(s.direct_eps);
  }
  const eps_usd = to_usd(eps_local, s.fx);
  const eps_state = eps_usd === null ? "missing" : eps_usd > 0 ? "pos" : "nonpos";
  const pe = fin(s.pe);
  const half = fin(s.pe_half);
  const pe_lo = pe !== null && half !== null ? fin(pe - half) : null;
  const pe_hi = pe !== null && half !== null ? fin(pe + half) : null;
  const value = scenario_value(pe, eps_usd);
  return {
    rev: rev, net_income: net_income, eps_local: eps_local, eps_usd: eps_usd, eps_state: eps_state,
    implied_pe: implied_pe(s.price, eps_usd), value: value, value_lo: scenario_value(pe_lo, eps_usd),
    value_hi: scenario_value(pe_hi, eps_usd), value_vs_price: pct_diff(value, s.price),
    zone: zone(rev, s.rev_cons, s.half_width),
  };
}

if (typeof module !== "undefined") {
  module.exports = {
    fin, zone, to_usd, implied_pe, scenario_value, pct_diff, pct_rank, weighted_value, seg_revenue, implied_margin,
    margin_eps, quarter_path, grid_pos, live_state,
  };
}
