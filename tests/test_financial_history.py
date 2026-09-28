"""financial_history: expectations are hand-computed, never produced by the module itself."""
import json
import sqlite3
from datetime import date, timedelta
from pathlib import Path

import pandas as pd
import pytest

from terminal.financial_history import (
    apply_recorded_splits,
    assign_display_quarters,
    build_financial_history,
    compute_changes,
    detect_split_like_jumps,
    income_change,
    income_change_label,
    prepare_financial_history,
    quarterly_price_returns,
    select_future_estimates,
    _Live,
)

AS_OF = date(2026, 9, 28)


# ---------------------------------------------------------------------------
# Pure calculations
# ---------------------------------------------------------------------------

class TestIncomeChange:
    def test_profit_growth(self):
        kind, pct = income_change(100.0, 120.0)
        assert kind == "growth" and pct == pytest.approx(20.0)

    def test_loss_narrowed(self):
        kind, pct = income_change(-100.0, -60.0)
        assert kind == "loss_narrowed" and pct == pytest.approx(-40.0)
        assert income_change_label(kind, pct) == "亏损收窄40%"

    def test_loss_widened(self):
        kind, pct = income_change(-50.0, -75.0)
        assert kind == "loss_widened" and pct == pytest.approx(50.0)

    def test_turned_profit(self):
        assert income_change(-60.0, 20.0) == ("turned_profit", None)
        assert income_change_label("turned_profit", None) == "扭亏"

    def test_turned_loss(self):
        assert income_change(10.0, -5.0) == ("turned_loss", None)

    def test_zero_base(self):
        assert income_change(0.0, 10.0) == ("zero_base", None)


def _row(end, kind="actual", revenue=100.0, ni=10.0, rb="reported", nb="gaap"):
    return {"period_end": end, "kind": kind, "revenue": revenue, "net_income": ni,
            "revenue_basis": rb, "net_income_basis": nb}


class TestComputeChanges:
    def test_actual_gaap_to_unknown_forecast_is_na_then_forecasts_compare(self):
        rows = [
            _row("2026-03-31", revenue=1000.0, ni=100.0),
            _row("2026-06-30", kind="estimate", revenue=1100.0, ni=150.0, rb="consensus", nb="unknown"),
            _row("2026-09-30", kind="estimate", revenue=1210.0, ni=180.0, rb="consensus", nb="unknown"),
        ]
        compute_changes(rows)
        assert rows[0]["revenue_na_reason"] == "no_base"
        assert rows[1]["revenue_qoq"] == pytest.approx(10.0)
        assert rows[1]["net_income_qoq"] is None
        assert rows[1]["net_income_na_reason"] == "basis_break"
        assert rows[2]["net_income_qoq"] == pytest.approx(20.0)       # 150 → 180
        assert rows[2]["revenue_qoq"] == pytest.approx(10.0)          # 1100 → 1210

    def test_missing_quarter_is_gap_not_zero(self):
        rows = [_row("2025-03-31"), _row("2025-09-30", revenue=130.0)]
        compute_changes(rows)
        assert rows[1]["revenue_qoq"] is None
        assert rows[1]["revenue_na_reason"] == "gap"

    def test_zero_income_base(self):
        rows = [_row("2025-03-31", ni=0.0), _row("2025-06-30", ni=5.0)]
        compute_changes(rows)
        assert rows[1]["net_income_na_reason"] == "zero_base"
        assert rows[1]["net_income_qoq"] is None

    def test_loss_change_not_plotted_as_qoq(self):
        rows = [_row("2025-03-31", ni=-100.0), _row("2025-06-30", ni=-60.0)]
        compute_changes(rows)
        assert rows[1]["net_income_qoq"] is None
        assert rows[1]["income_change_type"] == "loss_narrowed"
        assert rows[1]["income_change_label"] == "亏损收窄40%"

    def test_gross_vs_street_revenue_is_basis_break(self):
        rows = [_row("2025-03-31"), _row("2025-06-30", rb="street_net")]
        compute_changes(rows)
        assert rows[1]["revenue_na_reason"] == "basis_break"


