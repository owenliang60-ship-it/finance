import csv
import json
import subprocess
from datetime import date, timedelta

import pytest

from backtest.research import prosperity_s1 as s1
from tests.prosperity_fixtures import qends, qin

Q8 = qends(8)                      # 2024-09-30 … 2026-06-30
FLAT = {"revenue_yoy_pct": 0.0, "revenue_yoy_accel": 0.0, "gm_pct": 50.0, "gm_yoy_pp": 0.0,
        "fcf_margin_yoy_pp": 0.0, "net_margin_yoy_pp": 0.0}


def site_row(symbol="AAA", values=None, series=None, repairs=()):
    """One row in the original site's JSON shape; series default to a flat business (revenue 100M)."""
    s = dict(series or {})
    q = s.get("q", Q8)
    n = len(q)
    return {"symbol": symbol, "latest_q": q[-1], "filed": "2026-08-01", "q_series": list(q),
            "rev_abs_series": s.get("rev", [100.0] * n), "gm_series": s.get("gm", [50.0] * n),
            "fcfm_series": s.get("fcfm", [20.0] * n), "nim_series": s.get("nim", [10.0] * n),
            "dq": {"repairs": [{"quarter": d, "field": f} for d, f in repairs]},
            **FLAT, **(values or {})}


def fixture(*boards):
    return {"asof": "2026-09-02", "boards": [{"asof": a, "rows": rows} for a, rows in boards]}


def flat_quarters(n=8):
    return [qin(f, rev=100e6) for f in qends(n)]     # gross 50%, net 10%, FCF 20% of revenue


# ---- Task 1: the original site's rows ----

def test_load_site_rows_maps_symbols_and_factor_keys():
    data = fixture(("2026-06-30", [site_row("SQ"), site_row("BRK.B"), site_row("BRK.A"), site_row("MSFT")]))
    rows = s1.load_site_rows(data)
    assert [(r.site_symbol, r.symbol, r.skip_reason) for r in rows] == [
        ("SQ", "XYZ", None), ("BRK.B", "BRK-B", None), ("BRK.A", "BRK-A", "duplicate_share_class"),
        ("MSFT", "MSFT", None)]
    r = rows[-1]
    assert (r.board, r.latest_q) == ("2026-06-30", Q8[-1])
    assert r.values == {"revenue_yoy": 0.0, "revenue_accel": 0.0, "gm_level": 50.0, "gm_yoy": 0.0,
                        "fcf_margin_yoy": 0.0, "net_margin_yoy": 0.0}
    assert [q.fiscal_date for q in r.quarters] == Q8
    last = r.quarters[-1]
    assert (last.rev, last.gm, last.fcfm, last.nim) == (100.0, 50.0, 20.0, 10.0)


def test_load_site_rows_keeps_nulls_and_maps_repair_fields():
    row = site_row(values={"fcf_margin_yoy_pp": None}, repairs=[(Q8[-1], "gross_margin"), (Q8[-2], "mystery")])
    r = s1.load_site_rows(fixture(("2026-06-30", [row])))[0]
    assert r.values["fcf_margin_yoy"] is None
    assert r.repairs == ((Q8[-1], "gm"), (Q8[-2], "mystery"))


# ---- Task 2: our side of the same fiscal quarter ----

def test_match_index_pairs_the_same_fiscal_quarter_within_20_days():
    qs = flat_quarters()
    assert s1.match_index(qs, Q8[-1]) == 7
    assert s1.match_index(qs, "2026-06-12") == 7          # 18 days apart still pairs
    assert s1.match_index(qs, Q8[-2]) == 6                # ours is a quarter ahead: trim to index 6
    assert s1.match_index(qs, "2026-09-30") is None       # ours is behind


def test_timing_status():
    assert s1.timing_status("2026-06-30", "2026-06-25") == "match"
    assert s1.timing_status("2026-06-30", "2026-03-31") == "ours_ahead"
    assert s1.timing_status("2026-03-31", "2026-06-30") == "ours_behind"
    assert s1.timing_status(None, "2026-06-30") == "ours_none"


