"""Single-file HTML v2: KPI strip, sticky control deck, four SVG panels, segment logic card, audit drawer.

render.py writes the static skeleton; every ledger or payload string it writes goes through
html.escape. Live numbers and the SVG panels are filled in by ui.js (kernel, state, charts, ui are
inlined in that order). A blocked page carries only the banner and the gate report.
"""
from __future__ import annotations

import html
import json
from pathlib import Path

ASSETS = Path(__file__).resolve().parent / "assets"
SCRIPTS = ("kernel.js", "state.js", "charts.js", "ui.js")
PANEL_TITLES = (("p1", "Scenario Fair Value Ranges vs. Current Price", "情景估值区间 vs 现价（USD/股）"),
                ("p2", "NTM Revenue Stack by Segment", "分部 NTM 营收堆叠（报告币种）"),
                ("p3", "Quarterly Revenue Trajectory", "季度营收走势：近 4 季实际 → 未来 4 季情景"),
                ("p4", "Valuation Matrix Heatmap", "NTM EPS（USD）× Forward P/E，按相对现价着色"))
KIND_TEXT = {"history": "历史", "actual": "实绩", "street": "卖方", "ledger": "台账", "boss": "Boss"}


def _e(value) -> str:
    return html.escape(str(value), quote=True)


def _asset(name: str) -> str:
    text = (ASSETS / name).read_text(encoding="utf-8")
    if "</script" in text.lower() or "</style" in text.lower():
        raise ValueError(f"asset {name} contains a closing tag and cannot be inlined")
    return text


def _payload_json(payload: dict) -> str:
    """JSON for a <script type="application/json"> block: <, > and & become unicode escapes,
    so no ledger string can close the block or open a comment (stricter than </ → <\\/)."""
    text = json.dumps(payload, ensure_ascii=False, allow_nan=False)
    return text.replace("&", "\\u0026").replace("<", "\\u003c").replace(">", "\\u003e")


def _money(x, currency) -> str:
    if x is None:
        return "不可得"
    prefix = {"USD": "$", "TWD": "NT$"}.get(currency, f"{currency} ")
    a = abs(x)
    body = (f"{a / 1e12:.2f}T" if a >= 1e12 else f"{a / 1e9:.1f}B" if a >= 1e9 else f"{a / 1e6:.1f}M" if a >= 1e6
            else f"{a:,.0f}")
    return ("−" if x < 0 else "") + prefix + body


def _pct(x, digits=1) -> str:
    return "不可得" if x is None else f"{x * 100:.{digits}f}%"


def _banner(payload: dict) -> str:
    st = payload["status"]
    lines = list(st["banner"])
    if st["page_blocked"]:
        cls, head = "blocked", "整页熔断：页级门控阻断，本页不展示面板与滑块。"
    else:
        model, cons = payload["model"], payload["consensus"]
        if model["eps_mode"] != "margin":
            lines.append(f"净利率法禁用：{cons['eps_mode_reason']}；EPS 固定为卖方共识，分部与净利率滑块停用。")
        if model["mode"] == "total" and model["total_reason"]:
            lines.append(model["total_reason"])
        cls = "degraded" if st["exit_code"] != 0 else "ok"
        head = "降级：以下条目未通过门控，已从算术中剔除。" if cls == "degraded" else "全部门控通过。"
    items = "".join(f"<li>{_e(line)}</li>" for line in lines)
    hidden = " hidden" if cls == "ok" and not lines else ""
    return (f'<section id="banner" class="banner {cls}"{hidden}><p>{_e(head)}</p>'
            f'<ul id="banner-lines">{items}</ul></section>')


def _kpi(payload: dict) -> str:
    meta, st = payload["meta"], payload["status"]
    n = len(st["results"])
    bad = sum(1 for r in st["results"] if r["status"] != "VERIFIED")
    badge = f"门控 {n - bad}/{n} VERIFIED" if bad == 0 else f"门控 {bad} 项未通过"
    cls = "ok" if bad == 0 else "warn"

    def item(label, vid, sub_id=None, extra=""):
        sub = f'<span class="kpi-sub" id="{sub_id}"></span>' if sub_id else ""
        return (f'<div class="kpi{extra}"><span class="kpi-label">{_e(label)}</span>'
                f'<span class="kpi-value" id="{vid}"></span>{sub}</div>')

    return (f'<header id="kpi" class="kpi-strip"><div class="kpi kpi-title"><h1>{_e(meta["symbol"])}</h1>'
            f'<span class="kpi-sub">估值情景 · as_of {_e(meta["as_of"])} · {_e(meta["eps_basis"])}</span></div>'
            + item("Ref Price", "kpi-price", "kpi-price-date") + item("Mkt Cap", "kpi-mktcap")
            + item("NTM Window", "kpi-window", "kpi-window-sub") + item("现价隐含 NTM P/E", "kpi-implied-pe", "kpi-implied-sub")
            + item("Custom 实时情景值", "kpi-custom", "kpi-custom-sub", " kpi-custom")
            + item("加权情景期望值 EV", "kpi-ev", "kpi-ev-sub", " kpi-ev")
            + f'<button type="button" id="badge-gates" class="badge {cls}" title="展开审计栏">{_e(badge)}</button>'
            f'</header>')


