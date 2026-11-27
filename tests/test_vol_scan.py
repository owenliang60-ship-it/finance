"""Tests for terminal/options/vol_scan.py (pure) + scripts/vol_scan.py (CLI).

PRIVACY: synthetic data only. No real positions / amounts / sheet IDs.
"""
from __future__ import annotations

import json
import math
import os
import subprocess
import sys
from datetime import date, timedelta
from pathlib import Path

import pytest

from terminal.options.iv_solver import bs_price
from terminal.options import vol_scan as vs

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = ROOT / "tests" / "fixtures" / "vol_scan"
PY = sys.executable
AS_OF = date(2026, 11, 27)


def _bars(returns, start_close=100.0, amp=0.02, start=date(2025, 1, 2)):
    """Ascending OHLC bars; high/low = close*(1±amp/2)."""
    bars = [{"date": start.isoformat(), "open": start_close, "high": start_close,
             "low": start_close, "close": start_close}]
    c = start_close
    d = start
    for r in returns:
        d += timedelta(days=1)
        c = c * (1 + r)
        bars.append({"date": d.isoformat(), "open": c, "high": c * (1 + amp / 2),
                     "low": c * (1 - amp / 2), "close": c})
    return bars


# ── HV panel ──

class TestHvPanel:
    def test_constant_returns_equal_hv_all_windows(self):
        panel = vs.compute_hv_panel(_bars([0.02] * 80))
        hvs = [panel[k] for k in ("hv5", "hv10", "hv20", "hv30", "hv60")]
        assert all(h is not None for h in hvs)
        assert max(hvs) - min(hvs) < 1e-9

    def test_alternating_returns_magnitude(self):
        """±2% alternating: sample stdev ≈ 2% daily (n-1 factor only)."""
        panel = vs.compute_hv_panel(_bars([0.02, -0.02] * 40))
        assert panel["hv60"] == pytest.approx(0.02 * math.sqrt(252), rel=0.02)

    def test_split_day_flagged_and_excluded(self):
        base = vs.compute_hv_panel(_bars([0.02, -0.02] * 40))
        rets = [0.02, -0.02] * 40
        rets.insert(70, -0.50)
        panel = vs.compute_hv_panel(_bars(rets))
        assert len(panel["split_anomalies"]) == 1
        assert panel["split_anomalies"][0]["pct_change"] == pytest.approx(-0.50)
        assert panel["hv20"] == pytest.approx(base["hv20"], rel=1e-9)
        assert panel["hv60"] == pytest.approx(base["hv60"], rel=1e-9)

    def test_insufficient_samples_none(self):
        panel = vs.compute_hv_panel(_bars([0.01] * 8))
        assert panel["hv5"] is not None
        assert panel["hv10"] is None and panel["hv60"] is None

    def test_top2_var_share_and_recent_moves(self):
        rets = [0.01] * 18 + [0.10, -0.10]
        panel = vs.compute_hv_panel(_bars(rets))
        # two big moves dominate the 20d window
        assert panel["hv20_top2_var_share"] > 0.9
        assert len(panel["recent_daily_moves"]) == 5
        assert panel["recent_daily_moves"][-1]["pct_change"] == pytest.approx(-0.10)
        assert panel["latest_price_date"] == panel["recent_daily_moves"][-1]["date"]


class TestRangeContraction:
    def test_metrics(self):
        rets = [0.06] + [0.01] * 300
        bars = _bars(rets, amp=0.03)
        for b in bars[-5:]:
            b["high"], b["low"] = b["close"] * 1.005, b["close"] * 0.995
        rc = vs.compute_range_contraction(bars)
        assert rc["consec_no_5pct_close"] == 300          # the +6% close breaks it
        assert rc["consec_no_5pct_intraday_amp"] == 301   # that day's range is only ~3%
        assert rc["range_5d_avg"] < rc["range_20d_avg"]
        assert rc["range_5d_over_20d"] < 1
        assert rc["range_5d_1y_pctl"] is not None and rc["range_5d_1y_pctl"] < 5

    def test_short_history_pctl_none(self):
        rc = vs.compute_range_contraction(_bars([0.01] * 30))
        assert rc["range_5d_1y_pctl"] is None

    def test_split_day_excluded(self):
        rets = [0.01] * 30
        rets[20] = -0.5
        rc = vs.compute_range_contraction(_bars(rets))
        assert rc["consec_no_5pct_close"] == 29