def test_our_side_computes_both_bases_and_input_series():
    qs = [qin(f, rev=r * 1e6) for f, r in zip(Q8, [100, 100, 100, 100, 100, 110, 130, 140])]
    qs[-1] = qin(Q8[-1], rev=140e6, gm=0.6, nm=0.08, fcfm=0.25, days=98)
    ours = s1.our_side(qs, aligned_by="asof")
    assert (ours.fiscal_date, ours.aligned_by) == (Q8[-1], "asof")
    assert len(ours.pairing_differs) == 6 and not any(ours.pairing_differs.values())
    assert ours.values["site"]["revenue_yoy"] == pytest.approx(40.0)          # unadjusted 140 vs 100
    assert ours.values["ours"]["revenue_yoy"] == pytest.approx(30.0)          # 140 × 91/98 = 130 vs 100
    assert ours.values["site"]["gm_yoy"] == pytest.approx(10.0)
    assert len(ours.quarters) == 8
    q = ours.quarters[-1]
    assert (q.fiscal_date, q.rev) == (Q8[-1], pytest.approx(140.0))            # millions
    assert (q.gm, q.fcfm, q.nim) == (pytest.approx(60.0), pytest.approx(25.0), pytest.approx(8.0))


def test_pairing_differs_when_the_year_ago_quarter_is_missing():
    qs = [q for i, q in enumerate(flat_quarters()) if i != 3]
    ours = s1.our_side(qs, aligned_by="asof")
    assert ours.pairing_differs["revenue_yoy"] is True and ours.pairing_differs["revenue_accel"] is True
    assert ours.pairing_differs["gm_level"] is False
    assert ours.values["ours"]["revenue_yoy"] is None and ours.missing["ours"]["revenue_yoy"] == "no_yoy_base"
    assert ours.values["site"]["revenue_yoy"] == pytest.approx(0.0)           # position takes quarters[-5]


# ---- Task 3: comparison and cause classification ----

def compare_one(values=None, series=None, repairs=(), quarters=None, ours_missing=""):
    site = s1.load_site_rows(fixture(("2026-06-30", [site_row(values=values, series=series, repairs=repairs)])))[0]
    ours = None if ours_missing else s1.our_side(quarters or flat_quarters(), aligned_by="asof")
    return {(c.factor, c.basis): c for c in s1.compare(site, ours, missing_reason=ours_missing)}


def test_matching_row_passes_every_factor_on_both_bases():
    out = compare_one()
    assert len(out) == 12
    assert all(c.counted and c.passed and c.category == "match" for c in out.values())
    assert {c.fiscal_date for c in out.values()} == {Q8[-1]}


def test_diff_is_ours_minus_site_and_the_bound_is_strict():
    out = compare_one(values={"gm_pct": 50.4, "gm_yoy_pp": 0.5})
    assert out[("gm_level", "site")].passed and out[("gm_level", "site")].diff == pytest.approx(-0.4)
    assert not out[("gm_yoy", "site")].passed                     # |diff| = 0.5 is not < 0.5


def test_inputs_agree_but_factor_differs_is_unexplained():
    assert compare_one(values={"revenue_yoy_pct": 3.0})[("revenue_yoy", "site")].category == "unexplained"


def test_input_difference_names_field_and_quarter():
    rev = [100.0] * 8
    rev[3] = 97.0                                                  # their year-ago revenue differs
    c = compare_one(values={"revenue_yoy_pct": 3.1}, series={"rev": rev})[("revenue_yoy", "site")]
    assert (c.category, c.detail) == ("input_diff", f"rev@{Q8[3]}")


def test_input_tolerance_absorbs_rounding():
    rev = [100.0] * 8
    rev[3] = 100.05                                                # one-decimal rounding on their side
    c = compare_one(values={"revenue_yoy_pct": 3.0}, series={"rev": rev})[("revenue_yoy", "site")]
    assert c.category == "unexplained"


