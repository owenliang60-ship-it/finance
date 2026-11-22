"""Fail-closed tri-state gates and the page breaker (Task 4)."""
import pytest

from terminal.valuation.gates import (CONFLICT, UNVERIFIED, VERIFIED, implied_fx, run_gates, summarize)
from terminal.valuation.ledger import load_ledger

FADR_LEDGER = 'schema = 1\nsymbol = "ACME"\nreporting_currency = "TWD"\neps_basis = "ifrs"\ncyclical = true\n'
SEG_QUOTE = "quote = '''Segment A revenue was $900 million and Segment B revenue was $600 million'''"


def _run(facts, ledger):
    results = run_gates(facts, ledger)
    return {r.id: r for r in results}, summarize(results, facts, ledger)


@pytest.fixture
def acme(valuation_db, read_facts, ledger_file):
    db, _ = valuation_db
    return read_facts(db), load_ledger(ledger_file())


def test_acme_full_ledger_all_verified(acme):
    facts, ledger = acme
    by_id, summary = _run(facts, ledger)
    assert all(r.status == VERIFIED for r in by_id.values()), [r for r in by_id.values() if r.status != VERIFIED]
    assert summary.overall == VERIFIED
    assert summary.exit_code == 0 and summary.page_blocked is False
    assert summary.fx0 == 1.0
    assert summary.usable == frozenset({"seg:2026-09-30", "eps:2026-09-30", "guidance:g-2026q4-rev"})
    assert summary.g4_reextract == ()
    for gate in ("P1", "P2", "C1", "C2a", "C2", "C3", "G1a:2026-09-30", "G1c:2026-09-30",
                 "G2:seg:2026-09-30", "G2:eps:2026-09-30", "G2:g-2026q4-rev", "G3a", "G3b", "G3c"):
        assert gate in by_id
    assert by_id["P1"].scope == "page" and by_id["G1a:2026-09-30"].scope == "item"


def _fadr(valuation_db, read_facts, ledger_file, edit=None):
    _, seed = valuation_db
    return read_facts(seed(fadr=True, edit=edit)), load_ledger(ledger_file(text=FADR_LEDGER))


def test_fadr_implied_fx(valuation_db, read_facts, ledger_file):
    facts, ledger = _fadr(valuation_db, read_facts, ledger_file)
    assert facts.reporting_currency == "TWD" and facts.is_adr is True
    fx = implied_fx(facts)
    assert fx["fwd_rev"] == 32.0
    assert fx["fwd_eps_fmp"] == 32.0 and fx["fwd_eps_yf"] == 32.0 and fx["act_rev"] == 32.0
    by_id, summary = _run(facts, ledger)
    assert summary.fx0 == 32.0
    assert by_id["C1"].status == VERIFIED
    assert by_id["C2a"].status == VERIFIED
    assert by_id["C2"].status == VERIFIED
    assert by_id["C3"].status == VERIFIED
    assert summary.page_blocked is False


def test_fadr_yfinance_conflict_blocks_page(valuation_db, read_facts, ledger_file):
    def edit(rows):
        rows["forward"][0]["eps_avg"] = 2.3

    by_id, summary = _run(*_fadr(valuation_db, read_facts, ledger_file, edit))
    assert by_id["C2"].status == CONFLICT and by_id["C2"].blocks_page is True
    assert summary.page_blocked is True and summary.exit_code == 20


def test_fadr_without_yfinance_degrades(valuation_db, read_facts, ledger_file):
    def edit(rows):
        rows["forward"] = []

    by_id, summary = _run(*_fadr(valuation_db, read_facts, ledger_file, edit))
    assert by_id["C2"].status == UNVERIFIED and by_id["C2"].blocks_page is False
    assert summary.page_blocked is False and summary.exit_code == 10


