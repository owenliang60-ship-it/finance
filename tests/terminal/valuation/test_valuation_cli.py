"""CLI: tri-state exit codes, zero writes, hard failures (Task 7)."""
import hashlib
import importlib.util
import json
from pathlib import Path

import pytest

from terminal.valuation.ledger import load_ledger

ROOT = Path(__file__).resolve().parents[3]
FIXTURES = ROOT / "tests" / "fixtures" / "valuation"
FADR_LEDGER = 'schema = 1\nsymbol = "ACME"\nreporting_currency = "TWD"\neps_basis = "ifrs"\ncyclical = true\n'


def _cli():
    spec = importlib.util.spec_from_file_location("valuation_cli", ROOT / "scripts" / "valuation.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _main(capsys, db, ledger, out, symbol="ACME"):
    code = _cli().main([symbol, "--ledger", str(ledger), "--db", str(db), "--as-of", "2026-11-22", "--out", str(out)])
    captured = capsys.readouterr()
    return code, captured.out, captured.err


def test_full_ledger_exit_zero_and_no_writes(valuation_db, ledger_file, tmp_path, capsys):
    db, _ = valuation_db
    before_hash, before_files = _sha(db), sorted(p.name for p in db.parent.iterdir())
    out = tmp_path / "out" / "acme.html"
    code, stdout, _ = _main(capsys, db, ledger_file(), out)
    assert code == 0
    assert out.is_file() and 'id="p1"' in out.read_text(encoding="utf-8")
    line = json.loads(stdout.strip())
    assert set(line) == {"symbol", "as_of", "overall", "page_blocked", "exit_code", "html", "non_verified",
                         "g4_reextract"}
    assert line["overall"] == "VERIFIED" and line["exit_code"] == 0 and line["page_blocked"] is False
    assert line["html"] == str(out) and line["non_verified"] == [] and line["g4_reextract"] == []
    assert _sha(db) == before_hash
    after = sorted(p.name for p in db.parent.iterdir())
    assert not [n for n in after if n.endswith(("-wal", "-shm", "-journal"))]
    assert set(before_files) <= set(after)


def test_quote_miss_degrades(valuation_db, ledger_file, tmp_path, capsys):
    db, _ = valuation_db
    ledger = ledger_file(("$600 million'''", "$700 million'''"))
    code, stdout, _ = _main(capsys, db, ledger, tmp_path / "a.html")
    line = json.loads(stdout)
    assert code == 10 and line["exit_code"] == 10
    assert {"id": "G2:seg:2026-09-30", "status": "CONFLICT"}.items() <= next(
        x for x in line["non_verified"] if x["id"] == "G2:seg:2026-09-30").items()
    assert line["g4_reextract"] == ["G2:seg:2026-09-30"]


def test_vendor_conflict_blocks_page(valuation_db, ledger_file, tmp_path, capsys):
    _, seed = valuation_db

    def edit(rows):
        rows["forward"][0]["eps_avg"] = 2.3

    db = seed(fadr=True, edit=edit)
    out = tmp_path / "blocked.html"
    code, stdout, _ = _main(capsys, db, ledger_file(text=FADR_LEDGER), out)
    assert code == 20 and json.loads(stdout)["page_blocked"] is True
    html = out.read_text(encoding="utf-8")
    assert 'id="p1"' not in html and 'id="gates"' in html


def test_bad_toml_is_hard_failure(valuation_db, ledger_file, tmp_path, capsys):
    db, _ = valuation_db
    out = tmp_path / "x.html"
    code, stdout, stderr = _main(capsys, db, ledger_file(append="broken = [\n"), out)
    assert code == 1 and not out.exists() and stdout == ""
    assert "LedgerError" in stderr


def test_missing_db_is_hard_failure(ledger_file, tmp_path, capsys):
    out = tmp_path / "x.html"
    code, _, stderr = _main(capsys, tmp_path / "absent.db", ledger_file(), out)
    assert code == 1 and not out.exists() and stderr
    assert not (tmp_path / "absent.db").exists()


def test_symbol_mismatch_is_hard_failure(valuation_db, ledger_file, tmp_path, capsys):
    db, _ = valuation_db
    out = tmp_path / "x.html"
    code, _, stderr = _main(capsys, db, ledger_file(), out, symbol="MU")
    assert code == 1 and not out.exists() and "MU" in stderr


def test_non_finite_payload_is_hard_failure(valuation_db, ledger_file, tmp_path, capsys, monkeypatch):
    db, _ = valuation_db
    cli = _cli()
    real = cli.load_facts
    monkeypatch.setattr(cli, "load_facts", lambda *a: __import__("dataclasses").replace(real(*a), shares=float("nan")))
    out = tmp_path / "x.html"
    code = cli.main(["ACME", "--ledger", str(ledger_file()), "--db", str(db), "--as-of", "2026-11-22", "--out", str(out)])
    assert code == 1 and not out.exists()
    assert "non-finite" in capsys.readouterr().err


@pytest.mark.parametrize("name,symbol,currency,basis,cycles", [
    ("smoke-MU.toml", "MU", "USD", "non_gaap", ("2018-19", "2022-23")),
    ("smoke-TSM.toml", "TSM", "TWD", "ifrs", ()),
])
def test_smoke_ledgers_load(name, symbol, currency, basis, cycles):
    ledger = load_ledger(FIXTURES / name)
    assert ledger.symbol == symbol and ledger.reporting_currency == currency and ledger.eps_basis == basis
    assert ledger.cyclical is True
    assert tuple(c["id"] for c in ledger.cycles) == cycles
    assert ledger.sources == {} and ledger.segment_quarters == () and ledger.eps_facts == ()
    assert ledger.guidance == () and ledger.drivers == () and ledger.boss_margin is None
