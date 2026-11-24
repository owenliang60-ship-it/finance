"""Engine payload v2: segment-driven net-margin model, scenarios, cycles, trajectory, heatmap (v2 Task 3)."""
import json
import statistics
from dataclasses import replace
from datetime import date, timedelta

import pytest

from terminal.valuation import kernel
from terminal.valuation.engine import (build_payload, cycle_replays, incremental_margins, margin_history, mild_zone,
                                       pe_history, quantile)
from terminal.valuation.gates import UNVERIFIED, VERIFIED, run_gates, summarize
from terminal.valuation.ledger import load_ledger

TOP_KEYS = {"meta", "status", "matrix", "consensus", "margins", "model", "sliders", "markers", "margin_history",
            "scenarios", "cycles", "trajectory", "heatmap", "ev_init", "selfcheck", "pe_sorted", "guidance_notes",
            "replay_note"}
LIVE_KEYS = {"rev", "net_income", "eps_local", "eps_usd", "eps_state", "implied_pe", "value", "value_lo",
             "value_hi", "value_vs_price", "zone"}
FADR_LEDGER = 'schema = 1\nsymbol = "ACME"\nreporting_currency = "TWD"\neps_basis = "ifrs"\ncyclical = true\n'


def _gates(facts, ledger):
    return summarize(run_gates(facts, ledger), facts, ledger)


def _payload(facts, ledger):
    return build_payload(facts, ledger, _gates(facts, ledger))


def _column(payload, key):
    return next(c for c in payload["matrix"]["columns"] if c["key"] == key)


def _scenario(payload, sid):
    return next(s for s in payload["scenarios"] if s["id"] == sid)


@pytest.fixture
def acme(valuation_db, read_facts, ledger_file):
    db, _ = valuation_db
    return read_facts(db), load_ledger(ledger_file())


@pytest.fixture
def acme_payload(acme):
    return _payload(*acme)


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


def test_incremental_margins_kept_for_audit(acme, acme_payload):
    facts, ledger = acme
    m = incremental_margins(facts, ledger)
    assert m["hist"]["value"] == 0.2 and m["street"]["value"] == 0.5 and m["boss"]["value"] == 0.35
    assert acme_payload["margins"]["street"]["value"] == 0.5


def test_mild_zone_matches_rule(acme):
    facts, _ = acme
    rows = sorted((r for r in facts.earnings if r["announce_date"] <= facts.as_of and r["match_method"] != "none"
                   and (r["revenue_actual"] or 0) > 0 and (r["revenue_estimated"] or 0) > 0),
                  key=lambda r: r["announce_date"])[-20:]
    surprises = [r["revenue_actual"] / r["revenue_estimated"] - 1 for r in rows]
    zone = mild_zone(facts)
    assert zone["n"] == 12
    assert zone["half_width"] == 2.0 * statistics.stdev(surprises)


def test_margin_history(acme):
    facts, ledger = acme
    h = margin_history(facts, ledger)
    assert h["n"] == 12 and h["n_missing"] == 0
    assert h["median"] == pytest.approx(0.10) and h["min"]["value"] == pytest.approx(0.10)
    assert h["max"]["value"] == pytest.approx(2.0 * 100e6 / 1500e6)
    assert h["latest"] == {"fiscal_date": "2026-09-30", "value": pytest.approx(2.0 * 100e6 / 1500e6)}


def test_payload_model_segments(acme_payload):
    p = acme_payload
    assert set(p) == TOP_KEYS
    m = p["model"]
    assert m["mode"] == "segments" and m["eps_mode"] == "margin" and m["direct_eps"] is None
    assert m["currency"] == "USD" and m["split_quarter"] == "2026-09-30"
    assert [s["base"] for s in m["segments"]] == pytest.approx([3840e6, 2560e6])
    assert [s["share"] for s in m["segments"]] == pytest.approx([0.6, 0.4])
    assert [s["latest_value"] for s in m["segments"]] == pytest.approx([900e6, 600e6])
    assert [s["role"] for s in m["segments"]] == ["稳态底座", "高弹性分部"]
    assert [s["color"] for s in m["segments"]] == ["#4c78a8", "#f58518"]
    logic = m["logic"]
    assert logic["axis_rationale"].startswith("夹具") and logic["pct_derived"] is False
    assert "等比切分" in logic["formula_note"] and logic["g1_dev"] == pytest.approx(0.0)
    assert m["init"] == {"adjs": [0.0, 0.0], "margin": 0.15625, "pe": 8.0, "pe_half": 0.0, "fx": 1.0}
    assert p["consensus"]["margin_cons"] == 0.15625
    assert p["consensus"]["mkt_cap"] == 80.0 * 100e6
    assert p["consensus"]["ntm_window"]["label"].startswith("2026-12")
    assert p["sliders"]["pe"]["init"] == 8.0
    assert p["sliders"]["seg"] == {"min": -0.60, "max": 0.40, "disabled": False}
    json.dumps(p, allow_nan=False)


