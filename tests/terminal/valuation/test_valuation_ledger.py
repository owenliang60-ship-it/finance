"""TOML decision ledger contract and validation (Task 3)."""
from pathlib import Path

import pytest

from terminal.valuation.ledger import Ledger, LedgerError, load_ledger

ACME = Path(__file__).resolve().parents[2] / "fixtures" / "valuation" / "ACME" / "ledger.toml"
MINIMAL = 'schema = 1\nsymbol = "ACME"\nreporting_currency = "USD"\neps_basis = "gaap"\n'


def _variant(tmp_path, old, new):
    text = ACME.read_text(encoding="utf-8")
    assert old in text
    path = tmp_path / "ledger.toml"
    path.write_text(text.replace(old, new, 1), encoding="utf-8")
    return path


def test_acme_fixture_loads():
    ledger = load_ledger(ACME)
    assert isinstance(ledger, Ledger)
    assert ledger.path == ACME
    assert ledger.symbol == "ACME"
    assert ledger.reporting_currency == "USD" and ledger.eps_basis == "non_gaap"
    assert ledger.cyclical is True
    assert ledger.t1_domains == ("investors.acme.com",)
    assert ledger.boss_margin == 0.35
    assert ledger.fx_local_per_usd is None
    assert set(ledger.sources) == {"acme-pr-2026q3"}
    assert ledger.segment_unit == 1e6
    assert ledger.segment_names == ("A", "B")
    assert len(ledger.segment_quarters) == 1
    assert ledger.segment_quarters[0]["values"] == (900.0, 600.0)
    assert ledger.segment_quarters[0]["prior_year_restated"] == (600.0, 400.0)
    assert ledger.eps_facts[0]["value"] == 2.0
    assert ledger.drivers[0]["id"] == "hbm" and ledger.drivers[0]["base"] == 800e6
    assert ledger.guidance[0]["is_lower_bound"] is False
    assert ledger.cycles[0]["id"] == "2025-26"
    assert "G3a:2026-06-30" in ledger.acks
    assert ledger.segment_axis_rationale == "夹具：按两个业务分部拆分营收"
    assert ledger.segment_roles == ("稳态底座", "高弹性分部")
    assert ledger.segment_colors == ()
    assert ledger.segment_secondary_note.startswith("夹具次轴注释")
    assert ledger.consensus_weight == 5.0
    assert [s["id"] for s in ledger.scenarios] == ["bull", "base", "bear"]
    bull = ledger.scenarios[0]
    assert bull["tone"] == "bull" and bull["seg_adjs"] == (0.10, 0.05) and bull["net_margin"] == 0.18
    assert bull["pe_mid"] == 9.0 and bull["pe_half"] == 1.0 and bull["weight"] == 20.0
    assert ledger.segment_quarters[0].get("pct_derived") is None


def test_colors_and_pct_derived_load(tmp_path):
    text = ACME.read_text(encoding="utf-8").replace(
        'secondary_note = "', 'colors = ["#4c78a8", "#F58518"]\nsecondary_note = "', 1).replace(
        "prior_year_restated = [600.0, 400.0]", "prior_year_restated = [600.0, 400.0]\npct_derived = true", 1)
    path = tmp_path / "ledger.toml"
    path.write_text(text, encoding="utf-8")
    ledger = load_ledger(path)
    assert ledger.segment_colors == ("#4c78a8", "#F58518")
    assert ledger.segment_quarters[0]["pct_derived"] is True


