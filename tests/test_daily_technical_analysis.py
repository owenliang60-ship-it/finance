#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Unit tests for fetch_vol_env.py math, sentinel guards, and fail-closed logic."""

import datetime as dt
import json
import math
import re
import subprocess
import unittest
from pathlib import Path
import pandas as pd
import pytest

from portfolio.holdings.sheet_book import (
    SHEET_PRICED_US_SYMBOLS,
    SheetBook,
    SheetBookError,
    SheetHolding,
    parse_sheet_book,
)
from scripts.daily_technical_analysis import chunk_telegram as ct
from scripts.daily_technical_analysis import fetch_vol_env as m
from scripts.daily_technical_analysis import render_ta_html as rhtml


class TestVolEnv(unittest.TestCase):
    def test_calc_percentile_exact_formula(self):
        vals = [10.0, 20.0, 30.0, 40.0, 50.0]
        self.assertEqual(m.calc_percentile(vals, 10.0), 20.0)
        self.assertEqual(m.calc_percentile(vals, 30.0), 60.0)
        self.assertEqual(m.calc_percentile(vals, 50.0), 100.0)
        self.assertIsNone(m.calc_percentile([], 10.0))

    def test_parse_dvol_2y_separates_forming_bar_and_checks_min_days(self):
        day = m.DAY_MS
        today_ms = 20_000 * day
        now_ms = today_ms + 3600_000  # 01:00 UTC
        # 730 closed bars ending yesterday + 1 forming bar today
        rows = []
        for i in range(730):
            ts = today_ms - (730 - i) * day
            rows.append([ts, 40, 45, 38, 35.0 + (i / 730.0) * 30.0])
        rows.append([today_ms, 65, 99, 60, 99.9])  # forming intraday bar must NOT pollute 2Y pct
        # Add epoch sentinel row to verify filtering
        rows.insert(0, [0, 10, 10, 10, 1.0])

        res = m.parse_dvol_2y(rows, now_ms=now_ms)
        self.assertTrue(res["available"])
        self.assertEqual(res["dvol_2y_days"], 730)
        self.assertEqual(res["dvol_2y_pct"], 100.0)
        self.assertEqual(res["dvol_live_intraday"], 99.9)
        self.assertLess(res["dvol"], 66.0)

        # Insufficient sample must fail closed
        short_res = m.parse_dvol_2y(rows[-100:], now_ms=now_ms)
        self.assertFalse(short_res["available"])
        self.assertIn("样本不足", short_res["reason"])

    def test_parse_cnn_fng_sentinel_and_dedup(self):
        today = dt.date(2026, 11, 26)
        base_dt = dt.datetime(2026, 11, 26, 0, 0, tzinfo=m.UTC)
        hist = [{"x": 0, "y": 99.0}]  # 1970 epoch sentinel
        for i in range(520):
            d = base_dt - dt.timedelta(days=519 - i)
            score = 50.0 if i < 519 else 15.0
            hist.append({"x": d.timestamp() * 1000.0, "y": score})

        payload = {
            "fear_and_greed": {
                "score": 15.0,
                "rating": "extreme fear",
                "timestamp": "2026-11-26T00:05:00+00:00",  # 2026-11-25 19:05 ET
                "previous_1_week": 22.4,
                "previous_1_month": 48.1,
            },
            "fear_and_greed_historical": {"data": hist},
        }
        res = m.parse_cnn_fng_2y(payload, today_et=today)
        self.assertTrue(res["available"])
        self.assertEqual(res["as_of_date"], "2026-11-25")
        self.assertEqual(res["score"], 15.0)
        self.assertEqual(res["rating_cn"], "极度恐慌")
        self.assertAlmostEqual(res["pct_2y"], round(1 / 520 * 100, 1), places=1)
        self.assertEqual(res["max_2y"], 50.0)  # sentinel 99.0 excluded

    def test_split_us_closed_bars_handles_cboe_extended_hours_vs_post_close(self):
        dates = ["2026-11-24", "2026-11-25", "2026-11-26"]
        closes = [25.0, 28.29, 27.09]
        # 03:45 AM ET (16:45 SGT): 2026-11-26 is still forming -> pop to live_intraday
        d1, c1, live1 = m.split_us_closed_bars(dates, closes, dt.datetime(2026, 11, 26, 3, 45, tzinfo=m.ET_TZ))
        self.assertEqual(d1[-1], "2026-11-25")
        self.assertEqual(c1[-1], 28.29)
        self.assertEqual(live1, 27.09)
        # 19:05 PM ET (08:05 next morning SGT): 2026-11-26 regular session closed -> keep as closed
        d2, c2, live2 = m.split_us_closed_bars(dates, closes, dt.datetime(2026, 11, 26, 19, 5, tzinfo=m.ET_TZ))
        self.assertEqual(d2[-1], "2026-11-26")
        self.assertEqual(c2[-1], 27.09)
        self.assertIsNone(live2)

    def test_telegram_chunker_preserves_paragraphs_and_balances_markdown(self):
        sec1 = "*【一、总览】*\n• Wilder_ATR14 正常 `code` [link] **加粗测试**\n• 未闭合星号 *测试"
        sec2 = "*【二、奶牛 & 余人推演】*\n" + ("• 详细推演内容行测试正常分段不截断实体\n" * 120)
        sec3 = "*【三、SOXX 面板】*\n• 趋势：*双空共振*"
        full = f"{sec1}\n\n{sec2}\n\n{sec3}"
        chunks = ct.split_paragraphs_safe(full, max_len=1500)
        self.assertGreaterEqual(len(chunks), 2)
        for ch in chunks:
            self.assertLessEqual(len(ch), 1500)
            self.assertNotIn("_", ch)
            self.assertNotIn("`", ch)
            self.assertNotIn("[", ch)
            for line in ch.splitlines():
                self.assertEqual(line.count("*") % 2, 0)

    def test_atm_iv_30d_variance_interpolation_and_bs_fallback(self):
        spot = 100.0
        today = dt.date(2026, 11, 26)
        exps = ["2026-12-18", "2027-01-08"]  # 22d and 43d

        def loader(exp: str):
            dte = (dt.date.fromisoformat(exp) - today).days
            true_iv = 0.40 if dte == 22 else 0.50
            t_yr = dte / 365.0
            call_p = m._bs_call_put_price(spot, 100.0, t_yr, true_iv, is_call=True)
            put_p = m._bs_call_put_price(spot, 100.0, t_yr, true_iv, is_call=False)
            # Simulate Yahoo off-hours bug where impliedVolatility=1e-5 and bid=0, testing BS fallback on lastPrice
            calls = pd.DataFrame([{"strike": 100.0, "impliedVolatility": 1e-5, "bid": 0.0, "ask": 0.0, "lastPrice": call_p}])
            puts = pd.DataFrame([{"strike": 100.0, "impliedVolatility": 1e-5, "bid": 0.0, "ask": 0.0, "lastPrice": put_p}])
            return calls, puts

        res = m.compute_atm_iv_30d_from_chains(spot, today, exps, loader)
        self.assertTrue(res["available"])
        expected_var30 = (0.40 ** 2) * 22 + ((30 - 22) / (43 - 22)) * ((0.50 ** 2) * 43 - (0.40 ** 2) * 22)
        expected_iv30 = math.sqrt(expected_var30 / 30.0) * 100.0
        self.assertAlmostEqual(res["atm_iv_30d"], round(expected_iv30, 2), delta=0.15)

    def test_compute_position_mgmt_slow_layer_ema20_and_extreme_d(self):
        # Build 400 daily bars (~57 weeks) with a steady uptrend then 2 sharp down days below EMA20
        start = dt.date(2025, 10, 1)
        dates, highs, lows, closes = [], [], [], []
        for i in range(400):
            d = start + dt.timedelta(days=i)
            c = 100.0 + i * 0.5
            if i >= 398:
                c = 250.0  # drop well below EMA20 (~294) but still above 50W SMA (~210)
            dates.append(d.isoformat())
            highs.append(c + 2.0)
            lows.append(c - 2.0)
            closes.append(c)

        res = m.compute_position_mgmt(dates, highs, lows, closes)
        self.assertTrue(res["available"])
        self.assertTrue(res["slow_layer"]["available"])
        self.assertTrue(res["slow_layer"]["slow_bull"])
        self.assertEqual(res["slow_layer"]["budget_long_pct"], 4.5)
        self.assertEqual(res["slow_layer"]["budget_short_pct"], 2.5)
        self.assertEqual(res["days_below_ema20"], 2)
        self.assertEqual(res["days_above_ema20"], 0)
        self.assertIn("D_minus_5_5", res["extreme_price_levels"])
        self.assertLess(res["extreme_price_levels"]["D_minus_5_5"], res["extreme_price_levels"]["D_minus_4_8"])