def test_consensus_scenario_equals_price(acme_payload):
    cons = acme_payload["scenarios"][0]
    assert cons["id"] == "consensus" and cons["enabled"] is True and cons["weight"] == 5.0
    assert cons["out"]["value"] == pytest.approx(80.0, rel=1e-12)
    assert set(cons["out"]) == LIVE_KEYS
    assert cons["band"]["lo"] == pytest.approx(8.0 * 8.0) and cons["band"]["hi"] == pytest.approx(8.0 * 12.0)
    assert "拥挤度" in cons["band"]["note"]


def test_reference_scenarios(acme_payload):
    p = acme_payload
    bull, base, bear = (_scenario(p, s) for s in ("bull", "base", "bear"))
    assert [s["id"] for s in p["scenarios"]] == ["consensus", "bull", "base", "bear"]
    assert bull["out"]["value"] == pytest.approx(111.9744)
    assert bull["out"]["value_lo"] == pytest.approx(99.5328) and bull["out"]["value_hi"] == pytest.approx(124.416)
    assert base["out"]["value"] == pytest.approx(65.9456)
    assert bear["out"]["value"] == pytest.approx(22.9376)
    assert p["ev_init"] == pytest.approx(65.10208)
    assert p["scenarios"][1]["seg_rev"] == pytest.approx([4224e6, 2688e6])
    for s in p["scenarios"]:
        assert s["enabled"] is True and s["reason"] is None
        total = 0.0
        for q in s["quarters"]:
            total += q
        assert total == pytest.approx(s["out"]["rev"], rel=1e-12)
    assert bull["tone"] == "bull" and bull["thesis"] == "夹具乐观情景" and bull["pe_half"] == 1.0


def test_margin_slider_and_markers(acme_payload):
    p = acme_payload
    assert p["sliders"]["margin"]["min"] == 0.0 and p["sliders"]["margin"]["max"] == 0.25
    assert p["sliders"]["margin"]["init"] == 0.15625 and p["sliders"]["margin"]["disabled"] is False
    marks = {m["label"]: m for m in p["markers"] if m["slider"] == "margin"}
    assert marks["10Y 中位"]["value"] == pytest.approx(0.10)
    assert "夹具周期 周期谷底（2025-03-31）" in marks
    assert marks["最新季实绩"]["value"] == pytest.approx(2.0 * 100e6 / 1500e6)
    assert marks["卖方共识隐含"]["value"] == 0.15625
    assert all(m["source"] for m in p["markers"])
    assert not any(m["slider"] == "rev" for m in p["markers"])
    assert {m["label"] for m in p["markers"] if m["slider"] == "pe"} == {"P10", "P25", "P50", "P75", "P90"}


def test_cycle_replay_v2(acme, acme_payload):
    facts, ledger = acme
    raw = cycle_replays(facts, ledger)[0]
    assert raw["enabled"] is True and raw["rev_ratio"] == 1.0
    c = acme_payload["cycles"][0]
    assert c["id"] == "2025-26" and c["enabled"] is True and c["reason"] is None
    assert c["adj"] == 0.0 and c["margin"] == pytest.approx(0.10) and c["trough_quarter"] == "2025-03-31"
    assert acme_payload["replay_note"] is None


def test_heatmap(acme_payload):
    h = acme_payload["heatmap"]
    assert h["pe_levels"] == [6.0, 7.0, 8.0, 9.0, 10.0]
    assert h["eps_levels"][0] == pytest.approx(3.2768) and h["eps_levels"][-1] == pytest.approx(12.4416)
    for i, eps in enumerate(h["eps_levels"]):
        for j, pe in enumerate(h["pe_levels"]):
            assert h["cells"][i][j] == kernel.scenario_value(pe, eps)
            assert h["pct"][i][j] == kernel.pct_diff(h["cells"][i][j], 80.0)
    r, c = h["price_cell"]
    best = min(abs(x) for row in h["pct"] for x in row)
    assert abs(h["pct"][r][c]) == best
    base = next(m for m in h["marks"] if m["id"] == "base")
    assert base["c_pos"] == 2.0 and 0.0 < base["r_pos"] < 4.0


