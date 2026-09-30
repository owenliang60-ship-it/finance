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