def test_site_repair_explains_only_known_fields():
    known = compare_one(values={"gm_pct": 55.0}, repairs=[(Q8[-1], "gross_margin")])
    unknown = compare_one(values={"gm_pct": 55.0}, repairs=[(Q8[-1], "mystery")])
    assert known[("gm_level", "site")].category == "site_repaired"
    assert unknown[("gm_level", "site")].category == "unexplained"


def test_quarter_sequence_mismatch():
    qs = [q for i, q in enumerate(flat_quarters(9)) if i != 4]     # ours lacks 2025-06-30
    c = compare_one(values={"revenue_yoy_pct": 5.0}, quarters=qs)[("revenue_yoy", "site")]
    assert c.category == "quarter_sequence"


def test_our_missing_value_carries_the_engine_reason():
    qs = flat_quarters()
    qs[-1] = qin(Q8[-1], rev=100e6, fcfm=0.0)                        # FCF exactly 0: placeholder
    c = compare_one(quarters=qs)[("fcf_margin_yoy", "site")]
    assert (c.counted, c.passed, c.category, c.detail) == (True, False, "ours_missing", "fcf_zero_placeholder")


def test_quarter_not_found_fails_every_counted_factor():
    out = compare_one(ours_missing="quarter_not_found")
    assert len(out) == 12
    assert all(c.counted and not c.passed and (c.category, c.detail) == ("ours_missing", "quarter_not_found")
               for c in out.values())


def test_site_null_is_not_counted():
    c = compare_one(values={"fcf_margin_yoy_pp": None})[("fcf_margin_yoy", "site")]
    assert (c.counted, c.category, c.detail) == (False, "site_missing", "ours_has_value")


def test_basis_only_day_adjust():
    qs = flat_quarters()
    qs[-1] = qin(Q8[-1], rev=100e6, days=98)                         # 14-week quarter
    out = compare_one(quarters=qs)
    assert out[("revenue_yoy", "site")].passed
    assert out[("revenue_yoy", "ours")].category == "basis_only_day_adjust"


def test_basis_only_pairing():
    qs = [q for i, q in enumerate(flat_quarters()) if i != 3]
    out = compare_one(quarters=qs, series={"q": [q.fiscal_date for q in qs]})
    assert out[("revenue_yoy", "site")].passed
    c = out[("revenue_yoy", "ours")]
    assert (c.category, c.our_value) == ("basis_only_pairing", None)


def test_basis_split_is_judged_per_factor():
    # drop 2025-03-31: the current quarter keeps its year-ago base, only the prior quarter loses one
    qs = [q for i, q in enumerate(flat_quarters(9)) if i != 3]
    qs[-1] = qin(qs[-1].fiscal_date, rev=100e6, days=98)
    out = compare_one(quarters=qs, series={"q": [q.fiscal_date for q in qs]})
    assert out[("revenue_yoy", "site")].passed and out[("revenue_accel", "site")].passed
    assert out[("revenue_yoy", "ours")].category == "basis_only_day_adjust"
    assert out[("revenue_accel", "ours")].category == "basis_only_pairing"


# ---- Task 4: summary ----

def comp(board, factor="revenue_yoy", basis="site", passed=True, counted=True, category="match"):
    return s1.Comparison(board=board, symbol="AAA", site_symbol="AAA", fiscal_date="2026-06-30", factor=factor,
                         basis=basis, site_value=1.0 if counted else None, our_value=1.0,
                         diff=0.0 if passed else 1.0, counted=counted, passed=passed,
                         category=category, detail="", aligned_by="asof")