def test_fadr_without_upcoming_row_blocks_page(valuation_db, read_facts, ledger_file):
    def edit(rows):
        rows["earnings"] = [r for r in rows["earnings"] if r["eps_actual"] is not None]

    by_id, summary = _run(*_fadr(valuation_db, read_facts, ledger_file, edit))
    assert by_id["C2a"].status == UNVERIFIED
    assert summary.fx0 is None
    assert summary.page_blocked is True and summary.exit_code == 20


def test_fadr_loss_quarter_guard(valuation_db, read_facts, ledger_file):
    def edit(rows):
        rows["estimates"][0]["eps_avg"] = 0.01

    by_id, summary = _run(*_fadr(valuation_db, read_facts, ledger_file, edit))
    assert by_id["C2"].status == UNVERIFIED
    assert summary.fx0 == 32.0
    assert summary.page_blocked is False


def test_ledger_currency_mismatch_blocks_page(valuation_db, read_facts, ledger_file):
    _, seed = valuation_db
    facts = read_facts(seed(fadr=True))
    by_id, summary = _run(facts, load_ledger(ledger_file()))
    assert by_id["C1"].status == CONFLICT and by_id["C1"].blocks_page is True
    assert summary.page_blocked is True and summary.exit_code == 20


def test_segment_sum_conflict_drops_quarter_only(acme, ledger_file):
    facts, _ = acme
    ledger = load_ledger(ledger_file(("values = [900.0, 600.0]", "values = [900.0, 500.0]")))
    by_id, summary = _run(facts, ledger)
    assert by_id["G1a:2026-09-30"].status == CONFLICT
    assert "seg:2026-09-30" not in summary.usable
    assert "eps:2026-09-30" in summary.usable
    assert summary.page_blocked is False and summary.exit_code == 10


def test_quote_word_change_is_conflict_and_reextracted(acme, ledger_file):
    facts, _ = acme
    ledger = load_ledger(ledger_file(("$600 million'''", "$700 million'''")))
    by_id, summary = _run(facts, ledger)
    assert by_id["G2:seg:2026-09-30"].status == CONFLICT
    assert "G2:seg:2026-09-30" in summary.g4_reextract
    assert "seg:2026-09-30" not in summary.usable


def test_quote_whitespace_difference_is_verified(acme, ledger_file):
    facts, _ = acme
    ledger = load_ledger(ledger_file(("$900 million and Segment B", "$900 million and\nSegment B")))
    by_id, _ = _run(facts, ledger)
    assert by_id["G2:seg:2026-09-30"].status == VERIFIED


def test_raw_sha_mismatch_is_unverified_not_reextracted(acme, ledger_file):
    facts, _ = acme
    ledger = load_ledger(ledger_file(("sha256 = \"ee19", "sha256 = \"ff19")))
    by_id, summary = _run(facts, ledger)
    assert by_id["G2:seg:2026-09-30"].status == UNVERIFIED
    assert by_id["G2:g-2026q4-rev"].status == UNVERIFIED
    assert summary.g4_reextract == ()
    assert summary.usable == frozenset()


def test_missing_raw_file_is_unverified(acme, ledger_file):
    facts, _ = acme
    ledger = load_ledger(ledger_file(('raw = "sources/acme-pr-2026q3.txt"', 'raw = "sources/absent.txt"')))
    by_id, _ = _run(facts, ledger)
    assert by_id["G2:eps:2026-09-30"].status == UNVERIFIED


def test_non_t1_domain_is_unverified(acme, ledger_file):
    facts, _ = acme
    ledger = load_ledger(ledger_file(('url = "https://investors.acme.com/q3-2026"',
                                      'url = "https://seekingalpha.com/x"')))
    by_id, summary = _run(facts, ledger)
    assert by_id["G2:seg:2026-09-30"].status == UNVERIFIED
    assert summary.g4_reextract == ()


def test_short_quote_is_unverified(acme, ledger_file):
    facts, _ = acme
    ledger = load_ledger(ledger_file(("quote = '''non-GAAP diluted earnings per share of $2.00'''",
                                      "quote = '''share of $2.00'''")))
    by_id, _ = _run(facts, ledger)
    assert by_id["G2:eps:2026-09-30"].status == UNVERIFIED


