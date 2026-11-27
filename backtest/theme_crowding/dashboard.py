"""Standalone light-theme HTML dashboard renderer for US Theme Basket Crowding V2.2.

Zero external CDN dependencies, zero host CSS variable dependencies.
"""
from __future__ import annotations

import json

TEMPLATE = r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<title>美股主题拥挤度看板 (V2 定稿版)</title>
<style>
  :root {
    --bg: #FAF9F6;
    --panel: #FFFFFF;
    --panel-alt: #F4F3EF;
    --border: #E2E0D8;
    --border-subtle: #ECEAE3;
    --text: #1C1F24;
    --body-text: #2D333B;
    --muted: #5F6672;
    --dim: #8C939E;
    --green: #15803D;
    --green-line: #16A34A;
    --green-soft: rgba(21, 128, 61, 0.09);
    --amber: #B45309;
    --amber-soft: rgba(180, 83, 9, 0.11);
    --red: #C81E1E;
    --red-soft: rgba(200, 30, 30, 0.10);
    --blue: #1D4ED8;
    --blue-soft: rgba(29, 78, 216, 0.08);
    --purple: #7C3AED;
    --price-line: #64748B;
  }
  * { box-sizing: border-box; }
  body {
    margin: 0;
    padding: 0;
    background: var(--bg);
    color: var(--text);
    font-family: -apple-system, BlinkMacSystemFont, "PingFang SC", "Hiragino Sans GB", "Microsoft YaHei", "Segoe UI", Roboto, sans-serif;
    font-size: 14px;
    line-height: 1.6;
    -webkit-font-smoothing: antialiased;
  }
  .container {
    max-width: 1260px;
    margin: 0 auto;
    padding: 24px 24px 56px;
  }
  h1, h2, h3 { margin: 0; font-weight: 600; letter-spacing: -0.01em; color: var(--text); }
  .mono { font-family: "SF Mono", "JetBrains Mono", Menlo, Consolas, monospace; font-variant-numeric: tabular-nums; }

  .page-header {
    display: flex;
    flex-wrap: wrap;
    justify-content: space-between;
    align-items: flex-end;
    gap: 12px;
    padding-bottom: 16px;
    border-bottom: 1px solid var(--border);
    margin-bottom: 18px;
  }
  .page-title { font-size: 21px; color: var(--text); }
  .page-sub { font-size: 13px; color: var(--muted); margin-top: 4px; }
  .badge {
    display: inline-block;
    padding: 2px 9px;
    border-radius: 4px;
    font-size: 11.5px;
    font-weight: 600;
  }
  .badge-green { background: var(--green-soft); color: var(--green); border: 1px solid rgba(21,128,61,0.25); }
  .badge-amber { background: var(--amber-soft); color: var(--amber); border: 1px solid rgba(180,83,9,0.28); }
  .badge-red { background: var(--red-soft); color: var(--red); border: 1px solid rgba(200,30,30,0.28); }
  .badge-blue { background: var(--blue-soft); color: var(--blue); border: 1px solid rgba(29,78,216,0.22); }
  .badge-neutral { background: #F1F0EC; color: #4B5563; border: 1px solid #D8D5CC; }

  /* 7-Basket Strip */
  .strip-grid {
    display: grid;
    grid-template-columns: repeat(7, minmax(0, 1fr));
    gap: 8px;
    margin-bottom: 18px;
  }
  @media (max-width: 980px) {
    .strip-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); }
  }
  .strip-item {
    background: var(--panel);
    border: 1px solid var(--border);
    border-radius: 7px;
    padding: 10px 12px;
    cursor: pointer;
    transition: border-color 0.15s, background 0.15s, box-shadow 0.15s;
  }
  .strip-item:hover { border-color: #B8B4A8; background: #FDFDFB; }
  .strip-item.active {
    border-color: var(--green);
    background: rgba(21, 128, 61, 0.04);
    box-shadow: 0 0 0 1px var(--green);
  }
  .strip-code { font-size: 12px; color: var(--muted); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  .strip-score-row { display: flex; align-items: baseline; justify-content: space-between; margin: 3px 0 5px; }
  .strip-score { font-size: 20px; font-weight: 700; }
  .strip-raw { font-size: 11px; color: var(--dim); }
  .strip-bar-bg { height: 5px; background: #EAE8E1; border-radius: 3px; overflow: hidden; }
  .strip-bar-fill { height: 100%; border-radius: 3px; }
  .strip-state { font-size: 11px; margin-top: 6px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; font-weight: 500; }

  /* Main Dashboard Box */
  .terminal-box {
    background: var(--panel);
    border: 1px solid var(--border);
    border-radius: 8px;
    padding: 18px 20px 14px;
    margin-bottom: 20px;
    box-shadow: 0 1px 3px rgba(0, 0, 0, 0.03);
  }
  .term-top {
    display: flex;
    flex-wrap: wrap;
    justify-content: space-between;
    align-items: flex-start;
    gap: 12px;
    margin-bottom: 8px;
  }
  .term-title-row { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }
  .term-title { font-size: 17px; font-weight: 600; color: var(--text); }
  .term-subtitle { font-size: 12.5px; color: var(--muted); margin-top: 3px; }

  .controls-row {
    display: flex;
    flex-wrap: wrap;
    gap: 8px;
    align-items: center;
  }
  .btn-group {
    display: inline-flex;
    background: var(--panel-alt);
    border: 1px solid var(--border);
    border-radius: 6px;
    padding: 2px;
    gap: 2px;
  }
  .btn-toggle {
    background: transparent;
    color: var(--muted);
    border: none;
    border-radius: 4px;
    padding: 4px 10px;
    font-size: 12px;
    cursor: pointer;
  }
  .btn-toggle:hover { color: var(--text); }
  .btn-toggle.active {
    background: var(--panel);
    color: var(--text);
    font-weight: 600;
    box-shadow: 0 1px 2px rgba(0, 0, 0, 0.07);
  }

  .legend-row {
    display: flex;
    flex-wrap: wrap;
    justify-content: center;
    gap: 16px;
    font-size: 12px;
    color: var(--muted);
    margin: 8px 0;
  }
  .leg-item { display: inline-flex; align-items: center; gap: 6px; }
  .leg-box-price { width: 11px; height: 11px; border: 1.5px solid var(--price-line); background: rgba(100,116,139,0.10); display: inline-block; }
  .leg-box-crowd { width: 11px; height: 11px; border: 1.5px solid var(--green-line); background: rgba(22,163,74,0.15); display: inline-block; }
  .leg-line-raw { width: 16px; border-top: 1.5px solid rgba(22,163,74,0.32); display: inline-block; }
  .leg-line-hot9 { width: 16px; border-top: 1.5px dashed rgba(200,30,30,0.65); display: inline-block; }
  .leg-line-hot8 { width: 16px; border-top: 1.5px dotted rgba(180,83,9,0.7); display: inline-block; }
  .leg-line-mid5 { width: 16px; border-top: 1.5px solid rgba(75,85,99,0.55); display: inline-block; }
  .leg-line-v1 { width: 16px; border-top: 1.5px dashed #9CA3AF; display: inline-block; }
  .leg-line-sub { width: 16px; border-top: 2px solid var(--purple); display: inline-block; }

  .canvas-wrap {
    position: relative;
    width: 100%;
    height: 340px;
    margin-bottom: 12px;
    border-bottom: 1px solid var(--border-subtle);
    padding-bottom: 4px;
  }
  canvas#crowdChart {
    width: 100%;
    height: 100%;
    display: block;
    cursor: crosshair;
  }
  .chart-tooltip {
    position: absolute;
    pointer-events: none;
    background: rgba(255, 255, 255, 0.97);
    border: 1px solid #D0CEC6;
    border-radius: 6px;
    padding: 8px 11px;
    font-size: 12px;
    color: var(--text);
    display: none;
    z-index: 10;
    white-space: nowrap;
    box-shadow: 0 6px 18px rgba(0, 0, 0, 0.10);
  }

  .sub-hint {
    font-size: 12px;
    color: var(--muted);
    margin-bottom: 6px;
    display: flex;
    justify-content: space-between;
    align-items: center;
  }
  table.term-table {
    width: 100%;
    border-collapse: collapse;
    font-size: 13px;
  }
  table.term-table th {
    background: var(--panel-alt);
    color: var(--muted);
    font-weight: 500;
    padding: 8px 10px;
    border-bottom: 1px solid var(--border);
    text-align: left;
  }
  table.term-table td {
    padding: 9px 10px;
    border-bottom: 1px solid var(--border-subtle);
    color: var(--body-text);
  }
  table.term-table tr.sub-row {
    cursor: pointer;
    transition: background 0.12s;
  }
  table.term-table tr.sub-row:hover { background: #F8F7F3; }
  table.term-table tr.sub-row.selected-sub { background: rgba(124, 58, 237, 0.07); }
  table.term-table th.num, table.term-table td.num { text-align: right; }
  .score-hot { color: var(--red); font-weight: 600; }
  .score-warm { color: var(--amber); font-weight: 600; }
  .score-norm { color: var(--text); font-weight: 500; }

  .section-grid-2 {
    display: grid;
    grid-template-columns: 5fr 7fr;
    gap: 18px;
    margin-bottom: 20px;
  }
  @media (max-width: 960px) {
    .section-grid-2 { grid-template-columns: 1fr; }
  }
  .card {
    background: var(--panel);
    border: 1px solid var(--border);
    border-radius: 8px;
    padding: 16px 18px;
    box-shadow: 0 1px 3px rgba(0, 0, 0, 0.025);
  }
  .card-title {
    font-size: 15px;
    font-weight: 600;
    color: var(--text);
    margin-bottom: 4px;
    display: flex;
    justify-content: space-between;
    align-items: center;
  }
  .card-desc {
    font-size: 12.5px;
    color: var(--muted);
    margin-bottom: 10px;
  }
  table.data-table {
    width: 100%;
    border-collapse: collapse;
    font-size: 12.5px;
  }
  table.data-table th {
    background: var(--panel-alt);
    color: var(--muted);
    font-weight: 500;
    padding: 7px 9px;
    border-bottom: 1px solid var(--border);
    text-align: left;
    position: sticky;
    top: 0;
  }
  table.data-table td {
    padding: 7px 9px;
    border-bottom: 1px solid var(--border-subtle);
    color: var(--body-text);
  }
  table.data-table th.num, table.data-table td.num { text-align: right; }
  .pos { color: var(--green); font-weight: 500; }
  .neg { color: var(--red); font-weight: 500; }
  .warn { color: var(--amber); font-weight: 500; }
</style>
</head>
<body>
<div class="container">

  <div class="page-header">
    <div>
      <h1 class="page-title">美股主题篮子拥挤度看板（双脑终局定稿版）</h1>
      <div class="page-sub">数据截至 <span class="mono" id="hdrAsOf"></span> · 五子项统一 <span class="mono">EMA3</span> 线性平滑 · 量价 500 日窗 + 估值 <span class="mono">500..1000</span> 日封顶窗 · 状态灯 <span class="mono">8.0/7.5</span> 迟滞</div>
    </div>
    <div style="display:flex; gap:8px; align-items:center;">
      <span class="badge badge-green">V2.2 终局定稿</span>
      <span class="badge badge-blue" id="hdrSpan"></span>
    </div>
  </div>

  <!-- 7大篮子温度条 -->
  <div class="strip-grid" id="basketStrip"></div>

  <!-- 主图 + 五项指标明细 -->
  <div class="terminal-box">
    <div class="term-top">
      <div>
        <div class="term-title-row">
          <div class="term-title" id="termTitle"></div>
          <span id="termStateBadge" class="badge"></span>
        </div>
        <div class="term-subtitle" id="termSubtitle"></div>
      </div>
      <div class="controls-row">
        <div class="btn-group" id="rangeBtns">
          <button class="btn-toggle active" data-range="2026">2026年以来</button>
          <button class="btn-toggle" data-range="full">近三年全区间 (2023.11–2026.11)</button>
        </div>
        <div class="btn-group" id="cmpBtns">
          <button class="btn-toggle active" data-cmp="raw_ref">附浅色未平滑原线</button>
          <button class="btn-toggle" data-cmp="clean">仅看 EMA3 主线</button>
          <button class="btn-toggle" data-cmp="v1_ref">对比原图 V1 旧口径</button>
        </div>
      </div>
    </div>

    <div class="legend-row">
      <span class="leg-item"><span class="leg-box-price"></span> 成分股市值加权复权指数 (右轴，年内以上年末=100)</span>
      <span class="leg-item"><span class="leg-line-hot9"></span> 拥挤线 (9.0进/8.5退)</span>
      <span class="leg-item"><span class="leg-line-hot8"></span> 偏热线 (8.0进/7.5退)</span>
      <span class="leg-item"><span class="leg-line-mid5"></span> 中性线 (5.0)</span>
      <span class="leg-item"><span class="leg-box-crowd"></span> V2.2 拥挤度 EMA3 (左轴 0–10)</span>
      <span class="leg-item" id="rawLegend"><span class="leg-line-raw"></span> 当日未平滑瞬时值</span>
      <span class="leg-item" id="subLegend" style="display:none;"><span class="leg-line-sub"></span> <span id="subLegendName">选中子项</span></span>
      <span class="leg-item" id="v1Legend" style="display:none;"><span class="leg-line-v1"></span> 原图 V1 旧口径</span>
    </div>

    <div class="canvas-wrap">
      <canvas id="crowdChart"></canvas>
      <div class="chart-tooltip" id="chartTooltip"></div>
    </div>

    <div class="sub-hint">
      <span>💡 <strong>提示</strong>：点击下方任意一行子指标，可在上方走势图中叠加查看该子指标的 <span class="mono">EMA3</span> 历史曲线；三维度均分相加 <strong>100% 严格等于</strong> 总分。</span>
      <span id="clearSubBtn" style="color:var(--purple);cursor:pointer;display:none;font-weight:500;">✕ 取消子指标叠加</span>
    </div>

    <table class="term-table">
      <thead>
        <tr>
          <th style="width:12%;">维度 (权重)</th>
          <th style="width:27%;">定稿子指标 (回看窗口)</th>
          <th class="num" style="width:24%;">最新原值</th>
          <th class="num" style="width:17%;">分位 / 门控</th>
          <th class="num" style="width:11%;">EMA3得分 (瞬时)</th>
          <th class="num" style="width:9%;">维度均分</th>
        </tr>
      </thead>
      <tbody id="termTableBody"></tbody>
    </table>
  </div>

  <!-- 下方双栏：左=成分股成交额与权重明细；右=该篮子分档历史表现与迟滞灯记录 -->
  <div class="section-grid-2">
    <div class="card">
      <div class="card-title">
        <span id="constTitle">成分股成交额与市值贡献明细</span>
        <span class="badge badge-blue" id="constCountBadge"></span>
      </div>
      <div class="card-desc">篮子交易量按<strong>全部成分股真实成交额加总</strong>计算（不含 ETF 自身代码），按近 5 日日均成交额排序：</div>
      <div style="max-height: 365px; overflow-y: auto;">
        <table class="data-table">
          <thead>
            <tr>
              <th>代码</th>
              <th>公司</th>
              <th class="num">5日均成交额</th>
              <th class="num">占篮子量</th>
              <th class="num">市值权重</th>
            </tr>
          </thead>
          <tbody id="constBody"></tbody>
        </table>
      </div>
    </div>

    <div style="display:flex; flex-direction:column; gap:18px;">
      <div class="card">
        <div class="card-title">
          <span id="bucketCardTitle">分档后 60 交易日表现（基于平滑序列重算）</span>
          <span class="badge badge-amber">对比自身基线</span>
        </div>
        <div class="card-desc">在定稿平滑序列上重算各分档随后 60 日描述性统计（注：日频窗口存在重叠；QQQ/SOX 已接入 2021 起季报 PIT 持仓，IGV/XLF 在 2026-07-13 前为首期快照代理回放；财报版本表始于 2026-08-22）：</div>
        <table class="data-table">
          <thead>
            <tr>
              <th>拥挤度区间</th>
              <th class="num">历史天数</th>
              <th class="num">后60日均涨</th>
              <th class="num">相对常态基线</th>
              <th class="num">60日平均最大回撤</th>
              <th class="num">回撤&ge;15%率</th>
            </tr>
          </thead>
          <tbody id="basketBucketBody"></tbody>
        </table>
      </div>

      <div class="card">
        <div class="card-title">
          <span id="episodeCardTitle">偏热状态灯亮起事件簿（进 8.0 亮 / 退 7.5 灭）</span>
          <span class="badge badge-green">物理迟滞去重</span>
        </div>
        <div style="max-height: 160px; overflow-y: auto; margin-top:6px;">
          <table class="data-table">
            <thead>
              <tr>
                <th>亮灯日期</th>
                <th class="num">EMA3读数 (瞬时)</th>
                <th class="num">后20日收益</th>
                <th class="num">后60日收益</th>
                <th class="num">60日内最大回撤</th>
              </tr>
            </thead>
            <tbody id="episodeBody"></tbody>
          </table>
        </div>
      </div>
    </div>
  </div>

  <!-- 横向全览对比表 -->
  <div class="card">
    <div class="card-title">
      <span>七大主题篮子横向总览（最新定稿读数、状态灯标签 &amp; 偏热区对比）</span>
    </div>
    <table class="data-table" style="margin-top:8px;">
      <thead>
        <tr>
          <th>主题篮子</th>
          <th class="num">最新 EMA3 (瞬时)</th>
          <th>当前状态判定</th>
          <th class="num">收益率维</th>
          <th class="num">交易量维</th>
          <th class="num">估值维(SP)</th>
          <th class="num">5日均成交额 (全美股占比)</th>
          <th class="num">&ge;8.0后60日均涨 (vs基线)</th>
        </tr>
      </thead>
      <tbody id="overviewBody"></tbody>
    </table>
  </div>

</div>

<script>
const DATA = __CROWDING_JSON_PAYLOAD__;

const ALL_CODES = ["SEMI_EQUIP", "QQQ", "XLF", "MEMORY", "SOX", "HYPERSCALERS", "IGV"];
const SUB_META = {
  s_mom: "MA200年线偏离分(EMA3)",
  s_vol: "门控超额波动分(EMA3)",
  s_dvs: "5日成交占比分(EMA3)",
  s_trn: "5日换手率分(EMA3)",
  s_val: "市销率SP估值分(EMA3)"
};

let curBasket = "SOX";
let curRange = "2026";
let cmpMode = "raw_ref";
let selectedSubKey = null;

function scoreColor(val, stCode) {
  if (stCode) {
    if (stCode === "CROWDED") return "#C81E1E";
    if (stCode === "WARM") return "#B45309";
    if (stCode === "PULLBACK_HIGHVOL") return "#9A6700";
    if (stCode === "COLD") return "#1D4ED8";
    if (stCode === "LOW_MILD") return "#475569";
    return "#15803D";
  }
  if (val >= 9.0) return "#C81E1E";
  if (val >= 8.0) return "#B45309";
  if (val < 3.5) return "#1D4ED8";
  return "#15803D";
}

function stateBadgeClass(stCode) {
  if (stCode === "CROWDED") return "badge-red";
  if (stCode === "WARM" || stCode === "PULLBACK_HIGHVOL") return "badge-amber";
  if (stCode === "COLD") return "badge-blue";
  return "badge-neutral";
}

function scoreClass(val) {
  if (val >= 8.0) return "score-hot";
  if (val >= 7.0) return "score-warm";
  return "score-norm";
}

function fmtSigned(v, digits = 2, suffix = "%") {
  if (v === null || v === undefined || Number.isNaN(v)) return "—";
  return (v >= 0 ? "+" : "") + v.toFixed(digits) + suffix;
}

function initStatic() {
  document.getElementById("hdrAsOf").textContent = DATA.as_of;
  const b0 = DATA.baskets["SOX"];
  document.getElementById("hdrSpan").textContent = `${b0.valid_start} ~ ${b0.valid_end} (${b0.n_valid_days}日)`;

  const sumMap = {};
  DATA.equal_basket_regime_summary.forEach(r => { sumMap[r.code] = r; });

  const sorted = ALL_CODES.slice().sort((a, b) => DATA.baskets[b].latest.crowding - DATA.baskets[a].latest.crowding);
  document.getElementById("overviewBody").innerHTML = sorted.map(code => {
    const b = DATA.baskets[code];
    const L = b.latest;
    const sm = sumMap[code];
    const hotStr = sm.hot_r60 !== null
      ? `${fmtSigned(sm.hot_r60)} (<span class="${sm.hot_vs_base >= 0 ? 'pos' : 'neg'}">${fmtSigned(sm.hot_vs_base, 2, 'pp')}</span>)`
      : "—";
    return `<tr style="cursor:pointer;" onclick="selectBasket('${code}')">
      <td><strong>${b.name}</strong></td>
      <td class="num mono"><strong style="color:${scoreColor(L.crowding, L.state_code)};">${L.crowding.toFixed(2)}</strong> <span style="color:#8C939E;">(${L.crowding_raw.toFixed(2)})</span></td>
      <td><span class="badge ${stateBadgeClass(L.state_code)}">${L.state_label}</span></td>
      <td class="num mono">${L.d_ret.toFixed(2)}</td>
      <td class="num mono ${scoreClass(L.d_vol)}">${L.d_vol.toFixed(2)}</td>
      <td class="num mono ${scoreClass(L.d_val)}">${L.d_val.toFixed(2)}</td>
      <td class="num mono">$${L.dv5_usd_bn.toFixed(1)}B (${L.dv5_share_pct.toFixed(2)}%)</td>
      <td class="num mono">${hotStr}</td>
    </tr>`;
  }).join("");
}

window.selectBasket = function(code) {
  curBasket = code;
  renderAll();
  window.scrollTo({ top: 0, behavior: "smooth" });
};

function renderStrip() {
  const sortedCodes = ALL_CODES.slice().sort((a, b) => DATA.baskets[b].latest.crowding - DATA.baskets[a].latest.crowding);
  const wrap = document.getElementById("basketStrip");
  wrap.innerHTML = sortedCodes.map(code => {
    const b = DATA.baskets[code];
    const L = b.latest;
    const sc = L.crowding;
    const col = scoreColor(sc, L.state_code);
    return `<div class="strip-item ${code === curBasket ? 'active' : ''}" data-code="${code}">
      <div class="strip-code" title="${b.name}">${b.name}</div>
      <div class="strip-score-row mono">
        <span class="strip-score" style="color:${col};">${sc.toFixed(2)}</span>
        <span class="strip-raw">瞬时 ${L.crowding_raw.toFixed(1)}</span>
      </div>
      <div class="strip-bar-bg"><div class="strip-bar-fill" style="width:${Math.min(100, sc * 10)}%;background:${col};"></div></div>
      <div class="strip-state" style="color:${col};" title="${L.state_label}">${L.state_label}</div>
    </div>`;
  }).join("");

  wrap.querySelectorAll(".strip-item").forEach(el => {
    el.addEventListener("click", () => {
      curBasket = el.getAttribute("data-code");
      renderAll();
    });
  });
}

function getActiveSeries(b) {
  if (curRange === "2026") {
    return b.series.filter(x => x.d >= "2026-01-01").map(x => ({ ...x, nav: x.nav_2026 }));
  }
  return b.series.map(x => ({ ...x, nav: x.nav_full }));
}

let currentPts = [];

function drawChart() {
  const b = DATA.baskets[curBasket];
  const ser = getActiveSeries(b);
  currentPts = ser;

  const canvas = document.getElementById("crowdChart");
  const rect = canvas.getBoundingClientRect();
  const dpr = window.devicePixelRatio || 1;
  canvas.width = rect.width * dpr;
  canvas.height = rect.height * dpr;
  const ctx = canvas.getContext("2d");
  ctx.scale(dpr, dpr);

  const W = rect.width;
  const H = rect.height;
  const padL = 40, padR = 52, padT = 14, padB = 28;
  const plotW = W - padL - padR;
  const plotH = H - padT - padB;

  ctx.clearRect(0, 0, W, H);

  const yOf = v => padT + (1 - v / 10.0) * plotH;
  ctx.fillStyle = "rgba(200, 30, 30, 0.035)";
  ctx.fillRect(padL, yOf(10.0), plotW, yOf(9.0) - yOf(10.0));
  ctx.fillStyle = "rgba(180, 83, 9, 0.035)";
  ctx.fillRect(padL, yOf(9.0), plotW, yOf(8.0) - yOf(9.0));

  const navs = ser.map(x => x.nav);
  let pMin = Math.min(...navs);
  let pMax = Math.max(...navs);
  const pSpan = Math.max(5, pMax - pMin);
  pMin = Math.floor((pMin - pSpan * 0.08) / 5) * 5;
  pMax = Math.ceil((pMax + pSpan * 0.08) / 5) * 5;

  const xOf = i => padL + (ser.length <= 1 ? 0 : (i / (ser.length - 1)) * plotW);
  const yCrowd = yOf;
  const yPrice = v => padT + (1 - (v - pMin) / (pMax - pMin)) * plotH;

  ctx.font = "11px 'SF Mono', Menlo, monospace";
  ctx.textBaseline = "middle";
  for (let lv = 0; lv <= 10; lv += 2) {
    const y = yCrowd(lv);
    ctx.strokeStyle = "#EFECE6";
    ctx.lineWidth = 1;
    ctx.setLineDash([]);
    ctx.beginPath(); ctx.moveTo(padL, y); ctx.lineTo(W - padR, y); ctx.stroke();
    ctx.fillStyle = "#6B7280";
    ctx.textAlign = "right";
    ctx.fillText(String(lv), padL - 8, y);
  }

  const nTicks = 5;
  for (let k = 0; k <= nTicks; k++) {
    const pv = pMin + (k / nTicks) * (pMax - pMin);
    ctx.fillStyle = "#6B7280";
    ctx.textAlign = "left";
    ctx.fillText(pv.toFixed(0), W - padR + 8, yPrice(pv));
  }

  // Reference lines: 5.0 / 8.0 / 9.0
  ctx.strokeStyle = "rgba(75, 85, 99, 0.45)";
  ctx.lineWidth = 1.2;
  ctx.setLineDash([]);
  ctx.beginPath(); ctx.moveTo(padL, yCrowd(5.0)); ctx.lineTo(W - padR, yCrowd(5.0)); ctx.stroke();

  ctx.strokeStyle = "rgba(180, 83, 9, 0.55)";
  ctx.lineWidth = 1.1;
  ctx.setLineDash([2, 4]);
  ctx.beginPath(); ctx.moveTo(padL, yCrowd(8.0)); ctx.lineTo(W - padR, yCrowd(8.0)); ctx.stroke();

  ctx.strokeStyle = "rgba(200, 30, 30, 0.55)";
  ctx.lineWidth = 1.1;
  ctx.setLineDash([5, 5]);
  ctx.beginPath(); ctx.moveTo(padL, yCrowd(9.0)); ctx.lineTo(W - padR, yCrowd(9.0)); ctx.stroke();
  ctx.setLineDash([]);

  const tickCount = Math.min(7, ser.length);
  ctx.fillStyle = "#6B7280";
  ctx.textAlign = "center";
  ctx.textBaseline = "top";
  for (let t = 0; t < tickCount; t++) {
    const idx = Math.round((t / (tickCount - 1)) * (ser.length - 1));
    ctx.fillText(ser[idx].d, xOf(idx), H - padB + 8);
  }

  // Price index line
  ctx.strokeStyle = "#64748B";
  ctx.lineWidth = 1.4;
  ctx.beginPath();
  ser.forEach((pt, i) => {
    const x = xOf(i), y = yPrice(pt.nav);
    if (i === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y);
  });
  ctx.stroke();

  // Faint unsmoothed raw crowding line (default reference)
  if (cmpMode === "raw_ref") {
    ctx.strokeStyle = "rgba(22, 163, 74, 0.27)";
    ctx.lineWidth = 1.15;
    ctx.beginPath();
    ser.forEach((pt, i) => {
      const x = xOf(i), y = yCrowd(pt.c_raw);
      if (i === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y);
    });
    ctx.stroke();
  }

  // Draw vertical PIT weekly-snapshot boundary marker (2026-07-13) for official ETF baskets
  if (b.weekly_pit_start) {
    const pitIdx = ser.findIndex(pt => pt.d >= b.weekly_pit_start);
    if (pitIdx > 0 && pitIdx < ser.length - 1) {
      const px = xOf(pitIdx);
      ctx.save();
      ctx.strokeStyle = "rgba(29, 78, 216, 0.35)";
      ctx.lineWidth = 1;
      ctx.setLineDash([2, 3]);
      ctx.beginPath();
      ctx.moveTo(px, padT);
      ctx.lineTo(px, H - padB);
      ctx.stroke();
      ctx.fillStyle = "rgba(29, 78, 216, 0.72)";
      ctx.font = "10px -apple-system, sans-serif";
      ctx.fillText("周快照PIT起", Math.min(px + 4, W - padR - 64), padT + 11);
      ctx.restore();
    }
  }

  // Optional V1 original overlay
  if (cmpMode === "v1_ref") {
    ctx.strokeStyle = "#9CA3AF";
    ctx.lineWidth = 1.35;
    ctx.setLineDash([4, 3]);
    ctx.beginPath();
    let started = false;
    ser.forEach((pt, i) => {
      if (pt.c_v1 === null || pt.c_v1 === undefined) return;
      const x = xOf(i), y = yCrowd(pt.c_v1);
      if (!started) { ctx.moveTo(x, y); started = true; } else ctx.lineTo(x, y);
    });
    ctx.stroke();
    ctx.setLineDash([]);
  }

  // Optional selected sub-indicator curve
  if (selectedSubKey) {
    ctx.strokeStyle = "#7C3AED";
    ctx.lineWidth = 1.65;
    ctx.beginPath();
    ser.forEach((pt, i) => {
      const x = xOf(i), y = yCrowd(pt[selectedSubKey]);
      if (i === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y);
    });
    ctx.stroke();
  }

  // Main V2.2 EMA3 Crowding line
  ctx.strokeStyle = "#16A34A";
  ctx.lineWidth = 2.25;
  ctx.beginPath();
  ser.forEach((pt, i) => {
    const x = xOf(i), y = yCrowd(pt.c);
    if (i === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y);
  });
  ctx.stroke();

  canvas._chartGeom = { padL, padR, padT, padB, plotW, plotH, W, H, xOf, yCrowd, yPrice };
}

function toggleSubKey(key) {
  selectedSubKey = (selectedSubKey === key) ? null : key;
  document.getElementById("subLegend").style.display = selectedSubKey ? "inline-flex" : "none";
  document.getElementById("clearSubBtn").style.display = selectedSubKey ? "inline" : "none";
  if (selectedSubKey) {
    document.getElementById("subLegendName").textContent = SUB_META[selectedSubKey];
  }
  renderTerminalTable();
  drawChart();
}

function renderTerminalTable() {
  const b = DATA.baskets[curBasket];
  const ser = getActiveSeries(b);
  const L = b.latest;

  document.getElementById("termTitle").textContent = `${b.name} — 拥挤度走势`;
  const badge = document.getElementById("termStateBadge");
  badge.className = `badge ${stateBadgeClass(L.state_code)}`;
  badge.textContent = L.state_label;

  document.getElementById("termSubtitle").textContent =
    `${ser[0].d} 至 ${ser[ser.length - 1].d} (${ser.length} 个交易日) · 成分 ${b.n_members} 只 · ${b.source_note} · 最新 EMA3 总分 ${L.crowding.toFixed(2)} (当日瞬时 ${L.crowding_raw.toFixed(2)})`;

  const selCls = k => (selectedSubKey === k ? "sub-row selected-sub" : "sub-row");

  document.getElementById("termTableBody").innerHTML = `
    <tr class="${selCls('s_mom')}" data-sub="s_mom">
      <td><strong>收益率 (1/3)</strong></td>
      <td>MA200 年线偏离度 <span style="color:#8C939E;">(500日窗)</span></td>
      <td class="num mono">${fmtSigned(L.bias200_pct, 2, "%")}</td>
      <td class="num mono">${L.p_bias200.toFixed(1)}% 分位</td>
      <td class="num mono ${scoreClass(L.s_mom)}">${L.s_mom.toFixed(2)} <span style="color:#8C939E;">(${L.s_mom_raw.toFixed(1)})</span></td>
      <td class="num mono" rowspan="2" style="vertical-align:middle;font-weight:600;border-left:1px solid var(--border-subtle);">${L.d_ret.toFixed(2)}</td>
    </tr>
    <tr class="${selCls('s_vol')}" data-sub="s_vol">
      <td></td>
      <td>20日超额波动率 × 60日回撤门控 <span style="color:#8C939E;">(500日窗)</span></td>
      <td class="num mono">${L.exvol20_pct.toFixed(2)}% <span style="color:#8C939E;">(回撤 -${L.dd60_pct.toFixed(1)}%)</span></td>
      <td class="num mono">${L.p_exvol_raw.toFixed(1)}% × 门控 ${L.dd_gate.toFixed(2)}</td>
      <td class="num mono ${scoreClass(L.s_exvol)}">${L.s_exvol.toFixed(2)} <span style="color:#8C939E;">(${L.s_exvol_raw.toFixed(1)})</span></td>
    </tr>
    <tr class="${selCls('s_dvs')}" data-sub="s_dvs">
      <td><strong>交易量 (1/3)</strong></td>
      <td>5日均值 · 成分股成交占本库美股普通股比 <span style="color:#8C939E;">(500日窗)</span></td>
      <td class="num mono">$${L.dv5_usd_bn.toFixed(2)}B/日 (占 ${L.dv5_share_pct.toFixed(2)}%)</td>
      <td class="num mono">${L.p_dvshare.toFixed(1)}% 分位</td>
      <td class="num mono ${scoreClass(L.s_dvshare)}">${L.s_dvshare.toFixed(2)} <span style="color:#8C939E;">(${L.s_dvshare_raw.toFixed(1)})</span></td>
      <td class="num mono ${scoreClass(L.d_vol)}" rowspan="2" style="vertical-align:middle;font-weight:600;border-left:1px solid var(--border-subtle);">${L.d_vol.toFixed(2)}</td>
    </tr>
    <tr class="${selCls('s_trn')}" data-sub="s_trn">
      <td></td>
      <td>5日均值 · 成分股合计日换手率 <span style="color:#8C939E;">(500日窗)</span></td>
      <td class="num mono">${L.turn5_pct.toFixed(3)}%/日</td>
      <td class="num mono">${L.p_turn.toFixed(1)}% 分位</td>
      <td class="num mono ${scoreClass(L.s_turn)}">${L.s_turn.toFixed(2)} <span style="color:#8C939E;">(${L.s_turn_raw.toFixed(1)})</span></td>
    </tr>
    <tr class="${selCls('s_val')}" data-sub="s_val">
      <td><strong>估值 (1/3)</strong></td>
      <td>市销率倒数 TTM SP <span style="color:#15803D;font-weight:500;">(有界 ${L.val_window_days}日窗)</span></td>
      <td class="num mono">SP ${L.sp_pct.toFixed(2)}% <span style="color:#8C939E;">(即 P/S ${L.ps_multiple !== null ? L.ps_multiple.toFixed(2) + 'x' : '—'})</span></td>
      <td class="num mono">昂贵分位 ${L.p_sp_crowd.toFixed(1)}%</td>
      <td class="num mono ${scoreClass(L.s_val)}">${L.s_val.toFixed(2)} <span style="color:#8C939E;">(${L.s_val_raw.toFixed(1)})</span></td>
      <td class="num mono ${scoreClass(L.d_val)}" style="font-weight:600;border-left:1px solid var(--border-subtle);">${L.d_val.toFixed(2)}</td>
    </tr>
  `;

  document.querySelectorAll("#termTableBody tr.sub-row").forEach(tr => {
    tr.addEventListener("click", () => toggleSubKey(tr.getAttribute("data-sub")));
  });
}

function renderBasketDetails() {
  const b = DATA.baskets[curBasket];

  document.getElementById("constTitle").textContent = `${b.name} — 成分股成交额与权重明细`;
  const topList = b.constituents_top || [];
  document.getElementById("constCountBadge").textContent =
    b.constituents_total_count > topList.length ? `前 ${topList.length} / 共 ${b.constituents_total_count} 只` : `共 ${b.constituents_total_count} 只`;

  document.getElementById("constBody").innerHTML = topList.map(c => `
    <tr>
      <td class="mono"><strong>${c.symbol}</strong></td>
      <td style="color:var(--muted);">${c.name}</td>
      <td class="num mono">$${c.dv5_bn.toFixed(2)}B</td>
      <td class="num mono">${c.dv_wt_pct.toFixed(1)}%</td>
      <td class="num mono">${c.mcap_wt_pct.toFixed(1)}%</td>
    </tr>
  `).join("");

  document.getElementById("bucketCardTitle").textContent = `${b.name} — 分档后 60 日表现 (EMA3 序列)`;
  document.getElementById("basketBucketBody").innerHTML = b.bucket_stats.map(st => {
    if (!st.n_days) {
      return `<tr><td style="color:#8C939E;">${st.bucket}</td><td class="num mono" style="color:#8C939E;">0</td><td colspan="4" class="num" style="color:#8C939E;">未触发</td></tr>`;
    }
    const rowStyle = st.is_baseline ? 'style="background:#F4F3EF;font-weight:600;"' : '';
    const vsBase = st.is_baseline ? '自身基线' : fmtSigned(st.fwd60_vs_base, 2, 'pp');
    const vsCls = st.is_baseline ? '' : (st.fwd60_vs_base >= 0 ? 'pos' : 'neg');
    return `<tr ${rowStyle}>
      <td>${st.bucket}</td>
      <td class="num mono">${st.n_days}</td>
      <td class="num mono ${st.fwd60_mean >= 0 ? 'pos' : 'neg'}">${fmtSigned(st.fwd60_mean)}</td>
      <td class="num mono ${vsCls}">${vsBase}</td>
      <td class="num mono neg">${st.fwd60_mdd_mean.toFixed(2)}%</td>
      <td class="num mono ${st.prob_dd15_60d >= 50 ? 'neg' : ''}">${st.prob_dd15_60d.toFixed(1)}%</td>
    </tr>`;
  }).join("");

  document.getElementById("episodeCardTitle").textContent = `${b.name} — 迟滞偏热灯亮起事件 (${b.episodes_ge8.length} 轮)`;
  const eBody = document.getElementById("episodeBody");
  if (!b.episodes_ge8.length) {
    eBody.innerHTML = `<tr><td colspan="5" style="color:#8C939E;text-align:center;">样本期内未亮起偏热灯</td></tr>`;
  } else {
    eBody.innerHTML = b.episodes_ge8.slice().reverse().map(ep => `<tr>
      <td class="mono">${ep.date}</td>
      <td class="num mono warn">${ep.score.toFixed(2)} <span style="color:#8C939E;">(${ep.raw_score.toFixed(1)})</span></td>
      <td class="num mono ${ep.fwd20_ret !== null ? (ep.fwd20_ret >= 0 ? 'pos' : 'neg') : ''}">${ep.fwd20_ret !== null ? fmtSigned(ep.fwd20_ret) : '未满20日'}</td>
      <td class="num mono ${ep.fwd60_ret !== null ? (ep.fwd60_ret >= 0 ? 'pos' : 'neg') : ''}">${ep.fwd60_ret !== null ? fmtSigned(ep.fwd60_ret) : '未满60日'}</td>
      <td class="num mono neg">${ep.fwd60_mdd !== null ? ep.fwd60_mdd.toFixed(2) + '%' : '—'}</td>
    </tr>`).join("");
  }
}

function setupInteractions() {
  document.getElementById("clearSubBtn").addEventListener("click", () => toggleSubKey(selectedSubKey));

  document.querySelectorAll("#rangeBtns .btn-toggle").forEach(btn => {
    btn.addEventListener("click", () => {
      document.querySelectorAll("#rangeBtns .btn-toggle").forEach(x => x.classList.remove("active"));
      btn.classList.add("active");
      curRange = btn.getAttribute("data-range");
      renderAll();
    });
  });

  document.querySelectorAll("#cmpBtns .btn-toggle").forEach(btn => {
    btn.addEventListener("click", () => {
      document.querySelectorAll("#cmpBtns .btn-toggle").forEach(x => x.classList.remove("active"));
      btn.classList.add("active");
      cmpMode = btn.getAttribute("data-cmp");
      document.getElementById("rawLegend").style.display = cmpMode === "raw_ref" ? "inline-flex" : "none";
      document.getElementById("v1Legend").style.display = cmpMode === "v1_ref" ? "inline-flex" : "none";
      drawChart();
    });
  });

  const canvas = document.getElementById("crowdChart");
  const tip = document.getElementById("chartTooltip");

  canvas.addEventListener("mousemove", e => {
    const g = canvas._chartGeom;
    if (!g || !currentPts.length) return;
    const rect = canvas.getBoundingClientRect();
    const mx = e.clientX - rect.left;
    if (mx < g.padL || mx > g.W - g.padR) {
      tip.style.display = "none";
      drawChart();
      return;
    }
    const idx = Math.max(0, Math.min(currentPts.length - 1, Math.round(((mx - g.padL) / g.plotW) * (currentPts.length - 1))));
    const pt = currentPts[idx];

    drawChart();
    const ctx = canvas.getContext("2d");
    const x = g.xOf(idx);
    ctx.save();
    ctx.strokeStyle = "rgba(30, 41, 59, 0.28)";
    ctx.lineWidth = 1;
    ctx.setLineDash([3, 3]);
    ctx.beginPath(); ctx.moveTo(x, g.padT); ctx.lineTo(x, g.H - g.padB); ctx.stroke();

    ctx.fillStyle = "#16A34A";
    ctx.beginPath(); ctx.arc(x, g.yCrowd(pt.c), 4, 0, Math.PI * 2); ctx.fill();

    if (selectedSubKey) {
      ctx.fillStyle = "#7C3AED";
      ctx.beginPath(); ctx.arc(x, g.yCrowd(pt[selectedSubKey]), 3.5, 0, Math.PI * 2); ctx.fill();
    }

    ctx.fillStyle = "#475569";
    ctx.beginPath(); ctx.arc(x, g.yPrice(pt.nav), 3.5, 0, Math.PI * 2); ctx.fill();
    ctx.restore();

    tip.innerHTML = `
      <div class="mono" style="font-weight:600;color:#1C1F24;margin-bottom:3px;">${pt.d} · <span style="color:${scoreColor(pt.c, pt.st)};">${pt.st_lbl || pt.st}</span></div>
      <div>EMA3 拥挤度：<strong class="mono" style="color:${scoreColor(pt.c, pt.st)};">${pt.c.toFixed(2)}</strong> <span class="mono" style="color:#8C939E;">(瞬时 ${pt.c_raw.toFixed(2)})</span></div>
      ${cmpMode === "v1_ref" && pt.c_v1 !== null ? `<div style="color:#6B7280;">原图 V1 口径：<span class="mono">${pt.c_v1.toFixed(2)}</span></div>` : ''}
      ${selectedSubKey ? `<div style="color:#7C3AED;">${SUB_META[selectedSubKey]}：<strong class="mono">${pt[selectedSubKey].toFixed(2)}</strong></div>` : ''}
      <div>价格指数：<span class="mono">${pt.nav.toFixed(2)}</span></div>
      <div style="color:#5F6672;margin-top:3px;" class="mono">年线 ${pt.s_mom.toFixed(1)} · 波动 ${pt.s_vol.toFixed(1)} · 占比 ${pt.s_dvs.toFixed(1)} · 换手 ${pt.s_trn.toFixed(1)} · 估值 ${pt.s_val.toFixed(1)}</div>
    `;
    tip.style.display = "block";
    const tipW = tip.offsetWidth;
    const leftPos = (x + 16 + tipW > g.W - g.padR) ? Math.max(8, x - tipW - 14) : (x + 14);
    tip.style.left = leftPos + "px";
    tip.style.top = "16px";
  });

  canvas.addEventListener("mouseleave", () => {
    tip.style.display = "none";
    drawChart();
  });

  window.addEventListener("resize", () => drawChart());
}

function renderAll() {
  renderStrip();
  renderTerminalTable();
  renderBasketDetails();
  drawChart();
}

initStatic();
setupInteractions();
renderAll();
</script>
</body>
</html>
"""


def render_dashboard_html(payload: dict) -> str:
    """Render self-contained interactive HTML dashboard from crowding payload."""
    compact = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
    safe_json = compact.replace("</", "<\\/")
    return TEMPLATE.replace("__CROWDING_JSON_PAYLOAD__", safe_json)