# ---------------------------------------------------------------------------
# Task 2: Active offensive holdings extraction & privacy sanitization
# ---------------------------------------------------------------------------

def _make_holding(
    symbol: str,
    market: str,
    category: str,
    *,
    shares: float = 100.0,
    cost_per_share: float = 50.0,
    sheet_price: float = 60.0,
    market_value: float = 6000.0,
    is_leaps: bool = False,
    categories: tuple[str, ...] = (),
) -> SheetHolding:
    return SheetHolding(
        symbol=symbol,
        raw_ticker=symbol,
        market=market,
        shares=shares,
        cost_per_share=cost_per_share,
        sheet_price=sheet_price,
        market_value=market_value,
        category=category,
        is_leaps=is_leaps,
        categories=categories or ((category,) if category else ()),
    )


def _make_book(holdings: list[SheetHolding], *, cash_usd: float = 111000.0, total_capital_usd: float = 9999999.0) -> SheetBook:
    return SheetBook(
        holdings=holdings,
        cash_usd=cash_usd,
        total_capital_usd=total_capital_usd,
        fetched_at=dt.datetime(2026, 11, 27, 0, 5, tzinfo=dt.timezone.utc),
        whole_port_net_total_usd=total_capital_usd,
    )


def test_tv_symbol_hint_mapping_us_hk_kr():
    assert m.build_tv_symbol_hint("0700", "HK") == ("0700", "HKEX:700", False)
    assert m.build_tv_symbol_hint("09988", "HK") == ("09988", "HKEX:9988", False)
    assert m.build_tv_symbol_hint("0001", "HK") == ("0001", "HKEX:1", False)
    assert m.build_tv_symbol_hint("KRX:005930", "KR") == ("005930", "KRX:005930", False)
    assert m.build_tv_symbol_hint("000660", "KR") == ("000660", "KRX:000660", False)
    assert m.build_tv_symbol_hint("5930", "KR") == ("005930", "KRX:005930", False)
    assert m.build_tv_symbol_hint("660", "KR") == ("000660", "KRX:000660", False)
    assert m.build_tv_symbol_hint("ABC123", "KR") == ("ABC123", None, True)
    assert m.build_tv_symbol_hint("NASDAQ:SMH", "US") == ("SMH", "NASDAQ:SMH", False)
    assert m.build_tv_symbol_hint("QQQ", "US") == ("QQQ", "NASDAQ:QQQ", False)
    assert m.build_tv_symbol_hint("SOXX", "US") == ("SOXX", "NASDAQ:SOXX", False)
    assert m.build_tv_symbol_hint("NVDA", "US") == ("NVDA", None, True)


def test_offensive_holdings_all_lth_returns_empty():
    book = _make_book([
        _make_holding("AAA", "US", "LTH"),
        _make_holding("0700", "HK", " lth "),
    ])
    res = m.fetch_active_offensive_holdings(
        now_sg=dt.datetime(2026, 11, 27, 8, 5, tzinfo=m.SG_TZ),
        sheet_id="FAKE_SHEET_ID_999",
        book_loader=lambda sheet_id, timeout: book,
    )
    assert res["status"] == "empty"
    assert res["items"] == []


def test_offensive_holdings_filters_leaps_options_dram_crypto_and_keeps_mixed_duplicate():
    book = _make_book([
        _make_holding("KRX:005930", "KR", "Fundamental"),
        _make_holding("0700", "HK", "Value"),
        _make_holding("QQQ", "US", "LTH", categories=("LTH", "Sentiment")),
        _make_holding("AAA", "US", "Momentum"),
        _make_holding("BBB LEAPS", "US", "Momentum", is_leaps=True),
        _make_holding("SPY PUT", "US", "Hedge"),
        _make_holding("DRAM", "US", "Momentum"),
        _make_holding("BTC", "CRYPTO", "Momentum"),
        _make_holding("CLOSED_SYM", "US", "Momentum", shares=0.0),
    ])
    items = m.extract_offensive_holdings(book, SHEET_PRICED_US_SYMBOLS)
    assert [it["ticker"] for it in items] == ["AAA", "QQQ", "0700", "005930"]
    assert [it["market"] for it in items] == ["US", "US", "HK", "KR"]
    by_ticker = {it["ticker"]: it for it in items}
    assert by_ticker["QQQ"]["category"] == "Sentiment"
    assert by_ticker["QQQ"]["tv_symbol_hint"] == "NASDAQ:QQQ"
    assert by_ticker["QQQ"]["needs_tv_search"] is False
    assert by_ticker["AAA"]["tv_symbol_hint"] is None
    assert by_ticker["AAA"]["needs_tv_search"] is True
    assert by_ticker["0700"]["tv_symbol_hint"] == "HKEX:700"
    assert by_ticker["005930"]["tv_symbol_hint"] == "KRX:005930"


