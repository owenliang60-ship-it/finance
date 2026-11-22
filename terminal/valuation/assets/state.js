// Page state: pure functions over (state, payload). Every number goes through kernel.js
// (fin, scenario_state, pct_rank, weighted_value are its globals); null is never compared.
"use strict";

const SLIDER_KEYS = ["rev", "margin", "pe", "fx"];

function _bridge_ok(payload) {
  return payload.consensus.bridge_enabled === true;
}

function _clamp(v, lo, hi) {
  lo = fin(lo);
  hi = fin(hi);
  if (lo !== null && v < lo) v = lo;
  if (hi !== null && v > hi) v = hi;
  return v;
}

function _copy_state(state) {
  return Object.assign({}, state, {
    drivers: Object.assign({}, state.drivers),
    scenarios: state.scenarios.map((x) => Object.assign({}, x)),
  });
}

function init_state(payload) {
  const s = payload.sliders;
  const c = payload.consensus;
  const drivers = {};
  for (const d of s.drivers) drivers[d.id] = d.base;
  return {
    rev: s.rev.init, margin: s.margin.init, pe: s.pe.init, fx: s.fx ? s.fx.init : c.fx0, drivers: drivers,
    eps_mode: c.eps_mode_init, direct_eps: c.direct_eps_init, preset_id: null, scenarios: [],
  };
}

function _parse_weight(text) {
  const t = String(text === undefined || text === null ? "" : text).trim();
  if (t === "") return null;
  const v = fin(Number(t));
  return v !== null && v >= 0 ? v : null;
}

function reduce(state, action, payload) {
  const next = _copy_state(state);
  switch (action.type) {
    case "set": {
      const v = fin(action.value);
      const sl = SLIDER_KEYS.indexOf(action.key) >= 0 ? payload.sliders[action.key] : null;
      if (v === null || !sl || sl.disabled) return state;
      next[action.key] = _clamp(v, sl.min, sl.max);
      if ((action.key === "rev" || action.key === "margin") && _bridge_ok(payload)) {
        next.eps_mode = "bridge";
        next.preset_id = null;
      }
      return next;
    }
    case "driver": {
      const v = fin(action.value);
      const d = payload.sliders.drivers.find((x) => x.id === action.id);
      if (v === null || !d) return state;
      next.drivers[d.id] = _clamp(v, d.min, d.max);
      if (_bridge_ok(payload)) {
        next.eps_mode = "bridge";
        next.preset_id = null;
      }
      return next;
    }
    case "preset": {
      const p = payload.presets.find((x) => x.id === action.id);
      if (!p || p.enabled !== true) return state;
      next.rev = p.rev;
      for (const d of payload.sliders.drivers) next.drivers[d.id] = d.base;
      next.eps_mode = "direct";
      next.direct_eps = p.direct_eps;
      next.preset_id = p.id;
      return next;
    }
    case "reset":
      return Object.assign(init_state(payload), {scenarios: next.scenarios});
    case "save": {
      const d = derive(state, payload);
      next.scenarios.push({name: "情景 " + (state.scenarios.length + 1), eps_usd: d.eps_usd, pe: fin(state.pe),
                           value: d.value, weight: null});
      return next;
    }
    case "weight": {
      if (!next.scenarios[action.index]) return state;
      next.scenarios[action.index].weight = _parse_weight(action.text);
      return next;
    }
    case "delete": {
      if (!next.scenarios[action.index]) return state;
      next.scenarios.splice(action.index, 1);
      return next;
    }
    default:
      return state;
  }
}

function derive(state, payload) {
  const c = payload.consensus;
  let delta = 0.0;
  for (const d of payload.sliders.drivers) {
    const v = fin(state.drivers[d.id]);
    delta = v === null || delta === null ? null : delta + (v - d.base);
  }
  const out = scenario_state({
    rev: state.rev, rev_cons: c.rev_cons, eps_cons: c.eps_cons, margin: state.margin, shares: c.shares,
    half_width: c.half_width, fx: state.fx, price: c.price, pe: state.pe, driver_delta: delta,
    eps_mode: state.eps_mode, direct_eps: state.direct_eps,
  });
  out.pe_rank = pct_rank(payload.pe_sorted, out.implied_pe);
  out.weighted = null;
  out.weighted_reason = "尚未填写权重";
  const sc = state.scenarios;
  if (sc.length >= 1 && sc.every((x) => x.weight !== null)) {
    out.weighted = weighted_value(sc.map((x) => x.value), sc.map((x) => x.weight));
    let total = 0.0;
    for (const x of sc) total += x.weight;
    out.weighted_reason = out.weighted !== null ? null
      : Math.abs(total - 100) > 0.01 ? "权重合计不为 100" : "含无意义情景";
  }
  return out;
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

if (typeof module !== "undefined") {
  module.exports = {init_state, reduce, derive, fmt};
}
