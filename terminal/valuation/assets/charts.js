// Four-panel draw lists (plan v2 §8). Pure functions: (view, payload) → [{t: rect|line|path|circle|text, …}].
// No DOM access. Every text item carries x, y, size, anchor and an estimated width w; its box is
// x-range from anchor/w and y-range [y - size, y + 0.25 size]. Layout keeps boxes apart and a final
// declutter pass drops lower-priority labels that would still collide, so no two boxes ever intersect.
"use strict";

const VIEW_W = 560;
const VIEW_H = 340;
const TONE = {consensus: "#6e7781", bull: "#1a7f37", base: "#0969da", bear: "#cf222e", custom: "#5b4bdb"};
const C_PRICE = "#d4730b";
const C_EV = "#1f3a8a";
const INK = "#1f2328";
const MUTED = "#57606a";
const GRID = "#eaeef2";
const ROW_NAMES = {consensus: "卖方共识", custom: "Custom"};

function text_w(s, size) {
  let w = 0;
  for (const ch of String(s)) {
    const c = ch.codePointAt(0);
    const wide = (c >= 0x2e80 && c <= 0x9fff) || (c >= 0xac00 && c <= 0xd7af) || (c >= 0xf900 && c <= 0xfaff)
      || (c >= 0xfe30 && c <= 0xfe4f) || (c >= 0xff00 && c <= 0xffef) || (c >= 0x3000 && c <= 0x303f);
    w += (wide ? 1.0 : 0.58) * size;
  }
  return w;
}

function text_box(it) {
  const x0 = it.anchor === "end" ? it.x - it.w : it.anchor === "middle" ? it.x - it.w / 2 : it.x;
  return {x0: x0, x1: x0 + it.w, y0: it.y - it.size, y1: it.y + 0.25 * it.size};
}

function boxes_hit(a, b) {
  return a.x0 < b.x1 && b.x0 < a.x1 && a.y0 < b.y1 && b.y0 < a.y1;
}

function spread(ys, gap, lo, hi) {
  const order = ys.map((y, i) => [y, i]).sort((a, b) => a[0] - b[0]);
  const out = new Array(ys.length);
  let prev = null;
  for (const [y, i] of order) {
    let v = Math.max(y, lo);
    if (prev !== null && v < prev + gap) v = prev + gap;
    out[i] = v;
    prev = v;
  }
  // push back up from the bottom edge when the stack overflows hi
  let next = null;
  for (let k = order.length - 1; k >= 0; k -= 1) {
    const i = order[k][1];
    let v = Math.min(out[i], hi);
    if (next !== null && v > next - gap) v = next - gap;
    out[i] = v;
    next = v;
  }
  return out;
}

function _t(s, x, y, size, anchor, extra) {
  const s2 = String(s);
  return Object.assign({t: "text", text: s2, x: x, y: y, size: size, anchor: anchor, w: text_w(s2, size),
                        fill: INK, prio: 1}, extra || {});
}

// Clamp each text box into the viewBox (EDGE px inset: the 0.58 width estimate runs a little short for
// capitals), then drop lower-priority texts that still collide.
const EDGE = 4;
function _finish(items) {
  for (const it of items) {
    if (it.t !== "text") continue;
    const b = text_box(it);
    if (b.x0 < EDGE) it.x += EDGE - b.x0;
    if (b.x1 > VIEW_W - EDGE) it.x -= b.x1 - (VIEW_W - EDGE);
    if (b.y0 < 0) it.y += -b.y0;
    if (b.y1 > VIEW_H) it.y -= b.y1 - VIEW_H;
  }
  const texts = items.map((it, i) => [it, i]).filter((p) => p[0].t === "text")
    .sort((a, b) => (b[0].prio - a[0].prio) || (a[1] - b[1]));
  const kept = [];
  const drop = new Set();
  for (const [it, i] of texts) {
    const b = text_box(it);
    if (kept.some((k) => boxes_hit(k, b))) drop.add(i);
    else kept.push(b);
  }
  return items.filter((_, i) => !drop.has(i));
}

function _money(x, currency) {
  return fmt_money(x, currency);
}