def test_offensive_holdings_strict_privacy_whitelist():
    book = _make_book(
        [
            _make_holding(
                "NVDA_TEST",
                "US",
                "Momentum",
                shares=12345,
                cost_per_share=67.89,
                sheet_price=123.45,
                market_value=888888.0,
            ),
        ],
        cash_usd=111000.0,
        total_capital_usd=9999999.0,
    )
    res = m.fetch_active_offensive_holdings(
        now_sg=dt.datetime(2026, 11, 27, 8, 5, tzinfo=m.SG_TZ),
        sheet_id="FAKE_SHEET_ID_SECRET",
        book_loader=lambda sheet_id, timeout: book,
    )
    assert res["status"] == "ok"
    assert len(res["items"]) == 1
    expected_keys = {"ticker", "market", "category", "tv_symbol_hint", "needs_tv_search"}
    for item in res["items"]:
        assert set(item.keys()) == expected_keys
    dumped = json.dumps(res, ensure_ascii=False)
    for forbidden in ("12345", "67.89", "888888", "111000", "9999999", "FAKE_SHEET_ID_SECRET"):
        assert forbidden not in dumped


def test_offensive_holdings_fail_closed_on_missing_env_and_download_error(tmp_path, monkeypatch):
    monkeypatch.delenv("PORTFOLIO_SHEET_ID", raising=False)
    res_missing = m.fetch_active_offensive_holdings(
        now_sg=dt.datetime(2026, 11, 27, 8, 5, tzinfo=m.SG_TZ),
        env_file=tmp_path / "none.env",
    )
    assert res_missing["status"] == "unavailable"
    assert res_missing["items"] == []

    def boom_sheet(sheet_id, timeout):
        raise SheetBookError(
            "sheet download failed: HTTP 500 https://docs.google.com/d/FAKE_SECRET/export"
        )

    res_sheet_err = m.fetch_active_offensive_holdings(
        now_sg=dt.datetime(2026, 11, 27, 8, 5, tzinfo=m.SG_TZ),
        sheet_id="FAKE_SECRET",
        book_loader=boom_sheet,
    )
    assert res_sheet_err["status"] == "unavailable"
    assert res_sheet_err["items"] == []
    assert "FAKE_SECRET" not in res_sheet_err["reason"]
    assert "https://" not in res_sheet_err["reason"]
    assert "docs.google.com" not in res_sheet_err["reason"]

    def boom_runtime(sheet_id, timeout):
        raise RuntimeError("unexpected FAKE_SECRET https://example.com/secret")

    res_rt_err = m.fetch_active_offensive_holdings(
        now_sg=dt.datetime(2026, 11, 27, 8, 5, tzinfo=m.SG_TZ),
        sheet_id="FAKE_SECRET",
        book_loader=boom_runtime,
    )
    assert res_rt_err["status"] == "unavailable"
    assert res_rt_err["items"] == []
    assert "FAKE_SECRET" not in res_rt_err["reason"]
    assert "https://" not in res_rt_err["reason"]


# ---------------------------------------------------------------------------
# Task 3: Dual-stop state machine, reconciliation, responsive HTML, sidecar.json
# ---------------------------------------------------------------------------

def _base_pos(**overrides):
    pos = {
        "ticker": "AAA",
        "market": "US",
        "category": "Momentum",
        "exchange_symbol": "NASDAQ:AAA",
        "bar_date": "2026-11-26",
        "close": 100.0,
        "change_pct": 1.25,
        "st_direction": "long",
        "st_stop": 95.0,
        "st_flipped_bear": False,
        "cow_direction": 1,
        "cow_lower": 96.0,
        "cow_mid": 98.0,
        "cow_upper": 102.0,
        "cow_flipped_bear": False,
    }
    pos.update(overrides)
    return pos


def _minimal_symbol(ticker: str = "BTC"):
    return {
        "ticker": ticker,
        "exchange_symbol": f"EXCH:{ticker}",
        "timeframe": "1D",
        "bar_date": "2026-11-26",
        "close": 100.0,
        "change_pct": 1.5,
        "trend": {
            "resonance": 1,
            "st_direction": "long",
            "st_stop": 95.0,
            "st_status": "多头",
            "st_flip_text": "95.00",
            "cow_direction_text": "多头",
            "cow_upper": 103.0,
            "cow_mid": 99.0,
            "cow_lower": 96.0,
        },
        "extremity": {
            "regime_code": 0,
            "z_val": 0.2,
            "z_sigma": 0.3,
            "z_2y_pct": 55.0,
            "s_val": 0.1,
            "s_pct_extreme": 12.0,
            "s_2y_pct": 50.0,
        },
        "original_d": {"d_val": 0.5, "d_2y_pct": 52.0, "d_overheat": 4.5, "d_oversold": -4.5},
        "volume": {"vol": 1000, "vol_ma20": 900, "threshold_2sigma": 1800, "rvol": 1.1, "z_score": 0.4},
        "conclusions": {
            "trend": "多头",
            "extremity": "常态",
            "volume": "平量",
            "levels": "95-103",
            "trend_1w": "1W 多头",
        },
        "new_alerts": [],
    }


def test_select_closed_daily_bar_kr_0805_sgt_drops_intraday_bar():
    now_utc = dt.datetime(2026, 11, 27, 0, 5, 0, tzinfo=dt.timezone.utc)  # 08:05 SGT / 09:05 KST

    kr_bars = [
        {"date": "2026-11-25", "close": 50000.0},
        {"date": "2026-11-26", "close": 51000.0},
        {"date": "2026-11-27", "close": 51500.0},  # intraday forming at 09:05 KST
    ]
    kr_bar, kr_note = rhtml.select_closed_daily_bar(kr_bars, "KR", now_utc)
    assert kr_bar is not None
    assert kr_bar["date"] == "2026-11-26"
    assert kr_note != ""

    hk_bars = [
        {"date": "2026-11-25", "close": 380.0},
        {"date": "2026-11-26", "close": 385.0},
    ]
    hk_bar, hk_note = rhtml.select_closed_daily_bar(hk_bars, "HK", now_utc)
    assert hk_bar is not None
    assert hk_bar["date"] == "2026-11-26"
    assert hk_note == ""

    us_bars = [
        {"date": "2026-11-25", "close": 100.0},
        {"date": "2026-11-26", "close": 102.0},
    ]
    us_bar, us_note = rhtml.select_closed_daily_bar(us_bars, "US", now_utc)
    assert us_bar is not None
    assert us_bar["date"] == "2026-11-26"
    assert us_note == ""