@pytest.mark.parametrize("old,new,match", [
    ('eps_basis = "non_gaap"', 'eps_basis = "adjusted"', "eps_basis"),
    ("values = [900.0, 600.0]", "values = [900.0]", r"segments\.quarters\[0\]\.values"),
    ("prior_year_restated = [600.0, 400.0]", "prior_year_restated = [600.0]",
     r"segments\.quarters\[0\]\.prior_year_restated"),
    ('source = "acme-pr-2026q3"\nquote', 'source = "missing"\nquote', "source"),
    ("incremental_margin = 0.35", "incremental_margin = nan", "incremental_margin"),
    ("value = 1650e6", "value = inf", r"guidance\[0\]\.value"),
    ("cyclical = true", "cyclical = true\ncylical = true", "cylical"),
    ('reporting_currency = "USD"', 'reporting_currency = "TWD"', "non_gaap"),
    ("schema = 1", "schema = 2", "schema"),
    ("min = 0.0", "min = 900e6", r"drivers\[0\]"),
    ('period_type = "quarter"', 'period_type = "annualized"', "period_type"),
    ('denominator = "total_revenue"', 'denominator = "gross"', "denominator"),
    ('fetched_at = "2026-11-22"', 'fetched_at = "2026/11/22"', "fetched_at"),
    ("is_lower_bound = false", "is_lower_bnd = false", "is_lower_bnd"),
    ('roles = ["稳态底座", "高弹性分部"]', 'roles = ["稳态底座"]', r"segments\.roles"),
    ('secondary_note = "', 'colors = ["red", "#00ff00"]\nsecondary_note = "', r"segments\.colors"),
    ("seg_adjs = [0.10, 0.05]", "seg_adjs = [0.10]", r"scenarios\[0\]\.seg_adjs"),
    ("seg_adjs = [0.10, 0.05]", "seg_adjs = [0.5, 0.05]", r"scenarios\[0\]\.seg_adjs"),
    ("pe_mid = 9.0\npe_half = 1.0", "pe_mid = 9.0\npe_half = 9.0", r"scenarios\[0\]\.pe_half"),
    ('tone = "base"', 'tone = "bull"', r"scenarios\[1\]\.tone"),
    ('id = "bull"', 'id = "custom"', r"scenarios\[0\]\.id"),
    ("weight = 20", "weight = 15", "weights"),
    ('tone = "bull"', 'tone = "bull"\nupside = 1.0', r"scenarios\[0\]\.upside"),
    ("net_margin = 0.18", "net_margin = -1.0", r"scenarios\[0\]\.net_margin"),
    ("consensus_weight = 5", "consensus_weight = 105", "consensus_weight"),
    ('tone = "bear"', 'tone = "worst"', r"scenarios\[2\]\.tone"),
])
def test_invalid_variants_raise(tmp_path, old, new, match):
    with pytest.raises(LedgerError, match=match):
        load_ledger(_variant(tmp_path, old, new))


def test_missing_required_key(tmp_path):
    path = tmp_path / "ledger.toml"
    path.write_text(MINIMAL.replace('symbol = "ACME"\n', ""), encoding="utf-8")
    with pytest.raises(LedgerError, match="symbol"):
        load_ledger(path)


@pytest.mark.parametrize("tail,line", [("broken = \nx = 1\n", "line 5"), ("broken = [\n", "line")])
def test_toml_syntax_error_reports_line(tmp_path, tail, line):
    path = tmp_path / "ledger.toml"
    path.write_text(MINIMAL + tail, encoding="utf-8")
    with pytest.raises(LedgerError, match=line):
        load_ledger(path)


def test_minimal_ledger_loads(tmp_path):
    path = tmp_path / "ledger.toml"
    path.write_text(MINIMAL, encoding="utf-8")
    ledger = load_ledger(path)
    assert ledger.symbol == "ACME" and ledger.cyclical is False
    assert ledger.boss_margin is None and ledger.fx_local_per_usd is None and ledger.segment_unit is None
    assert ledger.sources == {} and ledger.acks == frozenset()
    assert ledger.segment_names == () and ledger.segment_quarters == () and ledger.eps_facts == ()
    assert ledger.drivers == () and ledger.guidance == () and ledger.cycles == () and ledger.t1_domains == ()
    assert ledger.scenarios == () and ledger.consensus_weight is None and ledger.segment_roles == ()
    assert ledger.segment_axis_rationale is None and ledger.segment_secondary_note is None


def test_more_than_three_scenarios_raise(tmp_path):
    text = ACME.read_text(encoding="utf-8")
    extra = text[text.index("[[scenarios]]"):text.index("[[scenarios]]", text.index("[[scenarios]]") + 1)]
    path = tmp_path / "ledger.toml"
    path.write_text(text + "\n" + extra.replace('id = "bull"', 'id = "bull2"'), encoding="utf-8")
    with pytest.raises(LedgerError, match="scenarios"):
        load_ledger(path)


def test_scenarios_without_segments_take_one_adjustment(tmp_path):
    body = MINIMAL + (
        '[assumptions]\nconsensus_weight = 40\n\n[[scenarios]]\nid = "base"\nlabel = "Base"\ntone = "base"\n'
        'thesis = "单一总营收"\nseg_adjs = [-0.1]\nnet_margin = 0.2\npe_mid = 10.0\npe_half = 0.0\nweight = 60\n')
    path = tmp_path / "ledger.toml"
    path.write_text(body, encoding="utf-8")
    assert load_ledger(path).scenarios[0]["seg_adjs"] == (-0.1,)
    path.write_text(body.replace("seg_adjs = [-0.1]", "seg_adjs = [-0.1, 0.0]"), encoding="utf-8")
    with pytest.raises(LedgerError, match=r"scenarios\[0\]\.seg_adjs"):
        load_ledger(path)