def _ticks(payload: dict, key: str) -> tuple:
    marks = [(i, mk) for i, mk in enumerate(payload["markers"]) if mk["slider"] == key]
    lines = "".join(f'<span class="track-mark" data-track-mark="{i}"></span>' for i, _ in marks)
    buttons = "".join(
        f'<button type="button" class="tick" data-kind="{_e(mk["kind"])}" data-marker="{i}" '
        f'title="{_e(mk["label"] + " · " + KIND_TEXT.get(mk["kind"], mk["kind"]) + " · " + mk["source"])}">'
        f'<span class="tick-label">{_e(mk["label"])}</span> <span class="tick-value" data-marker-value="{i}"></span>'
        f'</button>' for i, mk in marks)
    return lines, (f'<div class="tick-group" data-for="{key}">{buttons}</div>' if buttons else "")


def _range(rid: str, key: str, label: str, payload: dict, extra: str = "") -> str:
    lines, buttons = _ticks(payload, key)
    return (f'<div class="ctl" data-key="{key}"><div class="ctl-head"><label for="{rid}">{_e(label)}</label>'
            f'<output id="val-{key}" for="{rid}"></output></div><div class="track">'
            f'<input type="range" id="{rid}" step="any"><div class="track-marks">{lines}</div></div>'
            f'{buttons}{extra}</div>')