# ── chain helpers ──

def _c(exp, dte, side, strike, iv, delta=None, bid=1.0, ask=1.1, oi=1000, vol=100):
    return {"expiration": exp, "dte": dte, "side": side, "strike": strike, "iv": iv,
            "delta": delta, "bid": bid, "ask": ask, "open_interest": oi, "volume": vol}


class TestAtmIv:
    def test_interpolates_and_averages_sides(self):
        exp = (AS_OF + timedelta(days=30)).isoformat()
        contracts = [_c(exp, 30, "call", 95, 0.40), _c(exp, 30, "call", 105, 0.30),
                     _c(exp, 30, "put", 95, 0.42), _c(exp, 30, "put", 105, 0.32)]
        out = vs.atm_iv_by_expiry(contracts, 100.0, AS_OF)
        row = out["rows"][0]
        assert row["dte"] == 30
        assert row["atm_iv"] == pytest.approx(0.36)
        assert row["atm_iv_source"] == "call_put_avg"
        assert out["iv_30d_live"] == pytest.approx(0.36)

    def test_single_side_flagged(self):
        exp = (AS_OF + timedelta(days=30)).isoformat()
        contracts = [_c(exp, 30, "call", 95, 0.40), _c(exp, 30, "call", 105, 0.30)]
        row = vs.atm_iv_by_expiry(contracts, 100.0, AS_OF)["rows"][0]
        assert row["atm_iv"] == pytest.approx(0.35)
        assert row["atm_iv_source"] == "call_only"

    def test_total_variance_30d_interpolation(self):
        e1 = (AS_OF + timedelta(days=20)).isoformat()
        e2 = (AS_OF + timedelta(days=50)).isoformat()
        contracts = [_c(e1, 20, s, 100, 0.40) for s in ("call", "put")] + \
                    [_c(e2, 50, s, 100, 0.30) for s in ("call", "put")]
        out = vs.atm_iv_by_expiry(contracts, 100.0, AS_OF)
        w1, w2 = 0.40 ** 2 * 20 / 365, 0.30 ** 2 * 50 / 365
        w30 = w1 + (w2 - w1) * (30 - 20) / (50 - 20)
        assert out["iv_30d_live"] == pytest.approx(math.sqrt(w30 / (30 / 365)))

    def test_no_bracket_gives_none_with_reason(self):
        e1 = (AS_OF + timedelta(days=40)).isoformat()
        contracts = [_c(e1, 40, s, 100, 0.40) for s in ("call", "put")]
        out = vs.atm_iv_by_expiry(contracts, 100.0, AS_OF)
        assert out["iv_30d_live"] is None
        assert out["iv_30d_live_reason"] == "no_bracketing_expiries"


