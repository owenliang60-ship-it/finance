"""Tests for scripts/scan_themes.py — momentum signal detection, theme matching, report formatting.

Tests:
1. has_momentum_signal: RS Rating B trigger
2. has_momentum_signal: RS Rating C trigger
3. has_momentum_signal: DV acceleration trigger
4. has_momentum_signal: RVOL sustained trigger
5. has_momentum_signal: PMARP breakout trigger
6. has_momentum_signal: no signal
7. get_momentum_tickers: returns sorted list
8. match_themes: matches tickers to themes
9. match_themes: no overlap
10. format_theme_report: produces valid report
"""
import pytest
from datetime import datetime
from pathlib import Path

import pandas as pd

# Patch sys.path before importing
import sys
sys.path.insert(0, str(Path(__file__).parent.parent))

from scripts.scan_themes import (
    has_momentum_signal,
    get_momentum_tickers,
    match_themes,
    format_theme_report,
)


# ---------------------------------------------------------------------------
# Helpers — mock momentum results
# ---------------------------------------------------------------------------

def _make_rs_df(data):
    """Create a mock RS Rating DataFrame."""
    return pd.DataFrame(data)


def _make_dv_df(data):
    """Create a mock DV acceleration DataFrame."""
    return pd.DataFrame(data)


EMPTY_MOMENTUM = {
    "rs_rating_b": pd.DataFrame(columns=["symbol", "rs_rank"]),
    "rs_rating_c": pd.DataFrame(columns=["symbol", "rs_rank"]),
    "dv_acceleration": pd.DataFrame(columns=["symbol", "signal"]),
    "rvol_sustained": [],
    "symbols_scanned": 0,
    "price_dict_size": 0,
}

EMPTY_INDICATOR_SUMMARY = {
    "total": 0,
    "with_signals": 0,
    "errors": 0,
    "signals": {},
    "top_pmarp": [],
    "top_rvol": [],
    "low_pmarp": [],
    "pmarp_crossovers": {
        "breakout_98": [],
        "fading_98": [],
        "crashed_2": [],
        "recovery_2": [],
    },
}


# ---------------------------------------------------------------------------
# Tests: has_momentum_signal
# ---------------------------------------------------------------------------

class TestHasMomentumSignal:
    """Tests for has_momentum_signal()."""

    def test_rs_rating_b_trigger(self):
        """RS Rating B >= threshold triggers."""
        momentum = dict(EMPTY_MOMENTUM)
        momentum["rs_rating_b"] = _make_rs_df([
            {"symbol": "NVDA", "rs_rank": 95},
            {"symbol": "AAPL", "rs_rank": 50},
        ])
        assert has_momentum_signal("NVDA", momentum, EMPTY_INDICATOR_SUMMARY) is True
        assert has_momentum_signal("AAPL", momentum, EMPTY_INDICATOR_SUMMARY) is False

    def test_rs_rating_c_trigger(self):
        """RS Rating C >= threshold triggers."""
        momentum = dict(EMPTY_MOMENTUM)
        momentum["rs_rating_c"] = _make_rs_df([
            {"symbol": "TSLA", "rs_rank": 85},
        ])
        assert has_momentum_signal("TSLA", momentum, EMPTY_INDICATOR_SUMMARY) is True

    def test_dv_acceleration_trigger(self):
        """DV acceleration signal=True triggers."""
        momentum = dict(EMPTY_MOMENTUM)
        momentum["dv_acceleration"] = _make_dv_df([
            {"symbol": "AMD", "signal": True, "ratio": 2.0},
            {"symbol": "INTC", "signal": False, "ratio": 1.0},
        ])
        assert has_momentum_signal("AMD", momentum, EMPTY_INDICATOR_SUMMARY) is True
        assert has_momentum_signal("INTC", momentum, EMPTY_INDICATOR_SUMMARY) is False

    def test_rvol_sustained_trigger(self):
        """RVOL sustained entry triggers."""
        momentum = dict(EMPTY_MOMENTUM)
        momentum["rvol_sustained"] = [
            {"symbol": "META", "level": "sustained_3d", "values": [3.0, 2.5, 2.1]},
        ]
        assert has_momentum_signal("META", momentum, EMPTY_INDICATOR_SUMMARY) is True
        assert has_momentum_signal("GOOG", momentum, EMPTY_INDICATOR_SUMMARY) is False

    def test_pmarp_breakout_trigger(self):
        """PMARP breakout_98 triggers."""
        summary = dict(EMPTY_INDICATOR_SUMMARY)
        summary["pmarp_crossovers"] = {
            "breakout_98": [{"symbol": "AVGO", "value": 99.0}],
            "fading_98": [],
            "crashed_2": [],
            "recovery_2": [],
        }
        assert has_momentum_signal("AVGO", EMPTY_MOMENTUM, summary) is True

    def test_pmarp_recovery_trigger(self):
        """PMARP recovery_2 triggers."""
        summary = dict(EMPTY_INDICATOR_SUMMARY)
        summary["pmarp_crossovers"] = {
            "breakout_98": [],
            "fading_98": [],
            "crashed_2": [],
            "recovery_2": [{"symbol": "BA", "value": 3.0}],
        }
        assert has_momentum_signal("BA", EMPTY_MOMENTUM, summary) is True

    def test_no_signal(self):
        """No signal for unlisted ticker."""
        assert has_momentum_signal("ZZZZZ", EMPTY_MOMENTUM, EMPTY_INDICATOR_SUMMARY) is False

    def test_custom_threshold(self):
        """Custom RS threshold works."""
        momentum = dict(EMPTY_MOMENTUM)
        momentum["rs_rating_b"] = _make_rs_df([
            {"symbol": "NVDA", "rs_rank": 70},
        ])
        assert has_momentum_signal("NVDA", momentum, EMPTY_INDICATOR_SUMMARY, rs_threshold=80) is False
        assert has_momentum_signal("NVDA", momentum, EMPTY_INDICATOR_SUMMARY, rs_threshold=60) is True


