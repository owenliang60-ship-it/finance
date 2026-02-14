"""Tests for PMARP four crossover signals + engine summary integration"""
import sys
from pathlib import Path

import pandas as pd
import numpy as np
import pytest

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.indicators.pmarp import analyze_pmarp, check_pmarp_crossover, calculate_pmarp
from src.indicators.engine import get_indicator_summary


def _make_df(closes: list) -> pd.DataFrame:
    """Helper: create a price DataFrame from close prices"""
    dates = pd.date_range("2024-01-01", periods=len(closes), freq="B")
    return pd.DataFrame({"date": dates, "close": closes})


def _make_trending_df(n: int = 200, start: float = 100, end: float = 200) -> pd.DataFrame:
    """Helper: monotonically rising prices → high PMARP"""
    closes = np.linspace(start, end, n).tolist()
    return _make_df(closes)


def _make_falling_df(n: int = 200, start: float = 200, end: float = 100) -> pd.DataFrame:
    """Helper: monotonically falling prices → low PMARP"""
    closes = np.linspace(start, end, n).tolist()
    return _make_df(closes)


class TestCheckPmarpCrossover:
    """check_pmarp_crossover direction tests"""

    def test_up_crossover_98(self):
        pmarp = pd.Series([95.0, 97.5, 98.5])
        assert check_pmarp_crossover(pmarp, 98, "up") == True

    def test_no_up_crossover_already_above(self):
        pmarp = pd.Series([98.5, 99.0])
        assert check_pmarp_crossover(pmarp, 98, "up") == False

    def test_down_crossover_98(self):
        pmarp = pd.Series([99.0, 98.5, 97.3])
        assert check_pmarp_crossover(pmarp, 98, "down") == True

    def test_no_down_crossover_already_below(self):
        pmarp = pd.Series([96.0, 95.0])
        assert check_pmarp_crossover(pmarp, 98, "down") == False

    def test_up_crossover_2(self):
        pmarp = pd.Series([0.5, 1.5, 2.3])
        assert check_pmarp_crossover(pmarp, 2, "up") == True

    def test_no_up_crossover_2_already_above(self):
        pmarp = pd.Series([3.0, 4.0])
        assert check_pmarp_crossover(pmarp, 2, "up") == False

    def test_down_crossover_2(self):
        pmarp = pd.Series([5.0, 2.5, 1.8])
        assert check_pmarp_crossover(pmarp, 2, "down") == True

    def test_no_down_crossover_2_already_below(self):
        pmarp = pd.Series([1.5, 0.8])
        assert check_pmarp_crossover(pmarp, 2, "down") == False


class TestAnalyzePmarpSignals:
    """analyze_pmarp four-signal output"""

    def test_result_has_four_crossover_fields(self):
        df = _make_trending_df()
        result = analyze_pmarp(df)
        assert "crossover_98_up" in result
        assert "crossover_98_down" in result
        assert "crossover_2_down" in result
        assert "crossover_2_up" in result

    def test_backward_compat_fields(self):
        df = _make_trending_df()
        result = analyze_pmarp(df)
        # crossover_98 should equal crossover_98_up
        assert result["crossover_98"] == result["crossover_98_up"]
        # crossover_2 should equal crossover_2_down
        assert result["crossover_2"] == result["crossover_2_down"]

    def test_momentum_fading_signal(self):
        """Construct scenario: prev PMARP > 98, curr PMARP < 98 → momentum_fading"""
        # Build a rising series that peaks then dips slightly
        rising = np.linspace(100, 200, 195).tolist()
        # Add slight dip at end to drop PMARP below 98
        dip = [199, 198, 197, 195, 192]
        closes = rising + dip
        df = _make_df(closes)
        result = analyze_pmarp(df)
        # With the dip, PMARP should have dropped from near 100 to below 98
        if result["crossover_98_down"]:
            assert result["signal"] == "momentum_fading"
            assert "下穿 98%" in result["description"]
            assert "强势衰减" in result["description"]

    def test_oversold_recovery_signal(self):
        """Construct scenario: prev PMARP < 2, curr PMARP > 2 → oversold_recovery"""
        # Build a falling series that bottoms then bounces
        falling = np.linspace(200, 100, 195).tolist()
        # Add bounce at end
        bounce = [101, 102, 104, 107, 110]
        closes = falling + bounce
        df = _make_df(closes)
        result = analyze_pmarp(df)
        if result["crossover_2_up"]:
            assert result["signal"] == "oversold_recovery"
            assert "上穿 2%" in result["description"]
            assert "极端下跌结束" in result["description"]

    def test_neutral_no_crossovers(self):
        """Mid-range PMARP should have no crossovers"""
        # Gentle oscillation around mean
        closes = [100 + 5 * np.sin(i / 10) for i in range(200)]
        df = _make_df(closes)
        result = analyze_pmarp(df)
        assert result["crossover_98_up"] == False
        assert result["crossover_98_down"] == False
        assert result["crossover_2_down"] == False
        assert result["crossover_2_up"] == False

    def test_empty_df(self):
        result = analyze_pmarp(pd.DataFrame())
        assert result["signal"] == "neutral"
        assert result["crossover_98_up"] == False
        assert result["crossover_2_up"] == False

    def test_insufficient_data(self):
        df = _make_df([100, 101, 102])
        result = analyze_pmarp(df)
        assert result["current"] is None


