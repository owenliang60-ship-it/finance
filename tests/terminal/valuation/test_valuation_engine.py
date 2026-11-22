"""Reverse-engineering matrix, margin bridge, cycle replay and payload (Task 5)."""
import json
import statistics
from dataclasses import replace
from datetime import date, timedelta

import pytest

from terminal.valuation.engine import (build_payload, cycle_replays, incremental_margins, mild_zone, pe_history,
                                       quantile)
from terminal.valuation.gates import UNVERIFIED, VERIFIED, run_gates, summarize
from terminal.valuation.ledger import load_ledger

TOP_KEYS = {"meta", "status", "matrix", "consensus", "margins", "sliders", "markers", "presets",
            "guidance_notes", "segments", "pe_sorted", "replay_note", "selfcheck"}


def _gates(facts, ledger):
    return summarize(run_gates(facts, ledger), facts, ledger)


def _payload(facts, ledger):
    return build_payload(facts, ledger, _gates(facts, ledger))


def _column(payload, key):
    return next(c for c in payload["matrix"]["columns"] if c["key"] == key)


@pytest.fixture
def acme(valuation_db, read_facts, ledger_file):
    db, _ = valuation_db
    return read_facts(db), load_ledger(ledger_file())


def test_quantile():
    assert quantile([1, 2, 3, 4], 0.5) == 2.5
    assert quantile([1, 2, 3, 4], 0.0) == 1 and quantile([1, 2, 3, 4], 1.0) == 4
    assert quantile([5.0], 0.9) == 5.0
    assert quantile([], 0.5) is None


def test_pe_history(acme):
    facts, _ = acme
    pe = pe_history(facts.closes, facts.eps)
    assert pe["n_no_ttm"] == 0 and pe["n_nonpositive"] == 0
    assert pe["n_valid"] == len(facts.closes)
    assert pe["sorted"][0] == 10.0 and pe["sorted"][-1] == 20.0
    assert pe["start"] == "2025-06-02" and pe["end"] == "2026-11-20"


def test_incremental_margins(acme):
    facts, ledger = acme
    m = incremental_margins(facts, ledger)
    assert m["hist"]["value"] == 0.2
    assert m["street"]["value"] == 0.5
    assert m["boss"]["value"] == 0.35


def test_mild_zone_matches_rule(acme):
    facts, _ = acme
    rows = sorted((r for r in facts.earnings if r["announce_date"] <= facts.as_of and r["match_method"] != "none"
                   and (r["revenue_actual"] or 0) > 0 and (r["revenue_estimated"] or 0) > 0),
                  key=lambda r: r["announce_date"])[-20:]
    surprises = [r["revenue_actual"] / r["revenue_estimated"] - 1 for r in rows]
    zone = mild_zone(facts)
    assert zone["n"] == 12
    assert zone["half_width"] == 2.0 * statistics.stdev(surprises)


def test_payload_sliders_and_selfcheck(acme):
    facts, ledger = acme
    p = _payload(facts, ledger)
    assert set(p) == TOP_KEYS
    assert p["sliders"]["pe"]["init"] == 8.0
    assert p["sliders"]["rev"]["init"] == 6400e6
    assert p["sliders"]["rev"]["min"] == 6400e6 * 0.5 and p["sliders"]["rev"]["max"] == 6400e6 * 1.5
    assert p["sliders"]["margin"]["init"] == 0.5
    assert p["sliders"]["fx"] is None
    assert p["sliders"]["drivers"][0]["id"] == "hbm"
    assert p["selfcheck"][0]["name"] == "init"
    assert p["selfcheck"][0]["in"]["eps_mode"] == "bridge"
    assert p["selfcheck"][0]["out"]["value"] == pytest.approx(80.0, rel=1e-12)
    names = [s["name"] for s in p["selfcheck"]]
    assert {"rev:min", "rev:max", "margin:min", "margin:max", "pe:min", "pe:max", "preset:2025-26"} <= set(names)
    assert not any(n.startswith("fx:") for n in names)
    json.dumps(p, allow_nan=False)