def test_evaluate_position_stop_both_bull_full_hold():
    res = rhtml.evaluate_position_stop(_base_pos())
    assert res["available"] is True
    assert res["target_hold_pct"] == 100
    assert res["badge_text"] == "满额持有 (100%)"
    assert res["st_dist_pct"] == -5.0
    assert res["cow_dist_pct"] == -4.0
    assert res["banner_alert"] is None

    # Newly listed / unconfirmed cow channel (cow_direction == 0): only ST leg confirmed -> 50%
    res_new_ipo = rhtml.evaluate_position_stop(_base_pos(cow_direction=0, cow_lower=96.0, close=100.0))
    assert res_new_ipo["available"] is True
    assert res_new_ipo["cow_bull"] is False
    assert res_new_ipo["target_hold_pct"] == 50
    assert res_new_ipo["badge_text"] == "留半仓 (50%)"
    assert res_new_ipo["banner_alert"] is None
    assert "待确认" in res_new_ipo["cow_status_text"]

    # cow_direction == 0 with cow_flipped_bear=True is contradictory -> fail closed
    res_ipo_bad = rhtml.evaluate_position_stop(_base_pos(cow_direction=0, cow_flipped_bear=True))
    assert res_ipo_bad["available"] is False


def test_evaluate_position_stop_single_leg_break_new_vs_stale_and_null_st_stop():
    # New ST break with st_stop=None (Pine v12.0 outputs null for bull stop when flipped short)
    st_null = rhtml.evaluate_position_stop(
        _base_pos(st_direction="short", st_stop=None, st_flipped_bear=True, cow_direction=1, cow_lower=96.0, close=100.0)
    )
    assert st_null["available"] is True
    assert st_null["target_hold_pct"] == 50
    assert st_null["badge_text"] == "🚨 清一半 (50%)"
    assert st_null["banner_alert"] is not None
    assert "96.00" in st_null["banner_alert"]

    # New ST break with explicit short stop > close
    st_num = rhtml.evaluate_position_stop(
        _base_pos(st_direction="short", st_stop=105.0, st_flipped_bear=True, cow_direction=1, cow_lower=96.0, close=100.0)
    )
    assert st_num["available"] is True
    assert st_num["target_hold_pct"] == 50
    assert st_num["badge_text"] == "🚨 清一半 (50%)"
    assert st_num["banner_alert"] is not None
    assert "96.00" in st_num["banner_alert"]

    # New Cow break while ST remains long
    cow_brk = rhtml.evaluate_position_stop(
        _base_pos(st_direction="long", st_stop=94.0, cow_direction=-1, cow_lower=98.0, cow_flipped_bear=True, close=97.0)
    )
    assert cow_brk["available"] is True
    assert cow_brk["target_hold_pct"] == 50
    assert cow_brk["badge_text"] == "🚨 清一半 (50%)"
    assert cow_brk["banner_alert"] is not None
    assert "94.00" in cow_brk["banner_alert"]

    # Stale single-leg break (flipped_bear=False)
    stale_st = rhtml.evaluate_position_stop(
        _base_pos(st_direction="short", st_stop=None, st_flipped_bear=False, cow_direction=1, cow_lower=96.0, close=100.0)
    )
    assert stale_st["available"] is True
    assert stale_st["target_hold_pct"] == 50
    assert stale_st["badge_text"] == "留半仓 (50%)"
    assert stale_st["banner_alert"] is None

    # Cow hysteresis: cow_direction == -1 even when close rebounds between cow_lower and cow_upper
    hyst = rhtml.evaluate_position_stop(
        _base_pos(
            st_direction="long",
            st_stop=94.0,
            cow_direction=-1,
            cow_lower=96.0,
            cow_upper=104.0,
            cow_flipped_bear=False,
            close=100.0,
        )
    )
    assert hyst["available"] is True
    assert hyst["cow_bull"] is False
    assert hyst["badge_text"] == "留半仓 (50%)"
    assert hyst["banner_alert"] is None


def test_evaluate_position_stop_dual_break_new_vs_stale():
    new_dual = rhtml.evaluate_position_stop(
        _base_pos(
            st_direction="short",
            st_stop=None,
            st_flipped_bear=True,
            cow_direction=-1,
            cow_lower=102.0,
            cow_mid=105.0,
            cow_upper=108.0,
            cow_flipped_bear=False,
            close=100.0,
        )
    )
    assert new_dual["available"] is True
    assert new_dual["target_hold_pct"] == 0
    assert new_dual["badge_text"] == "🚨 双破全清"
    assert new_dual["banner_alert"] is not None

    stale_dual = rhtml.evaluate_position_stop(
        _base_pos(
            st_direction="short",
            st_stop=106.0,
            st_flipped_bear=False,
            cow_direction=-1,
            cow_lower=102.0,
            cow_mid=105.0,
            cow_upper=108.0,
            cow_flipped_bear=False,
            close=100.0,
        )
    )
    assert stale_dual["available"] is True
    assert stale_dual["target_hold_pct"] == 0
    assert stale_dual["badge_text"] == "⚠️ 双破全清 · 存量未清 (0%)"
    assert stale_dual["banner_alert"] is None