def _deck(payload: dict) -> str:
    model, sliders = payload["model"], payload["sliders"]
    cur = model["currency"] or "本币"
    presets = []
    for sc in payload["scenarios"]:
        attrs = "" if sc["enabled"] else f' disabled title="{_e(sc["reason"] or "")}"'
        tip = "" if not sc["enabled"] else f' title="{_e(sc["thesis"])}"'
        presets.append(f'<button type="button" class="preset tone-{_e(sc["tone"])}" data-preset="{_e(sc["id"])}"'
                       f'{attrs}{tip}>{_e(sc["label"])}</button>')
    for cyc in payload["cycles"]:
        attrs = "" if cyc["enabled"] else f' disabled title="{_e(cyc["reason"] or "")}"'
        tip = "" if not cyc["enabled"] else (
            f' title="{_e("台账周期 " + cyc["start"] + " 至 " + cyc["end"] + "：分部营收按峰→谷比例同调，净利率取窗口最低季")}"')
        presets.append(f'<button type="button" class="preset tone-cycle" data-preset="cycle:{_e(cyc["id"])}"'
                       f'{attrs}{tip}>{_e(cyc["label"] if cyc["label"].endswith("周期") else cyc["label"] + " 周期")}'
                       f'</button>')
    replay = f'<p class="note">{_e(payload["replay_note"])}</p>' if payload["replay_note"] else ""
    segs = []
    for i, s in enumerate(model["segments"]):
        segs.append(
            f'<div class="ctl seg"><div class="ctl-head"><label for="sl-seg-{i}"><span class="dot" '
            f'style="background:{_e(s["color"])}"></span>{_e(s["name"])}</label>'
            f'<output id="val-seg-{i}" for="sl-seg-{i}"></output></div>'
            f'<input type="range" id="sl-seg-{i}" step="any">'
            f'<div class="ctl-foot"><span id="base-seg-{i}"></span><span class="live" id="amt-seg-{i}"></span></div></div>')
    readout = "".join(f'<div><dt>{_e(t)}</dt><dd id="{i}"></dd></div>' for i, t in (
        ("out-rev", f"实时 NTM 营收（{cur}）"), ("out-ni", f"实时 NTM 净利（{cur}）"),
        ("out-eps-local", "实时 NTM EPS（本币）"), ("out-eps-usd", "实时 NTM EPS（USD）"),
        ("out-implied-pe", "现价 ÷ 实时 EPS")))
    parts = [
        '<section id="deck" class="deck" aria-label="参数控制台">',
        '<h2>参考情景预设（可随时拖动滑块与修改权重覆盖）</h2>',
        f'<div class="presets">{"".join(presets)}<button type="button" id="btn-reset" class="preset reset">重置</button></div>',
        replay,
        '<p id="pill-zone" class="pill" hidden>已超出近 20 季 ±2σ 温和区，建议同步参考历史下行期净利率刻度</p>',
        f'<dl class="readout">{readout}</dl>',
        f'<h2>分部营收调整（−60% 至 +40%，0% = 卖方共识）</h2>{"".join(segs)}',
        '<h2>利润与估值</h2>',
        _range("sl-margin", "margin", "NTM 净利率（Non-GAAP / 报表口径）", payload),
        _range("sl-pe", "pe", "Forward P/E", payload,
               '<div class="ctl-foot"><label for="in-pe-half">P/E 半宽 ±</label>'
               '<input type="number" id="in-pe-half" min="0" step="0.5" inputmode="decimal"></div>'),
    ]
    if sliders["fx"] is not None:
        parts.append(_range("sl-fx", "fx", f"汇率（{cur}/USD）", payload))
    for d in sliders["drivers"]:
        did = _e(d["id"])
        note = f'<p class="note">{_e(d["basis_note"])}</p>' if d.get("basis_note") else ""
        parts.append(f'<div class="ctl"><div class="ctl-head"><label for="sl-driver-{did}">{_e(d["label"])}（收入增量假设）'
                     f'</label></div><input type="range" id="sl-driver-{did}" step="any">'
                     f'<output id="val-driver-{did}" for="sl-driver-{did}"></output>{note}</div>')
    weights = "".join(
        f'<label class="w"><span>{_e(sc["label"])}</span><input type="text" inputmode="decimal" id="w-{_e(sc["id"])}" '
        f'data-weight="{_e(sc["id"])}"><span>%</span></label>' for sc in payload["scenarios"])
    parts.append(f'<h2>情景权重（合计 100% 时显示 EV）</h2><div class="weights">{weights}</div>'
                 '<p id="w-note" class="note"></p></section>')
    return "".join(parts)


def _panel(pid: str, title: str, sub: str, note: str = "") -> str:
    return (f'<section class="panel" id="panel-{pid}"><header><h3>{_e(title)}</h3><span>{_e(sub)}</span></header>'
            f'<svg id="{pid}" viewBox="0 0 560 340" role="img" aria-label="{_e(title)}"></svg>{note}</section>')


def _logic(payload: dict) -> str:
    model = payload["model"]
    if model["mode"] == "total" or model["logic"] is None:
        return (f'<section id="logic-card" class="logic"><div class="logic-head"><h3>营收拆分逻辑</h3></div>'
                f'<p>{_e(model["total_reason"] or "分部切分不可用")}</p></section>')
    logic, cur = model["logic"], model["currency"]
    if logic["pct_derived"]:
        badge = "G1a 百分比折算（构造性）· G2 引文通过"
    else:
        dev = logic["g1_dev"]
        badge = f"G1a 对账通过 · 偏差 {abs(dev) * 100:.2f}%" if dev is not None else "G1a 对账通过"
    chips = "".join(
        f'<li><span class="dot" style="background:{_e(s["color"])}"></span><b>{_e(s["name"])}</b>'
        f'<span class="amt">{_e(_money(s["latest_value"], cur))} · {_e(_pct(s["share"]))}</span>'
        + (f'<em>{_e(s["role"])}</em>' if s["role"] else "") + "</li>" for s in model["segments"])
    rationale = f'<p class="rationale">{_e(logic["axis_rationale"])}</p>' if logic["axis_rationale"] else ""
    second = f'<p class="note">{_e(logic["secondary_note"])}</p>' if logic["secondary_note"] else ""
    return (f'<section id="logic-card" class="logic"><div class="logic-head">'
            f'<h3>营收拆分逻辑 · 最新已核实季 {_e(model["split_quarter"])}</h3>'
            f'<span class="badge ok">{_e(badge)}</span></div><div class="logic-body">{rationale}'
            f'<div class="logic-main"><ul class="chips">{chips}</ul>{second}'
            f'<p class="note">{_e(logic["formula_note"])}</p></div></div></section>')