class TestSelectFutureEstimates:
    def test_skips_same_quarter_with_vendor_date_drift(self):
        actual_ends = [date(2026, 3, 29), date(2026, 6, 29)]
        est = [{"fiscal_date": d} for d in
               ("2026-06-30", "2026-09-28", "2026-12-28", "2027-03-29", "2027-06-28", "2027-09-27")]
        sel, notes = select_future_estimates(date(2026, 6, 29), actual_ends, est, 4)
        assert [r["fiscal_date"] for r in sel] == ["2026-09-28", "2026-12-28", "2027-03-29", "2027-06-28"]
        assert notes == []

    def test_only_two_quarters_available(self):
        sel, _ = select_future_estimates(
            date(2026, 6, 30), [date(2026, 6, 30)],
            [{"fiscal_date": "2026-09-30"}, {"fiscal_date": "2026-12-31"}], 4)
        assert len(sel) == 2

    def test_stops_at_discontinuity(self):
        sel, notes = select_future_estimates(
            date(2026, 6, 30), [date(2026, 6, 30)],
            [{"fiscal_date": "2026-09-30"}, {"fiscal_date": "2027-06-30"}], 4)
        assert [r["fiscal_date"] for r in sel] == ["2026-09-30"]
        assert notes


class TestDisplayQuarters:
    def test_52_53_week_ends_move_back_when_more_continuous(self):
        ends = [date(2025, 3, 28), date(2025, 6, 27), date(2025, 10, 3), date(2026, 1, 2), date(2026, 4, 3)]
        got = [(str(p), s) for p, s in assign_display_quarters(ends)]
        assert got == [("2025Q1", False), ("2025Q2", False), ("2025Q3", True),
                       ("2025Q4", True), ("2026Q1", True)]

    def test_offset_fiscal_year_not_shifted(self):
        # CRDO-style: Jan-31 / May-2 / Aug-1 / Nov-1 stay in their end quarter
        ends = [date(2025, 11, 1), date(2026, 1, 31), date(2026, 5, 2), date(2026, 8, 1)]
        got = [(str(p), s) for p, s in assign_display_quarters(ends)]
        assert got == [("2025Q4", False), ("2026Q1", False), ("2026Q2", False), ("2026Q3", False)]


class TestPriceReturns:
    def test_prior_quarter_end_to_quarter_end_and_flags(self):
        prices = [(date(2025, 12, 31), 100.0), (date(2026, 2, 1), 90.0), (date(2026, 3, 31), 110.0),
                  (date(2026, 5, 1), 120.0), (date(2026, 6, 30), 99.0), (date(2026, 9, 25), 132.0)]
        out = quarterly_price_returns(prices, pd.Period("2026Q1", "Q"), AS_OF)
        assert [q["quarter"] for q in out] == ["2026Q1", "2026Q2", "2026Q3"]
        assert out[0]["return_pct"] == pytest.approx(10.0)          # 100 → 110
        assert out[1]["return_pct"] == pytest.approx(-10.0)         # 110 → 99
        assert out[2]["return_pct"] == pytest.approx(33.3333, rel=1e-4)
        assert out[0]["flags"] == [] and out[2]["flags"] == ["QTD"]

    def test_ipo_quarter_partial(self):
        prices = [(date(2026, 2, 10), 20.0), (date(2026, 3, 31), 25.0), (date(2026, 6, 30), 30.0)]
        out = quarterly_price_returns(prices, pd.Period("2025Q4", "Q"), date(2026, 6, 30))
        assert out[0]["quarter"] == "2026Q1" and out[0]["flags"] == ["partial"]
        assert out[0]["return_pct"] == pytest.approx(25.0)
        assert out[1]["flags"] == [] and out[1]["return_pct"] == pytest.approx(20.0)