def test_summarize_gates_on_the_site_basis_since_2021():
    comps = ([comp("2021-03-31")] * 19 + [comp("2021-03-31", passed=False, category="input_diff")]
             + [comp("2020-12-31", passed=False, category="unexplained")] * 5
             + [comp("2021-03-31", basis="ours", passed=False, category="basis_only_day_adjust")] * 10
             + [comp("2021-03-31", counted=False, passed=False, category="site_missing")] * 3)
    timing = [("2021-03-31", "AAA", "match", "asof"), ("2021-03-31", "BBB", "ours_ahead", "trimmed"),
              ("2020-12-31", "AAA", "match", "asof")]
    excluded = [("2021-03-31", "MARA", "not_in_our_data"), ("2020-12-31", "MARA", "not_in_our_data")]
    s = s1.summarize(comps, site_rows={"2020-12-31": 2, "2021-03-31": 3}, excluded=excluded,
                     errors=[("2021-03-31", "CCC", "ValueError: bad row")], timing=timing, since="2021-01-01")
    g = s["gate"]
    assert (g["boards"], g["counted"], g["passed"], g["pass"]) == (1, 20, 19, True)
    assert g["rate"] == pytest.approx(0.95)
    assert s["by_factor"]["revenue_yoy"] == {"counted": 20, "passed": 19, "rate": pytest.approx(0.95)}
    assert s["by_board"]["2021-03-31"] == {"site_rows": 3, "compared": 1, "excluded": 1, "errors": 1,
                                           "reconciled": True, "counted": 20, "passed": 19,
                                           "rate": pytest.approx(0.95)}
    assert s["by_board"]["2020-12-31"]["reconciled"] is True and s["reconciled"] is True
    assert s["categories"]["site"] == {"match": 19, "input_diff": 1, "site_missing": 3}
    assert s["categories"]["ours"] == {"basis_only_day_adjust": 10}
    assert s["timing"] == {"match": 1, "ours_ahead": 1} and s["aligned_by"] == {"asof": 1, "trimmed": 1}
    assert (s["pre_since"]["counted"], s["pre_since"]["passed"]) == (5, 0)
    assert s["excluded"] == {"symbols": {"MARA": "not_in_our_data"}, "rows_all": 2, "rows_since": 1,
                             "list": [{"board": "2021-03-31", "site_symbol": "MARA", "reason": "not_in_our_data"},
                                      {"board": "2020-12-31", "site_symbol": "MARA", "reason": "not_in_our_data"}]}
    assert s["errors"] == [{"board": "2021-03-31", "site_symbol": "CCC", "message": "ValueError: bad row"}]


def test_gate_fails_below_95_percent():
    comps = [comp("2022-06-30")] * 18 + [comp("2022-06-30", passed=False, category="unexplained")] * 2
    s = s1.summarize(comps, site_rows={"2022-06-30": 1}, excluded=[], errors=[], timing=[], since="2021-01-01")
    assert s["gate"]["pass"] is False and s["gate"]["rate"] == pytest.approx(0.9)


def test_failure_list_names_rows_without_values():
    s = s1.summarize([comp("2022-06-30", passed=False, category="input_diff")], site_rows={"2022-06-30": 1},
                     excluded=[], errors=[], timing=[], since="2021-01-01")
    assert s["failures"] == [{"board": "2022-06-30", "symbol": "AAA", "site_symbol": "AAA",
                              "factor": "revenue_yoy", "category": "input_diff", "detail": ""}]
    assert "site_value" not in json.dumps(s) and "our_value" not in json.dumps(s)


def test_summary_markdown_lists_gate_and_failures():
    s = s1.summarize([comp("2022-06-30", passed=False, category="input_diff")], site_rows={"2022-06-30": 1},
                     excluded=[], errors=[], timing=[], since="2021-01-01")
    md = s1.summary_markdown(s)
    assert "未达标" in md and "| 2022-06-30 | AAA | revenue_yoy | input_diff |" in md


def test_a_board_whose_rows_do_not_add_up_is_not_reconciled():
    s = s1.summarize([comp("2022-06-30")], site_rows={"2022-06-30": 2}, excluded=[], errors=[], timing=[],
                     since="2021-01-01")
    assert s["by_board"]["2022-06-30"]["reconciled"] is False and s["reconciled"] is False