def test_matrix_columns(acme):
    facts, ledger = acme
    p = _payload(facts, ledger)
    assert _column(p, "ntm")["implied_pe"] == 8.0
    ttm = _column(p, "ttm")
    assert ttm["implied_pe"] == 10.0 and ttm["pe_rank"] > 0
    assert len(ttm["cells"]) == 5
    assert p["matrix"]["pe_sample"]["note"] == "历史分位为 TTM 街口 P/E"
    assert p["matrix"]["cyclical"]["peak_ttm"] == 8.0
    assert p["replay_note"] is None


def test_cycle_replay_enabled(acme):
    facts, ledger = acme
    cycle = cycle_replays(facts, ledger)[0]
    assert cycle["id"] == "2025-26" and cycle["enabled"] is True
    assert cycle["peak"]["fiscal_date"] == "2025-12-31"
    assert cycle["eps_ratio"] == 1.0 and cycle["rev_ratio"] == 1.0
    assert cycle["rev"] == 6400e6 and cycle["direct_eps"] == 10.0


def test_cycle_with_missing_quarter_is_disabled(valuation_db, read_facts, ledger_file):
    _, seed = valuation_db

    def edit(rows):
        next(r for r in rows["earnings"] if r["fiscal_date"] == "2026-03-31")["eps_actual"] = None

    facts, ledger = read_facts(seed(edit=edit)), load_ledger(ledger_file())
    cycle = cycle_replays(facts, ledger)[0]
    assert cycle["enabled"] is False and "2026-03-31" in cycle["reason"]
    p = _payload(facts, ledger)
    assert p["replay_note"] == "样本内无可回放的深度下行周期"
    assert not any(m["kind"] == "history" and m["slider"] == "rev" for m in p["markers"])


def test_negative_ttm_blanks_ttm_column(valuation_db, read_facts, ledger_file):
    _, seed = valuation_db

    def edit(rows):
        for r in rows["earnings"][8:12]:
            r["eps_actual"] = -1.0
        rows["prices"] = [{"date": d, "close": 80.0} for d in _weekdays("2024-11-01", "2026-11-20")]

    facts, ledger = read_facts(seed(edit=edit)), load_ledger(ledger_file())
    assert facts.ttm_eps_usd == -4.0
    assert pe_history(facts.closes, facts.eps)["n_nonpositive"] > 0
    p = _payload(facts, ledger)
    ttm = _column(p, "ttm")
    assert ttm["implied_pe"] is None and ttm["cells"] == [None] * 5


def _weekdays(start, end):
    d, out = date.fromisoformat(start), []
    while d <= date.fromisoformat(end):
        if d.weekday() < 5:
            out.append(d.isoformat())
        d += timedelta(days=1)
    return out


def test_negative_consensus_disables_pe(valuation_db, read_facts, ledger_file):
    _, seed = valuation_db

    def edit(rows):
        for r in rows["estimates"]:
            if r["period_type"] == "Q":
                r["eps_avg"] = -0.5

    facts, ledger = read_facts(seed(edit=edit)), load_ledger(ledger_file())
    p = _payload(facts, ledger)
    assert p["sliders"]["pe"]["disabled"] is True
    assert p["selfcheck"][0]["out"]["value"] is None
    assert _column(p, "ntm")["cells"] == [None] * 5
    json.dumps(p, allow_nan=False)


def test_missing_fy_rows_disable_bridge(valuation_db, read_facts, ledger_file):
    _, seed = valuation_db

    def edit(rows):
        rows["estimates"] = [r for r in rows["estimates"] if r["period_type"] != "FY"]

    facts, ledger = read_facts(seed(edit=edit)), load_ledger(ledger_file())
    gates = _gates(facts, ledger)
    assert {r.id: r.status for r in gates.results}["B1"] == UNVERIFIED
    p = build_payload(facts, ledger, gates)
    assert p["sliders"]["rev"]["disabled"] is True and p["sliders"]["margin"]["disabled"] is True
    init = p["selfcheck"][0]
    assert init["in"]["eps_mode"] == "direct" and init["in"]["direct_eps"] == 10.0
    assert init["out"]["value"] == pytest.approx(80.0)


def test_few_surprise_quarters_disable_bridge(valuation_db, read_facts, ledger_file):
    _, seed = valuation_db

    def edit(rows):
        for r in rows["earnings"][:6]:
            r["revenue_estimated"] = None

    facts, ledger = read_facts(seed(edit=edit)), load_ledger(ledger_file())
    gates = _gates(facts, ledger)
    assert {r.id: r.status for r in gates.results}["B2"] == UNVERIFIED
    p = build_payload(facts, ledger, gates)
    assert p["selfcheck"][0]["in"]["eps_mode"] == "direct"
    assert p["sliders"]["rev"]["disabled"] is True