class TestSplits:
    def test_unadjusted_recorded_split_is_back_adjusted(self):
        prices = [(date(2026, 6, 10), 2100.0), (date(2026, 6, 11), 2400.0), (date(2026, 6, 12), 250.0)]
        adj, notes = apply_recorded_splits(prices, [{"date": "2026-06-12", "numerator": 10, "denominator": 1}])
        assert [c for _, c in adj] == [pytest.approx(210.0), pytest.approx(240.0), 250.0]
        assert notes

    def test_already_adjusted_series_untouched(self):
        prices = [(date(2026, 6, 11), 240.0), (date(2026, 6, 12), 250.0)]
        adj, notes = apply_recorded_splits(prices, [{"date": "2026-06-12", "numerator": 10, "denominator": 1}])
        assert adj == prices and notes == []

    def test_real_crash_is_not_a_split(self):
        # CRDO 2023-02-15: 19.36 → 10.30 (-47%) was a guidance crash
        assert detect_split_like_jumps([(date(2023, 2, 14), 19.36), (date(2023, 2, 15), 10.30)]) == []
        assert detect_split_like_jumps([(date(2024, 6, 7), 1200.0), (date(2024, 6, 10), 121.0)])


# ---------------------------------------------------------------------------
# Fixture market.db
# ---------------------------------------------------------------------------

SCHEMA = """
CREATE TABLE daily_price (symbol TEXT, date TEXT, close REAL, PRIMARY KEY (symbol, date));
CREATE TABLE income_quarterly (date TEXT, symbol TEXT, reported_currency TEXT, filing_date TEXT,
  accepted_date TEXT, fiscal_year TEXT, period TEXT, revenue REAL, net_income REAL, eps REAL,
  eps_diluted REAL, PRIMARY KEY (symbol, date));
CREATE TABLE fmp_estimates (symbol TEXT, snapshot_date TEXT, fiscal_date TEXT, period_type TEXT,
  snapshot_kind TEXT DEFAULT 'weekly', eps_avg REAL, eps_high REAL, eps_low REAL, rev_avg REAL,
  rev_high REAL, rev_low REAL, net_income_avg REAL, ebitda_avg REAL, num_analysts_eps INTEGER,
  num_analysts_rev INTEGER, PRIMARY KEY (symbol, snapshot_date, fiscal_date, period_type));
CREATE TABLE fmp_forward_runs (snapshot_date TEXT, run_kind TEXT, status TEXT,
  target_universe_json TEXT, target_count INTEGER, quarter_success INTEGER DEFAULT 0,
  quarter_failure_count INTEGER DEFAULT 0, started_at TEXT, completed_at TEXT, summary_json TEXT,
  PRIMARY KEY (snapshot_date, run_kind));
CREATE TABLE fmp_earnings (symbol TEXT, announce_date TEXT, fiscal_date TEXT, match_method TEXT,
  eps_actual REAL, eps_estimated REAL, revenue_actual REAL, revenue_estimated REAL,
  last_updated TEXT, PRIMARY KEY (symbol, announce_date));
CREATE TABLE fmp_stock_splits (symbol TEXT, date TEXT, numerator REAL, denominator REAL,
  split_type TEXT, source TEXT, fetched_at TEXT, PRIMARY KEY (symbol, date));
"""


def _quarter_ends(first: str, n: int):
    return [(pd.Period(first, "Q") + i).end_time.date() for i in range(n)]