# ---------------------------------------------------------------------------
# Tests: get_momentum_tickers
# ---------------------------------------------------------------------------

class TestGetMomentumTickers:
    """Tests for get_momentum_tickers()."""

    def test_returns_sorted_list(self):
        """Returns sorted list of tickers with momentum signals."""
        momentum = dict(EMPTY_MOMENTUM)
        momentum["rs_rating_b"] = _make_rs_df([
            {"symbol": "TSLA", "rs_rank": 95},
            {"symbol": "NVDA", "rs_rank": 90},
            {"symbol": "AAPL", "rs_rank": 50},
        ])
        result = get_momentum_tickers(
            momentum, EMPTY_INDICATOR_SUMMARY, ["NVDA", "TSLA", "AAPL"]
        )
        assert result == ["NVDA", "TSLA"]

    def test_empty_when_no_signals(self):
        """Returns empty list when no momentum signals."""
        result = get_momentum_tickers(
            EMPTY_MOMENTUM, EMPTY_INDICATOR_SUMMARY, ["AAPL", "GOOG"]
        )
        assert result == []


# ---------------------------------------------------------------------------
# Tests: match_themes
# ---------------------------------------------------------------------------

class TestMatchThemes:
    """Tests for match_themes()."""

    def test_matches_tickers_to_themes(self):
        """Tickers are matched to correct themes."""
        seed = {
            "ai_chip": {"keywords": ["GPU"], "tickers": ["NVDA", "AMD"]},
            "memory": {"keywords": ["DRAM"], "tickers": ["MU"]},
            "fintech": {"keywords": ["payments"], "tickers": ["V", "MA"]},
        }
        result = match_themes(["NVDA", "MU", "AAPL"], seed=seed)

        assert "ai_chip" in result
        assert result["ai_chip"] == ["NVDA"]
        assert "memory" in result
        assert result["memory"] == ["MU"]
        assert "fintech" not in result

    def test_no_overlap(self):
        """No tickers match any theme."""
        seed = {
            "ai_chip": {"keywords": ["GPU"], "tickers": ["NVDA"]},
        }
        result = match_themes(["AAPL", "GOOG"], seed=seed)
        assert result == {}

    def test_empty_input(self):
        """Empty ticker list returns empty themes."""
        result = match_themes([])
        assert result == {}

    def test_uses_default_seed(self):
        """Uses THEME_KEYWORDS_SEED by default."""
        result = match_themes(["NVDA", "AMD"])
        assert "ai_chip" in result
        assert "NVDA" in result["ai_chip"]


# ---------------------------------------------------------------------------
# Tests: format_theme_report
# ---------------------------------------------------------------------------

class TestFormatThemeReport:
    """Tests for format_theme_report()."""

    def test_produces_valid_report(self):
        """Report contains all sections."""
        report = format_theme_report(
            momentum_tickers=["NVDA", "TSLA"],
            theme_map={"ai_chip": ["NVDA"]},
            cluster_result={"clusters": {"0": ["NVDA", "AMD"]}},
            elapsed=42.0,
        )

        assert "A. 动量信号" in report
        assert "B. 主题热力图" in report
        assert "C. 聚类周报" in report
        assert "D. 建议深度分析" in report
        assert "NVDA" in report
        assert "42s" in report

    def test_empty_report(self):
        """Report works with empty data."""
        report = format_theme_report(
            momentum_tickers=[],
            theme_map={},
            cluster_result={},
            elapsed=1.0,
        )
        assert "无动量信号" in report
        assert "无主题信号" in report

    def test_cluster_display(self):
        """Cluster section shows member counts."""
        report = format_theme_report(
            momentum_tickers=["NVDA"],
            theme_map={},
            cluster_result={"clusters": {"0": ["NVDA", "AMD"], "1": ["TSLA"]}},
            elapsed=1.0,
        )
        assert "2 个集群" in report
