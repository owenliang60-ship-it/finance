"""Self-contained single-file HTML v2: four SVG panels, control deck, logic card, audit drawer (v2 Task 4)."""
import json
import re
from pathlib import Path

import pytest

from terminal.valuation.engine import build_payload
from terminal.valuation.gates import run_gates, summarize
from terminal.valuation.ledger import load_ledger
from terminal.valuation.render import render_html

ASSETS = Path(__file__).resolve().parents[3] / "terminal" / "valuation" / "assets"
SCRIPTS = ("kernel.js", "state.js", "charts.js", "ui.js")
BANNED = ("NaN", "undefined", "Infinity", "目标价", "target price", "建议仓位", "买入", "卖出")
FADR_LEDGER = 'schema = 1\nsymbol = "ACME"\nreporting_currency = "TWD"\neps_basis = "ifrs"\ncyclical = true\n'
PAYLOAD_RE = re.compile(r'<script type="application/json" id="payload">(.*?)</script>', re.S)
DISCLAIMER = "本页呈现事实反推、台账登记的参考情景预设与你输入的情景演算；参考情景与权重是可覆盖的假设，不是模型结论。"


def _payload(facts, ledger):
    return build_payload(facts, ledger, summarize(run_gates(facts, ledger), facts, ledger))


@pytest.fixture
def acme_payload(valuation_db, read_facts, ledger_file):
    db, _ = valuation_db
    return _payload(read_facts(db), load_ledger(ledger_file()))


def _embedded(html):
    found = PAYLOAD_RE.findall(html)
    assert len(found) == 1
    return found[0]


def _visible(html):
    return re.sub(r"<(script|style)\b.*?</\1>", "", html, flags=re.S | re.I)


def test_no_external_resources(acme_payload):
    html = render_html(acme_payload)
    for bad in ("<script src", "<link", "@import", "url(", "var(--", "<img"):
        assert bad not in html
    assert html.startswith("<!DOCTYPE html>")


def test_banned_words_absent(acme_payload):
    html = render_html(acme_payload)
    for text in (_visible(html), _embedded(html)):
        for word in BANNED:
            assert re.search(re.escape(word), text, re.I) is None, word
    assert DISCLAIMER in _visible(html)
    assert "参考情景预设（可随时拖动滑块与修改权重覆盖）" in _visible(html)


def test_required_elements_and_order(acme_payload):
    html = render_html(acme_payload)
    for marker in ('id="p1"', 'id="p2"', 'id="p3"', 'id="p4"', 'id="logic-card"', 'id="deck"', 'id="sl-seg-0"',
                   'id="sl-seg-1"', 'id="sl-margin"', 'id="sl-pe"', 'id="in-pe-half"', 'id="w-consensus"', 'id="w-bull"',
                   'id="w-base"', 'id="w-bear"', 'data-preset="consensus"', 'data-preset="bull"',
                   'data-preset="cycle:2025-26"', 'id="btn-reset"', 'id="pill-zone"', 'id="kpi-price"',
                   'id="kpi-mktcap"', 'id="kpi-window"', 'id="kpi-implied-pe"', 'id="kpi-custom"', 'id="kpi-ev"',
                   'id="badge-gates"', 'id="w-note"', 'id="gates"', 'id="sources"', 'id="sl-driver-hbm"',
                   'class="tick"', 'data-track-mark=', 'viewBox="0 0 560 340"', 'step="any"'):
        assert marker in html, marker
    assert re.search(r'<details id="audit"\s*>', html)
    assert not re.search(r'<details id="audit"[^>]*\bopen\b', html)
    order = [html.index(m) for m in ('id="kpi"', 'id="deck"', 'id="p1"', 'id="p2"', 'id="logic-card"', 'id="p3"',
                                     'id="p4"', 'id="audit"')]
    assert order == sorted(order)