// Axis ticks: strip trailing fraction zeros ("$400.0B" → "$400B", "NT$1.50T" → "NT$1.5T").
function _trim(s) {
  return s.replace(/(\.\d*?)0+(?=[TBM]$)/, "$1").replace(/\.(?=[TBM]$)/, "");
}

function _axis_money(x, currency) {
  return _trim(fmt_money(x, currency));
}

// Same amount without the currency prefix (Panel 3 states the unit once in its corner).
function _bare_money(x, currency) {
  return fmt_money(x, currency).replace(/^(−?)(NT\$|\$|[A-Z]{3} )/, "$1");
}

function _pct0(x) {
  return fmt_signed(x, 0);
}

function _nice_step(span, n) {
  const raw = span / Math.max(n, 1);
  const p = Math.pow(10, Math.floor(Math.log10(raw)));
  const m = raw / p;
  return (m <= 1 ? 1 : m <= 2 ? 2 : m <= 5 ? 5 : 10) * p;
}

function _ticks(lo, hi, n) {
  if (!(hi > lo)) return [];
  const step = _nice_step(hi - lo, n);
  const out = [];
  for (let v = Math.ceil(lo / step) * step; v <= hi + step * 1e-9; v += step) out.push(Math.round(v / step) * step);
  return out;
}

function _scenario_rows(view, payload) {
  const rows = [];
  for (const sc of payload.scenarios) {
    const out = sc.out;
    const ok = sc.enabled === true && out;
    const band = sc.band || {};
    rows.push({id: sc.id, tone: sc.tone, label: sc.id === "consensus" ? ROW_NAMES.consensus : sc.label,
               enabled: !!ok, reason: sc.reason, weight: view.weights ? view.weights[sc.id] : sc.weight,
               lo: ok ? fin(band.lo) : null, mid: ok ? fin(out.value) : null, hi: ok ? fin(band.hi) : null,
               pct: ok ? fin(out.value_vs_price) : null, eps_state: ok ? out.eps_state : "missing"});
  }
  const live = view.live;
  rows.push({id: "custom", tone: "custom", label: ROW_NAMES.custom, enabled: true, reason: null, weight: null,
             lo: fin(live.value_lo), mid: fin(live.value), hi: fin(live.value_hi), pct: fin(live.value_vs_price),
             eps_state: live.eps_state});
  return rows;
}

function value_label(r, max_w) {
  if (!r.enabled) return "不可用";
  if (r.eps_state === "nonpos") return "负盈利（周期谷底）";
  if (r.mid === null) return "不可得";
  const whole = Math.max(r.mid, r.hi !== null ? r.hi : r.mid) >= 100;
  const px = (v) => (whole ? "$" + Math.round(v).toLocaleString("en-US") : fmt_px(v));
  const head = px(r.mid) + " (" + _pct0(r.pct) + ")";
  if (r.lo !== null && r.hi !== null && r.hi > r.lo) {
    const exact = head + " [" + px(r.lo).replace("$", "") + "–" + px(r.hi).replace("$", "") + "]";
    if (text_w(exact, 11) <= max_w) return exact;
    const lo = Math.round(r.lo);
    const hi = Math.round(r.hi);
    const rounded = head + " [" + lo.toLocaleString("en-US") + "–" + hi.toLocaleString("en-US") + "]";
    if (hi > lo && text_w(rounded, 11) <= max_w) return rounded;
  }
  return head;
}

