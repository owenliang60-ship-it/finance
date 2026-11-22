"""state.js v2 reducer and derive, run in node against ACME payloads (dashboard v2 Task 4)."""
import json
import shutil
import subprocess
from dataclasses import replace
from pathlib import Path

import pytest

from terminal.valuation.engine import build_payload
from terminal.valuation.gates import run_gates, summarize
from terminal.valuation.ledger import load_ledger

ASSETS = Path(__file__).resolve().parents[3] / "terminal" / "valuation" / "assets"

DRIVER = r"""
Object.assign(globalThis, require(process.argv[1]));
const st = require(process.argv[2]);
let raw = "";
process.stdin.on("data", (c) => { raw += c; });
process.stdin.on("end", () => {
  const {p, direct} = JSON.parse(raw);
  const out = {};
  const s0 = st.init_state(p);
  const before = JSON.stringify(s0);
  const d0 = st.derive(s0, p);
  out.init = {live: d0.live, ev: d0.ev, custom: st.custom_text(d0), ev_text: st.ev_text(d0), weights: s0.weights,
              pe_half: s0.pe_half, preset_id: s0.preset_id};
  out.presets = {};
  for (const sc of p.scenarios) {
    const s = st.reduce(s0, {type: "preset", id: sc.id}, p);
    out.presets[sc.id] = {live: st.derive(s, p).live, preset_id: s.preset_id, pe_half: s.pe_half};
  }
  const cyc = st.reduce(st.reduce(s0, {type: "set", key: "pe", value: 9.5}, p), {type: "preset", id: "cycle:2025-26"}, p);
  out.cycle = {adjs: cyc.adjs, margin: cyc.margin, pe: cyc.pe, preset_id: cyc.preset_id};

  const sl = p.sliders;
  const segs = p.model.segments.length;
  const combos = [];
  const ends = [sl.seg.min, sl.seg.max];
  const walk = (i, adjs) => {
    if (i === segs) { combos.push(adjs.slice()); return; }
    for (const v of ends) { adjs.push(v); walk(i + 1, adjs); adjs.pop(); }
  };
  walk(0, []);
  out.sweep = [];
  for (const adjs of combos) {
    for (const margin of [sl.margin.min, 0, sl.margin.max]) {
      for (const pe of [sl.pe.min, sl.pe.max]) {
        let s = s0;
        adjs.forEach((v, i) => { s = st.reduce(s, {type: "seg", index: i, value: v}, p); });
        s = st.reduce(st.reduce(s, {type: "set", key: "margin", value: margin}, p), {type: "set", key: "pe", value: pe}, p);
        const d = st.derive(s, p);
        out.sweep.push({live: d.live, text: st.custom_text(d), tail: d.tail});
      }
    }
  }
  const low = st.derive(st.reduce(s0, {type: "set", key: "margin", value: sl.margin.min}, p), p);
  out.margin_min = {text: st.custom_text(low), state: low.live.eps_state};

  let w = st.reduce(s0, {type: "weight", id: "bull", text: "10"}, p);
  const dw = st.derive(w, p);
  out.w_bad = {ev: dw.ev, reason: dw.ev_reason, text: st.ev_text(dw)};
  const blank = st.derive(st.reduce(s0, {type: "weight", id: "base", text: ""}, p), p);
  out.w_blank = {ev: blank.ev, reason: blank.ev_reason};
  out.w_neg = st.reduce(s0, {type: "weight", id: "base", text: "-5"}, p).weights.base;
  out.w_unknown_same = JSON.stringify(st.reduce(s0, {type: "weight", id: "nope", text: "5"}, p)) === before;
  w = st.reduce(st.reduce(w, {type: "seg", index: 0, value: 9}, p), {type: "seg", index: 1, value: -9}, p);
  out.clamped = w.adjs;
  out.reset = st.reduce(w, {type: "reset"}, p).weights;
  out.bad_set_same = JSON.stringify(st.reduce(s0, {type: "set", key: "price", value: 1}, p)) === before
    && JSON.stringify(st.reduce(s0, {type: "seg", index: 0, value: NaN}, p)) === before
    && JSON.stringify(st.reduce(s0, {type: "seg", index: 7, value: 0.1}, p)) === before
    && JSON.stringify(st.reduce(s0, {type: "preset", id: "nope"}, p)) === before;
  out.pe_half = st.reduce(s0, {type: "set", key: "pe_half", value: -3}, p).pe_half;
  out.pure = JSON.stringify(s0) === before;

  const t0 = st.init_state(direct);
  const tb = JSON.stringify(t0);
  out.direct = {seg_same: JSON.stringify(st.reduce(t0, {type: "seg", index: 0, value: 0.2}, direct)) === tb,
                margin_same: JSON.stringify(st.reduce(t0, {type: "set", key: "margin", value: 0.2}, direct)) === tb,
                bull_same: JSON.stringify(st.reduce(t0, {type: "preset", id: "bull"}, direct)) === tb,
                live: st.derive(t0, direct).live, text: st.custom_text(st.derive(t0, direct))};

  out.fmt = [st.fmt(null, "usd"), st.fmt(594.51, "usd"), st.fmt(8, "x"), st.fmt(0.1234, "pct"),
             st.fmt_money(276.29e9, "USD"), st.fmt_money(7057.98e9, "TWD"), st.fmt_money(5.4e10, "EUR"),
             st.fmt_px(1066.2), st.fmt_px(65.9456), st.fmt_px(-3.2), st.fmt_signed(0.371, 0),
             st.fmt_signed(-0.35, 1), st.fmt_signed(0, 0)];
  process.stdout.write(JSON.stringify(out));
});
"""


