// Thin DOM layer v2: one state, one reduce(), one render() per animation frame. Numbers come from
// payload, kernel.js, state.js and charts.js; text is written with textContent and SVG nodes with
// createElementNS only. The DOM never stores numbers.
(function () {
  "use strict";
  const root = document.documentElement;
  const payload = JSON.parse(document.getElementById("payload").textContent);
  const $ = (id) => document.getElementById(id);
  const put = (el, text) => { if (el) el.textContent = text; };
  const controls = () => Array.prototype.slice.call(document.querySelectorAll("input, button"));
  const SVG_NS = "http://www.w3.org/2000/svg";
  const PANELS = [["p1", panel1], ["p2", panel2], ["p3", panel3], ["p4", panel4]];

  function svgBoxes(svg) {
    const out = [];
    svg.querySelectorAll("text").forEach((el) => {
      const b = el.getBBox();
      out.push({x0: b.x, y0: b.y, x1: b.x + b.width, y1: b.y + b.height, text: el.textContent});
    });
    return out;
  }

  window.__valuationAudit = function () {
    const text = document.body.innerText || "";
    const lonely = [];
    document.querySelectorAll("body *").forEach((el) => {
      if (el.children.length === 0 && el.textContent.trim() === "—" && el.offsetParent !== null) {
        lonely.push(el.id || el.className || el.tagName);
      }
    });
    const overlaps = [];
    for (const [id] of PANELS) {
      const svg = $(id);
      if (!svg) continue;
      const boxes = svgBoxes(svg);
      for (let i = 0; i < boxes.length; i += 1) {
        const a = boxes[i];
        if (a.x0 < -0.5 || a.y0 < -0.5 || a.x1 > VIEW_W + 0.5 || a.y1 > VIEW_H + 0.5) {
          overlaps.push(id + ": outside " + a.text);
        }
        for (let j = i + 1; j < boxes.length; j += 1) {
          const b = boxes[j];
          if (a.x0 < b.x1 - 0.5 && b.x0 < a.x1 - 0.5 && a.y0 < b.y1 - 0.5 && b.y0 < a.y1 - 0.5) {
            overlaps.push(id + ": " + a.text + " × " + b.text);
          }
        }
      }
    }
    return {
      selfcheck: root.dataset.selfcheck || null,
      badText: text.match(/NaN|undefined|Infinity/g) || [],
      lonelyDash: lonely,
      textOverlaps: overlaps,
      disabledControls: controls().filter((el) => el.disabled)
        .map((el) => el.id || (el.dataset.preset ? "preset:" + el.dataset.preset : el.className || el.tagName)),
    };
  };

  function bannerLine(text, cls) {
    const box = $("banner");
    const ul = $("banner-lines");
    if (!ul) return;
    const li = document.createElement("li");
    li.textContent = text;
    if (cls) li.className = cls;
    ul.appendChild(li);
    if (box) box.hidden = false;
  }

  if (payload.status.page_blocked) {
    root.dataset.selfcheck = "pass";
    return;
  }

  const c = payload.consensus;
  const m = payload.model;
  const sliders = payload.sliders;
  const cur = m.currency;

  // ---- load-time self-check: JS kernel and state layer must reproduce the Python vectors ----
  const same = (a, b) => (a === null && b === null) || (typeof a === "string" && a === b)
    || (typeof a === "number" && typeof b === "number" && Math.abs(a - b) <= 1e-9 * Math.max(1, Math.abs(b)));
  const matches = (got, want) => {
    if (!got || !want) return got === want;
    const keys = Object.keys(want);
    return keys.length === Object.keys(got).length && keys.every((k) => same(got[k], want[k]));
  };
  const failed = [];
  for (const item of payload.selfcheck) {
    if (!matches(live_state(item.in), item.out)) failed.push(item.name);
  }
  const init = init_state(payload);
  if (!matches(derive(init, payload).live, payload.selfcheck[0].out)) failed.push("init_state");
  for (const sc of payload.scenarios) {
    if (sc.enabled === true && !matches(derive(reduce(init, {type: "preset", id: sc.id}, payload), payload).live,
                                        sc.out)) {
      failed.push("preset:" + sc.id);
    }
  }
  let state = init;

  // ---- static numbers ----
  put($("kpi-price"), fmt(c.price, "usd"));
  put($("kpi-price-date"), "收盘 " + (c.price_date || "日期缺失"));
  put($("kpi-mktcap"), fmt_money(c.mkt_cap, "USD"));
  put($("kpi-window"), c.ntm_window.label);
  put($("kpi-implied-pe"), c.pe_init !== null ? fmt(c.pe_init, "x") : "不适用");
  put($("kpi-implied-sub"), "NTM 共识 EPS " + fmt(c.ntm_eps_usd, "usd"));
  const ttl = payload.trajectory;
  put($("p3-note"), ttl.note || ttl.reason || "");
  const cy = payload.matrix.cyclical;
  const sample = payload.matrix.pe_sample;
  const cr = c.crowding;
  put($("p4-note-cycle"), cy ? "周期股双口径：峰值 TTM EPS " + fmt(cy.peak_ttm, "usd") + "（" + (cy.peak_date || "日期缺失")
    + "）× P10 = " + fmt(cy.peak_x_low, "usd") + "；中位 TTM EPS " + fmt(cy.mid_ttm, "usd") + " × P50 = "
    + fmt(cy.mid_x_normal, "usd") : "非周期股台账：不列峰值口径");
  put($("p4-note-sample"), sample.note + "：" + (sample.start || "起点缺失") + " 至 " + (sample.end || "终点缺失")
    + "，有效 " + sample.n_valid + " 天，剔除 TTM≤0 " + sample.n_nonpositive + " 天、无 TTM " + sample.n_no_ttm + " 天");
  put($("p4-note-crowd"), "卖方区间（" + c.crowding_note + "）：营收低/高值 " + fmt_signed(cr.rev_low_vs_avg, 1) + " / "
    + fmt_signed(cr.rev_high_vs_avg, 1) + "；EPS 低/高值 " + fmt_signed(cr.eps_low_vs_avg, 1) + " / "
    + fmt_signed(cr.eps_high_vs_avg, 1) + "。快照 " + (c.snapshot_date || "缺失"));
  const heat = payload.heatmap;
  if (heat && heat.reason) put($("p4-note-crowd"), heat.reason);
  m.segments.forEach((s, i) => put($("base-seg-" + i), "共识基数 " + fmt_money(s.base, cur)));
  document.querySelectorAll("[data-marker-value]").forEach((el) => {
    const mk = payload.markers[Number(el.dataset.markerValue)];
    const kind = mk.slider === "margin" ? "pct" : mk.slider === "pe" ? "x" : "num";
    put(el, fmt(mk.value, kind));
  });
  const margins = payload.margins;
  put($("audit-margins"), "增量利润率三读数（只作审计，不进算术）：历史 " + fmt(margins.hist.value, "pct") + "（"
    + margins.hist.detail + "）；街口 " + fmt(margins.street.value, "pct") + "（" + margins.street.detail + "）；Boss "
    + fmt(margins.boss.value, "pct") + "（" + margins.boss.detail + "）");

  // ---- controls ----
  let pending = false;
  const dispatch = (action) => {
    state = reduce(state, action, payload);
    if (!pending) {
      pending = true;
      window.requestAnimationFrame(() => { pending = false; render(); });
    }
  };
  const range = (el, lo, hi) => {
    if (!el) return;
    if (fin(lo) !== null && fin(hi) !== null) {
      el.min = String(lo);
      el.max = String(hi);
    }
  };
  m.segments.forEach((s, i) => {
    const el = $("sl-seg-" + i);
    if (!el) return;
    range(el, sliders.seg.min, sliders.seg.max);
    el.disabled = sliders.seg.disabled === true;
    el.addEventListener("input", () => dispatch({type: "seg", index: i, value: Number(el.value)}));
  });
  for (const key of ["margin", "pe", "fx"]) {
    const el = $("sl-" + key);
    const sl = sliders[key];
    if (!el || !sl) continue;
    range(el, sl.min, sl.max);
    el.disabled = sl.disabled === true;
    el.addEventListener("input", () => dispatch({type: "set", key: key, value: Number(el.value)}));
  }
  const half = $("in-pe-half");
  if (half) {
    half.disabled = sliders.pe.disabled === true;
    half.addEventListener("input", () => dispatch({type: "set", key: "pe_half", value: Number(half.value)}));
  }
  for (const d of sliders.drivers) {
    const el = $("sl-driver-" + d.id);
    if (!el) continue;
    range(el, d.min, d.max);
    el.disabled = m.eps_mode !== "margin";
    el.addEventListener("input", () => dispatch({type: "driver", id: d.id, value: Number(el.value)}));
  }
  document.querySelectorAll("button.tick").forEach((btn) => {
    const mk = payload.markers[Number(btn.dataset.marker)];
    const sl = sliders[mk.slider];
    const lo = sl ? fin(sl.min) : null;
    const hi = sl ? fin(sl.max) : null;
    const v = fin(mk.value);
    if (lo === null || hi === null || v === null || hi <= lo || v < lo || v > hi) {
      btn.disabled = true;
      btn.title = btn.title + "（超出滑轨范围）";
      return;
    }
    btn.disabled = sl.disabled === true;
    btn.addEventListener("click", () => dispatch({type: "set", key: mk.slider, value: mk.value}));
  });
  document.querySelectorAll("[data-track-mark]").forEach((el) => {
    const mk = payload.markers[Number(el.dataset.trackMark)];
    const sl = sliders[mk.slider];
    const lo = sl ? fin(sl.min) : null;
    const hi = sl ? fin(sl.max) : null;
    const v = fin(mk.value);
    if (lo === null || hi === null || v === null || hi <= lo || v < lo || v > hi) {
      el.hidden = true;
      return;
    }
    el.style.left = ((v - lo) / (hi - lo) * 100).toFixed(3) + "%";
  });
  document.querySelectorAll("button[data-preset]").forEach((btn) => {
    btn.addEventListener("click", () => dispatch({type: "preset", id: btn.dataset.preset}));
  });
  $("btn-reset").addEventListener("click", () => dispatch({type: "reset"}));
  document.querySelectorAll("input[data-weight]").forEach((input) => {
    input.addEventListener("input", () => dispatch({type: "weight", id: input.dataset.weight, text: input.value}));
  });
  const badge = $("badge-gates");
  if (badge) {
    badge.addEventListener("click", () => {
      const audit = $("audit");
      audit.open = true;
      audit.scrollIntoView({behavior: "smooth", block: "start"});
    });
  }

  // ---- render ----
  function draw(svg, list) {
    while (svg.firstChild) svg.removeChild(svg.firstChild);
    for (const it of list) {
      let el;
      if (it.t === "text") {
        el = document.createElementNS(SVG_NS, "text");
        el.setAttribute("x", it.x.toFixed(2));
        el.setAttribute("y", it.y.toFixed(2));
        el.setAttribute("font-size", String(it.size));
        el.setAttribute("text-anchor", it.anchor);
        el.setAttribute("fill", it.fill);
        if (it.weight) el.setAttribute("font-weight", String(it.weight));
        if (it.halo) {
          el.setAttribute("stroke", it.halo);
          el.setAttribute("stroke-width", "3");
          el.setAttribute("stroke-linejoin", "round");
          el.setAttribute("paint-order", "stroke");
        }
        el.textContent = it.text;
      } else if (it.t === "rect") {
        el = document.createElementNS(SVG_NS, "rect");
        el.setAttribute("x", it.x.toFixed(2));
        el.setAttribute("y", it.y.toFixed(2));
        el.setAttribute("width", Math.max(it.w, 0).toFixed(2));
        el.setAttribute("height", Math.max(it.h, 0).toFixed(2));
        el.setAttribute("fill", it.fill);
        if (it.rx) el.setAttribute("rx", String(it.rx));
        if (it.opacity !== undefined) el.setAttribute("fill-opacity", String(it.opacity));
      } else if (it.t === "line") {
        el = document.createElementNS(SVG_NS, "line");
        el.setAttribute("x1", it.x1.toFixed(2));
        el.setAttribute("y1", it.y1.toFixed(2));
        el.setAttribute("x2", it.x2.toFixed(2));
        el.setAttribute("y2", it.y2.toFixed(2));
      } else if (it.t === "path") {
        el = document.createElementNS(SVG_NS, "path");
        el.setAttribute("d", it.pts.map((p, i) => (i ? "L" : "M") + p[0].toFixed(2) + " " + p[1].toFixed(2)).join(" "));
        el.setAttribute("fill", "none");
        el.setAttribute("stroke-linejoin", "round");
      } else if (it.t === "circle") {
        el = document.createElementNS(SVG_NS, "circle");
        el.setAttribute("cx", it.cx.toFixed(2));
        el.setAttribute("cy", it.cy.toFixed(2));
        el.setAttribute("r", String(it.r));
        el.setAttribute("fill", it.fill);
        if (it.opacity !== undefined) el.setAttribute("fill-opacity", String(it.opacity));
      } else {
        continue;
      }
      if (it.t !== "text") {
        if (it.stroke) el.setAttribute("stroke", it.stroke);
        if (it.sw) el.setAttribute("stroke-width", String(it.sw));
        if (it.dash) el.setAttribute("stroke-dasharray", it.dash);
      }
      svg.appendChild(el);
    }
  }

  function render() {
    const d = derive(state, payload);
    const live = d.live;
    m.segments.forEach((s, i) => {
      const el = $("sl-seg-" + i);
      if (el && fin(state.adjs[i]) !== null) el.value = String(state.adjs[i]);
      put($("val-seg-" + i), fmt_signed(state.adjs[i], 1));
      put($("amt-seg-" + i), fmt_money(d.seg_rev[i], cur));
    });
    const setv = (key, kind) => {
      const el = $("sl-" + key);
      if (el && fin(state[key]) !== null) el.value = String(state[key]);
      put($("val-" + key), fmt(state[key], kind));
    };
    setv("margin", "pct");
    setv("pe", "x");
    setv("fx", "num");
    if (half && document.activeElement !== half) half.value = String(state.pe_half);
    for (const dr of sliders.drivers) {
      const el = $("sl-driver-" + dr.id);
      const v = state.drivers[dr.id];
      if (el && fin(v) !== null) el.value = String(v);
      put($("val-driver-" + dr.id), fmt_money(v, cur) + "（相对基准 " + fmt_money(fin(v) !== null ? v - dr.base : null, cur) + "）");
    }
    document.querySelectorAll("input[data-weight]").forEach((input) => {
      if (document.activeElement === input) return;
      const w = state.weights[input.dataset.weight];
      input.value = fin(w) === null ? "" : String(w);
    });
    put($("out-rev"), fmt_money(live.rev, cur));
    put($("out-ni"), live.net_income !== null ? fmt_money(live.net_income, cur) : "直接取共识 EPS");
    put($("out-eps-local"), live.eps_local !== null ? fmt(live.eps_local, "num") + " " + cur : "输入不足");
    put($("out-eps-usd"), fmt(live.eps_usd, "usd"));
    put($("out-implied-pe"), live.implied_pe !== null ? fmt(live.implied_pe, "x")
      + (d.pe_rank !== null ? " · 历史分位 " + fmt(d.pe_rank, "pct") : "") : "负盈利，不适用");
    put($("kpi-custom"), custom_text(d));
    put($("kpi-custom-sub"), "P/E " + fmt(state.pe, "x") + (state.pe_half > 0 ? " ± " + fmt(state.pe_half, "num") : "")
      + " · 净利率 " + (m.eps_mode === "margin" ? fmt(state.margin, "pct") : "直接取共识"));
    put($("kpi-ev"), ev_text(d));
    put($("kpi-ev-sub"), d.ev !== null ? "权重合计 100%" : "权重合计 " + fmt(d.weight_total, "num") + "%");
    const note = $("w-note");
    put(note, d.ev !== null ? "权重合计 100%，EV 线已显示" : d.ev_reason + "（当前合计 " + fmt(d.weight_total, "num") + "%），EV 线隐藏");
    if (note) note.className = d.ev !== null ? "note" : "note warn";
    const pill = $("pill-zone");
    if (pill) pill.hidden = !d.tail;
    document.querySelectorAll("button[data-preset]").forEach((btn) => {
      btn.classList.toggle("active", btn.dataset.preset === state.preset_id);
    });
    for (const [id, fn] of PANELS) {
      const svg = $(id);
      if (svg) draw(svg, fn(d, payload));
    }
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