// ---- Panel 1 · Scenario Fair Value Ranges vs. Current Price ----
function panel1(view, payload) {
  const items = [];
  const rows = _scenario_rows(view, payload);
  const price = fin(payload.consensus.price);
  const ev = fin(view.ev);
  const X0 = 100;
  const X1 = 380;
  const vals = [];
  for (const r of rows) for (const v of [r.lo, r.mid, r.hi]) if (v !== null && v > 0) vals.push(v);
  if (price !== null) vals.push(price);
  if (ev !== null) vals.push(ev);
  let lo = vals.length ? Math.min.apply(null, vals) * 0.9 : 0;
  let hi = vals.length ? Math.max.apply(null, vals) * 1.05 : 1;
  if (!(hi > lo)) hi = lo + 1;
  lo = Math.max(0, lo);
  const sx = (v) => X0 + (Math.min(Math.max(v, lo), hi) - lo) / (hi - lo) * (X1 - X0);
  const top = 44;
  const bottom = 292;
  for (const v of _ticks(lo, hi, 6)) {
    const x = sx(v);
    items.push({t: "line", x1: x, y1: top, x2: x, y2: bottom, stroke: GRID, sw: 1});
    items.push(_t("$" + Math.round(v).toLocaleString("en-US"), x, 306, 10, "middle",
                  {fill: MUTED, prio: 0, role: "axis"}));
  }
  rows.forEach((r, i) => {
    const y = 60 + i * 52;
    const color = TONE[r.tone] || TONE.consensus;
    items.push(_t(r.label, 92, y - 1, 12, "end", {fill: INK, weight: 600, prio: 3, role: "row-label"}));
    const sub = r.id === "custom" ? "实时演算" : !r.enabled ? "门控停用"
      : fin(r.weight) === null ? "权重未填" : "权重 " + r.weight + "%";
    items.push(_t(sub, 92, y + 13, 10, "end", {fill: MUTED, prio: 2, role: "row-sub"}));
    items.push({t: "rect", x: X0, y: y - 11, w: X1 - X0, h: 22, fill: i % 2 ? "#ffffff" : "#f6f8fa", rx: 3,
                role: "row-bg"});
    if (r.enabled && r.eps_state !== "nonpos" && r.mid !== null) {
      const a = r.lo !== null ? sx(r.lo) : sx(r.mid);
      const b = r.hi !== null ? sx(r.hi) : sx(r.mid);
      const w = Math.max(b - a, 3);
      items.push({t: "rect", x: w > b - a ? (a + b) / 2 - w / 2 : a, y: y - 8, w: w, h: 16,
                  fill: color, opacity: r.id === "custom" ? 0.9 : 0.75, rx: 3,
                  stroke: r.id === "custom" ? "#2d2391" : null, sw: r.id === "custom" ? 2 : 0, role: "bar",
                  key: r.id});
      items.push({t: "circle", cx: sx(r.mid), cy: y, r: 4, fill: "#ffffff", stroke: color, sw: 1.5,
                  role: "mid", key: r.id});
    }
    const label = value_label(r, 164);
    items.push(_t(label, 388, y + 4, 11, "start",
                  {fill: r.enabled && r.mid !== null ? INK : MUTED, weight: r.id === "custom" ? 600 : 400, prio: 3,
                   role: "value-label", key: r.id}));
  });
  if (price !== null) {
    const x = sx(price);
    items.push({t: "line", x1: x, y1: 40, x2: x, y2: bottom, stroke: C_PRICE, sw: 1.5, dash: "5 4", role: "price"});
    const s = "Current " + fmt(price, "usd");
    const w = text_w(s, 11);
    const cx = Math.min(Math.max(x, X0 + w / 2), X1 - w / 2);
    items.push(_t(s, cx, 34, 11, "middle", {fill: C_PRICE, weight: 600, prio: 4, role: "price-label"}));
  }
  if (ev !== null) {
    const x = sx(ev);
    items.push({t: "line", x1: x, y1: 40, x2: x, y2: 318, stroke: C_EV, sw: 1.5, dash: "1 3", role: "ev"});
    const s = "EV " + fmt_px(ev) + " (" + _pct0(view.ev_vs_price) + ")";
    const w = text_w(s, 11);
    const cx = Math.min(Math.max(x, X0 + w / 2), X1 - w / 2);
    items.push(_t(s, cx, 332, 11, "middle", {fill: C_EV, weight: 600, prio: 4, role: "ev-label"}));
  }
  return _finish(items);
}