def test_short_pe_sample_blocks_page(valuation_db, read_facts, ledger_file):
    _, seed = valuation_db

    def edit(rows):
        rows["prices"] = [p for p in rows["prices"] if p["date"] >= "2026-01-01"]

    facts, ledger = read_facts(seed(edit=edit)), load_ledger(ledger_file())
    gates = _gates(facts, ledger)
    assert {r.id: r.status for r in gates.results}["P3"] == UNVERIFIED
    assert gates.page_blocked is True


def test_acme_gates_include_engine_checks(acme):
    facts, ledger = acme
    status = {r.id: r.status for r in _gates(facts, ledger).results}
    assert status["P3"] == VERIFIED and status["B1"] == VERIFIED and status["B2"] == VERIFIED


def _rev_guidance(p):
    return [m for m in p["markers"] if m["kind"] == "guidance"]


def test_guidance_marker(acme):
    facts, ledger = acme
    p = _payload(facts, ledger)
    marks = _rev_guidance(p)
    assert len(marks) == 1 and marks[0]["slider"] == "rev"
    assert marks[0]["value"] == 6400e6 - 1600e6 + 1650e6
    assert p["guidance_notes"] == []


def test_lower_bound_guidance_is_note_only(acme, ledger_file):
    facts, _ = acme
    ledger = load_ledger(ledger_file(("is_lower_bound = false", "is_lower_bound = true")))
    p = _payload(facts, ledger)
    assert _rev_guidance(p) == []
    note = p["guidance_notes"][0]
    assert note["id"] == "g-2026q4-rev" and "下限" in note["flags"]


def test_run_rate_guidance_is_note_only(acme, ledger_file):
    facts, _ = acme
    ledger = load_ledger(ledger_file(('period_type = "quarter"', 'period_type = "run_rate"')))
    p = _payload(facts, ledger)
    assert _rev_guidance(p) == []
    assert "年化口径" in p["guidance_notes"][0]["flags"]


def test_segments_only_usable_quarters(acme, ledger_file):
    facts, ledger = acme
    p = _payload(facts, ledger)
    assert [q["fiscal_date"] for q in p["segments"]["quarters"]] == ["2026-09-30"]
    assert p["segments"]["quarters"][0]["yoy"] == [900.0 / 600.0 - 1, 600.0 / 400.0 - 1]
    bad = load_ledger(ledger_file(("values = [900.0, 600.0]", "values = [900.0, 500.0]")))
    assert _payload(facts, bad)["segments"]["quarters"] == []


def test_blocked_page_has_only_meta_and_status(valuation_db, read_facts, ledger_file):
    _, seed = valuation_db
    facts = read_facts(seed(fadr=True))
    p = _payload(facts, load_ledger(ledger_file()))
    assert set(p) == {"meta", "status"}
    assert p["status"]["page_blocked"] is True


def test_non_finite_fact_raises(acme):
    facts, ledger = acme
    gates = _gates(facts, ledger)
    with pytest.raises(ValueError):
        build_payload(replace(facts, shares=float("nan")), ledger, gates)


def test_fadr_payload_has_fx_slider(valuation_db, read_facts, ledger_file):
    _, seed = valuation_db
    facts = read_facts(seed(fadr=True))
    ledger = load_ledger(ledger_file(text='schema = 1\nsymbol = "ACME"\nreporting_currency = "TWD"\n'
                                          'eps_basis = "ifrs"\ncyclical = true\n'))
    p = _payload(facts, ledger)
    assert p["sliders"]["fx"]["init"] == 32.0
    assert p["sliders"]["fx"]["min"] == 0.85 * 32.0 and p["sliders"]["fx"]["max"] == 1.15 * 32.0
    assert _column(p, "ntm")["eps_usd"] == 10.0
    assert p["selfcheck"][0]["out"]["value"] == pytest.approx(80.0, rel=1e-12)
    assert {"fx:min", "fx:max"} <= {s["name"] for s in p["selfcheck"]}