class TestTermStructure:
    def test_forward_vol_and_backwardation(self):
        rows = [{"expiration": "2026-12-27", "dte": 30, "atm_iv": 0.40},
                {"expiration": "2027-01-26", "dte": 60, "atm_iv": 0.30}]
        ts = vs.term_structure_metrics(rows, [])
        assert ts["state"] == "BACKWARDATION"
        assert ts["front_minus_back_pt"] == pytest.approx(10.0)
        assert ts["forwards"][0]["forward_iv"] == pytest.approx(0.1414, abs=1e-3)
        assert ts["inversion_source"] == "NO_IDENTIFIED_EVENT"

    def test_negative_forward_variance_bad_quote(self):
        rows = [{"expiration": "2026-12-27", "dte": 30, "atm_iv": 0.60},
                {"expiration": "2027-01-26", "dte": 60, "atm_iv": 0.40}]
        fwd = vs.term_structure_metrics(rows, [])["forwards"][0]
        assert fwd["forward_iv"] is None
        assert fwd["flag"] == "BAD_QUOTE"

    def test_states_and_event_source(self):
        flat = [{"expiration": "2026-12-27", "dte": 30, "atm_iv": 0.30},
                {"expiration": "2027-01-26", "dte": 60, "atm_iv": 0.305}]
        assert vs.term_structure_metrics(flat, [])["state"] == "FLAT"
        cont = [{"expiration": "2026-12-27", "dte": 30, "atm_iv": 0.30},
                {"expiration": "2027-01-26", "dte": 60, "atm_iv": 0.35}]
        assert vs.term_structure_metrics(cont, [])["state"] == "CONTANGO"
        back = [{"expiration": "2026-12-27", "dte": 30, "atm_iv": 0.40},
                {"expiration": "2027-01-26", "dte": 60, "atm_iv": 0.30}]
        ev_c = [{"symbol": "MU", "earnings_date": "2026-12-17", "date_status": "CONFIRMED"}]
        ev_e = [{"symbol": "MU", "earnings_date": "2026-12-17", "date_status": "ESTIMATED"}]
        ts_c = vs.term_structure_metrics(back, ev_c)
        assert ts_c["inversion_source"] == "CONFIRMED_EVENT"
        assert ts_c["inversion_events"] == [{"symbol": "MU", "earnings_date": "2026-12-17",
                                             "date_status": "CONFIRMED"}]
        assert vs.term_structure_metrics(back, ev_e)["inversion_source"] == "ESTIMATED_EVENT"


class TestChainSkew:
    def test_picks_closest_to_30_in_window(self):
        contracts = [
            _c("a", 25, "put", 90, 0.40, -0.25), _c("a", 25, "call", 110, 0.30, 0.25),
            _c("b", 44, "put", 90, 0.50, -0.25), _c("b", 44, "call", 110, 0.30, 0.25),
            _c("c", 10, "put", 90, 0.90, -0.25), _c("c", 10, "call", 110, 0.30, 0.25),
        ]
        sk = vs.compute_chain_25d_skew(contracts)
        assert sk["expiration"] == "a"
        assert sk["skew_25d_pt"] == pytest.approx(10.0)

    def test_none_outside_window(self):
        contracts = [_c("c", 10, "put", 90, 0.9, -0.25), _c("c", 10, "call", 110, 0.3, 0.25)]
        assert vs.compute_chain_25d_skew(contracts) is None


class TestIvHistory:
    def _rows(self, last, n):
        return [{"date": (last - timedelta(days=i)).isoformat(), "iv_30d": 0.30 + 0.001 * (i % 50)}
                for i in range(n)]

    def test_stale_history_no_rank(self):
        ev = vs.evaluate_iv_history(self._rows(date(2026, 8, 21), 252), 0.35, AS_OF)
        assert ev["freshness"]["status"] == "STALE"
        assert ev["iv_30d_rank_252d"] is None
        assert ev["iv_30d_pctl_252d"] is None
        assert "STALE" in ev["reason"]

    def test_fresh_full_history_ranks(self):
        ev = vs.evaluate_iv_history(self._rows(date(2026, 11, 26), 252), 0.35, AS_OF)
        assert ev["freshness"]["status"] == "FRESH"
        assert ev["iv_30d_rank_252d"] is not None
        assert 0 <= ev["iv_30d_pctl_252d"] <= 100
        assert ev["reason"] is None

    def test_too_few_rows(self):
        ev = vs.evaluate_iv_history(self._rows(date(2026, 11, 26), 50), 0.35, AS_OF)
        assert ev["iv_30d_rank_252d"] is None
        assert "rows" in ev["reason"]

    def test_missing(self):
        ev = vs.evaluate_iv_history([], 0.35, AS_OF)
        assert ev["freshness"]["status"] == "MISSING"
        assert ev["iv_30d_rank_252d"] is None