// ---- Panel 2 · NTM Revenue Stack by Segment ----
function _bars(view, payload) {
  const segs = payload.model.segments;
  const bars = [];
  for (const sc of payload.scenarios) {
    const ok = sc.enabled === true && sc.seg_rev;
    bars.push({id: sc.id, tone: sc.tone, label: sc.id === "consensus" ? ROW_NAMES.consensus : sc.label,
               enabled: !!ok, blocks: ok ? segs.map((s, i) => ({name: s.name, color: s.color, value: sc.seg_rev[i]}))
                 : []});
  }
  bars.push({id: "custom", tone: "custom", label: ROW_NAMES.custom, enabled: true,
             blocks: segs.map((s, i) => ({name: s.name, color: s.color, value: view.seg_rev[i]}))});
  for (const b of bars) {
    let total = 0.0;
    for (const k of b.blocks) total += fin(k.value) !== null ? Math.max(k.value, 0) : 0;
    b.total = b.enabled ? total : null;
  }
  return bars;
}

function panel2(view, payload) {
  const items = [];
  const cur = payload.model.currency;
  const bars = _bars(view, payload);
  const X0 = 48;
  const X1 = 552;
  const Y0 = 40;
  const Y1 = 292;
  let max = 0;
  for (const b of bars) if (b.total !== null && b.total > max) max = b.total;
  const step = _nice_step(max > 0 ? max * 1.12 : 1, 4);
  const top = Math.max(step, Math.ceil(max * 1.12 / step) * step);
  const sy = (v) => Y1 - v / top * (Y1 - Y0);
  for (let v = 0; v <= top + step * 1e-9; v += step) {
    const y = sy(v);
    items.push({t: "line", x1: X0, y1: y, x2: X1, y2: y, stroke: v === 0 ? "#d0d7de" : GRID, sw: 1});
    items.push(_t(v === 0 ? "0" : _axis_money(v, cur), 44, y + 3.5, 10, "end", {fill: MUTED, prio: 0, role: "axis"}));
  }
  const slot = (X1 - X0) / bars.length;
  bars.forEach((b, i) => {
    const cx = X0 + slot * (i + 0.5);
    const x = cx - 28;
    items.push(_t(b.label, cx, 310, 11, "middle", {fill: b.id === "custom" ? TONE.custom : INK,
                                                   weight: 600, prio: 3, role: "bar-label"}));
    if (!b.enabled) {
      items.push({t: "rect", x: x, y: sy(max * 0.5), w: 56, h: Y1 - sy(max * 0.5), fill: "#f6f8fa",
                  stroke: "#8c959f", sw: 1, dash: "4 3", rx: 2, role: "disabled"});
      items.push(_t("不可用", cx, Y1 - 8, 10, "middle", {fill: MUTED, prio: 2, role: "disabled-label"}));
      return;
    }
    let acc = 0.0;
    b.blocks.forEach((k, j) => {
      const v = fin(k.value) !== null ? Math.max(k.value, 0) : 0;
      const yTop = sy(acc + v);
      const h = sy(acc) - yTop;
      acc += v;
      if (h <= 0) return;
      items.push({t: "rect", x: x, y: yTop, w: 56, h: h, fill: k.color, stroke: "#ffffff", sw: 1, role: "block",
                  key: b.id + ":" + j});
      const s = _money(v, cur);
      if (h >= 14 && text_w(s, 10) <= 52) {
        items.push(_t(s, cx, yTop + h / 2 + 3.5, 10, "middle",
                      {fill: "#ffffff", prio: 1, role: "block-label", key: b.id + ":" + j}));
      }
    });
    if (b.id === "custom") {
      items.push({t: "rect", x: x - 2, y: sy(acc) - 2, w: 60, h: Y1 - sy(acc) + 2, fill: "none",
                  stroke: TONE.custom, sw: 2, rx: 2, role: "custom-frame"});
    }
    items.push(_t(_money(b.total, cur), cx, sy(acc) - 6, 11, "middle",
                  {fill: INK, weight: 600, prio: 2, role: "bar-total"}));
  });
  // legend: measured left-to-right; names truncated when the row would overflow
  const segs = payload.model.segments;
  const full = segs.map((s) => s.name);
  const width = (names) => names.reduce((acc, n) => acc + 14 + text_w(n, 10) + 12, 0) - 12;
  const names = width(full) > X1 - X0 ? full.map((n) => (n.length > 10 ? n.slice(0, 10) + "…" : n)) : full;
  let lx = X0;
  segs.forEach((s, i) => {
    items.push({t: "rect", x: lx, y: 322, w: 10, h: 10, fill: s.color, rx: 2, role: "legend-swatch"});
    items.push(_t(names[i], lx + 14, 330.5, 10, "start", {fill: MUTED, prio: 1, role: "legend"}));
    lx += 14 + text_w(names[i], 10) + 12;
  });
  return _finish(items);
}

