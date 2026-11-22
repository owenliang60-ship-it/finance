"""state.js reducer and derive, run in node against the ACME payload (Task 6)."""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

from terminal.valuation.engine import build_payload
from terminal.valuation.gates import run_gates, summarize
from terminal.valuation.ledger import load_ledger

ASSETS = Path(__file__).resolve().parents[3] / "terminal" / "valuation" / "assets"

DRIVER = r"""
Object.assign(globalThis, require(process.argv[1]));
const st = require(process.argv[2]);
const same = (a, b) => JSON.stringify(a) === JSON.stringify(b);
let raw = "";
process.stdin.on("data", (c) => { raw += c; });
process.stdin.on("end", () => {
  const p = JSON.parse(raw);
  const out = {};
  const s0 = st.init_state(p);
  const before = JSON.stringify(s0);
  const d0 = st.derive(s0, p);
  out.init = {value: d0.value, source: d0.eps_source, vs: d0.value_vs_price, zone: d0.zone,
              pe_rank: d0.pe_rank, scenarios: s0.scenarios.length, preset_id: s0.preset_id,
              eps_mode: s0.eps_mode};

  const tail = st.derive(st.reduce(s0, {type: "set", key: "rev", value: 6400e6 * 1.2}, p), p);
  out.tail = {zone: tail.zone, value: tail.value, value_is_null: tail.value === null,
              fmt: st.fmt(tail.value, "usd")};
  const mild = st.derive(st.reduce(s0, {type: "set", key: "rev", value: 6400e6 * 1.05}, p), p);
  out.mild = {zone: mild.zone, source: mild.eps_source, value: mild.value};

  out.abc_same = same(st.reduce(s0, {type: "set", key: "rev", value: "abc"}, p), s0);
  out.nan_same = same(st.reduce(s0, {type: "set", key: "rev", value: NaN}, p), s0);
  out.inf_same = same(st.reduce(s0, {type: "set", key: "rev", value: Infinity}, p), s0);
  out.huge_rev = st.reduce(s0, {type: "set", key: "rev", value: 1e99}, p).rev;
  out.tiny_pe = st.reduce(s0, {type: "set", key: "pe", value: -5}, p).pe;
  out.bad_key_same = same(st.reduce(s0, {type: "set", key: "price", value: 1}, p), s0);

  const pre = st.reduce(s0, {type: "preset", id: "2025-26"}, p);
  const dp = st.derive(pre, p);
  out.preset = {source: dp.eps_source, preset_id: pre.preset_id, eps_mode: pre.eps_mode, value: dp.value};
  const back = st.reduce(pre, {type: "set", key: "margin", value: 0.4}, p);
  out.after_margin = {source: st.derive(back, p).eps_source, preset_id: back.preset_id, margin: back.margin};
  out.disabled_preset_same = same(st.reduce(s0, {type: "preset", id: "off"}, p), s0);
  out.unknown_preset_same = same(st.reduce(s0, {type: "preset", id: "nope"}, p), s0);

  const drv = st.reduce(pre, {type: "driver", id: "hbm", value: 1000e6}, p);
  out.driver = {value: drv.drivers.hbm, eps_mode: drv.eps_mode, preset_id: drv.preset_id,
                rev_eff: st.derive(drv, p).rev_eff};
  out.driver_clamped = st.reduce(s0, {type: "driver", id: "hbm", value: 9e99}, p).drivers.hbm;
  const reset = st.reduce(st.reduce(drv, {type: "save"}, p), {type: "reset"}, p);
  out.reset = {scenarios: reset.scenarios.length, rev: reset.rev, hbm: reset.drivers.hbm, preset_id: reset.preset_id,
               value: st.derive(reset, p).value};

  let s = st.reduce(st.reduce(s0, {type: "save"}, p), {type: "save"}, p);
  out.saved = s.scenarios.map((x) => [x.name, x.value, x.weight]);
  out.no_weight = st.derive(s, p).weighted_reason;
  s = st.reduce(st.reduce(s, {type: "weight", index: 0, text: "60"}, p), {type: "weight", index: 1, text: "40"}, p);
  out.w_60_40 = st.derive(s, p).weighted;
  s = st.reduce(s, {type: "weight", index: 1, text: "30"}, p);
  const w30 = st.derive(s, p);
  out.w_60_30 = {weighted: w30.weighted, reason: w30.weighted_reason};
  s = st.reduce(s, {type: "weight", index: 1, text: ""}, p);
  const wblank = st.derive(s, p);
  out.w_blank = {weighted: wblank.weighted, reason: wblank.weighted_reason, weight: s.scenarios[1].weight};
  out.w_neg = st.reduce(s, {type: "weight", index: 1, text: "-5"}, p).scenarios[1].weight;
  out.w_text = st.reduce(s, {type: "weight", index: 1, text: "abc"}, p).scenarios[1].weight;
  const tailSaved = st.reduce(st.reduce(st.reduce(s0, {type: "set", key: "rev", value: 6400e6 * 1.2}, p),
                                        {type: "save"}, p), {type: "weight", index: 0, text: "100"}, p);
  const wt = st.derive(tailSaved, p);
  out.w_tail = {weighted: wt.weighted, reason: wt.weighted_reason};
  out.deleted = st.reduce(s, {type: "delete", index: 0}, p).scenarios.map((x) => x.name);

  const kinds = ["usd", "x", "pct", "num"];
  out.fmt_missing = kinds.map((kd) => [st.fmt(null, kd), st.fmt(NaN, kd), st.fmt(undefined, kd),
                                       st.fmt(Infinity, kd)]);
  out.fmt_values = [st.fmt(594.51, "usd"), st.fmt(-129.0169, "usd"), st.fmt(8, "x"), st.fmt(0, "pct"),
                    st.fmt(-1e-16, "pct"), st.fmt(0.1234, "pct"), st.fmt(6400e6, "num"), st.fmt(31.7782, "num")];

  const actions = [{type: "set", key: "rev", value: 7e9}, {type: "preset", id: "2025-26"}, {type: "reset"},
                   {type: "save"}, {type: "driver", id: "hbm", value: 1e9}, {type: "weight", index: 0, text: "5"},
                   {type: "delete", index: 0}];
  const s2 = st.reduce(s0, {type: "save"}, p);
  const before2 = JSON.stringify(s2);
  for (const a of actions) st.reduce(s2, a, p);
  out.pure = JSON.stringify(s0) === before && JSON.stringify(s2) === before2;
  process.stdout.write(JSON.stringify(out));
});
"""