def test_logic_card_content(acme_payload, valuation_db, read_facts, ledger_file):
    html = render_html(acme_payload)
    card = html[html.index('id="logic-card"'):html.index('id="panel-p3"')]
    assert "营收拆分逻辑 · 最新已核实季 2026-09-30" in card
    assert "G1a 对账通过 · 偏差 0.00%" in card
    assert "$900.0M · 60.0%" in card and "$600.0M · 40.0%" in card
    assert "稳态底座" in card and "夹具次轴注释" in card and "等比切分" in card
    db, _ = valuation_db
    pct = _payload(read_facts(db), load_ledger(ledger_file(
        ("prior_year_restated = [600.0, 400.0]", "prior_year_restated = [600.0, 400.0]\npct_derived = true"))))
    assert "G1a 百分比折算（构造性）· G2 引文通过" in render_html(pct)
    bad = _payload(read_facts(db), load_ledger(ledger_file(("values = [900.0, 600.0]", "values = [900.0, 500.0]"))))
    html = render_html(bad)
    card = html[html.index('id="logic-card"'):html.index('id="panel-p3"')]
    assert "S1 未过" in card and "chips" not in card
    assert 'data-preset="bull" disabled title="' in html


def test_fx_slider_only_for_non_usd(acme_payload, valuation_db, read_facts, ledger_file):
    assert 'id="sl-fx"' not in render_html(acme_payload)
    _, seed = valuation_db
    fadr = _payload(read_facts(seed(fadr=True)), load_ledger(ledger_file(text=FADR_LEDGER)))
    assert 'id="sl-fx"' in render_html(fadr)


def test_ledger_strings_are_escaped(valuation_db, read_facts, ledger_file):
    db, _ = valuation_db
    evil = "</script><b>"
    ledger = load_ledger(ledger_file(('thesis = "夹具乐观情景"', f'thesis = "乐观 {evil}"'),
                                     ('axis_rationale = "夹具：', f'axis_rationale = "{evil}夹具：'),
                                     ('roles = ["稳态底座"', f'roles = ["{evil}"'),
                                     ('label = "夹具周期"', f'label = "周期 {evil}"')))
    payload = _payload(read_facts(db), ledger)
    html = render_html(payload)
    assert evil not in html
    assert "&lt;/script&gt;&lt;b&gt;" in html
    assert json.loads(_embedded(html)) == payload


def test_blocked_page_has_only_banner_and_gates(valuation_db, read_facts, ledger_file):
    _, seed = valuation_db
    payload = _payload(read_facts(seed(fadr=True)), load_ledger(ledger_file()))
    assert payload["status"]["page_blocked"] is True
    html = render_html(payload)
    for absent in ('id="p1"', 'type="range"', 'id="deck"', 'id="logic-card"', 'id="kpi-custom"'):
        assert absent not in html, absent
    assert 'id="gates"' in html and 'id="banner"' in html
    assert "C1" in _visible(html)


def test_inlined_assets_match_files(acme_payload):
    html = render_html(acme_payload)
    scripts = re.findall(r"<script>(.*?)</script>", html, re.S)
    assert scripts == [(ASSETS / name).read_text(encoding="utf-8") for name in SCRIPTS]
    style = re.findall(r"<style>(.*?)</style>", html, re.S)
    assert style == [(ASSETS / "page.css").read_text(encoding="utf-8")]


def test_css_uses_literal_light_theme():
    css = (ASSETS / "page.css").read_text(encoding="utf-8")
    assert "var(" not in css and "@import" not in css and "url(" not in css
    body = re.search(r"body\s*\{([^}]*)\}", css).group(1)
    assert "background" in body and "color" in body
    assert "position: sticky" in css and "@media (max-width: 1279px)" in css


def test_js_never_uses_inner_html():
    for name in SCRIPTS:
        text = (ASSETS / name).read_text(encoding="utf-8")
        assert "innerHTML" not in text and "outerHTML" not in text and "insertAdjacentHTML" not in text
        assert "document.write" not in text and "var(--" not in text


def test_js_kernel_exports_exactly_v2_functions():
    import shutil
    import subprocess
    if shutil.which("node") is None:
        pytest.fail("node required")
    run = subprocess.run(["node", "-e", "console.log(JSON.stringify(Object.keys(require(process.argv[1])).sort()))",
                          str(ASSETS / "kernel.js")], capture_output=True, text=True, timeout=30)
    assert run.returncode == 0, run.stderr
    assert json.loads(run.stdout) == sorted(["fin", "zone", "to_usd", "implied_pe", "scenario_value", "pct_diff",
                                             "pct_rank", "weighted_value", "seg_revenue", "implied_margin",
                                             "margin_eps", "quarter_path", "grid_pos", "live_state"])
