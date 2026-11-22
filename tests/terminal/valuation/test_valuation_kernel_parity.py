"""Python kernel.py and JS kernel.js must return the same value for every case (Task 1)."""
import json
import math
import shutil
import subprocess
from pathlib import Path

import pytest

from terminal.valuation import kernel

KERNEL_JS = Path(__file__).resolve().parents[3] / "terminal" / "valuation" / "assets" / "kernel.js"

NAN, INF, UNDEF = {"$": "NaN"}, {"$": "Infinity"}, {"$": "undefined"}

# Reads cases from stdin, decodes sentinels, writes results with non-finite numbers and
# undefined re-encoded as sentinels so JSON cannot silently turn them into null.
HARNESS = r"""
const k = require(process.argv[1]);
const decode = (v) => {
  if (Array.isArray(v)) return v.map(decode);
  if (v && typeof v === "object") {
    if (Object.keys(v).length === 1 && "$" in v) {
      return {NaN: NaN, Infinity: Infinity, undefined: undefined}[v.$];
    }
    const out = {};
    for (const key of Object.keys(v)) out[key] = decode(v[key]);
    return out;
  }
  return v;
};
const encode = (key, v) => {
  if (v === undefined) return {"$": "undefined"};
  if (typeof v === "number" && !Number.isFinite(v)) return {"$": String(v)};
  return v;
};
let raw = "";
process.stdin.on("data", (c) => { raw += c; });
process.stdin.on("end", () => {
  const cases = JSON.parse(raw);
  const results = cases.map((c) => k[c.fn](...decode(c.args)));
  process.stdout.write(JSON.stringify(results, encode));
});
"""

LIVE = {"bases": [3840e6, 2560e6], "adjs": [0.0, 0.0], "driver_delta": 0.0, "margin": 0.15625,
        "shares": 100e6, "fx": 1.0, "pe": 8.0, "pe_half": 0.0, "price": 80.0, "rev_cons": 6400e6,
        "half_width": 0.1, "eps_mode": "margin", "direct_eps": None}
MU_LIKE = {"bases": [91.73e9, 82.97e9, 66.82e9, 34.77e9], "adjs": [0.10, 0.15, 0.0, 0.05], "driver_delta": 0.0,
           "margin": 0.68, "shares": 1147e6, "fx": 1.0, "pe": 6.0, "pe_half": 1.0, "price": 594.51,
           "rev_cons": 276.29e9, "half_width": 0.104, "eps_mode": "margin", "direct_eps": None}
TSM_LIKE = {"bases": [4799.4e9, 1482.2e9, 564.6e9, 211.7e9], "adjs": [-0.10, -0.03, 0.0, -0.05], "driver_delta": 0.0,
            "margin": 0.46, "shares": 5186.39e6, "fx": 31.778176, "pe": 15.0, "pe_half": 1.5, "price": 319.15,
            "rev_cons": 7057.98e9, "half_width": 0.06, "eps_mode": "margin", "direct_eps": None}