// ---- Panel 3 · Quarterly Revenue Trajectory ----
function _xlabel(day, kind) {
  return String(day).slice(2, 7) + kind;
}

function panel3(view, payload) {
  const items = [];
  const t = payload.trajectory;
  const cur = payload.model.currency;
  const hist = (t.hist || []).filter((h) => fin(h.revenue) !== null);
  const X0 = 52;
  const X1 = 468;
  const Y0 = 40;
  const Y1 = 292;
  const future = t.future_dates ? t.future_dates : [];
  const n = hist.length + future.length;
  if (n === 0) return _finish([_t("历史季度营收不可得", 280, 170, 12, "middle", {fill: MUTED})]);
  const step = n > 1 ? (X1 - X0) / (n - 1) : 0;
  const px = (i) => X0 + i * step;
  const lines = [];
  if (future.length) {
    for (const sc of payload.scenarios) {
      if (sc.enabled === true && sc.quarters) lines.push({id: sc.id, tone: sc.tone, label: sc.id === "consensus"
        ? "共识" : sc.label, values: sc.quarters});
    }
    if (view.quarters) lines.push({id: "custom", tone: "custom", label: "Custom", values: view.quarters});
  }
  const vals = hist.map((h) => h.revenue);
  for (const l of lines) for (const v of l.values) if (fin(v) !== null) vals.push(v);
  let lo = Math.min.apply(null, vals);
  let hi = Math.max.apply(null, vals);
  const pad = (hi - lo) * 0.08 || hi * 0.1 || 1;
  lo = Math.max(0, lo - pad);
  hi = hi + pad;
  const ticks = _ticks(lo, hi, 5);
  const sy = (v) => Y1 - (v - lo) / (hi - lo) * (Y1 - Y0);
  for (const v of ticks) {
    const y = sy(v);
    items.push({t: "line", x1: X0, y1: y, x2: X1, y2: y, stroke: GRID, sw: 1});
    items.push(_t(_trim(_bare_money(v, cur)), 48, y + 3.5, 10, "end",
                  {fill: MUTED, prio: 0, role: "axis"}));
  }
  items.push(_t("单位 " + (cur === "USD" ? "USD" : cur === "TWD" ? "NT$" : String(cur)), 8, 22, 10, "start",
                {fill: MUTED, prio: 2, role: "unit"}));
  hist.forEach((h, i) => items.push(_t(_xlabel(h.fiscal_date, "A"), px(i), 310, 10, "middle",
                                        {fill: MUTED, prio: 1, role: "x-label"})));
  future.forEach((d, i) => items.push(_t(_xlabel(d, "E"), px(hist.length + i), 310, 10, "middle",
                                          {fill: MUTED, prio: 1, role: "x-label"})));
  const last = hist.length - 1;
  if (last >= 0 && future.length) {
    items.push({t: "line", x1: px(last), y1: Y0 - 4, x2: px(last), y2: Y1, stroke: C_PRICE, sw: 1.5, dash: "2 3",
                role: "latest"});
    items.push(_t("最新已公告季", px(last), Y0 - 10, 10, "middle", {fill: C_PRICE, prio: 2, role: "latest-label"}));
  }
  items.push({t: "path", pts: hist.map((h, i) => [px(i), sy(h.revenue)]), stroke: INK, sw: 2, role: "hist"});
  hist.forEach((h, i) => items.push({t: "circle", cx: px(i), cy: sy(h.revenue), r: 3, fill: INK, role: "hist-dot"}));
  const ends = [];
  for (const l of lines) {
    const pts = [[px(last), sy(hist[last].revenue)]];
    l.values.forEach((v, i) => { if (fin(v) !== null) pts.push([px(hist.length + i), sy(v)]); });
    const color = TONE[l.tone] || TONE.consensus;
    items.push({t: "path", pts: pts, stroke: color, sw: l.id === "custom" ? 3 : 1.5,
                dash: l.id === "consensus" ? "5 4" : null, role: "line", key: l.id});
    const endv = l.values[l.values.length - 1];
    items.push({t: "circle", cx: pts[pts.length - 1][0], cy: pts[pts.length - 1][1], r: l.id === "custom" ? 4 : 3,
                fill: color, role: "end-dot", key: l.id});
    ends.push({l: l, y: sy(endv) + 3.5, color: color, v: endv});
  }
  const ys = spread(ends.map((e) => e.y), 13, 46, 292);
  ends.forEach((e, i) => {
    items.push(_t(e.l.label + " " + _bare_money(e.v, cur), 472, ys[i], 10, "start",
                  {fill: e.color, weight: e.l.id === "custom" ? 600 : 400, prio: 3, role: "end-label", key: e.l.id}));
  });
  return _finish(items);
}

