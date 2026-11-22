"""Single-file HTML: inline CSS, embedded payload JSON, inline kernel.js + state.js + ui.js.

render.py writes the static skeleton (sliders, matrix grid, gate report, sources); every ledger
or payload string it writes goes through html.escape. Numbers are filled in by ui.js.
"""
from __future__ import annotations

import html
import json
from pathlib import Path

ASSETS = Path(__file__).resolve().parent / "assets"
DISCLAIMER = "本页只呈现事实反推与你输入的情景演算，不含模型结论。"


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


def _banner(payload: dict) -> str:
    st = payload["status"]
    lines = list(st["banner"])
    if st["page_blocked"]:
        cls, head = "blocked", "整页熔断：页级门控阻断，本页不展示矩阵与滑块。"
    else:
        cons, seg = payload["consensus"], payload["segments"]
        if not cons["bridge_enabled"]:
            lines.append(f"利润率桥禁用：{cons['bridge_reason']}；EPS 固定为卖方共识。")
        if seg["cleared"]:
            lines.append(seg["cleared"])
        cls = "degraded" if st["exit_code"] != 0 else "ok"
        head = "降级：以下条目未通过门控，已从算术中剔除。" if cls == "degraded" else "全部门控通过。"
    items = "".join(f"<li>{_e(line)}</li>" for line in lines)
    return f'<section id="banner" class="banner {cls}"><p>{_e(head)}</p><ul id="banner-lines">{items}</ul></section>'


def _matrix(payload: dict) -> str:
    m = payload["matrix"]
    cols = m["columns"]
    head = "".join(f"<th>{_e(c['label'])}</th>" for c in cols)
    rows = []
    for i, level in enumerate(m["levels"]):
        cells = "".join(f'<td data-cell="{_e(c["key"])}:{i}"></td>' for c in cols)
        rows.append(f'<tr><th>P{round(level * 100)}</th><td data-pe="{i}"></td>{cells}</tr>')
    eps = "".join(f'<td data-eps="{_e(c["key"])}"></td>' for c in cols)
    implied = "".join(f'<td data-implied="{_e(c["key"])}"></td>' for c in cols)
    span = len(cols) + 2
    foot = [f'<tr><td colspan="{span}" id="pe-sample"></td></tr>']
    if m["cyclical"] is not None:
        foot.append(f'<tr><td colspan="{span}" id="cyclical-row"></td></tr>')
    foot.append(f'<tr><td colspan="{span}" id="crowding"></td></tr>')
    return (f'<section class="card"><h2>事实反推矩阵：历史 P/E 分位 × EPS（USD）</h2>'
            f'<table id="matrix"><thead><tr><th>历史分位</th><th>P/E</th>{head}</tr></thead><tbody>'
            + "".join(rows)
            + f'<tr class="sum"><th colspan="2">EPS（USD）</th>{eps}</tr>'
            f'<tr class="sum"><th colspan="2">现价隐含 P/E 与历史分位</th>{implied}</tr>'
            f'</tbody><tfoot>{"".join(foot)}</tfoot></table></section>')


def _ticks(payload: dict, key: str) -> str:
    marks = [(i, m) for i, m in enumerate(payload["markers"]) if m["slider"] == key]
    buttons = "".join(
        f'<button type="button" class="tick" data-kind="{_e(m["kind"])}" data-marker="{i}" '
        f'title="{_e(m["label"] + " · " + m["source"])}" aria-label="{_e(m["label"] + " · " + m["source"])}"></button>'
        for i, m in marks)
    table = ""
    if marks:
        rows = "".join(f'<tr><td data-marker-value="{i}"></td><td class="text">{_e(m["label"])}</td>'
                       f'<td class="text">{_e(m["source"])}</td></tr>' for i, m in marks)
        table = (f'<table class="ticklist"><thead><tr><th>值</th><th class="text">刻度</th>'
                 f'<th class="text">出处</th></tr></thead><tbody>{rows}</tbody></table>')
    return buttons, table


def _slider(payload: dict, key: str, label: str) -> str:
    sl = payload["sliders"][key]
    buttons, table = _ticks(payload, key)
    disabled = " disabled" if sl["disabled"] else ""
    return (f'<div class="slider" data-key="{key}"><label for="sl-{key}">{_e(label)}</label>'
            f'<div class="track"><input type="range" id="sl-{key}" step="any"{disabled}>'
            f'<div class="ticks">{buttons}</div></div><output id="val-{key}" for="sl-{key}"></output>{table}</div>')


def _driver(d: dict) -> str:
    did = _e(d["id"])
    note = f'<p class="note">{_e(d["basis_note"])}</p>' if d.get("basis_note") else ""
    return (f'<div class="slider" data-driver="{did}"><label for="sl-driver-{did}">{_e(d["label"])}'
            f'（收入增量假设，初始增量为 0）</label><div class="track"><input type="range" id="sl-driver-{did}" '
            f'step="any"></div><output id="val-driver-{did}" for="sl-driver-{did}"></output>{note}</div>')