CASES = [
    {"fn": "fin", "args": [3.5]},
    {"fn": "fin", "args": [0]},
    {"fn": "fin", "args": [-2]},
    {"fn": "fin", "args": [None]},
    {"fn": "fin", "args": [True]},
    {"fn": "fin", "args": ["3"]},
    {"fn": "fin", "args": [NAN]},
    {"fn": "fin", "args": [INF]},
    {"fn": "fin", "args": [UNDEF]},
    {"fn": "zone", "args": [6400e6 * 1.2, 6400e6, 0.104]},
    {"fn": "zone", "args": [6400e6 * 1.05, 6400e6, 0.104]},
    {"fn": "zone", "args": [6400e6, 6400e6, None]},
    {"fn": "zone", "args": [6400e6, 0, 0.1]},
    {"fn": "zone", "args": [6400e6, -5, 0.1]},
    {"fn": "zone", "args": [6400e6, 6400e6, 0]},
    {"fn": "zone", "args": [INF, 6400e6, 0.1]},
    {"fn": "to_usd", "args": [686.06366, 31.778]},
    {"fn": "to_usd", "args": [-5.0, 32.0]},
    {"fn": "to_usd", "args": [64.0, 0]},
    {"fn": "to_usd", "args": [64.0, None]},
    {"fn": "to_usd", "args": [NAN, 32.0]},
    {"fn": "implied_pe", "args": [594.51, 174.94966]},
    {"fn": "implied_pe", "args": [80, 0]},
    {"fn": "implied_pe", "args": [80, -1.0]},
    {"fn": "implied_pe", "args": [0, 10.0]},
    {"fn": "implied_pe", "args": [None, 10.0]},
    {"fn": "scenario_value", "args": [8, 10.0]},
    {"fn": "scenario_value", "args": [8, -1.0]},
    {"fn": "scenario_value", "args": [0, 10.0]},
    {"fn": "scenario_value", "args": [UNDEF, 10.0]},
    {"fn": "pct_diff", "args": [88.0, 80.0]},
    {"fn": "pct_diff", "args": [-3.0, 80.0]},
    {"fn": "pct_diff", "args": [88.0, 0]},
    {"fn": "pct_diff", "args": [None, 80.0]},
    {"fn": "pct_diff", "args": [88.0, INF]},
    {"fn": "pct_rank", "args": [[1, 2, 3, 4], 2]},
    {"fn": "pct_rank", "args": [[1, 2, 3, 4], 0]},
    {"fn": "pct_rank", "args": [[6.86, 11.01, 18.91, 26.12, 34.67], 12.5]},
    {"fn": "pct_rank", "args": [[], 1.0]},
    {"fn": "pct_rank", "args": [[1, 2, 3], NAN]},
    {"fn": "pct_rank", "args": [[1, None, 3], 2]},
    {"fn": "pct_rank", "args": [None, 2]},
    {"fn": "weighted_value", "args": [[100, 50], [60, 40]]},
    {"fn": "weighted_value", "args": [[100.1, 50.3, 7.7], [33.3, 33.3, 33.4]]},
    {"fn": "weighted_value", "args": [[100, 50], [60, 30]]},
    {"fn": "weighted_value", "args": [[None, 50], [0, 100]]},
    {"fn": "weighted_value", "args": [[None, 50], [10, 90]]},
    {"fn": "weighted_value", "args": [[], []]},
    {"fn": "weighted_value", "args": [[100], [100, 0]]},
    {"fn": "weighted_value", "args": [[100, 50], [-10, 110]]},
    {"fn": "weighted_value", "args": [[100, 50], [NAN, 100]]},
    {"fn": "weighted_value", "args": [[100, UNDEF], [100, 0]]},
    {"fn": "seg_revenue", "args": [[3840e6, 2560e6], [0.10, 0.05], 0.0]},
    {"fn": "seg_revenue", "args": [[91.73e9, 82.97e9, 66.82e9, 34.77e9], [-0.45, -0.25, -0.45, -0.25], 0.0]},
    {"fn": "seg_revenue", "args": [[3840e6, 2560e6], [0.1, 0.0], 123.4e6]},
    {"fn": "seg_revenue", "args": [[3840e6, 2560e6], [0.1], 0.0]},
    {"fn": "seg_revenue", "args": [[], [], 0.0]},
    {"fn": "seg_revenue", "args": [[3840e6, NAN], [0.1, 0.0], 0.0]},
    {"fn": "seg_revenue", "args": [[3840e6], [0.1], UNDEF]},
    {"fn": "seg_revenue", "args": [None, [0.1], 0.0]},
    {"fn": "implied_margin", "args": [10.0, 100e6, 6400e6]},
    {"fn": "implied_margin", "args": [174.94966, 1147e6, 276290602294.0]},
    {"fn": "implied_margin", "args": [10.0, 0, 6400e6]},
    {"fn": "implied_margin", "args": [10.0, 100e6, 0]},
    {"fn": "implied_margin", "args": [INF, 100e6, 6400e6]},
    {"fn": "margin_eps", "args": [6912e6, 0.18, 100e6]},
    {"fn": "margin_eps", "args": [4e9, -0.5642, 1e9]},
    {"fn": "margin_eps", "args": [276290602294.0, 0.7263, 1147e6]},
    {"fn": "margin_eps", "args": [4e9, 0.2, 0]},
    {"fn": "margin_eps", "args": [4e9, NAN, 1e9]},
    {"fn": "quarter_path", "args": [[1, 1, 2], 8.0]},
    {"fn": "quarter_path", "args": [[1674.06e9, 1758.3e9, 1801.7e9, 1918.2e9], 7057.98e9]},
    {"fn": "quarter_path", "args": [[0, 0, 0], 8.0]},
    {"fn": "quarter_path", "args": [[1, -1, 2], 8.0]},
    {"fn": "quarter_path", "args": [[1, 1], None]},
    {"fn": "quarter_path", "args": [[1, INF], 8.0]},
    {"fn": "grid_pos", "args": [[6, 7, 8, 9, 10], 8.5]},
    {"fn": "grid_pos", "args": [[6, 7, 8, 9, 10], 3]},
    {"fn": "grid_pos", "args": [[6, 7, 8, 9, 10], 99]},
    {"fn": "grid_pos", "args": [[3.2768, 5.5688, 7.8608, 10.1528, 12.4416], 8.2432]},
    {"fn": "grid_pos", "args": [[6], 6]},
    {"fn": "grid_pos", "args": [[7, 6], 6.5]},
    {"fn": "grid_pos", "args": [[6, 7, 8], NAN]},
    {"fn": "live_state", "args": [LIVE]},
    {"fn": "live_state", "args": [{**LIVE, "adjs": [0.10, 0.05], "margin": 0.18, "pe": 9.0, "pe_half": 1.0}]},
    {"fn": "live_state", "args": [{**LIVE, "adjs": [-0.6, -0.6]}]},
    {"fn": "live_state", "args": [{**LIVE, "margin": -0.5642}]},
    {"fn": "live_state", "args": [{**LIVE, "eps_mode": "direct", "direct_eps": 12.25, "margin": None}]},
    {"fn": "live_state", "args": [{**LIVE, "eps_mode": "direct", "direct_eps": NAN}]},
    {"fn": "live_state", "args": [{**LIVE, "margin": NAN}]},
    {"fn": "live_state", "args": [{**LIVE, "pe": INF}]},
    {"fn": "live_state", "args": [{**LIVE, "driver_delta": UNDEF}]},
    {"fn": "live_state", "args": [{**LIVE, "fx": None}]},
    {"fn": "live_state", "args": [{**LIVE, "pe_half": 9.0}]},
    {"fn": "live_state", "args": [{**LIVE, "half_width": None, "price": 0}]},
    {"fn": "live_state", "args": [{**LIVE, "eps_mode": UNDEF}]},
    {"fn": "live_state", "args": [MU_LIKE]},
    {"fn": "live_state", "args": [TSM_LIKE]},
]