def test_trajectory(acme_payload, valuation_db, read_facts, ledger_file):
    t = acme_payload["trajectory"]
    assert t["scale"] == 1.0 and len(t["hist"]) == 4 and t["hist"][-1]["fiscal_date"] == "2026-09-30"
    assert t["future_dates"] == ["2026-12-31", "2027-03-31", "2027-06-30", "2027-09-30"]
    assert t["shape"] == [1600e6] * 4 and t["analysts"] == [10, 10, 10, 10]
    assert "不含季度节奏假设" in t["note"]
    _, seed = valuation_db
    thin = _payload(read_facts(seed(thin=True)), load_ledger(ledger_file()))
    tt = thin["trajectory"]
    assert tt["scale"] == pytest.approx(thin["consensus"]["rev_cons"] / (4 * 1600e6))
    assert "缩放" in tt["note"] and "10 / 10 / 2 / 2" in tt["note"]


def test_segment_conflict_falls_back_to_total(acme, ledger_file):
    facts, _ = acme
    bad = load_ledger(ledger_file(("values = [900.0, 600.0]", "values = [900.0, 500.0]")))
    p = _payload(facts, bad)
    m = p["model"]
    assert m["mode"] == "total" and m["logic"] is None and m["total_reason"]
    assert [s["base"] for s in m["segments"]] == [p["consensus"]["rev_cons"]]
    for s in p["scenarios"][1:]:
        assert s["enabled"] is False and s["reason"] and s["out"] is None
    assert p["scenarios"][0]["enabled"] is True
    assert p["ev_init"] is None
    assert p["cycles"][0]["enabled"] is True and p["cycles"][0]["adj"] == 0.0


def test_selfcheck_vectors(acme_payload):
    p = acme_payload
    names = [s["name"] for s in p["selfcheck"]]
    assert names[0] == "init"
    assert {"init", "seg:0:min", "seg:1:max", "margin:min", "margin:max", "pe:min", "pe:max", "scenario:bull",
            "scenario:base", "scenario:bear", "cycle:2025-26"} <= set(names)
    assert not any(n.startswith("fx:") for n in names)
    for item in p["selfcheck"]:
        assert item["out"] == kernel.live_state(item["in"]), item["name"]
    assert p["selfcheck"][0]["in"] == p["model"]["live_in"]
    assert p["selfcheck"][0]["out"]["value"] == pytest.approx(80.0, rel=1e-12)


def test_matrix_columns_kept(acme_payload):
    p = acme_payload
    assert _column(p, "ntm")["implied_pe"] == 8.0
    ttm = _column(p, "ttm")
    assert ttm["implied_pe"] == 10.0 and ttm["pe_rank"] > 0 and len(ttm["cells"]) == 5
    assert p["matrix"]["pe_sample"]["note"] == "历史分位为 TTM 街口 P/E"
    assert p["matrix"]["cyclical"]["peak_ttm"] == 8.0


def test_cycle_with_missing_quarter_is_disabled(valuation_db, read_facts, ledger_file):
    _, seed = valuation_db

    def edit(rows):
        next(r for r in rows["earnings"] if r["fiscal_date"] == "2026-03-31")["eps_actual"] = None

    facts, ledger = read_facts(seed(edit=edit)), load_ledger(ledger_file())
    assert cycle_replays(facts, ledger)[0]["enabled"] is False
    p = _payload(facts, ledger)
    assert p["cycles"][0]["enabled"] is False and "2026-03-31" in p["cycles"][0]["reason"]
    assert p["replay_note"] == "样本内无可回放的深度下行周期"
    assert not any(s["name"].startswith("cycle:") for s in p["selfcheck"])


def _weekdays(start, end):
    d, out = date.fromisoformat(start), []
    while d <= date.fromisoformat(end):
        if d.weekday() < 5:
            out.append(d.isoformat())
        d += timedelta(days=1)
    return out


