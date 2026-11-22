"""charts.js draw lists: no text overlap, nothing outside the 560×340 viewBox, finite geometry (v2 Task 4).

Boxes and text widths are recomputed here in Python (independent of charts.js): CJK and full-width
characters count 1.0 × size, others 0.58 × size; the vertical box is [y - size, y + 0.25 size].
"""
import json
import math
import shutil
import subprocess
from dataclasses import replace
from pathlib import Path

import pytest

from terminal.valuation.engine import build_payload
from terminal.valuation.gates import run_gates, summarize
from terminal.valuation.ledger import load_ledger

ASSETS = Path(__file__).resolve().parents[3] / "terminal" / "valuation" / "assets"
FADR_LEDGER = 'schema = 1\nsymbol = "ACME"\nreporting_currency = "TWD"\neps_basis = "ifrs"\ncyclical = true\n'
NUMERIC = ("x", "y", "w", "h", "x1", "y1", "x2", "y2", "cx", "cy", "r", "size")

DRIVER = r"""
Object.assign(globalThis, require(process.argv[1]));
Object.assign(globalThis, require(process.argv[2]));
const ch = require(process.argv[3]);
let raw = "";
process.stdin.on("data", (c) => { raw += c; });
process.stdin.on("end", () => {
  const payloads = JSON.parse(raw);
  const result = {};
  for (const [name, p] of Object.entries(payloads)) {
    const s0 = init_state(p);
    const sl = p.sliders;
    const apply = (s, end) => {
      p.model.segments.forEach((_, i) => { s = reduce(s, {type: "seg", index: i, value: sl.seg[end]}, p); });
      for (const key of ["margin", "pe", "fx"]) {
        if (sl[key]) s = reduce(s, {type: "set", key: key, value: sl[key][end]}, p);
      }
      return reduce(s, {type: "set", key: "pe_half", value: end === "max" ? 2 : 0}, p);
    };
    const states = {init: s0, min: apply(s0, "min"), max: apply(s0, "max")};
    for (const sc of p.scenarios) if (sc.enabled) states["preset:" + sc.id] = reduce(s0, {type: "preset", id: sc.id}, p);
    for (const c of p.cycles) if (c.enabled) states["cycle:" + c.id] = reduce(s0, {type: "preset", id: "cycle:" + c.id}, p);
    states.bad_weights = reduce(s0, {type: "weight", id: "consensus", text: "7"}, p);
    result[name] = {};
    for (const [sname, s] of Object.entries(states)) {
      const d = derive(s, p);
      result[name][sname] = {ev: d.ev, panels: [ch.panel1(d, p), ch.panel2(d, p), ch.panel3(d, p), ch.panel4(d, p)]};
    }
  }
  const long = ch.value_label({enabled: true, eps_state: "pos", mid: 12345678, lo: 11345678, hi: 13345678, pct: 20.766},
                              164);
  result.long = {text: long, w: ch.text_w(long, 11)};
  result.spread = ch.spread([100, 101, 102, 300, 301], 13, 46, 292);
  process.stdout.write(JSON.stringify(result));
});
"""


def _wide(ch: str) -> bool:
    c = ord(ch)
    return (0x2E80 <= c <= 0x9FFF or 0xAC00 <= c <= 0xD7AF or 0xF900 <= c <= 0xFAFF or 0xFE30 <= c <= 0xFE4F
            or 0xFF00 <= c <= 0xFFEF or 0x3000 <= c <= 0x303F)


def text_w(s: str, size: float) -> float:
    return sum((1.0 if _wide(ch) else 0.58) * size for ch in s)


def box(it):
    w = text_w(it["text"], it["size"])
    x0 = it["x"] - w if it["anchor"] == "end" else it["x"] - w / 2 if it["anchor"] == "middle" else it["x"]
    return x0, it["y"] - it["size"], x0 + w, it["y"] + 0.25 * it["size"]