def make_db(tmp_path, *, symbol="TST", n_quarters=24, first_q="2020Q3", revenue=None,
            net_income=None, earnings_revenue=None, estimates=None, price_start=date(2021, 1, 4),
            runs=None, skip=()):
    db = tmp_path / "market.db"
    conn = sqlite3.connect(db)
    conn.executescript(SCHEMA)
    ends = _quarter_ends(first_q, n_quarters)
    for i, end in enumerate(ends):
        if i in skip:
            continue
        rev = revenue[i] if revenue else 1000.0 + 10 * i
        ni = net_income[i] if net_income else 100.0 + i
        filing = end + timedelta(days=35)
        conn.execute("INSERT INTO income_quarterly VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                     (end.isoformat(), symbol, "USD", filing.isoformat(), f"{filing} 16:00:00",
                      str(end.year), f"Q{(end.month - 1) // 3 + 1}", rev, ni, 1.0, 1.0))
        street = earnings_revenue[i] if earnings_revenue else rev
        conn.execute("INSERT INTO fmp_earnings VALUES (?,?,?,?,?,?,?,?,?)",
                     (symbol, (end + timedelta(days=25)).isoformat(), end.isoformat(),
                      "estimates_window", 1.0, 0.9, street, street, "x"))
    d = price_start
    price = 50.0
    while d <= AS_OF - timedelta(days=3):
        if d.weekday() < 5:
            conn.execute("INSERT INTO daily_price VALUES (?,?,?)", (symbol, d.isoformat(), price))
            price *= 1.0005
        d += timedelta(days=1)
    for snap, status in (runs or [("2026-09-26", "complete")]):
        conn.execute("INSERT INTO fmp_forward_runs VALUES (?,?,?,?,?,?,?,?,?,?)",
                     (snap, "weekly", status, json.dumps([symbol]), 1, 1, 0, "s", "c", "{}"))
    for snap, fiscal, rev, ni in (estimates or []):
        conn.execute("""INSERT INTO fmp_estimates (symbol, snapshot_date, fiscal_date, period_type,
            snapshot_kind, eps_avg, rev_avg, net_income_avg, num_analysts_eps, num_analysts_rev)
            VALUES (?,?,?,?,?,?,?,?,?,?)""", (symbol, snap, fiscal, "Q", "weekly", 1.0, rev, ni, 20, 22))
    conn.commit()
    conn.close()
    return db


def _std_estimates(snap="2026-09-26"):
    # latest actual 2026-06-30 (24 quarters from 2020Q3); vendor dates drift by one day
    return [(snap, "2026-06-29", 1250.0, 130.0),          # same quarter as the reported 6/30 actual
            (snap, "2026-09-29", 1300.0, 150.0),
            (snap, "2026-12-30", 1365.0, 165.0),
            (snap, "2027-03-30", 1400.0, 150.0),
            (snap, "2027-06-29", 1470.0, 180.0),
            (snap, "2027-09-29", 1500.0, 190.0)]


def build(db, **kw):
    kw.setdefault("years", 5)
    return build_financial_history("TST", as_of=AS_OF, db_path=db, **kw)


