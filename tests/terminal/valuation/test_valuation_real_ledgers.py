"""MU / TSM real ledgers (v2 Task 5). No database; checks are re-implemented here, not taken from gates.py."""
import hashlib
import tomllib
from pathlib import Path
from urllib.parse import urlparse

import pytest

from terminal.valuation.ledger import load_ledger

ROOT = Path(__file__).resolve().parents[3] / "terminal" / "valuation" / "ledgers"
BANNED_WORDS = ("目标价", "target price", "建议仓位", "买入", "卖出")   # case-insensitive
BANNED_TOKENS = ("NaN", "undefined", "Infinity")                  # JS tokens, case-sensitive ("NAND" is fine)
REVENUE = {"MU": 54_229e6, "TSM": 1_494_243_284_000}
ADJS = {
    "MU": {"bull": [0.10, 0.15, 0.0, 0.05], "base": [-0.20, -0.05, -0.25, -0.10], "bear": [-0.45, -0.25, -0.45, -0.25]},
    "TSM": {"bull": [0.12, 0.05, 0.05, 0.0], "base": [-0.10, -0.03, 0.0, -0.05], "bear": [-0.28, -0.10, -0.08, -0.10]},
}
PARAMS = {  # (net_margin, pe_mid, pe_half, weight), work order 2026-11-22
    "MU": {"bull": (0.68, 6.0, 1.0, 20), "base": (0.50, 8.0, 1.0, 50), "bear": (0.23, 11.0, 1.5, 25)},
    "TSM": {"bull": (0.52, 18.0, 1.5, 20), "base": (0.46, 15.0, 1.5, 50), "bear": (0.38, 12.0, 1.5, 25)},
}
SYMBOLS = ("MU", "TSM")


def _raw(symbol):
    return tomllib.loads((ROOT / symbol / "ledger.toml").read_text(encoding="utf-8"))


def _norm(text):
    return " ".join(text.split())


def _strings(node):
    if isinstance(node, str):
        yield node
    elif isinstance(node, dict):
        for v in node.values():
            yield from _strings(v)
    elif isinstance(node, list):
        for v in node:
            yield from _strings(v)


@pytest.mark.parametrize("symbol", SYMBOLS)
def test_ledger_loads(symbol):
    ledger = load_ledger(ROOT / symbol / "ledger.toml")
    assert ledger.symbol == symbol and ledger.cyclical is True
    assert [c["id"] for c in ledger.cycles] == ["2018-19", "2022-23"]
    assert len(ledger.segment_names) == 4 and len(ledger.segment_roles) == 4


@pytest.mark.parametrize("symbol", SYMBOLS)
def test_sources_hash_and_domain(symbol):
    raw = _raw(symbol)
    allowed = ["sec.gov"] + raw.get("t1_domains", [])
    for src in raw["sources"]:
        path = ROOT / symbol / src["raw"]
        assert path.is_file()
        assert hashlib.sha256(path.read_bytes()).hexdigest() == src["sha256"]
        host = urlparse(src["url"]).hostname
        assert any(host == d or host.endswith("." + d) for d in allowed), host
        path.read_bytes().decode("utf-8")


@pytest.mark.parametrize("symbol", SYMBOLS)
def test_quotes_are_verbatim(symbol):
    raw = _raw(symbol)
    texts = {s["id"]: _norm((ROOT / symbol / s["raw"]).read_text(encoding="utf-8")) for s in raw["sources"]}
    items = raw["segments"]["quarters"] + raw.get("eps_facts", [])
    assert items
    for item in items:
        quote = _norm(item["quote"])
        assert len(quote) >= 20
        assert quote in texts[item["source"]]


@pytest.mark.parametrize("symbol", SYMBOLS)
def test_segment_total_matches_revenue(symbol):
    raw = _raw(symbol)
    q = raw["segments"]["quarters"][-1]
    total = 0.0
    for v in q["values"]:
        total += v * raw["segments"]["unit"]
    assert abs(total / REVENUE[symbol] - 1) <= 0.01


def test_mu_values_and_eps_match_source():
    raw = _raw("MU")
    assert raw["segments"]["quarters"][0]["values"] == [18002.0, 16283.0, 13114.0, 6824.0]
    text = _norm((ROOT / "MU" / "sources" / "mu-fq4-26-ex991.txt").read_text(encoding="utf-8"))
    for figure in ("$ 18,002", "$ 16,283", "$ 13,114", "$ 6,824", "$33.42 per diluted share", "54,229"):
        assert figure in text
    assert raw["eps_facts"][0]["value"] == 33.42


def test_tsm_values_are_disclosed_shares_of_revenue():
    raw = _raw("TSM")
    q = raw["segments"]["quarters"][0]
    assert q["pct_derived"] is True
    shares = (0.68, 0.21, 0.08, 0.03)
    for v, s in zip(q["values"], shares):
        assert abs(v * 1e9 - REVENUE["TSM"] * s) <= 2e6     # work-order values are rounded to NT$0.001B
    text = _norm((ROOT / "TSM" / "sources" / "tsm-3q26-mgmt-report.txt").read_text(encoding="utf-8"))
    assert "Net revenue was NT$1,494.24 billion" in text
    assert "eps_facts" not in raw


@pytest.mark.parametrize("symbol", SYMBOLS)
def test_scenarios_match_work_order(symbol):
    raw = _raw(symbol)
    total = raw["assumptions"]["consensus_weight"]
    for s in raw["scenarios"]:
        assert s["seg_adjs"] == ADJS[symbol][s["id"]], s["id"]
        assert (s["net_margin"], s["pe_mid"], s["pe_half"], s["weight"]) == PARAMS[symbol][s["id"]], s["id"]
        total += s["weight"]
    assert total == 100


@pytest.mark.parametrize("symbol", SYMBOLS)
def test_no_banned_words_or_duplicate_margin_history(symbol):
    raw = _raw(symbol)
    for s in _strings(raw):
        for word in BANNED_WORDS:
            assert word.lower() not in s.lower(), (word, s)
        for token in BANNED_TOKENS:
            assert token not in s, (token, s)
    note = raw["segments"]["secondary_note"]
    assert "净利率" not in note and "10Y" not in note and "中位" not in note