class TestSectorEvents:
    def test_positions(self):
        exps = ["2026-12-18", "2027-01-15"]
        own = {"earnings_date": None, "date_status": "NOT_APPLICABLE", "zone": "CLEAR"}
        sector = {
            "MU": {"earnings_date": "2026-12-17", "date_status": "CONFIRMED", "zone": "CAUTION"},
            "NVDA": {"earnings_date": "2027-02-25", "date_status": "ESTIMATED", "zone": "CLEAR"},
            "AMD": {"earnings_date": "2027-01-05", "date_status": "ESTIMATED", "zone": "CLEAR"},
            "TSM": {"earnings_date": None, "date_status": "UNKNOWN", "zone": "UNKNOWN"},
        }
        out = vs.build_sector_event_flags("SOXX", own, sector, exps)
        pos = {e["symbol"]: e["position"] for e in out["events"]}
        assert pos["MU"] == "BEFORE_FIRST_EXPIRY"
        assert pos["AMD"] == "BETWEEN_EXPIRIES"
        assert pos["NVDA"] == "AFTER_LAST_EXPIRY"
        assert out["unknown"] == ["TSM"]


# ── calendar template + strategy grid ──

def _nvda_calendar_legs(spread=0.10):
    """Synthetic NVDA-like $150 calendar: 28D / 50D, both 45%, quotes at BS ± spread/2."""
    r = 0.04
    s_px = bs_price(150.0, 150.0, 28 / 365.0, r, 0.45, "call")
    l_px = bs_price(150.0, 150.0, 50 / 365.0, r, 0.45, "call")
    e1 = (AS_OF + timedelta(days=28)).isoformat()
    e2 = (AS_OF + timedelta(days=50)).isoformat()
    return [
        {"expiration": e1, "side": "call", "strike": 150.0, "qty": -1, "iv": 0.45,
         "bid": s_px - spread / 2, "ask": s_px + spread / 2, "open_interest": 5000, "volume": 900},
        {"expiration": e2, "side": "call", "strike": 150.0, "qty": 1, "iv": 0.45,
         "bid": l_px - spread / 2, "ask": l_px + spread / 2, "open_interest": 4000, "volume": 700},
    ], r


def _cell(grid, scenario, day, label):
    return next(c for c in grid["grid"] if c["scenario"] == scenario
                and c["day"] == day and c["price_label"] == label)


class TestStrategyGrid:
    def test_front_minus_10pt_hold_7_matches_hand_bs(self):
        legs, r = _nvda_calendar_legs()
        e1 = legs[0]["expiration"]
        grid = vs.evaluate_strategy_grid(legs, 150.0, r, AS_OF,
                                         iv_scenarios={"front_minus_10": {e1: -0.10}})
        cell = _cell(grid, "front_minus_10", 7, "0%")
        mid_entry = (-1) * (legs[0]["bid"] + legs[0]["ask"]) / 2 + (legs[1]["bid"] + legs[1]["ask"]) / 2
        value = -bs_price(150.0, 150.0, 21 / 365.0, r, 0.35, "call") + \
            bs_price(150.0, 150.0, 43 / 365.0, r, 0.45, "call")
        assert abs(cell["pnl_mid"] - (value - mid_entry)) < 0.01

    def test_nvda_calendar_negative_at_t0_positive_after_7d(self):
        legs, r = _nvda_calendar_legs()
        grid = vs.evaluate_strategy_grid(legs, 150.0, r, AS_OF)
        t0 = _cell(grid, "unchanged", 0, "0%")
        t7 = _cell(grid, "unchanged", 7, "0%")
        assert t0["pnl_natural"] < 0
        assert t0["pnl_mid"] == pytest.approx(0.0, abs=1e-6)
        assert t7["pnl_mid"] > 0
        assert t7["pnl_natural"] > 0

    def test_entry_slippage_greeks_and_assumptions(self):
        legs, r = _nvda_calendar_legs(spread=0.20)
        grid = vs.evaluate_strategy_grid(legs, 150.0, r, AS_OF)
        assert grid["entry_natural_per_share"] - grid["entry_mid_per_share"] == pytest.approx(0.20)
        assert grid["slippage_per_share"] == pytest.approx(0.20)
        assert grid["net_greeks"]["theta_daily"] > 0       # short front decays faster
        assert grid["net_greeks"]["vega_1pt"] > 0          # long back vega dominates
        assert len(grid["per_leg_liquidity"]) == 2
        assert grid["model_assumptions"] == "European Black-Scholes, zero dividend, no early exercise"

    def test_time_points_and_default_scenarios(self):
        legs, r = _nvda_calendar_legs()
        grid = vs.evaluate_strategy_grid(legs, 150.0, r, AS_OF)
        assert grid["time_points"] == [0, 1, 7, 14, 27]
        assert set(grid["scenarios"]) == {"unchanged", "front_only_to_back",
                                          "parallel_minus_10pt", "both_to_55", "both_to_45"}
        labels = {c["price_label"] for c in grid["grid"]}
        assert {"0%", "+5%", "-5%", "+10%", "-10%", "+1σ", "-1σ", "+2σ", "-2σ"} <= labels

    def test_calendar_breakevens(self):
        legs, r = _nvda_calendar_legs()
        be = vs.evaluate_strategy_grid(legs, 150.0, r, AS_OF)["breakeven_at_short_expiry"]
        lo, hi = be["back_iv_unchanged"]
        assert lo < 150.0 < hi
        lo2, hi2 = be["back_iv_minus_20pt"]
        assert lo < lo2 < hi2 < hi   # lower back IV narrows the profit zone

    def test_single_expiry_breakeven_at_expiry(self):
        e1 = (AS_OF + timedelta(days=30)).isoformat()
        legs = [{"expiration": e1, "side": "call", "strike": 100.0, "qty": 1, "iv": 0.3,
                 "bid": 3.9, "ask": 4.1, "open_interest": 100, "volume": 10}]
        be = vs.evaluate_strategy_grid(legs, 100.0, 0.04, AS_OF)["breakeven_at_short_expiry"]
        assert be["at_expiry"] == [pytest.approx(104.0, abs=0.01)]