def hit(a, b):
    return a[0] < b[2] - 1e-9 and b[0] < a[2] - 1e-9 and a[1] < b[3] - 1e-9 and b[1] < a[3] - 1e-9


def _payload(facts, ledger):
    return build_payload(facts, ledger, summarize(run_gates(facts, ledger), facts, ledger))


@pytest.fixture
def drawn(valuation_db, read_facts, ledger_file):
    db, seed = valuation_db
    base = read_facts(db)
    acme = load_ledger(ledger_file())
    payloads = {
        "acme": _payload(base, acme),
        "thin": _payload(read_facts(seed(thin=True)), acme),
        "conflict": _payload(base, load_ledger(ledger_file(("values = [900.0, 600.0]", "values = [900.0, 500.0]")))),
        "fadr": _payload(read_facts(seed(fadr=True)), load_ledger(ledger_file(text=FADR_LEDGER))),
        "direct": _payload(replace(base, shares=0.0), acme),
    }
    if shutil.which("node") is None:
        pytest.fail("node required for charts.js tests")
    run = subprocess.run(["node", "-e", DRIVER, str(ASSETS / "kernel.js"), str(ASSETS / "state.js"),
                          str(ASSETS / "charts.js")], input=json.dumps(payloads, allow_nan=False),
                         capture_output=True, text=True, timeout=60)
    assert run.returncode == 0, run.stderr
    return payloads, json.loads(run.stdout)


def _cases(result):
    for name in ("acme", "thin", "conflict", "fadr", "direct"):
        for sname, item in result[name].items():
            for k, panel in enumerate(item["panels"]):
                yield f"{name}/{sname}/p{k + 1}", panel


def test_text_boxes_never_overlap_and_stay_inside(drawn):
    _, result = drawn
    for where, panel in _cases(result):
        texts = [it for it in panel if it["t"] == "text"]
        assert texts, where
        boxes = [box(it) for it in texts]
        for it, b in zip(texts, boxes):
            assert it["w"] >= text_w(it["text"], it["size"]) - 1e-9, (where, it["text"])
            assert b[0] >= -1e-9 and b[1] >= -1e-9 and b[2] <= 560 + 1e-9 and b[3] <= 340 + 1e-9, (where, it["text"])
            assert it["text"].strip() and it["text"].strip() != "—", (where, it)
            for bad in ("NaN", "undefined", "Infinity", "null"):
                assert bad not in it["text"], (where, it["text"])
        for i in range(len(boxes)):
            for j in range(i + 1, len(boxes)):
                assert not hit(boxes[i], boxes[j]), (where, texts[i]["text"], texts[j]["text"])


def test_geometry_is_finite(drawn):
    _, result = drawn
    for where, panel in _cases(result):
        for it in panel:
            for key in NUMERIC:
                if key in it:
                    assert isinstance(it[key], (int, float)) and math.isfinite(it[key]), (where, it)
            for pt in it.get("pts", []):
                assert all(isinstance(v, (int, float)) and math.isfinite(v) for v in pt), (where, it)


def test_four_panels_carry_core_marks(drawn):
    _, result = drawn
    p1, p2, p3, p4 = result["acme"]["init"]["panels"]
    roles = lambda panel: {it.get("role") for it in panel}
    assert {"bar", "mid", "value-label", "price", "price-label", "ev", "ev-label"} <= roles(p1)
    assert sum(1 for it in p1 if it.get("role") == "row-label") == 5
    assert {"block", "block-label", "bar-total", "legend"} <= roles(p2)
    assert {"hist", "line", "end-label", "latest"} <= roles(p3)
    assert sum(1 for it in p3 if it.get("role") == "line") == 5
    assert {"cell", "cell-value", "cell-pct", "price-cell", "mark"} <= roles(p4)
    assert sum(1 for it in p4 if it.get("role") == "cell") == 25