def _gates(payload: dict) -> str:
    rows = "".join(
        f'<tr class="{"" if r["status"] == "VERIFIED" else "bad"}"><td class="text">{_e(r["id"])}</td>'
        f'<td class="text">{_e(r["scope"])}</td><td class="text">{_e(r["status"])}{"（已确认）" if r["acked"] else ""}</td>'
        f'<td class="text">{"阻断" if r["blocks_page"] else ""}</td><td class="text">{_e(r["detail"])}</td></tr>'
        for r in payload["status"]["results"])
    return (f'<section class="card" id="gates"><h2>门控报告</h2><table><thead><tr><th>门控</th>'
            f'<th class="text">范围</th><th class="text">状态</th><th class="text">整页</th><th class="text">说明</th>'
            f'</tr></thead><tbody>{rows}</tbody></table></section>')


def _sources(payload: dict) -> str:
    meta = payload["meta"]
    items = "".join(
        f'<li><b>{_e(s["id"])}</b> · {_e(s["url"])} · 抓取 {_e(s["fetched_at"])} · 原文 {_e(s["raw"])} · '
        f'sha256 <code>{_e(s["sha256"])}</code></li>' for s in meta["sources"])
    quotes = "".join(f'<li><b>{_e(q["item"])}</b>（{_e(q["source"])}）：<q>{_e(q["quote"])}</q></li>'
                     for q in meta.get("quotes", []))
    guidance = "".join(
        f'<li>{_e(g["label"])}（{_e(g["fiscal_date"])}）：{_e(_money(g["value"], payload["model"]["currency"]))}'
        f'{" · " + _e("、".join(g["flags"])) if g["flags"] else ""} · 仅文字备注，不进算术 · {_e(g["source"])}</li>'
        for g in payload["guidance_notes"])
    return (f'<section class="card" id="sources"><h2>出处与逐字引文</h2><ul>{items or "<li>台账未登记原文</li>"}</ul>'
            + (f'<h3>引文</h3><ul class="quotes">{quotes}</ul>' if quotes else "")
            + (f'<h3>过门控的指引（文字备注）</h3><ul>{guidance}</ul>' if guidance else "")
            + f'<p class="note" id="audit-margins"></p>'
            f'<p class="note">行情与共识：market.db（只读）；台账：{_e(meta["ledger"])}；'
            f'报告币种 {_e(meta["reporting_currency"])}，EPS 口径 {_e(meta["eps_basis"])}。</p></section>')


def render_html(payload: dict) -> str:
    meta = payload["meta"]
    blocked = payload["status"]["page_blocked"]
    if blocked:
        body = (f'<main class="blocked-page"><header class="kpi-strip"><div class="kpi kpi-title">'
                f'<h1>{_e(meta["symbol"])}</h1><span class="kpi-sub">估值情景 · as_of {_e(meta["as_of"])}</span></div>'
                f'</header>{_banner(payload)}{_gates(payload)}</main>')
    else:
        p1, p2 = (_panel(pid, title, sub) for pid, title, sub in PANEL_TITLES[:2])
        p3 = _panel(*PANEL_TITLES[2], note='<p class="note" id="p3-note"></p>')
        p4 = _panel(*PANEL_TITLES[3], note='<p class="note" id="p4-note-cycle"></p><p class="note" id="p4-note-sample">'
                                           '</p><p class="note" id="p4-note-crowd"></p>')
        body = (f'<main class="dash">{_kpi(payload)}{_banner(payload)}{_deck(payload)}{p1}{p2}{_logic(payload)}{p3}{p4}'
                f'<details id="audit"><summary>审计栏：门控报告 · 一手原文出处与 SHA-256 · 逐字引文</summary>'
                f'{_gates(payload)}{_sources(payload)}</details></main>')
    scripts = "".join(f"<script>{_asset(n)}</script>" for n in SCRIPTS)
    return ("<!DOCTYPE html>\n"
            f'<html lang="zh-CN"><head><meta charset="utf-8">'
            f'<meta name="viewport" content="width=device-width, initial-scale=1">'
            f'<title>{_e(meta["symbol"])} 估值情景 · {_e(meta["as_of"])}</title><style>{_asset("page.css")}</style>'
            f'</head><body>{body}<footer>{_e(meta["disclaimer"])}</footer>'
            f'<script type="application/json" id="payload">{_payload_json(payload)}</script>{scripts}</body></html>\n')
