"""Tests for social attention indicators (analysis layer)."""
import pytest
from unittest.mock import patch, MagicMock

from src.indicators.social_attention import (
    weighted_buzz,
    attention_zscore,
    get_social_signals,
    scan_social_signals,
    ZSCORE_ALERT,
    ZSCORE_EXTREME,
    MIN_HISTORY_DAYS,
)


class TestWeightedBuzz:

    def test_both_sources(self):
        # Reddit: buzz=70, 100 mentions; X: buzz=80, 300 mentions
        # Expected: (70*100 + 80*300) / 400 = 31000/400 = 77.5
        result = weighted_buzz(70.0, 100, 80.0, 300)
        assert result == pytest.approx(77.5)

    def test_reddit_only(self):
        result = weighted_buzz(70.0, 100, None, None)
        assert result == 70.0

    def test_x_only(self):
        result = weighted_buzz(None, None, 80.0, 300)
        assert result == 80.0

    def test_both_none(self):
        result = weighted_buzz(None, None, None, None)
        assert result is None

    def test_zero_mentions_treated_as_missing(self):
        result = weighted_buzz(70.0, 0, 80.0, 300)
        assert result == 80.0

    def test_equal_weights(self):
        result = weighted_buzz(60.0, 100, 80.0, 100)
        assert result == pytest.approx(70.0)


class TestAttentionZscore:

    def test_basic_zscore(self):
        # Today: 200 mentions, baseline 20 days of 100 each
        history = [200] + [100] * 20
        z = attention_zscore(history)
        # std of [100]*20 = 0, but let's use real variation
        # All same = std ≈ 0 → z = 0.0
        assert z == 0.0  # no variation in baseline

    def test_zscore_with_variation(self):
        # Baseline: alternating 80 and 120 → mean=100, std=20
        baseline = [80, 120] * 10
        history = [200] + baseline  # today = 200
        z = attention_zscore(history)
        # mean=100, std=20, z=(200-100)/20=5.0
        assert z == pytest.approx(5.0)

    def test_insufficient_data(self):
        history = [100] * 5  # Less than MIN_HISTORY_DAYS
        z = attention_zscore(history)
        assert z is None

    def test_exact_minimum_data(self):
        # 10 days = MIN_HISTORY_DAYS, need 1 current + 9 baseline
        baseline = [80, 120, 80, 120, 80, 120, 80, 120, 80]
        history = [100] + baseline
        z = attention_zscore(history)
        assert z is not None

    def test_custom_window(self):
        history = [200] + [100] * 30
        # window=5 returns None because baseline (5 items) < MIN_HISTORY_DAYS-1 (9)
        z5 = attention_zscore(history, window=5)
        assert z5 is None
        # window=20 with constant baseline → z = 0.0
        z20 = attention_zscore(history, window=20)
        assert z20 == 0.0

    def test_negative_zscore(self):
        # Today way below baseline
        baseline = [80, 120] * 10
        history = [10] + baseline
        z = attention_zscore(history)
        assert z is not None
        assert z < 0


class TestGetSocialSignals:

    @patch("src.indicators.social_attention._get_daily_mentions")
    def test_with_data(self, mock_daily):
        mock_daily.return_value = [
            {
                "date": "2026-03-10",
                "reddit_mentions": 100, "x_mentions": 300,
                "reddit_buzz": 70.0, "x_buzz": 80.0,
                "reddit_sentiment": 0.05, "x_sentiment": 0.15,
                "reddit_bullish": 33, "x_bullish": 53,
                "reddit_bearish": 27, "x_bearish": 16,
                "reddit_trend": "rising", "x_trend": "rising",
                "combined": 400,
            },
        ] + [
            {
                "date": "2026-03-{:02d}".format(10 - i),
                "reddit_mentions": 80, "x_mentions": 200,
                "reddit_buzz": 65.0, "x_buzz": 75.0,
                "reddit_sentiment": 0.03, "x_sentiment": 0.10,
                "reddit_bullish": 30, "x_bullish": 50,
                "reddit_bearish": 30, "x_bearish": 20,
                "reddit_trend": "stable", "x_trend": "stable",
                "combined": 280,
            }
            for i in range(1, 21)
        ]

        sig = get_social_signals("NVDA")

        assert sig["has_data"] is True
        assert sig["symbol"] == "NVDA"
        assert sig["combined_mentions"] == 400
        assert sig["weighted_buzz"] is not None
        assert sig["attention_zscore"] is not None
        assert sig["reddit_mentions"] == 100
        assert sig["x_mentions"] == 300

    @patch("src.indicators.social_attention._get_daily_mentions")
    def test_no_data(self, mock_daily):
        mock_daily.return_value = []

        sig = get_social_signals("ZZZZ")
        assert sig["has_data"] is False

    @patch("src.indicators.social_attention._get_daily_mentions")
    def test_alert_level_extreme(self, mock_daily):
        # Today = 1000, baseline = 100 each, std ≈ 0
        # Z would be huge, but std=0 → z=0.0
        # Use variation: baseline alternating 50/150 → mean=100, std=50
        mock_daily.return_value = [
            {"date": "2026-03-10", "reddit_mentions": 200, "x_mentions": 500,
             "reddit_buzz": 90, "x_buzz": 95, "reddit_sentiment": 0.3,
             "x_sentiment": 0.4, "reddit_bullish": 60, "x_bullish": 70,
             "reddit_bearish": 10, "x_bearish": 8, "reddit_trend": "rising",
             "x_trend": "rising", "combined": 700},
        ] + [
            {"date": "2026-03-{:02d}".format(10 - i),
             "reddit_mentions": 30 + (i % 2) * 40, "x_mentions": 50 + (i % 2) * 60,
             "reddit_buzz": 50, "x_buzz": 55, "reddit_sentiment": 0.0,
             "x_sentiment": 0.0, "reddit_bullish": 30, "x_bullish": 30,
             "reddit_bearish": 30, "x_bearish": 30, "reddit_trend": "stable",
             "x_trend": "stable",
             "combined": 80 + (i % 2) * 100}
            for i in range(1, 21)
        ]

        sig = get_social_signals("NVDA")
        assert sig["attention_zscore"] is not None
        # 700 vs baseline mean ~130, should be high z
        assert sig["attention_zscore"] > ZSCORE_ALERT