def test_negative_ttm_blanks_ttm_column(valuation_db, read_facts, ledger_file):
    _, seed = valuation_db

    def edit(rows):
        for r in rows["earnings"][8:12]:
            r["eps_actual"] = -1.0
        rows["prices"] = [{"date": d, "close": 80.0} for d in _weekdays("2024-11-01", "2026-11-20")]

    facts, ledger = read_facts(seed(edit=edit)), load_ledger(ledger_file())
    assert facts.ttm_eps_usd == -4.0
    p = _payload(facts, ledger)
    ttm = _column(p, "ttm")
    assert ttm["implied_pe"] is None and ttm["cells"] == [None] * 5


def test_negative_consensus_disables_pe_and_heatmap(valuation_db, read_facts, ledger_file):
    _, seed = valuation_db

    def edit(rows):
        for r in rows["estimates"]:
            if r["period_type"] == "Q":
                r["eps_avg"] = -0.5

    facts, ledger = read_facts(seed(edit=edit)), load_ledger(ledger_file())
    p = _payload(facts, ledger)
    assert p["sliders"]["pe"]["disabled"] is True
    assert p["selfcheck"][0]["out"]["value"] is None
    assert p["selfcheck"][0]["out"]["eps_state"] == "nonpos"
    assert set(p["heatmap"]) == {"reason"}
    assert _column(p, "ntm")["cells"] == [None] * 5
    json.dumps(p, allow_nan=False)


def test_missing_shares_use_direct_mode(acme):
    facts, ledger = acme
    facts = replace(facts, shares=0.0)
    p = _payload(facts, ledger)
    assert p["model"]["eps_mode"] == "direct" and p["model"]["direct_eps"] == 10.0
    assert p["sliders"]["seg"]["disabled"] is True and p["sliders"]["margin"]["disabled"] is True
    assert p["selfcheck"][0]["out"]["value"] == pytest.approx(80.0)
    assert all(s["enabled"] is False for s in p["scenarios"][1:])
    assert all(c["enabled"] is False for c in p["cycles"])


def test_few_surprise_quarters_keep_margin_mode(valuation_db, read_facts, ledger_file):
    _, seed = valuation_db

    def edit(rows):
        for r in rows["earnings"][:6]:
            r["revenue_estimated"] = None

    facts, ledger = read_facts(seed(edit=edit)), load_ledger(ledger_file())
    gates = _gates(facts, ledger)
    assert {r.id: r.status for r in gates.results}["B2"] == UNVERIFIED
    p = build_payload(facts, ledger, gates)
    assert p["model"]["eps_mode"] == "margin" and p["sliders"]["seg"]["disabled"] is False
    assert p["selfcheck"][0]["out"]["zone"] is None


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
    assert status["S1"] == VERIFIED


def test_guidance_is_note_only(acme_payload):
    notes = acme_payload["guidance_notes"]
    assert [n["id"] for n in notes] == ["g-2026q4-rev"]
    assert notes[0]["value"] == 1650e6 and notes[0]["source"].startswith("acme-pr-2026q3")


def test_lower_bound_guidance_flags(acme, ledger_file):
    facts, _ = acme
    p = _payload(facts, load_ledger(ledger_file(("is_lower_bound = false", "is_lower_bound = true"))))
    assert "下限" in p["guidance_notes"][0]["flags"]
    p = _payload(facts, load_ledger(ledger_file(('period_type = "quarter"', 'period_type = "run_rate"'))))
    assert "年化口径" in p["guidance_notes"][0]["flags"]


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


def test_fadr_payload_total_mode_with_fx(valuation_db, read_facts, ledger_file):
    _, seed = valuation_db
    facts = read_facts(seed(fadr=True))
    p = _payload(facts, load_ledger(ledger_file(text=FADR_LEDGER)))
    assert p["sliders"]["fx"]["init"] == 32.0
    assert p["sliders"]["fx"]["min"] == 0.85 * 32.0 and p["sliders"]["fx"]["max"] == 1.15 * 32.0
    assert p["model"]["mode"] == "total" and "未登记分部" in p["model"]["total_reason"]
    assert p["model"]["currency"] == "TWD"
    assert _column(p, "ntm")["eps_usd"] == 10.0
    assert p["selfcheck"][0]["out"]["value"] == pytest.approx(80.0, rel=1e-12)
    assert {"fx:min", "fx:max"} <= {s["name"] for s in p["selfcheck"]}
    assert [s["id"] for s in p["scenarios"]] == ["consensus"] and p["scenarios"][0]["weight"] == 0.0
    assert p["ev_init"] is None
    assert p["consensus"]["mkt_cap"] == 80.0 * 100e6