// ---- Panel 4 · Valuation Matrix Heatmap ----
function _hex(c) {
  return [parseInt(c.slice(1, 3), 16), parseInt(c.slice(3, 5), 16), parseInt(c.slice(5, 7), 16)];
}

function _mix(a, b, f) {
  const x = _hex(a);
  const y = _hex(b);
  const out = x.map((v, i) => Math.round(v + (y[i] - v) * f));
  return "#" + out.map((v) => v.toString(16).padStart(2, "0")).join("");
}

function heat_t(value, price) {
  value = fin(value);
  price = fin(price);
  if (value === null || price === null || value <= 0 || price <= 0) return null;
  const t = Math.log(value / price) / Math.log(2);
  return Math.max(-1, Math.min(1, t));
}

function heat_color(t) {
  if (t === null) return "#eaeef2";
  return t < 0 ? _mix("#fee08b", "#d73027", -t) : _mix("#fee08b", "#1a9850", t);
}

function panel4(view, payload) {
  const items = [];
  const h = payload.heatmap;
  if (!h || !h.eps_levels) {
    return _finish([_t("热力矩阵不适用", 280, 160, 12, "middle", {fill: MUTED}),
                    _t((h && h.reason) || "", 280, 182, 10, "middle", {fill: MUTED})]);
  }
  const price = payload.consensus.price;
  const GX = 84;
  const GY = 52;
  const CW = 93.6;
  const CH = 44;
  const n = h.eps_levels.length;
  const m = h.pe_levels.length;
  const rowY = (r) => GY + (n - 1 - r) * CH;      // highest EPS on top
  const cellText = [];                             // drawn after the marks so dots never hide a number
  items.push(_t("EPS \\ P/E", 80, 44, 10, "end", {fill: MUTED, prio: 1, role: "corner"}));
  h.pe_levels.forEach((pe, j) => items.push(_t(fin(pe) === null ? "—" : pe.toFixed(1) + "x", GX + CW * (j + 0.5), 44, 11,
                                                  "middle", {fill: INK, weight: 600, prio: 2, role: "col-head"})));
  h.eps_levels.forEach((eps, i) => items.push(_t(fmt(eps, "usd"), 80, rowY(i) + CH / 2 + 4, 11, "end",
                                                  {fill: INK, weight: 600, prio: 2, role: "row-head"})));
  for (let i = 0; i < n; i += 1) {
    for (let j = 0; j < m; j += 1) {
      const v = h.cells[i][j];
      const t = heat_t(v, price);
      const x = GX + CW * j;
      const y = rowY(i);
      const bg = heat_color(t);
      items.push({t: "rect", x: x, y: y, w: CW, h: CH, fill: bg, stroke: "#ffffff", sw: 1, role: "cell"});
      const ink = t !== null && Math.abs(t) > 0.7 ? "#ffffff" : INK;
      cellText.push(_t(fmt_px(v), x + CW / 2, y + CH / 2 - 2, 11, "middle", {fill: ink, weight: 600, prio: 1,
                                                                           halo: bg, role: "cell-value"}));
      cellText.push(_t(fmt_signed(h.pct[i][j], 0), x + CW / 2, y + CH / 2 + 13, 10, "middle",
                       {fill: ink, prio: 1, halo: bg, role: "cell-pct"}));
    }
  }
  if (h.price_cell) {
    const [r, c] = h.price_cell;
    items.push({t: "rect", x: GX + CW * c + 1, y: rowY(r) + 1, w: CW - 2, h: CH - 2, fill: "none", stroke: C_PRICE,
                sw: 2, role: "price-cell"});
  }
  const dot = (r_pos, c_pos, color, key) => {
    const cx = GX + CW * (c_pos + 0.5);
    const cy = GY + (n - 1 - r_pos) * CH + CH / 2;
    items.push({t: "circle", cx: cx, cy: cy, r: 5, fill: color, stroke: "#ffffff", sw: 1.5, opacity: 0.85,
                role: "mark", key: key});
  };
  const ring = (r, c, color, key) => items.push({t: "rect", x: GX + CW * c + 3, y: rowY(r) + 3, w: CW - 6,
                                                 h: CH - 6, fill: "none", stroke: color, sw: 1.5, dash: "3 2",
                                                 role: "mark-cell", key: key});
  for (const mk of h.marks || []) {
    if (fin(mk.r_pos) === null || fin(mk.c_pos) === null) continue;
    ring(Math.round(mk.r_pos), Math.round(mk.c_pos), TONE.base, mk.id);
    dot(mk.r_pos, mk.c_pos, TONE.base, mk.id);
  }
  const heat = view.heat;
  if (heat && fin(heat.c_pos) !== null) {
    if (view.live.eps_state === "nonpos") {
      const cx = GX + CW * (heat.c_pos + 0.5);
      items.push({t: "circle", cx: cx, cy: GY + n * CH, r: 5, fill: TONE.custom, stroke: "#ffffff", sw: 1.5,
                  role: "mark", key: "custom"});
      items.push(_t("负盈利", cx, GY + n * CH + 16, 10, "middle", {fill: TONE.custom, weight: 600, prio: 3,
                                                                  role: "custom-note"}));
    } else if (fin(heat.r_pos) !== null) {
      ring(Math.round(heat.r_pos), Math.round(heat.c_pos), TONE.custom, "custom");
      dot(heat.r_pos, heat.c_pos, TONE.custom, "custom");
    }
  }
  // legend row: marks on the left, colour scale on the right
  let lx = GX;
  const legend = [["rect", C_PRICE, "现价所在格"], ["dot", TONE.base, "Base"], ["dot", TONE.custom, "Custom"]];
  for (const [kind, color, label] of legend) {
    if (kind === "rect") items.push({t: "rect", x: lx, y: 296, w: 10, h: 10, fill: "none", stroke: color, sw: 2});
    else items.push({t: "circle", cx: lx + 5, cy: 301, r: 4.5, fill: color});
    items.push(_t(label, lx + 14, 305, 10, "start", {fill: MUTED, prio: 1, role: "legend"}));
    lx += 14 + text_w(label, 10) + 14;
  }
  const SX = 352;
  const SW = 200;
  for (let k = 0; k < 10; k += 1) {
    const t = -1 + (k + 0.5) / 5;
    items.push({t: "rect", x: SX + SW / 10 * k, y: 312, w: SW / 10, h: 8, fill: heat_color(t)});
  }
  items.push(_t("−50%", SX, 333, 10, "start", {fill: MUTED, prio: 1, role: "scale"}));
  items.push(_t("现价", SX + SW / 2, 333, 10, "middle", {fill: MUTED, prio: 1, role: "scale"}));
  items.push(_t("+100%", SX + SW, 333, 10, "end", {fill: MUTED, prio: 1, role: "scale"}));
  return _finish(items.concat(cellText));
}

if (typeof module !== "undefined") {
  module.exports = {text_w, text_box, boxes_hit, spread, value_label, panel1, panel2, panel3, panel4, heat_t,
                    heat_color, VIEW_W, VIEW_H, TONE};
}