def _py(value):
    if isinstance(value, list):
        return [_py(v) for v in value]
    if isinstance(value, dict):
        if set(value) == {"$"}:
            return {"NaN": math.nan, "Infinity": math.inf, "undefined": None}[value["$"]]
        return {key: _py(v) for key, v in value.items()}
    return value


def _same(py, js):
    if isinstance(py, dict):
        return isinstance(js, dict) and set(py) == set(js) and all(_same(py[key], js[key]) for key in py)
    if isinstance(py, list):
        return isinstance(js, list) and len(py) == len(js) and all(_same(a, b) for a, b in zip(py, js))
    if py is None or js is None:
        return py is None and js is None
    if isinstance(py, str) or isinstance(js, str):
        return py == js
    return isinstance(js, (int, float)) and not isinstance(js, bool) and py == js


def test_case_count_covers_every_function():
    assert len(CASES) >= 40
    assert {c["fn"] for c in CASES} == {"fin", "zone", "to_usd", "implied_pe", "scenario_value", "pct_diff",
                                        "pct_rank", "weighted_value", "seg_revenue", "implied_margin",
                                        "margin_eps", "quarter_path", "grid_pos", "live_state"}
    assert sum(1 for c in CASES if c["fn"] == "live_state") >= 12


def test_python_and_js_kernels_agree():
    if shutil.which("node") is None:
        pytest.fail("node required for kernel parity")
    run = subprocess.run(["node", "-e", HARNESS, str(KERNEL_JS)], input=json.dumps(CASES),
                         capture_output=True, text=True, timeout=60)
    assert run.returncode == 0, run.stderr
    js_results = json.loads(run.stdout)
    assert len(js_results) == len(CASES)
    mismatches = []
    for case, js in zip(CASES, js_results):
        py = getattr(kernel, case["fn"])(*_py(case["args"]))
        if not _same(py, js):
            mismatches.append((case["fn"], case["args"], py, js))
    assert mismatches == []