def test_evaluate_position_stop_nan_inf_bool_and_inconsistency_fail_closed():
    bad_universal = [float("nan"), float("inf"), -float("inf"), True]
    for field in ("close", "cow_lower", "cow_upper", "change_pct", "st_stop"):
        for bad_val in bad_universal:
            res = rhtml.evaluate_position_stop(_base_pos(**{field: bad_val}))
            assert res["available"] is False, f"{field}={bad_val!r} must fail closed"
            assert res["badge_text"] == "⚠️ 指标数据不可用"

    for price_field in ("close", "cow_lower", "cow_upper", "st_stop"):
        res = rhtml.evaluate_position_stop(_base_pos(**{price_field: -1.0}))
        assert res["available"] is False, f"{price_field}=-1.0 must fail closed"
        assert res["badge_text"] == "⚠️ 指标数据不可用"

    # Negative change_pct is valid; ensure normal negative daily move does NOT fail closed
    res_neg_chg = rhtml.evaluate_position_stop(_base_pos(change_pct=-1.0))
    assert res_neg_chg["available"] is True

    # st_direction == "long" with st_stop=None or st_stop > close * 1.0005
    for bad_long_stop in (None, 105.0):
        res = rhtml.evaluate_position_stop(_base_pos(st_direction="long", st_stop=bad_long_stop, close=100.0))
        assert res["available"] is False
        assert res["badge_text"] == "⚠️ 指标数据不可用"

    # st_direction == "short" with numeric st_stop < close * 0.9995
    res_short_bad = rhtml.evaluate_position_stop(_base_pos(st_direction="short", st_stop=90.0, close=100.0))
    assert res_short_bad["available"] is False
    assert res_short_bad["badge_text"] == "⚠️ 指标数据不可用"

    # cow_direction == 1 with close significantly below cow_lower (ST stop valid so cow check is actually hit!)
    res_cow_bad = rhtml.evaluate_position_stop(
        _base_pos(st_direction="long", st_stop=90.0, cow_direction=1, cow_lower=96.0, cow_mid=99.0, cow_upper=102.0, close=94.0)
    )
    assert res_cow_bad["available"] is False
    assert res_cow_bad["badge_text"] == "⚠️ 指标数据不可用"
    assert "奶牛" in res_cow_bad["reason"]

    # Null flipped_bear flags must fail closed
    for flag_field in ("st_flipped_bear", "cow_flipped_bear"):
        res_null = rhtml.evaluate_position_stop(_base_pos(**{flag_field: None}))
        assert res_null["available"] is False
        assert res_null["badge_text"] == "⚠️ 指标数据不可用"

    # Direction vs flipped_bear contradictions must fail closed
    res_st_contra = rhtml.evaluate_position_stop(_base_pos(st_direction="long", st_flipped_bear=True))
    assert res_st_contra["available"] is False
    res_cow_contra = rhtml.evaluate_position_stop(_base_pos(cow_direction=1, cow_flipped_bear=True))
    assert res_cow_contra["available"] is False
    # cow_direction == -1 with cow_flipped_bear=True requires close < cow_lower (no false 🚨 on rebound)
    res_cow_rebound_flip = rhtml.evaluate_position_stop(
        _base_pos(cow_direction=-1, cow_lower=96.0, cow_mid=99.0, cow_upper=104.0, close=100.0, cow_flipped_bear=True)
    )
    assert res_cow_rebound_flip["available"] is False

    # cow_mid outside [cow_lower, cow_upper] must fail closed
    res_mid_high = rhtml.evaluate_position_stop(_base_pos(cow_lower=95.0, cow_mid=150.0, cow_upper=105.0))
    assert res_mid_high["available"] is False
    res_mid_low = rhtml.evaluate_position_stop(_base_pos(cow_lower=95.0, cow_mid=90.0, cow_upper=105.0))
    assert res_mid_low["available"] is False

    # Strict exact boundary checks (no loose 0.05% tolerance hiding a stop breach)
    res_long_eq = rhtml.evaluate_position_stop(
        _base_pos(close=100.0, st_direction="long", st_stop=100.0, cow_lower=96.0, cow_mid=99.0, cow_upper=104.0)
    )
    assert res_long_eq["available"] is True
    res_long_over = rhtml.evaluate_position_stop(
        _base_pos(close=100.0, st_direction="long", st_stop=100.04, cow_lower=96.0, cow_mid=99.0, cow_upper=104.0)
    )
    assert res_long_over["available"] is False

    res_short_eq = rhtml.evaluate_position_stop(
        _base_pos(close=100.0, st_direction="short", st_stop=100.0, cow_lower=96.0, cow_mid=99.0, cow_upper=104.0)
    )
    assert res_short_eq["available"] is True
    res_short_under = rhtml.evaluate_position_stop(
        _base_pos(close=100.0, st_direction="short", st_stop=99.96, cow_lower=96.0, cow_mid=99.0, cow_upper=104.0)
    )
    assert res_short_under["available"] is False

    # Huge integer OverflowError must fail closed cleanly (not crash)
    res_overflow = rhtml.evaluate_position_stop(_base_pos(close=10 ** 400))
    assert res_overflow["available"] is False
    assert rhtml.fmt_num(10 ** 400) == "—"
    assert rhtml.fmt_vol(10 ** 400) == "—"

    # Missing or invalid bar_date must fail closed
    assert rhtml.evaluate_position_stop(_base_pos(bar_date=""))["available"] is False
    assert rhtml.evaluate_position_stop(_base_pos(bar_date="2026-13-99"))["available"] is False


def test_reconcile_active_positions_prevents_fail_open_and_dropped_ticker():
    # vol_env unavailable must override active_positions empty
    rec_unavail = rhtml.reconcile_active_positions(
        active_positions={"status": "empty", "positions": []},
        vol_env_holdings={"status": "unavailable", "reason": "SheetBookError: timeout", "items": []},
    )
    assert rec_unavail is not None
    assert rec_unavail["status"] == "unavailable"
    assert "timeout" in rec_unavail["reason"]

    # vol_env ok has [AAA, 0700] while active_positions only returned [AAA]
    rec_partial = rhtml.reconcile_active_positions(
        active_positions={"status": "ok", "positions": [_base_pos(ticker="AAA", market="US")]},
        vol_env_holdings={
            "status": "ok",
            "items": [
                {"ticker": "AAA", "market": "US", "category": "Momentum", "tv_symbol_hint": None, "needs_tv_search": True},
                {"ticker": "0700", "market": "HK", "category": "Value", "tv_symbol_hint": "HKEX:700", "needs_tv_search": False},
            ],
        },
    )
    assert rec_partial is not None
    assert rec_partial["status"] == "ok"
    assert [p["ticker"] for p in rec_partial["positions"]] == ["AAA", "0700"]
    assert rec_partial["positions"][0]["available"] is True
    assert rec_partial["positions"][1]["available"] is False
    assert rec_partial["positions"][1]["badge_text"] == "⚠️ 指标数据不可用"


