// Thin DOM layer: one state, one reduce(), one render(). Numbers come from payload and
// kernel.js/state.js; text is written with textContent only. The DOM never stores numbers.
(function () {
  "use strict";
  const root = document.documentElement;
  const payload = JSON.parse(document.getElementById("payload").textContent);
  const $ = (id) => document.getElementById(id);
  const q = (sel) => document.querySelector(sel);
  const put = (el, text) => { if (el) el.textContent = text; };
  const controls = () => Array.prototype.slice.call(document.querySelectorAll("input, button"));
  const KINDS = {rev: "num", margin: "pct", pe: "x", fx: "num"};
  const SOURCE = {bridge: "利润率桥", blocked_tail: "尾部：利润率桥不外推", blocked_zone_unknown: "温和区不可得",
                  blocked_input: "输入缺失"};
  const ZONE = {mild: "温和区", tail: "尾部"};

  window.__valuationAudit = function () {
    const text = document.body.innerText || "";
    return {
      selfcheck: root.dataset.selfcheck || null,
      badText: text.match(/NaN|undefined|Infinity/g) || [],
      disabledControls: controls().filter((el) => el.disabled)
        .map((el) => el.id || (el.dataset.preset ? "preset:" + el.dataset.preset : el.className || el.tagName)),
    };
  };

  function bannerLine(text, cls) {
    const ul = $("banner-lines");
    if (!ul) return;
    const li = document.createElement("li");
    li.textContent = text;
    if (cls) li.className = cls;
    ul.appendChild(li);
  }

  put($("hdr-price"), fmt(payload.meta.price, "usd") + "（" + (payload.meta.price_date || "—") + "）");
  if (payload.status.page_blocked) {
    root.dataset.selfcheck = "pass";
    return;
  }

  // ---- load-time self-check: JS kernel and state layer must reproduce the Python vectors ----
  const same = (a, b) => (a === null && b === null) || (typeof a === "string" && a === b)
    || (typeof a === "number" && typeof b === "number" && Math.abs(a - b) <= 1e-9 * Math.max(1, Math.abs(b)));
  const matches = (got, want) => {
    const keys = Object.keys(want);
    return keys.length === Object.keys(got).length && keys.every((k) => same(got[k], want[k]));
  };
  const failed = [];
  for (const item of payload.selfcheck) {
    if (!matches(scenario_state(item.in), item.out)) failed.push(item.name);
  }
  let state = init_state(payload);
  const first = derive(state, payload);
  const want = payload.selfcheck[0].out;
  if (!Object.keys(want).every((k) => same(first[k], want[k]))) failed.push("init_state");

  const sliders = payload.sliders;
  const presets = payload.presets;

  // ---- static numbers: matrix, sample, crowding, segments, guidance notes ----
  const meaningless = (eps) => fin(eps) !== null && eps <= 0;
  payload.matrix.pe.forEach((v, i) => put(q('[data-pe="' + i + '"]'), fmt(v, "x")));
  for (const c of payload.matrix.columns) {
    c.cells.forEach((v, i) => {
      const td = q('[data-cell="' + c.key + ":" + i + '"]');
      put(td, v === null && meaningless(c.eps_usd) ? "无意义" : fmt(v, "usd"));
      if (td && c.bracket && c.bracket.indexOf(i) >= 0) td.classList.add("bracket");
    });
    put(q('[data-eps="' + c.key + '"]'), fmt(c.eps_usd, "usd"));
    put(q('[data-implied="' + c.key + '"]'), c.implied_pe !== null
      ? fmt(c.implied_pe, "x") + " · 分位 " + fmt(c.pe_rank, "pct") : meaningless(c.eps_usd) ? "无意义" : "—");
  }
  const s = payload.matrix.pe_sample;
  put($("pe-sample"), s.note + "；样本 " + (s.start || "—") + " 至 " + (s.end || "—") + "，有效 " + s.n_valid
    + " 天，剔除 TTM≤0 " + s.n_nonpositive + " 天、无 TTM " + s.n_no_ttm + " 天");
  const cy = payload.matrix.cyclical;
  if (cy) {
    put($("cyclical-row"), "周期股双口径：峰值 TTM EPS " + fmt(cy.peak_ttm, "usd") + "（" + (cy.peak_date || "—")
      + "）× P10 = " + fmt(cy.peak_x_low, "usd") + "；中位 TTM EPS " + fmt(cy.mid_ttm, "usd") + " × P50 = "
      + fmt(cy.mid_x_normal, "usd"));
  }
  const cs = payload.consensus;
  const cr = cs.crowding;
  put($("crowding"), "卖方区间（" + cs.crowding_note + "）：营收低值 " + fmt(cr.rev_low_vs_avg, "pct") + " / 高值 "
    + fmt(cr.rev_high_vs_avg, "pct") + "；EPS 低值 " + fmt(cr.eps_low_vs_avg, "pct") + " / 高值 "
    + fmt(cr.eps_high_vs_avg, "pct") + "。NTM 口径 " + (cs.ntm_basis === "quarter_sum" ? "逐季加总" : "财年混合")
    + "，快照 " + (cs.snapshot_date || "—") + "，NTM EPS（本币）" + fmt(cs.eps_cons, "num")
    + "，NTM 营收（本币）" + fmt(cs.rev_cons, "num") + "，fx0 " + fmt(cs.fx0, "num"));

  const seg = payload.segments;
  put($("segments-note"), seg.cleared || (seg.quarters.length ? "" : "无通过门控的分部季度"));
  const segBody = $("segments-rows");
  for (const row of seg.quarters) {
    const tr = document.createElement("tr");
    const th = document.createElement("th");
    th.textContent = row.fiscal_date;
    tr.appendChild(th);
    row.values.forEach((v, i) => {
      const td = document.createElement("td");
      const amount = fin(v) !== null && fin(seg.unit) !== null ? v * seg.unit : null;
      td.textContent = fmt(amount, "num") + "（同比 " + fmt(row.yoy[i], "pct") + "）";
      tr.appendChild(td);
    });
    const basis = document.createElement("td");
    basis.className = "text";
    basis.textContent = row.yoy_basis === "restated" ? "重述口径" : row.yoy_basis === "ledger" ? "台账上年同季" : "无上年同季";
    tr.appendChild(basis);
    if (segBody) segBody.appendChild(tr);
  }
  const notes = $("guidance-notes");
  for (const g of payload.guidance_notes) {
    const li = document.createElement("li");
    li.textContent = g.label + "（" + g.fiscal_date + "）：" + fmt(g.value, "num") + " · " + g.flags.join("、")
      + " · 仅文字标注，不进算术 · " + g.source;
    if (notes) notes.appendChild(li);
  }

  // ---- controls ----
  const dispatch = (action) => {
    state = reduce(state, action, payload);
    render();
  };
  for (const key of Object.keys(KINDS)) {
    const el = $("sl-" + key);
    const sl = sliders[key];
    if (!el || !sl) continue;
    if (fin(sl.min) !== null && fin(sl.max) !== null) {
      el.min = String(sl.min);
      el.max = String(sl.max);
    }
    el.disabled = sl.disabled === true;
    el.addEventListener("input", () => dispatch({type: "set", key: key, value: Number(el.value)}));
  }
  for (const d of sliders.drivers) {
    const el = $("sl-driver-" + d.id);
    if (!el) continue;
    el.min = String(d.min);
    el.max = String(d.max);
    el.addEventListener("input", () => dispatch({type: "driver", id: d.id, value: Number(el.value)}));
  }
  document.querySelectorAll("button.tick").forEach((btn) => {
    const m = payload.markers[Number(btn.dataset.marker)];
    const sl = sliders[m.slider];
    const lo = sl ? fin(sl.min) : null;
    const hi = sl ? fin(sl.max) : null;
    const v = fin(m.value);
    if (lo === null || hi === null || v === null || hi <= lo || v < lo || v > hi) {
      btn.hidden = true;
      return;
    }
    btn.style.left = ((v - lo) / (hi - lo) * 100) + "%";
    btn.disabled = sl.disabled === true;
    btn.addEventListener("click", () => dispatch({type: "set", key: m.slider, value: m.value}));
  });
  document.querySelectorAll("[data-marker-value]").forEach((td) => {
    const m = payload.markers[Number(td.dataset.markerValue)];
    const btn = q('button.tick[data-marker="' + td.dataset.markerValue + '"]');
    put(td, fmt(m.value, KINDS[m.slider]) + (btn && btn.hidden ? "（超出滑轨）" : ""));
  });
  document.querySelectorAll("button[data-preset]").forEach((btn) => {
    btn.addEventListener("click", () => dispatch({type: "preset", id: btn.dataset.preset}));
  });
  $("btn-reset").addEventListener("click", () => dispatch({type: "reset"}));
  $("btn-save").addEventListener("click", () => dispatch({type: "save"}));

  // ---- render ----
  function sourceText(d) {
    if (d.eps_source === "direct") {
      const p = presets.find((x) => x.id === state.preset_id);
      return p ? p.label + " 回放" : "卖方共识（利润率桥禁用）";
    }
    return SOURCE[d.eps_source] || "—";
  }

  function renderScenarios(d) {
    const body = $("scenario-rows");
    const rebuild = body.children.length !== state.scenarios.length;
    if (rebuild) {
      while (body.firstChild) body.removeChild(body.firstChild);
      state.scenarios.forEach((sc, i) => {
        const tr = document.createElement("tr");
        for (let c = 0; c < 4; c += 1) tr.appendChild(document.createElement("td"));
        tr.children[0].className = "text";
        const wtd = document.createElement("td");
        const input = document.createElement("input");
        input.type = "text";
        input.inputMode = "decimal";
        input.dataset.weight = String(i);
        input.value = sc.weight === null ? "" : String(sc.weight);
        input.addEventListener("input", () => dispatch({type: "weight", index: i, text: input.value}));
        wtd.appendChild(input);
        tr.appendChild(wtd);
        const xtd = document.createElement("td");
        const del = document.createElement("button");
        del.type = "button";
        del.textContent = "删除";
        del.addEventListener("click", () => dispatch({type: "delete", index: i}));
        xtd.appendChild(del);
        tr.appendChild(xtd);
        body.appendChild(tr);
      });
    }
    state.scenarios.forEach((sc, i) => {
      const cells = body.children[i].children;
      put(cells[0], sc.name);
      put(cells[1], fmt(sc.eps_usd, "usd"));
      put(cells[2], fmt(sc.pe, "x"));
      put(cells[3], sc.value === null && meaningless(sc.eps_usd) ? "无意义" : fmt(sc.value, "usd"));
    });
    put($("out-weighted"), fmt(d.weighted, "usd"));
    put($("out-weighted-reason"), d.weighted_reason ? "（" + d.weighted_reason + "）" : "");
  }

  function render() {
    const d = derive(state, payload);
    for (const key of Object.keys(KINDS)) {
      const el = $("sl-" + key);
      if (!el) continue;
      if (fin(state[key]) !== null) el.value = String(state[key]);
      put($("val-" + key), fmt(state[key], KINDS[key]));
    }
    for (const dr of sliders.drivers) {
      const el = $("sl-driver-" + dr.id);
      const v = state.drivers[dr.id];
      if (el && fin(v) !== null) el.value = String(v);
      put($("val-driver-" + dr.id), fmt(v, "num") + "（相对基准 " + fmt(fin(v) !== null ? v - dr.base : null, "num") + "）");
    }
    put($("out-eps"), fmt(d.eps_usd, "usd"));
    put($("out-eps-source"), sourceText(d));
    put($("out-zone"), ZONE[d.zone] || "—");
    put($("out-implied-pe"), d.implied_pe !== null ? fmt(d.implied_pe, "x") : meaningless(d.eps_usd) ? "无意义" : "—");
    put($("out-pe-rank"), fmt(d.pe_rank, "pct"));
    put($("out-value"), d.value !== null ? fmt(d.value, "usd") : meaningless(d.eps_usd) ? "无意义" : "—");
    put($("out-value-vs-price"), fmt(d.value_vs_price, "pct"));
    renderScenarios(d);
  }

  render();
  if (failed.length) {
    root.dataset.selfcheck = "fail";
    bannerLine("自检失败（页面已锁定）：" + failed.join("、"), "fail");
    controls().forEach((el) => { el.disabled = true; });
  } else {
    root.dataset.selfcheck = "pass";
  }
})();
