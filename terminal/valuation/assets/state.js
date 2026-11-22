// Page state v2: pure functions over (state, payload). Every number goes through kernel.js
// (fin, live_state, weighted_value, quarter_path, grid_pos, pct_diff, pct_rank are its globals).
// state = {adjs, margin, pe, pe_half, fx, drivers, weights, preset_id}; null is never compared.
"use strict";

const NONPOS_TEXT = "负盈利（周期谷底）";
const SET_KEYS = ["margin", "pe", "fx", "pe_half"];

function _clamp(v, lo, hi) {
  lo = fin(lo);
  hi = fin(hi);
  if (lo !== null && v < lo) v = lo;
  if (hi !== null && v > hi) v = hi;
  return v;
}

function _copy_state(state) {
  return Object.assign({}, state, {
    adjs: state.adjs.slice(), drivers: Object.assign({}, state.drivers), weights: Object.assign({}, state.weights),
  });
}

function _base_drivers(payload) {
  const drivers = {};
  for (const d of payload.sliders.drivers) drivers[d.id] = d.base;
  return drivers;
}

function init_state(payload) {
  const m = payload.model;
  const weights = {};
  for (const sc of payload.scenarios) weights[sc.id] = sc.weight;
  return {
    adjs: m.init.adjs.slice(), margin: m.init.margin, pe: m.init.pe, pe_half: m.init.pe_half, fx: m.init.fx,
    drivers: _base_drivers(payload), weights: weights, preset_id: null,
  };
}

function _parse_weight(text) {
  const t = String(text === undefined || text === null ? "" : text).trim();
  if (t === "") return null;
  const v = fin(Number(t));
  return v !== null && v >= 0 ? v : null;
}

function _set_bounds(key, payload) {
  const s = payload.sliders;
  if (key === "pe_half") {
    if (!s.pe || s.pe.disabled) return null;
    return {min: 0, max: s.pe.max};
  }
  const sl = s[key];
  if (!sl || sl.disabled) return null;
  return sl;
}

function reduce(state, action, payload) {
  const next = _copy_state(state);
  switch (action.type) {
    case "seg": {
      const v = fin(action.value);
      const sl = payload.sliders.seg;
      if (v === null || sl.disabled || !(action.index >= 0 && action.index < next.adjs.length)) return state;
      next.adjs[action.index] = _clamp(v, sl.min, sl.max);
      next.preset_id = null;
      return next;
    }
    case "set": {
      const v = fin(action.value);
      const b = SET_KEYS.indexOf(action.key) >= 0 ? _set_bounds(action.key, payload) : null;
      if (v === null || b === null) return state;
      next[action.key] = _clamp(v, b.min, b.max);
      next.preset_id = null;
      return next;
    }
    case "driver": {
      const v = fin(action.value);
      const d = payload.sliders.drivers.find((x) => x.id === action.id);
      if (v === null || !d || payload.model.eps_mode !== "margin") return state;
      next.drivers[d.id] = _clamp(v, d.min, d.max);
      next.preset_id = null;
      return next;
    }
    case "preset": {
      const id = String(action.id);
      if (id.indexOf("cycle:") === 0) {
        const c = payload.cycles.find((x) => "cycle:" + x.id === id);
        if (!c || c.enabled !== true) return state;
        next.adjs = next.adjs.map(() => c.adj);
        next.margin = c.margin;
        next.preset_id = id;
        return next;
      }
      const sc = payload.scenarios.find((x) => x.id === id);
      if (!sc || sc.enabled !== true) return state;
      next.adjs = sc.adjs.slice();
      next.margin = sc.margin;
      next.pe = sc.pe;
      next.pe_half = sc.pe_half;
      next.fx = payload.model.init.fx;
      next.drivers = _base_drivers(payload);
      next.preset_id = id;
      return next;
    }
    case "weight": {
      if (!(action.id in next.weights)) return state;
      next.weights[action.id] = _parse_weight(action.text);
      return next;
    }
    case "reset":
      return init_state(payload);
    default:
      return state;
  }
}

function _ev(state, payload) {
  const sc = payload.scenarios;
  const weights = sc.map((x) => state.weights[x.id]);
  let total = 0.0;
  let blank = false;
  for (const w of weights) {
    if (fin(w) === null) blank = true;
    else total += w;
  }
  if (blank) return {ev: null, reason: "权重未填完整", total: total};
  if (Math.abs(total - 100) > 0.01) return {ev: null, reason: "权重合计不为 100", total: total};
  const ev = weighted_value(sc.map((x) => (x.enabled === true && x.out ? x.out.value : null)), weights);
  return {ev: ev, reason: ev === null ? "正权重情景不可用" : null, total: total};
}

