"""Self-contained single-file HTML (Task 6)."""
import json
import re
from pathlib import Path

import pytest

from terminal.valuation.engine import build_payload
from terminal.valuation.gates import run_gates, summarize
from terminal.valuation.ledger import load_ledger
from terminal.valuation.render import render_html

ASSETS = Path(__file__).resolve().parents[3] / "terminal" / "valuation" / "assets"
BANNED = ("NaN", "undefined", "Infinity", "目标价", "target price", "建议仓位", "买入", "卖出")
FADR_LEDGER = 'schema = 1\nsymbol = "ACME"\nreporting_currency = "TWD"\neps_basis = "ifrs"\ncyclical = true\n'
PAYLOAD_RE = re.compile(r'<script type="application/json" id="payload">(.*?)</script>', re.S)


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
    assert "本页只呈现事实反推与你输入的情景演算，不含模型结论。" in _visible(html)
    assert "加权情景值（按你输入的权重计算）" in _visible(html)


def test_required_elements(acme_payload):
    html = render_html(acme_payload)
    for marker in ('id="banner"', 'id="matrix"', 'id="sl-rev"', 'id="sl-margin"', 'id="sl-pe"', 'id="sl-driver-hbm"',
                   'id="out-eps"', 'id="out-eps-source"', 'id="out-zone"', 'id="out-implied-pe"', 'id="out-pe-rank"',
                   'id="out-value"', 'id="out-value-vs-price"', 'data-preset="2025-26"', 'id="btn-reset"',
                   'id="sandbox"', 'id="btn-save"', 'id="out-weighted"', 'id="gates"', 'id="segments"',
                   'id="sources"', 'step="any"', 'class="tick"'):
        assert marker in html, marker
    assert re.search(r'<details id="sandbox"\s*>', html)
    assert not re.search(r'<details id="sandbox"[^>]*\bopen\b', html)
    order = [html.index(m) for m in ('id="banner"', 'id="matrix"', 'id="sl-rev"', 'id="sandbox"', 'id="segments"',
                                     'id="gates"', 'id="sources"')]
    assert order == sorted(order)


def test_fx_slider_only_for_non_usd(acme_payload, valuation_db, read_facts, ledger_file):
    assert 'id="sl-fx"' not in render_html(acme_payload)
    _, seed = valuation_db
    fadr = _payload(read_facts(seed(fadr=True)), load_ledger(ledger_file(text=FADR_LEDGER)))
    assert 'id="sl-fx"' in render_html(fadr)


def test_ledger_strings_are_escaped(valuation_db, read_facts, ledger_file):
    db, _ = valuation_db
    evil = "</script><b>"
    ledger = load_ledger(ledger_file(('label = "HBM 收入"', f'label = "HBM {evil}"'),
                                     ('label = "夹具周期"', f'label = "周期 {evil}"')))
    payload = _payload(read_facts(db), ledger)
    html = render_html(payload)
    assert evil not in html
    assert "&lt;/script&gt;&lt;b&gt;" in html
    restored = _embedded(html).replace("<\\/", "</")
    assert json.loads(restored) == payload


def test_blocked_page_has_only_banner_and_gates(valuation_db, read_facts, ledger_file):
    _, seed = valuation_db
    payload = _payload(read_facts(seed(fadr=True)), load_ledger(ledger_file()))
    assert payload["status"]["page_blocked"] is True
    html = render_html(payload)
    assert 'id="matrix"' not in html and 'type="range"' not in html and 'id="sandbox"' not in html
    assert 'id="gates"' in html and 'id="banner"' in html
    assert "C1" in _visible(html)


def test_inlined_assets_match_files(acme_payload):
    html = render_html(acme_payload)
    scripts = re.findall(r"<script>(.*?)</script>", html, re.S)
    assert scripts[0] == (ASSETS / "kernel.js").read_text(encoding="utf-8")
    assert scripts[1] == (ASSETS / "state.js").read_text(encoding="utf-8")
    assert scripts[2] == (ASSETS / "ui.js").read_text(encoding="utf-8")
    style = re.findall(r"<style>(.*?)</style>", html, re.S)
    assert style == [(ASSETS / "page.css").read_text(encoding="utf-8")]


def test_css_uses_literal_light_theme():
    css = (ASSETS / "page.css").read_text(encoding="utf-8")
    assert "var(" not in css and "@import" not in css and "url(" not in css
    body = re.search(r"body\s*\{([^}]*)\}", css).group(1)
    assert "background" in body and "color" in body


def test_js_never_uses_inner_html():
    for name in ("kernel.js", "state.js", "ui.js"):
        text = (ASSETS / name).read_text(encoding="utf-8")
        assert "innerHTML" not in text and "outerHTML" not in text and "insertAdjacentHTML" not in text
        assert "document.write" not in text
