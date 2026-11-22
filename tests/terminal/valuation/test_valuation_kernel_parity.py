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

STATE = {"rev": 6400e6, "rev_cons": 6400e6, "eps_cons": 10.0, "margin": 0.5, "shares": 100e6,
         "half_width": 0.1, "fx": 1.0, "price": 80.0, "pe": 8.0, "driver_delta": 0.0,
         "eps_mode": "bridge", "direct_eps": None}

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
    {"fn": "bridge_eps", "args": [6500e6, 6400e6, 10.0, 0.5, 100e6]},
    {"fn": "bridge_eps", "args": [6123.4e6, 6400e6, 10.0, 0.37, 98.7e6]},
    {"fn": "bridge_eps", "args": [6500e6, 6400e6, 10.0, 0.5, 0]},
    {"fn": "bridge_eps", "args": [6500e6, 6400e6, 10.0, 0.5, -1]},
    {"fn": "bridge_eps", "args": [None, 6400e6, 10.0, 0.5, 100e6]},
    {"fn": "bridge_eps", "args": [6500e6, NAN, 10.0, 0.5, 100e6]},
    {"fn": "bridge_eps", "args": [6500e6, 6400e6, UNDEF, 0.5, 100e6]},
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
    {"fn": "scenario_state", "args": [STATE]},
    {"fn": "scenario_state", "args": [{**STATE, "rev": 6400e6 * 1.07, "margin": 0.41}]},
    {"fn": "scenario_state", "args": [{**STATE, "rev": 6400e6 * 1.2}]},
    {"fn": "scenario_state", "args": [{**STATE, "half_width": None}]},
    {"fn": "scenario_state", "args": [{**STATE, "shares": 0}]},
    {"fn": "scenario_state", "args": [{**STATE, "eps_mode": "direct", "direct_eps": -129.0}]},
    {"fn": "scenario_state", "args": [{**STATE, "eps_mode": "direct", "direct_eps": 12.25, "fx": 31.778}]},
    {"fn": "scenario_state", "args": [{**STATE, "eps_mode": "direct", "direct_eps": NAN}]},
    {"fn": "scenario_state", "args": [{**STATE, "driver_delta": UNDEF}]},
    {"fn": "scenario_state", "args": [{**STATE, "driver_delta": 150e6, "fx": None}]},
    {"fn": "scenario_state", "args": [{**STATE, "pe": INF}]},
    {"fn": "scenario_state", "args": [{**STATE, "price": 0}]},
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
    if py is None or js is None:
        return py is None and js is None
    if isinstance(py, str) or isinstance(js, str):
        return py == js
    return isinstance(js, (int, float)) and not isinstance(js, bool) and py == js


def test_case_count_covers_every_function():
    assert len(CASES) >= 40
    assert {c["fn"] for c in CASES} == {"fin", "bridge_eps", "zone", "to_usd", "implied_pe",
                                        "scenario_value", "pct_diff", "pct_rank", "weighted_value",
                                        "scenario_state"}


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