class TestCalendarTemplate:
    def test_builds_atm_calendar(self):
        contracts = []
        for exp, dte in (("e10", 10), ("e30", 31), ("e60", 59), ("e120", 120)):
            for k in (95.0, 100.0, 105.0):
                contracts.append(_c(exp, dte, "call", k, 0.4, bid=2.0, ask=2.2))
        tpl = vs.build_atm_calendar_template(contracts, 101.0)
        assert tpl["available"] is True
        assert tpl["strike"] == 100.0
        assert [l["expiration"] for l in tpl["legs"]] == ["e30", "e60"]
        assert [l["qty"] for l in tpl["legs"]] == [-1, 1]

    def test_missing_long_leg(self):
        contracts = [_c("e30", 31, "call", 100.0, 0.4)]
        tpl = vs.build_atm_calendar_template(contracts, 100.0)
        assert tpl["available"] is False

    def test_invalid_quote(self):
        contracts = [_c("e30", 31, "call", 100.0, 0.4, bid=0.0, ask=0.0),
                     _c("e60", 59, "call", 100.0, 0.4)]
        tpl = vs.build_atm_calendar_template(contracts, 100.0)
        assert tpl["available"] is False


# ── CLI: scripts/vol_scan.py ──

def _env(**extra):
    env = {k: v for k, v in os.environ.items()
           if k not in ("FINANCE_ENV", "PORTFOLIO_SHEET_ID")}
    env.update({"FMP_API_KEY": "", "MARKETDATA_API_KEY": ""})
    env.update(extra)
    return env


def _run(args, **env):
    return subprocess.run([PY, str(ROOT / "scripts" / "vol_scan.py")] + args,
                          cwd=str(ROOT), capture_output=True, text=True,
                          env=_env(**env), timeout=120)