def _payloads(valuation_db, read_facts, ledger_file):
    db, _ = valuation_db
    facts, ledger = read_facts(db), load_ledger(ledger_file())
    payload = build_payload(facts, ledger, summarize(run_gates(facts, ledger), facts, ledger))
    zero = replace(facts, shares=0.0)
    direct = build_payload(zero, ledger, summarize(run_gates(zero, ledger), zero, ledger))
    return payload, direct


def _run(payload, direct):
    if shutil.which("node") is None:
        pytest.fail("node required for state.js tests")
    run = subprocess.run(["node", "-e", DRIVER, str(ASSETS / "kernel.js"), str(ASSETS / "state.js")],
                         input=json.dumps({"p": payload, "direct": direct}, allow_nan=False),
                         capture_output=True, text=True, timeout=60)
    assert run.returncode == 0, run.stderr
    return json.loads(run.stdout)


@pytest.fixture
def run_state(valuation_db, read_facts, ledger_file):
    payload, direct = _payloads(valuation_db, read_facts, ledger_file)
    return payload, direct, _run(payload, direct)


def test_initial_state_matches_selfcheck(run_state):
    p, _, out = run_state
    assert out["init"]["live"] == p["selfcheck"][0]["out"]
    assert out["init"]["weights"] == {"consensus": 5.0, "bull": 20.0, "base": 50.0, "bear": 25.0}
    assert out["init"]["ev"] == pytest.approx(65.10208)
    assert out["init"]["custom"] == "$80.00（0.0%）"
    assert out["init"]["ev_text"].startswith("$65.10")
    assert out["init"]["pe_half"] == 0 and out["init"]["preset_id"] is None


def test_presets_reproduce_payload_scenarios(run_state):
    p, _, out = run_state
    for sc in p["scenarios"]:
        assert sc["enabled"] is True
        got = out["presets"][sc["id"]]
        assert got["live"] == sc["out"], sc["id"]
        assert got["preset_id"] == sc["id"] and got["pe_half"] == sc["pe_half"]


def test_cycle_preset_only_moves_adjs_and_margin(run_state):
    p, _, out = run_state
    c = p["cycles"][0]
    assert out["cycle"] == {"adjs": [c["adj"], c["adj"]], "margin": c["margin"], "pe": 9.5,
                            "preset_id": "cycle:2025-26"}


def test_full_range_sweep_stays_finite(run_state):
    _, _, out = run_state
    assert len(out["sweep"]) == 4 * 3 * 2
    for row in out["sweep"]:
        for key in ("rev", "net_income", "eps_local", "eps_usd"):
            assert isinstance(row["live"][key], (int, float)), (key, row)
        assert row["text"] != "—" and "—" != row["text"].strip()
    assert any(r["tail"] for r in out["sweep"])
    assert out["margin_min"] == {"text": "负盈利（周期谷底）", "state": "nonpos"}


def test_weights_and_ev(run_state):
    _, _, out = run_state
    assert out["w_bad"]["ev"] is None and out["w_bad"]["reason"] == "权重合计不为 100"
    assert out["w_bad"]["text"] == "权重合计不为 100"
    assert out["w_blank"] == {"ev": None, "reason": "权重未填完整"}
    assert out["w_neg"] is None and out["w_unknown_same"] is True
    assert out["clamped"] == [0.40, -0.60]
    assert out["reset"] == {"consensus": 5.0, "bull": 20.0, "base": 50.0, "bear": 25.0}


def test_invalid_actions_and_purity(run_state):
    _, _, out = run_state
    assert out["bad_set_same"] is True
    assert out["pe_half"] == 0
    assert out["pure"] is True


def test_direct_mode_ignores_segment_and_margin(run_state):
    _, direct, out = run_state
    assert direct["model"]["eps_mode"] == "direct"
    d = out["direct"]
    assert d["seg_same"] and d["margin_same"] and d["bull_same"]
    assert d["live"]["net_income"] is None and d["live"]["eps_local"] == 10.0
    assert d["text"] == "$80.00（0.0%）"


def test_formatters(run_state):
    _, _, out = run_state
    assert out["fmt"] == ["—", "$594.51", "8.00x", "12.3%", "$276.3B", "NT$7.06T", "EUR 54.0B", "$1,066", "$65.95",
                          "−$3.20", "+37%", "−35.0%", "0%"]
