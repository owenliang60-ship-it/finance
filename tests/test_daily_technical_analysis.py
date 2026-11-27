#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Unit tests for fetch_vol_env.py math, sentinel guards, and fail-closed logic."""

import datetime as dt
import math
import unittest
import pandas as pd

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


if __name__ == "__main__":
    unittest.main()