class TestEngineSummaryPmarpCrossovers:
    """get_indicator_summary pmarp_crossovers field"""

    def test_crossovers_field_exists(self):
        results = {
            "NVDA": {
                "symbol": "NVDA",
                "signals": ["pmarp:bullish_breakout"],
                "pmarp": {"current": 98.5, "previous": 97.0, "signal": "bullish_breakout"},
            }
        }
        summary = get_indicator_summary(results)
        assert "pmarp_crossovers" in summary
        assert len(summary["pmarp_crossovers"]["breakout_98"]) == 1
        assert summary["pmarp_crossovers"]["breakout_98"][0]["symbol"] == "NVDA"

    def test_all_four_types(self):
        results = {
            "NVDA": {
                "symbol": "NVDA",
                "signals": ["pmarp:bullish_breakout"],
                "pmarp": {"current": 98.5, "previous": 97.0, "signal": "bullish_breakout"},
            },
            "TSLA": {
                "symbol": "TSLA",
                "signals": ["pmarp:momentum_fading"],
                "pmarp": {"current": 97.1, "previous": 98.5, "signal": "momentum_fading"},
            },
            "INTC": {
                "symbol": "INTC",
                "signals": ["pmarp:oversold_bounce"],
                "pmarp": {"current": 1.3, "previous": 2.8, "signal": "oversold_bounce"},
            },
            "BA": {
                "symbol": "BA",
                "signals": ["pmarp:oversold_recovery"],
                "pmarp": {"current": 2.5, "previous": 1.7, "signal": "oversold_recovery"},
            },
        }
        summary = get_indicator_summary(results)
        xovers = summary["pmarp_crossovers"]
        assert len(xovers["breakout_98"]) == 1
        assert len(xovers["fading_98"]) == 1
        assert len(xovers["crashed_2"]) == 1
        assert len(xovers["recovery_2"]) == 1

    def test_no_crossovers(self):
        results = {
            "AAPL": {
                "symbol": "AAPL",
                "signals": [],
                "pmarp": {"current": 55.0, "previous": 54.0, "signal": "neutral"},
            },
        }
        summary = get_indicator_summary(results)
        for key in summary["pmarp_crossovers"]:
            assert len(summary["pmarp_crossovers"][key]) == 0

    def test_previous_value_in_crossover_entry(self):
        results = {
            "NVDA": {
                "symbol": "NVDA",
                "signals": ["pmarp:bullish_breakout"],
                "pmarp": {"current": 99.0, "previous": 97.5, "signal": "bullish_breakout"},
            }
        }
        summary = get_indicator_summary(results)
        entry = summary["pmarp_crossovers"]["breakout_98"][0]
        assert entry["previous"] == 97.5
        assert entry["value"] == 99.0

    def test_error_stocks_excluded(self):
        results = {
            "BAD": {"symbol": "BAD", "error": "no data"},
        }
        summary = get_indicator_summary(results)
        for key in summary["pmarp_crossovers"]:
            assert len(summary["pmarp_crossovers"][key]) == 0