def test_render_html_daily_three_states_banner_and_xss_escaping():
    base_daily = {
        "mode": "daily",
        "report_date": "2026-11-27",
        "generated_at": "2026-11-27 08:05 SGT",
        "overview_bullets": ["总览要点"],
        "symbols": [_minimal_symbol("BTC")],
    }

    # 1. empty (with authoritative vol_env.active_holdings)
    html_empty = rhtml.render_html({
        **base_daily,
        "vol_env": {"active_holdings": {"status": "empty", "items": []}},
        "active_positions": {"status": "empty", "positions": []},
    })
    assert "30% 主动进攻仓 · 移动止损" in html_empty
    assert "无主动进攻仓" in html_empty
    assert "--bg: #f7f8fa" in html_empty
    assert not re.search(r'(?:src|href)=["\']https?://', html_empty)

    # 2. unavailable
    html_unavail = rhtml.render_html({
        **base_daily,
        "vol_env": {"active_holdings": {"status": "unavailable", "reason": "拉取失败 <script>alert(1)</script>", "items": []}},
        "active_positions": {"status": "unavailable", "reason": "拉取失败 <script>alert(1)</script>", "positions": []},
    })
    assert "30% 主动进攻仓 · 移动止损" in html_unavail
    assert "不可用" in html_unavail
    assert "<script>alert(1)</script>" not in html_unavail
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in html_unavail

    # 3. ok with full hold, new cut-half, new dual-break, stale dual-break, single unavailable + XSS
    sym_with_alert = _minimal_symbol("BTC")
    sym_with_alert["new_alerts"] = ["BTC 原有预警"]
    sym_with_alert["volume"]["unit"] = "<b>BTC</b>"
    html_ok = rhtml.render_html({
        **base_daily,
        "symbols": [sym_with_alert],
        "vol_env": {
            "active_holdings": {
                "status": "ok",
                "items": [
                    {"ticker": "AAA", "market": "US", "category": "Momentum", "tv_symbol_hint": None, "needs_tv_search": True},
                    {"ticker": "BBB", "market": "US", "category": "Sentiment", "tv_symbol_hint": None, "needs_tv_search": True},
                    {"ticker": "CCC", "market": "US", "category": "Value<script>alert(1)</script>", "tv_symbol_hint": None, "needs_tv_search": True},
                    {"ticker": "DDD", "market": "US", "category": "Fundamental", "tv_symbol_hint": None, "needs_tv_search": True},
                    {"ticker": "EEE", "market": "HK", "category": "Value", "tv_symbol_hint": "HKEX:700", "needs_tv_search": False},
                ],
            }
        },
        "active_positions": {
            "status": "ok",
            "positions": [
                _base_pos(ticker="AAA", category="Momentum"),
                _base_pos(
                    ticker="BBB",
                    category="Sentiment",
                    st_direction="short",
                    st_stop=None,
                    st_flipped_bear=True,
                    cow_direction=1,
                    cow_lower=96.0,
                ),
                _base_pos(
                    ticker="CCC",
                    category="Value<script>alert(1)</script>",
                    st_direction="short",
                    st_stop=None,
                    st_flipped_bear=True,
                    cow_direction=-1,
                    cow_lower=102.0,
                    cow_mid=105.0,
                    cow_upper=108.0,
                ),
                _base_pos(
                    ticker="DDD",
                    category="Fundamental",
                    st_direction="short",
                    st_stop=105.0,
                    st_flipped_bear=False,
                    cow_direction=-1,
                    cow_lower=102.0,
                    cow_mid=105.0,
                    cow_upper=108.0,
                ),
                {"ticker": "EEE", "market": "HK", "category": "Value", "available": False, "reason": "MCP 超时"},
            ],
        },
    })
    assert "30% 主动进攻仓 · 移动止损" in html_ok
    assert "满额持有 (100%)" in html_ok
    assert "🚨 清一半 (50%)" in html_ok
    assert "🚨 双破全清" in html_ok
    assert "⚠️ 双破全清 · 存量未清 (0%)" in html_ok
    assert "⚠️ 指标数据不可用" in html_ok
    assert "<script>alert(1)</script>" not in html_ok
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in html_ok
    assert "<b>BTC</b>" not in html_ok
    assert "&lt;b&gt;BTC&lt;/b&gt;" in html_ok
    assert not re.search(r'(?:src|href)=["\']https?://', html_ok)

    # Verify both newly triggered position breaks appear in .alert-banner BEFORE symbol alert
    banner_idx = html_ok.index('class="alert-banner"')
    pos_sec_idx = html_ok.index("30% 主动进攻仓 · 移动止损")
    overview_idx = html_ok.index("一、三标的横向总览")
    assert banner_idx < pos_sec_idx < overview_idx
    banner_slice = html_ok[banner_idx:pos_sec_idx]
    bbb_idx = banner_slice.index("BBB")
    ccc_idx = banner_slice.index("CCC")
    btc_idx = banner_slice.index("BTC 原有预警")
    assert bbb_idx < btc_idx
    assert ccc_idx < btc_idx


def test_render_html_single_mode_100_percent_backward_compatible():
    single_payload = {
        "mode": "single",
        "report_date": "2026-11-27",
        "symbols": [_minimal_symbol("NVDA_TEST")],
        "active_positions": {
            "status": "ok",
            "positions": [_base_pos(ticker="AAA", st_direction="short", st_stop=None, st_flipped_bear=True)],
        },
    }
    out = rhtml.render_html(single_payload)
    assert "30% 主动进攻仓" not in out
    assert "三板块技术分析面板" in out
    assert "NVDA_TEST" in out
    assert "清一半" not in out


def test_sidecar_json_valid_and_least_privilege():
    sidecar_path = Path(__file__).resolve().parents[1] / "scripts" / "daily_technical_analysis" / "sidecar.json"
    cfg = json.loads(sidecar_path.read_text(encoding="utf-8"))
    grants = cfg["agent_permissions"]["access_grants"]
    assert "mcp(tradingview/mcp-tv-search-symbols)" in grants
    for g in grants:
        assert ".env" not in g
        assert "*" not in g
    prompt = cfg["args"][-1]
    assert "active_holdings" in prompt
    assert "active_positions" in prompt
    assert "08:05" in prompt or "[-2]" in prompt
    assert "韩股" in prompt


# ---------------------------------------------------------------------------
# Codex Round-1 Audit Regression Tests (#1, #2, #3, #4, #5, #6A, #8, #9)
# ---------------------------------------------------------------------------

