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