class TestScanSocialSignals:

    @patch("src.indicators.social_attention.get_social_signals")
    def test_scan_collects_alerts(self, mock_sig):
        mock_sig.side_effect = [
            {"has_data": True, "symbol": "NVDA", "attention_zscore": 3.5,
             "alert_level": "alert", "weighted_buzz": 80, "combined_mentions": 500,
             "reddit_mentions": 200, "x_mentions": 300,
             "reddit_buzz": 70, "x_buzz": 85, "reddit_sentiment": 0.1,
             "x_sentiment": 0.2, "reddit_bullish": 33, "x_bullish": 53,
             "reddit_bearish": 27, "x_bearish": 16,
             "reddit_trend": "rising", "x_trend": "rising", "date": "2026-03-10"},
            {"has_data": True, "symbol": "AAPL", "attention_zscore": 1.0,
             "alert_level": None, "weighted_buzz": 60, "combined_mentions": 200,
             "reddit_mentions": 100, "x_mentions": 100,
             "reddit_buzz": 60, "x_buzz": 60, "reddit_sentiment": 0.0,
             "x_sentiment": 0.0, "reddit_bullish": 40, "x_bullish": 40,
             "reddit_bearish": 30, "x_bearish": 30,
             "reddit_trend": "stable", "x_trend": "falling", "date": "2026-03-10"},
            {"has_data": False, "symbol": "ZZZZ"},
        ]

        result = scan_social_signals(["NVDA", "AAPL", "ZZZZ"])

        assert result["symbols_with_data"] == 2
        assert len(result["alerts"]) == 1
        assert result["alerts"][0]["symbol"] == "NVDA"

        # AAPL has trend divergence
        assert len(result["trend_reversals"]) == 1
        assert result["trend_reversals"][0]["symbol"] == "AAPL"

    @patch("src.indicators.social_attention.get_social_signals")
    def test_scan_extreme_sentiment(self, mock_sig):
        mock_sig.return_value = {
            "has_data": True, "symbol": "GME", "attention_zscore": 1.0,
            "alert_level": None, "weighted_buzz": 90, "combined_mentions": 5000,
            "reddit_mentions": 4000, "x_mentions": 1000,
            "reddit_buzz": 95, "x_buzz": 80, "reddit_sentiment": 0.5,
            "x_sentiment": 0.3, "reddit_bullish": 75, "x_bullish": 65,
            "reddit_bearish": 10, "x_bearish": 15,
            "reddit_trend": "rising", "x_trend": "rising", "date": "2026-03-10",
        }

        result = scan_social_signals(["GME"])

        assert len(result["extreme_sentiment"]) >= 1
        bulls = [e for e in result["extreme_sentiment"] if e["bullish_pct"] >= 60]
        assert len(bulls) >= 1

    @patch("src.indicators.social_attention.get_social_signals")
    def test_scan_empty(self, mock_sig):
        mock_sig.return_value = {"has_data": False, "symbol": "ZZZZ"}

        result = scan_social_signals(["ZZZZ"])
        assert result["symbols_with_data"] == 0
        assert result["alerts"] == []