def _make_market_db(path):
    import sqlite3
    conn = sqlite3.connect(path)
    conn.executescript(
        "CREATE TABLE daily_price (symbol TEXT, date TEXT, open REAL, high REAL,"
        " low REAL, close REAL, volume REAL, change REAL, change_pct REAL);"
        "CREATE TABLE iv_daily (symbol TEXT, date TEXT, iv_30d REAL, iv_60d REAL,"
        " hv_30d REAL, put_call_ratio REAL, total_volume INTEGER, total_oi INTEGER,"
        " created_at TEXT);")
    d0 = date(2025, 10, 1)
    c = 500.0
    for i in range(300):
        d = d0 + timedelta(days=i * 7 // 5)
        c *= 1.002 if i % 2 else 0.999
        conn.execute("INSERT INTO daily_price VALUES ('QQQ',?,?,?,?,?,1e6,0,0)",
                     (d.isoformat(), c, c * 1.01, c * 0.99, c))
    for i in range(252):  # iv_daily stops at 2026-08-21 -> STALE
        d = date(2026, 8, 21) - timedelta(days=i)
        conn.execute("INSERT INTO iv_daily (symbol, date, iv_30d, created_at)"
                     " VALUES ('QQQ', ?, ?, 'x')", (d.isoformat(), 0.2 + 0.0005 * (i % 40)))
    conn.commit()
    conn.close()


class TestCli:
    def test_local_without_chain_source_refuses(self):
        res = _run(["--symbols", "QQQ"])
        assert res.returncode == 2
        assert "ssh aliyun" in res.stderr
        assert "--chain-dir" in res.stderr and "--no-chain" in res.stderr

    def test_offline_chain_dir_json(self, tmp_path):
        res = _run(["--chain-dir", str(FIXTURES), "--json",
                    "--market-db", str(tmp_path / "missing.db")])
        assert res.returncode == 0, res.stderr
        out = json.loads(res.stdout)
        assert set(out["symbols"]) == {"QQQ", "SOXX", "NVDA", "MU", "AMD", "TSM"}
        q = out["symbols"]["QQQ"]
        assert q["chain"]["status"] == "REPLAY"
        assert q["term_structure"]["available"] is True
        # No FMP key -> lookup failure -> UNKNOWN even for an ETF (only an FMP []
        # proves "no earnings"); confirmed-file entries still apply.
        assert q["gates"]["earnings"] == "UNKNOWN"
        assert out["symbols"]["NVDA"]["gates"]["earnings"] == "UNKNOWN"
        assert out["symbols"]["MU"]["earnings"]["date_status"] == "CONFIRMED"
        assert out["symbols"]["NVDA"]["chain"]["status"] == "MISSING"
        assert q["gates"]["price_data"] == "MISSING"
        assert out["meta"]["market_db"]["status"] == "MISSING"

    def test_stale_iv_daily_gate_and_no_rank(self, tmp_path):
        db = tmp_path / "market.db"
        _make_market_db(str(db))
        before = db.read_bytes()
        res = _run(["--chain-dir", str(FIXTURES), "--json", "--symbols", "QQQ",
                    "--market-db", str(db)])
        assert res.returncode == 0, res.stderr
        q = json.loads(res.stdout)["symbols"]["QQQ"]
        assert q["gates"]["iv"] == "STALE"
        assert q["iv_history"]["iv_30d_rank_252d"] is None
        assert q["hv"]["hv20"] is not None
        assert db.read_bytes() == before                     # read-only

    def test_custom_legs(self, tmp_path):
        fx = json.loads((FIXTURES / "QQQ.json").read_text())
        exps = sorted(set(fx["data"]["expiration"]))
        legs = "QQQ:%s:C:500:-1;QQQ:%s:C:500:+1" % (exps[1], exps[2])
        res = _run(["--chain-dir", str(FIXTURES), "--json", "--symbols", "QQQ",
                    "--legs", legs, "--market-db", str(tmp_path / "missing.db")])
        assert res.returncode == 0, res.stderr
        custom = json.loads(res.stdout)["symbols"]["QQQ"]["custom_structure"]
        assert custom["available"] is True
        assert [l["qty"] for l in custom["grid"]["legs"]] == [-1, 1]

    def test_bad_legs_rejected(self, tmp_path):
        res = _run(["--chain-dir", str(FIXTURES), "--symbols", "QQQ",
                    "--legs", "QQQ:bad", "--market-db", str(tmp_path / "x.db")])
        assert res.returncode == 2