def test_codex_p0_path_confinement_telegram_and_renderer(tmp_path):
    outside_txt = tmp_path / "readme.txt"
    outside_txt.write_text("harmless test content", encoding="utf-8")
    outside_html = tmp_path / "outside.html"
    outside_html.write_text("<html></html>", encoding="utf-8")

    send_script = Path(__file__).resolve().parents[1] / "scripts" / "daily_technical_analysis" / "send_telegram_html.sh"
    for bad_file in (outside_txt, outside_html):
        proc = subprocess.run(
            ["bash", str(send_script), str(bad_file), "test caption"],
            capture_output=True,
            text=True,
            check=False,
        )
        assert proc.returncode != 0
        assert "notes/ta" in (proc.stderr + proc.stdout)

    # render_ta_html CLI must reject output outside ALLOWED_REPORT_DIR and non-stdin --input
    with pytest.raises(ValueError, match="notes/ta"):
        rhtml.validate_report_output_path(str(outside_html))
    with pytest.raises(ValueError, match=".html"):
        rhtml.validate_report_output_path(str(rhtml.ALLOWED_REPORT_DIR / "bad.txt"))

    render_script = Path(__file__).resolve().parents[1] / "scripts" / "daily_technical_analysis" / "render_ta_html.py"
    proc_out = subprocess.run(
        ["/Users/owen/CC workspace/Finance/.venv/bin/python", str(render_script), "--output", str(outside_html)],
        input=json.dumps({"mode": "single", "symbols": []}),
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc_out.returncode != 0

    proc_in = subprocess.run(
        [
            "/Users/owen/CC workspace/Finance/.venv/bin/python",
            str(render_script),
            "--input",
            str(outside_txt),
            "--output",
            str(rhtml.ALLOWED_REPORT_DIR / "should_not_write.html"),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc_in.returncode != 0


def test_codex_p1_blank_category_row_order_end_to_end():
    from tests.test_portfolio.test_sheet_book import FETCHED_AT, _row, build_fixture

    rows_blank_first = [
        _row("US", "AAA", 10.0, 20, 8.0, 200.0, ""),
        _row("US", "AAA", 10.0, 50, 9.0, 500.0, "LTH"),
    ]
    rows_lth_first = list(reversed(rows_blank_first))

    book1 = parse_sheet_book(build_fixture(rows=rows_blank_first), FETCHED_AT)
    book2 = parse_sheet_book(build_fixture(rows=rows_lth_first), FETCHED_AT)
    items1 = m.extract_offensive_holdings(book1, SHEET_PRICED_US_SYMBOLS)
    items2 = m.extract_offensive_holdings(book2, SHEET_PRICED_US_SYMBOLS)
    assert [it["ticker"] for it in items1] == ["AAA"]
    assert [it["ticker"] for it in items2] == ["AAA"]


def test_codex_p1_reconcile_strict_market_no_offbook_no_spoofed_evaluated():
    # 1. Cross-market mismatch (US/EXAMPLE vs KR/EXAMPLE) must NOT match
    rec_cross = rhtml.reconcile_active_positions(
        active_positions={
            "status": "ok",
            "positions": [
                _base_pos(
                    ticker="EXAMPLE",
                    market="KR",
                    st_direction="short",
                    st_stop=None,
                    st_flipped_bear=True,
                    cow_direction=-1,
                    cow_lower=102.0,
                    cow_mid=105.0,
                    cow_upper=108.0,
                )
            ],
        },
        vol_env_holdings={
            "status": "ok",
            "items": [{"ticker": "EXAMPLE", "market": "US", "category": "Momentum", "tv_symbol_hint": None, "needs_tv_search": True}],
        },
    )
    assert rec_cross is not None
    assert len(rec_cross["positions"]) == 1
    assert rec_cross["positions"][0]["market"] == "US"
    assert rec_cross["positions"][0]["available"] is False
    assert rec_cross["positions"][0]["banner_alert"] is None

    # 2. Authoritative empty must ignore stray positions
    rec_empty = rhtml.reconcile_active_positions(
        active_positions={"status": "ok", "positions": [_base_pos(ticker="STRAY")]},
        vol_env_holdings={"status": "empty", "items": []},
    )
    assert rec_empty is not None
    assert rec_empty["status"] == "empty"
    assert rec_empty["positions"] == []

    # 3. Off-book extras and duplicate returned rows must NOT corrupt output
    rec_dup = rhtml.reconcile_active_positions(
        active_positions={
            "status": "ok",
            "positions": [
                _base_pos(ticker="AAA", market="US"),
                _base_pos(ticker="AAA", market="US"),
                _base_pos(ticker="EXTRA", market="US"),
            ],
        },
        vol_env_holdings={
            "status": "ok",
            "items": [{"ticker": "AAA", "market": "US", "category": "Momentum", "tv_symbol_hint": None, "needs_tv_search": True}],
        },
    )
    assert rec_dup is not None
    assert len(rec_dup["positions"]) == 1
    assert rec_dup["positions"][0]["ticker"] == "AAA"
    assert rec_dup["positions"][0]["available"] is False

    # 4. Malformed vol_env.active_holdings={} with active_positions=empty must fail closed as unavailable
    rec_bad_vh = rhtml.reconcile_active_positions(
        active_positions={"status": "empty", "positions": []},
        vol_env_holdings={},
    )
    assert rec_bad_vh is not None
    assert rec_bad_vh["status"] == "unavailable"

    # 5. External spoofed _evaluated=True must be ignored and re-evaluated (failing closed on missing prices)
    rec_spoof = rhtml.reconcile_active_positions(
        active_positions={
            "status": "ok",
            "positions": [
                {
                    "ticker": "AAA",
                    "market": "US",
                    "_evaluated": True,
                    "available": True,
                    "badge_text": "🚨 双破全清",
                    "banner_alert": "SPOOFED ALERT",
                }
            ],
        },
        vol_env_holdings={
            "status": "ok",
            "items": [{"ticker": "AAA", "market": "US", "category": "Momentum", "tv_symbol_hint": None, "needs_tv_search": True}],
        },
    )
    assert rec_spoof is not None
    assert rec_spoof["positions"][0]["available"] is False
    assert rec_spoof["positions"][0]["banner_alert"] is None

    # 6. vol_env ok + active_positions unavailable must mark all items unavailable (ignore leftover rows)
    rec_ap_unavail = rhtml.reconcile_active_positions(
        active_positions={"status": "unavailable", "reason": "MCP offline", "positions": [_base_pos(ticker="AAA", market="US")]},
        vol_env_holdings={
            "status": "ok",
            "items": [{"ticker": "AAA", "market": "US", "category": "Momentum", "tv_symbol_hint": None, "needs_tv_search": True}],
        },
    )
    assert rec_ap_unavail is not None
    assert rec_ap_unavail["status"] == "ok"
    assert len(rec_ap_unavail["positions"]) == 1
    assert rec_ap_unavail["positions"][0]["available"] is False
    assert "MCP offline" in rec_ap_unavail["positions"][0]["reason"]


def test_codex_p1_intraday_bar_blocked_in_render_html_and_weekend_no_repeat_alert():
    # 1. KR intraday bar_date=2026-11-27 at 2026-11-27 08:05 SGT must NOT trigger alert in render_html
    kr_intraday_payload = {
        "mode": "daily",
        "report_date": "2026-11-27",
        "generated_at": "2026-11-27 08:05 SGT",
        "vol_env": {
            "active_holdings": {
                "status": "ok",
                "items": [{"ticker": "005930", "market": "KR", "category": "Momentum", "tv_symbol_hint": "KRX:005930", "needs_tv_search": False}],
            }
        },
        "active_positions": {
            "status": "ok",
            "positions": [
                _base_pos(
                    ticker="005930",
                    market="KR",
                    exchange_symbol="KRX:005930",
                    bar_date="2026-11-27",
                    st_direction="short",
                    st_stop=None,
                    st_flipped_bear=True,
                    cow_direction=-1,
                    cow_lower=102.0,
                    cow_mid=105.0,
                    cow_upper=108.0,
                )
            ],
        },
        "symbols": [],
    }
    html_kr = rhtml.render_html(kr_intraday_payload)
    assert "⚠️ 指标数据不可用" in html_kr
    assert "🚨 双破全清" not in html_kr
    assert 'class="alert-banner"' not in html_kr

    # 2. select_closed_daily_bar must reject unparseable date and Saturday bar on Monday
    bar_bad, note_bad = rhtml.select_closed_daily_bar(
        [{"date": "not-a-date", "close": 100.0}],
        "KR",
        dt.datetime(2026, 11, 27, 0, 5, tzinfo=dt.timezone.utc),
    )
    assert bar_bad is None
    assert note_bad != ""

    bar_sat, note_sat = rhtml.select_closed_daily_bar(
        [{"date": "2026-11-27", "close": 100.0}, {"date": "2026-11-28", "close": 101.0}],  # Sat bar
        "KR",
        dt.datetime(2026, 11, 30, 0, 5, tzinfo=dt.timezone.utc),  # Mon 08:05 SGT
    )
    assert bar_sat is not None
    assert bar_sat["date"] == "2026-11-27"
    assert note_sat != ""

    # 3. Weekend repeat alert suppression (Scheme A): Friday 2026-11-27 KR break fires on Sat 11-28, NOT Sun 11-29
    base_fri_kr = {
        "mode": "daily",
        "vol_env": {
            "active_holdings": {
                "status": "ok",
                "items": [{"ticker": "005930", "market": "KR", "category": "Momentum", "tv_symbol_hint": "KRX:005930", "needs_tv_search": False}],
            }
        },
        "active_positions": {
            "status": "ok",
            "positions": [
                _base_pos(
                    ticker="005930",
                    market="KR",
                    exchange_symbol="KRX:005930",
                    bar_date="2026-11-27",
                    st_direction="short",
                    st_stop=None,
                    st_flipped_bear=True,
                    cow_direction=-1,
                    cow_lower=102.0,
                    cow_mid=105.0,
                    cow_upper=108.0,
                )
            ],
        },
        "symbols": [],
    }
    html_sat = rhtml.render_html({**base_fri_kr, "report_date": "2026-11-28", "generated_at": "2026-11-28 08:05 SGT"})
    assert "🚨 双破全清" in html_sat
    assert 'class="alert-banner"' in html_sat

    html_sun = rhtml.render_html({**base_fri_kr, "report_date": "2026-11-29", "generated_at": "2026-11-29 08:05 SGT"})
    assert "⚠️ 双破全清 · 存量未清 (0%)" in html_sun
    assert "🚨 双破全清" not in html_sun
    assert 'class="alert-banner"' not in html_sun


def test_codex_p1_single_mode_fmt_vol_escapes_unit():
    sym = _minimal_symbol("NVDA_TEST")
    sym["volume"]["unit"] = "<img src=x onerror=alert(1)>"
    out = rhtml.render_html({"mode": "single", "report_date": "2026-11-27", "symbols": [sym]})
    assert "<img src=x onerror=alert(1)>" not in out
    assert "&lt;img src=x onerror=alert(1)&gt;" in out


def test_rework_timestamp_bars_privacy_env_isolation_and_exchange_mismatch(tmp_path, monkeypatch):
    # 1. select_closed_daily_bar supports native Unix timestamp 't' (s & ms)
    now_utc = dt.datetime(2026, 11, 27, 0, 5, tzinfo=dt.timezone.utc)  # 09:05 KST Fri
    ts_thu = int(dt.datetime(2026, 11, 26, 0, 0, tzinfo=dt.timezone.utc).timestamp())
    ts_fri_ms = int(dt.datetime(2026, 11, 27, 0, 0, tzinfo=dt.timezone.utc).timestamp() * 1000)
    bar_t, note_t = rhtml.select_closed_daily_bar(
        [{"t": ts_thu, "close": 50000.0}, {"t": ts_fri_ms, "close": 51000.0}],
        "KR",
        now_utc,
    )
    assert bar_t is not None
    assert bar_t["bar_date"] == "2026-11-26"
    assert note_t != ""

    # 2. Explicit env_file must NOT fall back to os.environ["PORTFOLIO_SHEET_ID"]
    monkeypatch.setenv("PORTFOLIO_SHEET_ID", "ENV_LEAK_ID")
    res_iso = m.fetch_active_offensive_holdings(
        now_sg=dt.datetime(2026, 11, 27, 8, 5, tzinfo=m.SG_TZ),
        env_file=tmp_path / "missing.env",
    )
    assert res_iso["status"] == "unavailable"
    assert "PORTFOLIO_SHEET_ID" not in res_iso["reason"]
    assert "ENV_LEAK_ID" not in res_iso["reason"]

    # 3. Exception reason must NOT leak arbitrary private text from exception message
    res_priv = m.fetch_active_offensive_holdings(
        now_sg=dt.datetime(2026, 11, 27, 8, 5, tzinfo=m.SG_TZ),
        sheet_id="FAKE_ID",
        book_loader=lambda sid, t: (_ for _ in ()).throw(RuntimeError("PRIVATE_BALANCE_987654")),
    )
    assert res_priv["status"] == "unavailable"
    assert "PRIVATE_BALANCE_987654" not in res_priv["reason"]

    # 4. Invalid shares (NaN/bool) in extract_offensive_holdings fails closed to unavailable (not empty)
    bad_book = _make_book([_make_holding("AAA", "US", "Momentum", shares=float("nan"))])
    res_nan = m.fetch_active_offensive_holdings(
        now_sg=dt.datetime(2026, 11, 27, 8, 5, tzinfo=m.SG_TZ),
        sheet_id="FAKE_ID",
        book_loader=lambda sid, t: bad_book,
    )
    assert res_nan["status"] == "unavailable"

    # 5. URL injected into ticker/category fails closed to unavailable
    url_book = _make_book([_make_holding("AAA", "US", "https://evil.example/x")])
    res_url = m.fetch_active_offensive_holdings(
        now_sg=dt.datetime(2026, 11, 27, 8, 5, tzinfo=m.SG_TZ),
        sheet_id="FAKE_ID",
        book_loader=lambda sid, t: url_book,
    )
    assert res_url["status"] == "unavailable"

    # 6. All-closed sheet (0 open rows) returns empty (not unavailable) via allow_empty_holdings=True
    from tests.test_portfolio.test_sheet_book import FETCHED_AT, _row, build_fixture
    closed_xlsx = build_fixture(rows=[_row("US", "AAA", 10.0, 0, 0.0, 0.0, "Momentum")])
    res_closed = m.fetch_active_offensive_holdings(
        now_sg=dt.datetime(2026, 11, 27, 8, 5, tzinfo=m.SG_TZ),
        sheet_id="FAKE_ID",
        book_loader=lambda sid, t, allow_empty_holdings=False: parse_sheet_book(
            closed_xlsx, FETCHED_AT, allow_empty_holdings=allow_empty_holdings
        ),
    )
    assert res_closed["status"] == "empty"
    assert res_closed["items"] == []

    # 7. Exchange symbol mismatch against authoritative tv_symbol_hint fails closed
    rec_exch = rhtml.reconcile_active_positions(
        active_positions={"status": "ok", "positions": [_base_pos(ticker="0700", market="HK", exchange_symbol="NYSE:700")]},
        vol_env_holdings={
            "status": "ok",
            "items": [{"ticker": "0700", "market": "HK", "category": "Value", "tv_symbol_hint": "HKEX:700", "needs_tv_search": False}],
        },
    )
    assert rec_exch is not None
    assert rec_exch["positions"][0]["available"] is False
    assert "交易所代码" in rec_exch["positions"][0]["reason"]

    # 8. vol_env_holdings=ok with empty items fails closed as unavailable
    rec_empty_ok = rhtml.reconcile_active_positions(
        active_positions={"status": "ok", "positions": []},
        vol_env_holdings={"status": "ok", "items": []},
    )
    assert rec_empty_ok is not None
    assert rec_empty_ok["status"] == "unavailable"


if __name__ == "__main__":
    unittest.main()
