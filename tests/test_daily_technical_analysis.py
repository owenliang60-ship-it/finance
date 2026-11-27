#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Unit tests for fetch_vol_env.py math, sentinel guards, and fail-closed logic."""

import datetime as dt
import json
import math
import unittest
import pandas as pd
import pytest

from portfolio.holdings.sheet_book import (
    SHEET_PRICED_US_SYMBOLS,
    SheetBook,
    SheetBookError,
    SheetHolding,
)
from scripts.daily_technical_analysis import chunk_telegram as ct
from scripts.daily_technical_analysis import fetch_vol_env as m


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


if __name__ == "__main__":
    unittest.main()