class TestBuild:
    def test_normal_company_complete(self, tmp_path):
        db = make_db(tmp_path, estimates=_std_estimates())
        h = build(db)
        assert h["status"] == "complete", h["gaps"]
        est = [p for p in h["periods"] if p["kind"] == "estimate"]
        assert [p["period_end"] for p in est] == ["2026-09-29", "2026-12-30", "2027-03-30", "2027-06-29"]
        assert [(p["fiscal_year"], p["fiscal_quarter"]) for p in est] == [
            ("2026", "Q3"), ("2026", "Q4"), ("2027", "Q1"), ("2027", "Q2")]
        latest = [p for p in h["periods"] if p["kind"] == "actual"][-1]
        assert latest["period_end"] == "2026-06-30"
        assert latest["revenue"] == 1230.0                      # 1000 + 10*23
        # first forecast: revenue 1230 → 1300 is a current-snapshot comparison
        assert est[0]["revenue_qoq"] == pytest.approx((1300 / 1230 - 1) * 100)
        assert est[0]["net_income_na_reason"] == "basis_break"
        assert est[1]["net_income_qoq"] == pytest.approx(10.0)  # 150 → 165
        assert est[2]["net_income_qoq"] == pytest.approx((150 / 165 - 1) * 100)
        # window 2021Q4..: one base row before it, all rows ordered
        assert h["window_start_quarter"] == "2021Q4"
        assert h["periods"][0]["role"] == "qoq_base" and h["periods"][0]["display_quarter"] == "2021Q3"
        assert h["price"]["quarters"][-1]["flags"] == ["QTD"]
        assert h["estimate_snapshot"]["snapshot_date"] == "2026-09-26"

    def test_two_forecast_quarters_is_partial(self, tmp_path):
        db = make_db(tmp_path, estimates=_std_estimates()[:3])
        h = build(db)
        assert h["status"] == "partial"
        assert len([p for p in h["periods"] if p["kind"] == "estimate"]) == 2
        assert any("2/4" in g for g in h["gaps"])

    def test_incomplete_run_ignored_and_missing_symbol_falls_back(self, tmp_path):
        est = _std_estimates("2026-09-12")
        db = make_db(tmp_path, estimates=est,
                     runs=[("2026-09-26", "complete"), ("2026-09-19", "failed"), ("2026-09-12", "complete")])
        h = build(db)
        assert h["estimate_snapshot"]["snapshot_date"] == "2026-09-12"
        assert any("改用 2026-09-12" in w for w in h["warnings"])

    def test_missing_history_quarter_is_gap(self, tmp_path):
        db = make_db(tmp_path, estimates=_std_estimates(), skip=(15,))
        h = build(db)
        assert h["status"] == "partial"
        after = next(p for p in h["periods"] if p["period_end"] == "2024-09-30")
        assert after["revenue_na_reason"] == "gap" and after["revenue_qoq"] is None

    def test_ipo_short_history_partial(self, tmp_path):
        db = make_db(tmp_path, n_quarters=10, first_q="2024Q1", estimates=_std_estimates(),
                     price_start=date(2024, 2, 15))
        h = build(db)
        assert h["status"] == "partial"
        assert h["price"]["quarters"][0]["quarter"] == "2024Q1"
        assert h["price"]["quarters"][0]["flags"] == ["partial"]
        assert h["price"]["change"]["label"] == "可用行情以来"
        assert any("五年窗口" in g for g in h["gaps"])

    def test_bank_uses_street_net_revenue(self, tmp_path):
        gross = [2000.0 + 10 * i for i in range(24)]
        net = [1000.0 + 10 * i for i in range(24)]
        db = make_db(tmp_path, revenue=gross, earnings_revenue=net, estimates=_std_estimates())
        h = build(db, industry="Banks - Diversified")
        latest = [p for p in h["periods"] if p["kind"] == "actual"][-1]
        assert h["revenue_mode"] == "street_net"
        assert latest["revenue"] == 1230.0 and latest["gross_revenue"] == 2230.0
        assert latest["revenue_qoq"] == pytest.approx((1230 / 1220 - 1) * 100)

    def test_gross_net_mixing_detected_without_industry(self, tmp_path):
        gross = [2000.0 + 10 * i for i in range(24)]
        net = [1000.0 + 10 * i for i in range(24)]
        db = make_db(tmp_path, revenue=gross, earnings_revenue=net, estimates=_std_estimates())
        assert build(db)["revenue_mode"] == "street_net"

    def test_cross_profit_loss_labels(self, tmp_path):
        ni = [100.0] * 24
        ni[20], ni[21], ni[22], ni[23] = -100.0, -60.0, 20.0, 24.0
        db = make_db(tmp_path, net_income=ni, estimates=_std_estimates())
        rows = {p["period_end"]: p for p in build(db)["periods"]}
        assert rows["2025-09-30"]["income_change_type"] == "turned_loss"
        assert rows["2025-12-31"]["income_change_label"] == "亏损收窄40%"
        assert rows["2026-03-31"]["income_change_type"] == "turned_profit"
        assert rows["2026-06-30"]["net_income_qoq"] == pytest.approx(20.0)

    def test_unreleased_quarter_is_not_actual(self, tmp_path):
        db = make_db(tmp_path, estimates=_std_estimates())
        h = build_financial_history("TST", as_of=date(2026, 7, 20), db_path=db)
        actual = [p for p in h["periods"] if p["kind"] == "actual"]
        assert actual[-1]["period_end"] == "2026-03-31"          # 6/30 released 7/25, filed 8/4
        assert h["estimate_snapshot"] is None                     # no run on/before 7/20

    def test_release_before_filing_counts_as_reported(self, tmp_path):
        db = make_db(tmp_path, estimates=_std_estimates())
        h = build_financial_history("TST", as_of=date(2026, 8, 1), db_path=db)
        latest = [p for p in h["periods"] if p["kind"] == "actual"][-1]
        assert latest["period_end"] == "2026-06-30" and latest["net_income"] == 123.0

    def test_live_fills_announced_quarter_missing_from_db(self, tmp_path):
        db = make_db(tmp_path, estimates=_std_estimates(), skip=(23,))
        conn = sqlite3.connect(db)
        conn.execute("INSERT OR REPLACE INTO fmp_earnings VALUES ('TST','2026-07-25','2026-06-30',"
                     "'estimates_window',1.1,1.0,1230.0,1200.0,'x')")
        conn.commit()
        conn.close()

        class FakeClient:
            def get_income_statement(self, symbol, period, limit):
                return [{"date": "2026-06-30", "fiscalYear": "2026", "period": "Q2",
                         "filingDate": "2026-08-04", "acceptedDate": "2026-08-04 16:00:00",
                         "reportedCurrency": "USD", "revenue": 1230.0, "netIncome": 123.0}]

        sources = tmp_path / "chart_sources"
        h = build(db, live=_Live("TST", sources, FakeClient(), enabled=True))
        latest = [p for p in h["periods"] if p["kind"] == "actual"][-1]
        assert latest["period_end"] == "2026-06-30" and latest["net_income"] == 123.0
        assert latest["revenue_source"] == "fmp_live:income-statement"
        assert (sources / "fmp_income_statement.json").exists()
        assert [p["period_end"] for p in h["periods"] if p["kind"] == "estimate"][0] == "2026-09-29"

    def test_announced_but_unavailable_quarter_is_explicit_gap(self, tmp_path):
        db = make_db(tmp_path, estimates=_std_estimates(), skip=(23,))
        conn = sqlite3.connect(db)
        conn.execute("INSERT OR REPLACE INTO fmp_earnings VALUES ('TST','2026-07-25','2026-06-30',"
                     "'estimates_window',1.1,1.0,1230.0,1200.0,'x')")
        conn.commit()
        conn.close()
        h = build(db)                                       # live disabled
        latest = [p for p in h["periods"] if p["kind"] == "actual"][-1]
        assert latest["period_end"] == "2026-06-30" and latest["net_income"] is None
        assert h["status"] == "partial"
        assert [p["period_end"] for p in h["periods"] if p["kind"] == "estimate"][0] == "2026-09-29"