def test_small_blocks_have_no_label(drawn):
    _, result = drawn
    for where, panel in _cases(result):
        if not where.endswith("/p2"):
            continue
        labels = {it["key"] for it in panel if it.get("role") == "block-label"}
        for it in panel:
            if it.get("role") == "block" and it["h"] < 14:
                assert it["key"] not in labels, (where, it)


def test_end_labels_are_spread(drawn):
    _, result = drawn
    for where, panel in _cases(result):
        if not where.endswith("/p3"):
            continue
        ys = sorted(it["y"] for it in panel if it.get("role") == "end-label")
        assert all(b - a >= 12 for a, b in zip(ys, ys[1:])), (where, ys)


def test_ev_hidden_when_weights_do_not_sum(drawn):
    _, result = drawn
    bad = result["acme"]["bad_weights"]
    assert bad["ev"] is None
    assert not [it for it in bad["panels"][0] if it.get("role") in ("ev", "ev-label")]
    conflict = result["conflict"]["init"]
    assert conflict["ev"] is None
    assert not [it for it in conflict["panels"][0] if it.get("role") in ("ev", "ev-label")]


def test_nonpositive_custom_row(drawn):
    _, result = drawn
    p1 = result["acme"]["min"]["panels"][0]
    custom = [it for it in p1 if it.get("key") == "custom"]
    assert [it["text"] for it in custom if it.get("role") == "value-label"] == ["负盈利（周期谷底）"]
    assert not [it for it in custom if it.get("role") == "bar"]
    p4 = result["acme"]["min"]["panels"][3]
    assert [it["text"] for it in p4 if it.get("role") == "custom-note"] == ["负盈利"]


def test_disabled_scenarios_are_drawn_as_unavailable(drawn):
    _, result = drawn
    p1, p2, _, _ = result["conflict"]["init"]["panels"]
    assert sum(1 for it in p1 if it.get("role") == "value-label" and it["text"] == "不可用") == 3
    assert sum(1 for it in p2 if it.get("role") == "disabled") == 3


def test_long_value_label_drops_bracket(drawn):
    _, result = drawn
    assert "[" not in result["long"]["text"] and result["long"]["w"] <= 164
    assert result["long"]["text"].startswith("$12,345,678")


def test_spread_keeps_gap(drawn):
    _, result = drawn
    ys = result["spread"]
    order = sorted(ys)
    assert all(b - a >= 13 - 1e-9 for a, b in zip(order, order[1:]))
    assert min(ys) >= 46 and max(ys) <= 292


def test_core_labels_are_never_dropped(drawn):
    payloads, result = drawn
    count = lambda panel, role: sum(1 for it in panel if it.get("role") == role)
    for name in ("acme", "thin", "conflict", "fadr", "direct"):
        p = payloads[name]
        enabled = sum(1 for s in p["scenarios"] if s["enabled"])
        for sname, item in result[name].items():
            p1, p2, p3, p4 = item["panels"]
            rows = len(p["scenarios"]) + 1
            assert count(p1, "row-label") == rows and count(p1, "value-label") == rows, (name, sname)
            assert count(p1, "price-label") == 1, (name, sname)
            assert count(p1, "ev-label") == (1 if item["ev"] is not None else 0), (name, sname)
            assert count(p2, "bar-label") == rows and count(p2, "bar-total") == enabled + 1, (name, sname)
            assert count(p2, "legend") == len(p["model"]["segments"]), (name, sname)
            assert count(p3, "end-label") == count(p3, "line"), (name, sname)
            assert count(p3, "x-label") == len(p["trajectory"]["hist"]) + len(p["trajectory"].get("future_dates", []))
            if "eps_levels" in p["heatmap"]:
                assert count(p4, "cell-value") == 25 and count(p4, "cell-pct") == 25, (name, sname)
                assert count(p4, "row-head") == 5 and count(p4, "col-head") == 5, (name, sname)