def _payload(valuation_db, read_facts, ledger_file):
    db, _ = valuation_db
    facts, ledger = read_facts(db), load_ledger(ledger_file())
    payload = build_payload(facts, ledger, summarize(run_gates(facts, ledger), facts, ledger))
    payload["presets"].append({"id": "off", "label": "停用周期", "enabled": False, "reason": "测试", "rev": None,
                               "direct_eps": None})
    return payload


def _run(payload):
    if shutil.which("node") is None:
        pytest.fail("node required for state.js tests")
    run = subprocess.run(["node", "-e", DRIVER, str(ASSETS / "kernel.js"), str(ASSETS / "state.js")],
                         input=json.dumps(payload, allow_nan=False), capture_output=True, text=True, timeout=60)
    assert run.returncode == 0, run.stderr
    return json.loads(run.stdout)


def test_state_reducer_and_derive(valuation_db, read_facts, ledger_file):
    p = _payload(valuation_db, read_facts, ledger_file)
    out = _run(p)
    assert out["init"]["value"] == pytest.approx(80.0, rel=1e-12)
    assert out["init"]["source"] == "bridge" and out["init"]["vs"] == pytest.approx(0.0, abs=1e-12)
    assert out["init"]["zone"] == "mild" and out["init"]["pe_rank"] is not None
    assert out["init"]["scenarios"] == 0 and out["init"]["preset_id"] is None and out["init"]["eps_mode"] == "bridge"
    assert out["tail"] == {"zone": "tail", "value": None, "value_is_null": True, "fmt": "—"}
    assert out["mild"]["zone"] == "mild" and out["mild"]["source"] == "bridge" and out["mild"]["value"] > 80
    assert out["abc_same"] and out["nan_same"] and out["inf_same"] and out["bad_key_same"]
    assert out["huge_rev"] == p["sliders"]["rev"]["max"]
    assert out["tiny_pe"] == p["sliders"]["pe"]["min"]
    assert out["preset"] == {"source": "direct", "preset_id": "2025-26", "eps_mode": "direct",
                             "value": pytest.approx(80.0)}
    assert out["after_margin"] == {"source": "bridge", "preset_id": None, "margin": 0.4}
    assert out["disabled_preset_same"] and out["unknown_preset_same"]
    assert out["driver"]["value"] == 1000e6 and out["driver"]["eps_mode"] == "bridge"
    assert out["driver"]["preset_id"] is None
    assert out["driver"]["rev_eff"] == 6400e6 + 200e6
    assert out["driver_clamped"] == 2400e6
    assert out["reset"] == {"scenarios": 1, "rev": 6400e6, "hbm": 800e6, "preset_id": None,
                            "value": pytest.approx(80.0, rel=1e-12)}
    assert [x[0] for x in out["saved"]] == ["情景 1", "情景 2"]
    assert all(x[1] == pytest.approx(80.0) and x[2] is None for x in out["saved"])
    assert out["no_weight"] == "尚未填写权重"
    assert out["w_60_40"] == pytest.approx(80.0)
    assert out["w_60_30"] == {"weighted": None, "reason": "权重合计不为 100"}
    assert out["w_blank"] == {"weighted": None, "reason": "尚未填写权重", "weight": None}
    assert out["w_neg"] is None and out["w_text"] is None
    assert out["w_tail"] == {"weighted": None, "reason": "含无意义情景"}
    assert out["deleted"] == ["情景 2"]
    assert out["fmt_missing"] == [["—"] * 4] * 4
    assert out["fmt_values"] == ["$594.51", "-$129.02", "8.00x", "0.0%", "0.0%", "12.3%", "6.40B", "31.78"]
    assert out["pure"] is True