def test_eps_fact_mismatch_is_conflict(acme, ledger_file):
    facts, _ = acme
    ledger = load_ledger(ledger_file(("value = 2.0", "value = 2.1")))
    by_id, summary = _run(facts, ledger)
    assert by_id["G1c:2026-09-30"].status == CONFLICT
    assert "eps:2026-09-30" not in summary.usable


def test_share_jump_blocks_until_acked(valuation_db, read_facts, ledger_file):
    _, seed = valuation_db

    def edit(rows):
        rows["income"][-1]["weightedAverageShsOutDil"] = 120e6

    facts = read_facts(seed(edit=edit))
    by_id, summary = _run(facts, load_ledger(ledger_file()))
    gate = by_id["G3a:2026-09-30"]
    assert gate.status == CONFLICT and gate.blocks_page is True and gate.acked is False
    assert summary.page_blocked is True and summary.exit_code == 20
    acked = load_ledger(ledger_file(append='\n[[acks]]\nkey = "G3a:2026-09-30"\nnote = "Boss 确认增发"\n'))
    by_id, summary = _run(facts, acked)
    gate = by_id["G3a:2026-09-30"]
    assert gate.status == VERIFIED and gate.acked is True and gate.blocks_page is False
    assert summary.page_blocked is False and summary.exit_code == 0


def test_split_label_in_ttm_blocks_page(acme):
    from dataclasses import replace
    facts, ledger = acme
    eps = tuple(replace(q, labels=("eps_split_rescaled",)) if q.fiscal_date == "2026-06-30" else q
                for q in facts.eps)
    by_id, summary = _run(replace(facts, eps=eps), ledger)
    assert by_id["G3b:2026-06-30"].status == CONFLICT
    assert summary.page_blocked is True


def test_price_jump_note_blocks_page(acme):
    from dataclasses import replace
    facts, ledger = acme
    by_id, summary = _run(replace(facts, price_jump_notes=("疑似未复权 2:1 拆股",)), ledger)
    assert by_id["G3c"].status == CONFLICT and summary.page_blocked is True


RESTATED = """
[[segments.quarters]]
fiscal_date = "2025-09-30"
values = [600.0, 400.0]
source = "acme-pr-2026q3"
quote = '''for total revenue of $1.5 billion'''
"""


def test_restatement_conflict_clears_segment_area(acme, ledger_file):
    facts, _ = acme
    path = ledger_file(("prior_year_restated = [600.0, 400.0]", "prior_year_restated = [650.0, 400.0]"),
                       ("\n[[eps_facts]]", RESTATED + "\n[[eps_facts]]"))
    by_id, summary = _run(facts, load_ledger(path))
    assert by_id["G3d:2026-09-30:A"].status == CONFLICT
    assert by_id["G3d:2026-09-30:A"].blocks_page is False
    assert by_id["G3d:2026-09-30:B"].status == VERIFIED
    assert not any(u.startswith("seg:") for u in summary.usable)
    assert summary.page_blocked is False


def test_stale_price_blocks_page(valuation_db, read_facts, ledger_file):
    _, seed = valuation_db

    def edit(rows):
        rows["prices"] = [p for p in rows["prices"] if p["date"] <= "2026-11-12"]

    by_id, summary = _run(read_facts(seed(edit=edit)), load_ledger(ledger_file()))
    assert by_id["P1"].status == UNVERIFIED and by_id["P1"].blocks_page is True
    assert summary.page_blocked is True


def test_missing_ntm_blocks_page(valuation_db, read_facts, ledger_file):
    _, seed = valuation_db
    by_id, summary = _run(read_facts(seed(snapshot="2026-10-01")), load_ledger(ledger_file()))
    assert by_id["P2"].status == UNVERIFIED and summary.page_blocked is True