class TestPrepare:
    def test_writes_frozen_files_and_png(self, tmp_path):
        db = make_db(tmp_path, estimates=_std_estimates())
        rd = tmp_path / "research"
        out = prepare_financial_history("TST", rd, as_of=AS_OF, db_path=db, allow_live=False)
        assert out["status"] == "complete"
        for name in ("financial_history.json", "financial_history.csv", "financial_history.md",
                     "financial_history_manifest.json", "price_fundamentals_5y_4q.png"):
            assert (rd / name).exists(), name
        manifest = json.loads((rd / "financial_history_manifest.json").read_text())
        assert manifest["png"] == "price_fundamentals_5y_4q.png" and manifest["db_mode"] == "ro"
        md = (rd / "financial_history.md").read_text()
        assert "FY2026 Q3" in md and "口径切换" in md
        from PIL import Image
        with Image.open(rd / "price_fundamentals_5y_4q.png") as img:
            assert img.size[0] > 2500

    def test_unreadable_db_is_blocked_not_empty(self, tmp_path):
        empty = tmp_path / "market.db"
        empty.write_bytes(b"")
        out = prepare_financial_history("TST", tmp_path / "rd", as_of=AS_OF, db_path=empty, allow_live=False)
        assert out["status"] == "blocked"
        assert out["png_path"] is None
        assert "空壳" in out["gaps"][0]