function derive(state, payload) {
  const c = payload.consensus;
  const m = payload.model;
  let delta = 0.0;
  for (const d of payload.sliders.drivers) {
    const v = fin(state.drivers[d.id]);
    delta = v === null || delta === null ? null : delta + (v - d.base);
  }
  const bases = m.segments.map((s) => s.base);
  const live = live_state({
    bases: bases, adjs: state.adjs, driver_delta: delta, margin: state.margin, shares: c.shares, fx: state.fx,
    pe: state.pe, pe_half: state.pe_half, price: c.price, rev_cons: c.rev_cons, half_width: c.half_width,
    eps_mode: m.eps_mode, direct_eps: m.direct_eps,
  });
  const seg_rev = bases.map((b, i) => {
    const bb = fin(b);
    const a = fin(state.adjs[i]);
    return bb !== null && a !== null ? fin(bb * (1 + a)) : null;
  });
  const t = payload.trajectory;
  const h = payload.heatmap;
  const ev = _ev(state, payload);
  let heat = null;
  if (h && h.eps_levels) {
    heat = {r_pos: grid_pos(h.eps_levels, live.eps_usd), c_pos: grid_pos(h.pe_levels, state.pe),
            eps_usd: live.eps_usd, pe: fin(state.pe)};
  }
  return {
    live: live, seg_rev: seg_rev, driver_delta: delta, weights: state.weights,
    quarters: t && t.shape ? quarter_path(t.shape, live.rev) : null,
    ev: ev.ev, ev_reason: ev.reason, weight_total: ev.total, ev_vs_price: pct_diff(ev.ev, c.price),
    pe_rank: pct_rank(payload.pe_sorted, live.implied_pe), tail: live.zone === "tail", heat: heat,
  };
}

function _group(digits) {
  return digits.replace(/\B(?=(\d{3})+(?!\d))/g, ",");
}

function fmt(x, kind) {
  x = fin(x);
  if (x === null) return "—";
  if (kind === "pct") {
    const v = Math.round(x * 1000) / 10;
    return (v === 0 ? 0 : v).toFixed(1) + "%";
  }
  if (kind === "x") return x.toFixed(2) + "x";
  if (kind === "usd") {
    const parts = Math.abs(x).toFixed(2).split(".");
    return (x < 0 ? "-" : "") + "$" + _group(parts[0]) + "." + parts[1];
  }
  const a = Math.abs(x);
  if (a >= 1e12) return (x / 1e12).toFixed(2) + "T";
  if (a >= 1e9) return (x / 1e9).toFixed(2) + "B";
  if (a >= 1e6) return (x / 1e6).toFixed(2) + "M";
  return x.toFixed(2);
}

function _prefix(currency) {
  if (currency === "USD") return "$";
  if (currency === "TWD") return "NT$";
  return String(currency || "") + " ";
}

// Aggregate amounts (revenue, market cap): T with two decimals, B with one, M with one.
function fmt_money(x, currency) {
  x = fin(x);
  if (x === null) return "—";
  const a = Math.abs(x);
  const sign = x < 0 ? "−" : "";
  let body;
  if (a >= 1e12) body = (a / 1e12).toFixed(2) + "T";
  else if (a >= 1e9) body = (a / 1e9).toFixed(1) + "B";
  else if (a >= 1e6) body = (a / 1e6).toFixed(1) + "M";
  else body = _group(a.toFixed(0));
  return sign + _prefix(currency) + body;
}

// Per-share values in USD: whole dollars from $100 up, cents below.
function fmt_px(x) {
  x = fin(x);
  if (x === null) return "—";
  const a = Math.abs(x);
  const parts = a.toFixed(a >= 100 ? 0 : 2).split(".");
  return (x < 0 ? "−" : "") + "$" + _group(parts[0]) + (parts.length > 1 ? "." + parts[1] : "");
}

function fmt_signed(x, digits) {
  x = fin(x);
  if (x === null) return "—";
  const scale = Math.pow(10, digits);
  const v = Math.round(x * 100 * scale) / scale;
  if (v === 0) return (0).toFixed(digits) + "%";
  return (v > 0 ? "+" : "−") + Math.abs(v).toFixed(digits) + "%";
}

function custom_text(d) {
  const live = d.live;
  if (live.eps_state === "nonpos") return NONPOS_TEXT;
  if (live.value === null) return "输入不足，无法演算";
  return fmt(live.value, "usd") + "（" + fmt_signed(live.value_vs_price, 1) + "）";
}

function ev_text(d) {
  if (d.ev === null) return d.ev_reason || "不可得";
  return fmt(d.ev, "usd") + "（" + fmt_signed(d.ev_vs_price, 1) + "）";
}

if (typeof module !== "undefined") {
  module.exports = {init_state, reduce, derive, fmt, fmt_money, fmt_px, fmt_signed, custom_text, ev_text,
                    NONPOS_TEXT};
}