def _scenario(payload: dict) -> str:
    cur = payload["consensus"]["currency"] or "本币"
    parts = [_slider(payload, "rev", f"NTM 营收（{cur}）"), _slider(payload, "margin", "增量利润率"),
             _slider(payload, "pe", "情景 P/E")]
    if payload["sliders"]["fx"] is not None:
        parts.append(_slider(payload, "fx", f"汇率（{cur}/USD）"))
    parts += [_driver(d) for d in payload["sliders"]["drivers"]]
    buttons = []
    for p in payload["presets"]:
        attrs = "" if p["enabled"] else f' disabled title="{_e(p["reason"] or "")}"'
        buttons.append(f'<button type="button" data-preset="{_e(p["id"])}"{attrs}>{_e(p["label"])} 回放</button>')
    note = f'<span class="note">{_e(payload["replay_note"])}</span>' if payload["replay_note"] else ""
    outputs = "".join(f'<div><dt>{_e(t)}</dt><dd id="{i}"></dd></div>' for i, t in (
        ("out-eps", "情景 EPS（USD）"), ("out-eps-source", "EPS 来源"), ("out-zone", "营收所处区间"),
        ("out-implied-pe", "现价隐含 P/E"), ("out-pe-rank", "该 P/E 的历史分位"),
        ("out-value", "情景值（情景 P/E × 情景 EPS）"), ("out-value-vs-price", "情景值相对现价")))
    return (f'<section class="card" id="scenario"><h2>情景滑块（初始位 = 卖方共识，情景值 = 现价）</h2>'
            + "".join(parts)
            + f'<div class="presets">周期回放：{"".join(buttons)}{note}</div>'
            f'<dl class="outputs">{outputs}</dl>'
            f'<button type="button" id="btn-reset">回到初始位</button>'
            f'<ul id="guidance-notes" class="note"></ul></section>')


def _sandbox() -> str:
    return ('<details id="sandbox"><summary>情景沙盒：保存情景并自填权重</summary>'
            '<button type="button" id="btn-save">保存当前情景</button>'
            '<table id="scenarios"><thead><tr><th class="text">情景</th><th>EPS（USD）</th><th>P/E</th>'
            '<th>情景值</th><th>权重（%）</th><th></th></tr></thead><tbody id="scenario-rows"></tbody></table>'
            '<p>加权情景值（按你输入的权重计算）：<strong id="out-weighted">—</strong> '
            '<span id="out-weighted-reason" class="note"></span></p></details>')


def _segments(payload: dict) -> str:
    seg = payload["segments"]
    names = "".join(f"<th>{_e(n)}</th>" for n in seg["names"])
    return (f'<section class="card" id="segments"><h2>分部（仅通过 G1/G2 的季度）</h2>'
            f'<p class="note" id="segments-note"></p><table><thead><tr><th>季度</th>{names}'
            f'<th class="text">同比口径</th></tr></thead><tbody id="segments-rows"></tbody></table></section>')


def _gates(payload: dict) -> str:
    rows = "".join(
        f'<tr><td class="text">{_e(r["id"])}</td><td class="text">{_e(r["scope"])}</td>'
        f'<td class="text">{_e(r["status"])}{"（已确认）" if r["acked"] else ""}</td>'
        f'<td class="text">{"阻断" if r["blocks_page"] else ""}</td><td class="text">{_e(r["detail"])}</td></tr>'
        for r in payload["status"]["results"])
    return (f'<section class="card" id="gates"><h2>门控报告</h2><table><thead><tr><th>门控</th>'
            f'<th class="text">范围</th><th class="text">状态</th><th class="text">整页</th><th class="text">说明</th>'
            f'</tr></thead><tbody>{rows}</tbody></table></section>')


def _sources(payload: dict) -> str:
    items = "".join(
        f'<li>{_e(s["id"])} · {_e(s["url"])} · 抓取 {_e(s["fetched_at"])} · 原文 {_e(s["raw"])} · '
        f'sha256 {_e(s["sha256"])}</li>' for s in payload["meta"]["sources"])
    meta = payload["meta"]
    return (f'<section class="card" id="sources"><h2>出处</h2><ul>{items or "<li>台账未登记原文</li>"}</ul>'
            f'<p class="note">行情与共识：market.db（只读）；台账：{_e(meta["ledger"])}；'
            f'报告币种 {_e(meta["reporting_currency"])}，EPS 口径 {_e(meta["eps_basis"])}。</p></section>')


def render_html(payload: dict) -> str:
    meta = payload["meta"]
    blocked = payload["status"]["page_blocked"]
    body = [f'<header><h1>{_e(meta["symbol"])} 估值情景</h1>'
            f'<p class="sub">as_of {_e(meta["as_of"])} · 现价 <span id="hdr-price"></span></p></header>',
            _banner(payload)]
    if not blocked:
        body += [_matrix(payload), _scenario(payload), _sandbox(), _segments(payload)]
    body.append(_gates(payload))
    if not blocked:
        body.append(_sources(payload))
    body.append(f"<footer>{_e(DISCLAIMER)}</footer>")
    scripts = "".join(f"<script>{_asset(n)}</script>" for n in ("kernel.js", "state.js", "ui.js"))
    return ("<!DOCTYPE html>\n"
            f'<html lang="zh-CN"><head><meta charset="utf-8">'
            f'<meta name="viewport" content="width=device-width, initial-scale=1">'
            f'<title>{_e(meta["symbol"])} 估值情景 · {_e(meta["as_of"])}</title><style>{_asset("page.css")}</style>'
            f'</head><body>{"".join(body)}'
            f'<script type="application/json" id="payload">{_payload_json(payload)}</script>{scripts}</body></html>\n')
