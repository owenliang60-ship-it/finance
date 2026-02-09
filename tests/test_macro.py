"""
Tests for the macro data pipeline: MacroSnapshot, macro_fetcher, regime, pipeline integration.

Unit tests use mocks (no API calls). Integration tests require FRED_API_KEY.
"""
import json
import os
import pytest
from datetime import datetime, timedelta
from unittest.mock import patch, MagicMock

import sys
sys.path.insert(0, str(__file__).rsplit("/tests", 1)[0])

from terminal.macro_snapshot import MacroSnapshot
from terminal.macro_fetcher import (
    _classify_vix,
    _classify_dxy_trend,
    _assess_regime,
    _confidence,
    _latest_value,
    _trend_bp,
    _trend_raw,
    _cache_is_fresh,
    fetch_macro_snapshot,
    get_macro_snapshot,
    CACHE_FILE,
)
from terminal.regime import MarketRegime, RegimeAssessment, get_current_regime, get_regime_adjustment


# ===========================================================================
# MacroSnapshot dataclass tests
# ===========================================================================

class TestMacroSnapshot:
    """Tests for MacroSnapshot dataclass."""

    def test_default_values(self):
        ms = MacroSnapshot()
        assert ms.fetched_at == ""
        assert ms.us10y is None
        assert ms.vix is None
        assert ms.regime == "NEUTRAL"
        assert ms.vix_regime == "UNKNOWN"

    def test_serialization_roundtrip(self):
        ms = MacroSnapshot(
            us10y=4.25, us2y=4.15, vix=18.5,
            vix_regime="NORMAL", regime="NEUTRAL",
        )
        d = ms.to_dict()
        ms2 = MacroSnapshot.from_dict(d)
        assert ms2.us10y == 4.25
        assert ms2.us2y == 4.15
        assert ms2.vix == 18.5

    def test_json_roundtrip(self):
        ms = MacroSnapshot(us10y=4.25, dxy=104.5)
        j = ms.to_json()
        ms2 = MacroSnapshot.from_json(j)
        assert ms2.us10y == 4.25
        assert ms2.dxy == 104.5

    def test_from_dict_ignores_unknown_fields(self):
        ms = MacroSnapshot.from_dict({"us10y": 3.5, "bogus_field": 999})
        assert ms.us10y == 3.5

    def test_data_source_count(self):
        ms = MacroSnapshot()
        assert ms.data_source_count == 0

        ms = MacroSnapshot(us10y=4.0, vix=18.0, dxy=104.0)
        assert ms.data_source_count == 3

        # All 16 primary fields
        ms = MacroSnapshot(
            us2y=4.0, us5y=4.1, us10y=4.2, us30y=4.5,
            spread_10y_2y=0.2, spread_10y_3m=0.1,
            fed_funds=4.3, cpi_yoy=2.5,
            gdp_growth=2.8, unemployment=4.1,
            vix=18.0, hy_spread=3.0,
            dxy=104.0, usdjpy=153.0, japan_rate=0.5,
            fed_balance_sheet_t=6.9,
        )
        assert ms.data_source_count == 16

    def test_format_for_prompt_basic(self):
        ms = MacroSnapshot(us10y=4.25, vix=18.5, vix_regime="NORMAL")
        text = ms.format_for_prompt()
        assert "### Macro Environment" in text
        assert "US 10Y: 4.25%" in text
        assert "VIX: 18.5 (NORMAL)" in text
        assert "Regime: NEUTRAL" in text

    def test_format_for_prompt_with_trends(self):
        ms = MacroSnapshot(
            us10y=4.25, us10y_30d_chg_bp=35,
            vix=18.5, vix_regime="NORMAL", vix_30d_chg=-2.3,
        )
        text = ms.format_for_prompt()
        assert "+35bp 30d" in text
        assert "-2.3 30d" in text

    def test_format_for_prompt_empty(self):
        """Empty snapshot still returns header + regime."""
        ms = MacroSnapshot()
        text = ms.format_for_prompt()
        assert "### Macro Environment" in text
        assert "Regime: NEUTRAL" in text

    def test_format_for_prompt_hy_spread_in_bp(self):
        ms = MacroSnapshot(hy_spread=3.15)
        text = ms.format_for_prompt()
        assert "HY Spread: 315bp" in text  # percentage points → basis points


# ===========================================================================
# VIX regime classification
# ===========================================================================

class TestVIXRegime:
    def test_low(self):
        assert _classify_vix(12.0) == "LOW"
        assert _classify_vix(14.99) == "LOW"

    def test_normal(self):
        assert _classify_vix(15.0) == "NORMAL"
        assert _classify_vix(24.99) == "NORMAL"

    def test_elevated(self):
        assert _classify_vix(25.0) == "ELEVATED"
        assert _classify_vix(34.99) == "ELEVATED"

    def test_panic(self):
        assert _classify_vix(35.0) == "PANIC"
        assert _classify_vix(80.0) == "PANIC"

    def test_none(self):
        assert _classify_vix(None) == "UNKNOWN"


# ===========================================================================
# DXY trend classification
# ===========================================================================

class TestDXYTrend:
    def test_strengthening(self):
        # Current is 5% above SMA
        series = [{"date": "2024-01-01", "value": 110.0}]
        series += [{"date": f"2024-01-{i:02d}", "value": 100.0} for i in range(2, 52)]
        assert _classify_dxy_trend(series) == "STRENGTHENING"

    def test_weakening(self):
        series = [{"date": "2024-01-01", "value": 95.0}]
        series += [{"date": f"2024-01-{i:02d}", "value": 100.0} for i in range(2, 52)]
        assert _classify_dxy_trend(series) == "WEAKENING"

    def test_stable(self):
        series = [{"date": f"2024-01-{i:02d}", "value": 100.0} for i in range(1, 52)]
        assert _classify_dxy_trend(series) == "STABLE"

    def test_insufficient_data(self):
        assert _classify_dxy_trend([]) == "UNKNOWN"
        assert _classify_dxy_trend([{"date": "2024-01-01", "value": 100}]) == "UNKNOWN"


# ===========================================================================
# Derived value helpers
# ===========================================================================

class TestDerivedValues:
    def test_latest_value(self):
        assert _latest_value([{"date": "2024-01-01", "value": 4.5}]) == 4.5
        assert _latest_value([]) is None

    def test_trend_bp(self):
        series = [{"date": f"d{i}", "value": 4.5 + i * 0.01} for i in range(40)]
        # series[0]=4.50, series[30]=4.80 → change = (4.50 - 4.80) * 100 = -30bp
        result = _trend_bp(series, lookback=30)
        assert result == -30

    def test_trend_bp_insufficient_data(self):
        assert _trend_bp([{"date": "d1", "value": 4.5}], lookback=30) is None

    def test_trend_raw(self):
        series = [{"date": f"d{i}", "value": 20.0 - i * 0.1} for i in range(40)]
        result = _trend_raw(series, lookback=30)
        assert result == 3.0  # 20.0 - 17.0

    def test_term_premium_derived(self):
        ms = MacroSnapshot(us30y=4.72, us2y=4.15)
        # term_premium is set by fetcher, not auto-computed
        assert ms.term_premium is None

    def test_real_rate_derived(self):
        ms = MacroSnapshot(us10y=4.48, cpi_yoy=2.5)
        assert ms.real_rate_10y is None  # set by fetcher


# ===========================================================================
# Regime decision tree
# ===========================================================================

class TestRegimeDecisionTree:
    def test_crisis_high_vix(self):
        ms = MacroSnapshot(vix=50.0)
        regime, conf, rationale = _assess_regime(ms)
        assert regime == "CRISIS"
        assert "50.0" in rationale

    def test_crisis_vix_plus_inversion(self):
        ms = MacroSnapshot(vix=38.0, spread_10y_2y=-0.8)
        regime, conf, rationale = _assess_regime(ms)
        assert regime == "CRISIS"

    def test_risk_off_vix_inversion(self):
        ms = MacroSnapshot(vix=28.0, spread_10y_2y=-0.2)
        regime, conf, rationale = _assess_regime(ms)
        assert regime == "RISK_OFF"

    def test_risk_off_gdp_negative(self):
        ms = MacroSnapshot(gdp_growth=-1.5)
        regime, conf, rationale = _assess_regime(ms)
        assert regime == "RISK_OFF"
        assert "contracting" in rationale.lower()

    def test_risk_off_hy_spread_wide(self):
        ms = MacroSnapshot(hy_spread=6.0)
        regime, conf, rationale = _assess_regime(ms)
        assert regime == "RISK_OFF"

    def test_risk_on(self):
        ms = MacroSnapshot(vix=14.0, spread_10y_2y=0.8, gdp_growth=3.0)
        regime, conf, rationale = _assess_regime(ms)
        assert regime == "RISK_ON"

    def test_neutral_default(self):
        ms = MacroSnapshot(vix=20.0, spread_10y_2y=0.3, gdp_growth=1.5)
        regime, conf, rationale = _assess_regime(ms)
        assert regime == "NEUTRAL"

    def test_neutral_empty(self):
        ms = MacroSnapshot()
        regime, conf, rationale = _assess_regime(ms)
        assert regime == "NEUTRAL"
        assert conf == "low"


# ===========================================================================
# Confidence calculation
# ===========================================================================

class TestConfidence:
    def test_high(self):
        ms = MacroSnapshot(
            us2y=4.0, us5y=4.1, us10y=4.2, us30y=4.5,
            spread_10y_2y=0.2, spread_10y_3m=0.1,
            fed_funds=4.3, cpi_yoy=2.5,
            gdp_growth=2.8, unemployment=4.1,
            vix=18.0, hy_spread=3.0,
        )
        assert _confidence(ms) == "high"

    def test_medium(self):
        ms = MacroSnapshot(us10y=4.2, vix=18.0, dxy=104.0, fed_funds=4.3)
        assert _confidence(ms) == "medium"

    def test_low(self):
        ms = MacroSnapshot(us10y=4.2, vix=18.0)
        assert _confidence(ms) == "low"


# ===========================================================================
# Cache freshness
# ===========================================================================

class TestCacheFreshness:
    def test_fresh_cache(self):
        ms = MacroSnapshot(fetched_at=datetime.now().isoformat())
        assert _cache_is_fresh(ms) is True

    def test_stale_cache(self):
        old = (datetime.now() - timedelta(hours=13)).isoformat()
        ms = MacroSnapshot(fetched_at=old)
        assert _cache_is_fresh(ms) is False

    def test_empty_fetched_at(self):
        ms = MacroSnapshot(fetched_at="")
        assert _cache_is_fresh(ms) is False

    def test_invalid_fetched_at(self):
        ms = MacroSnapshot(fetched_at="not-a-date")
        assert _cache_is_fresh(ms) is False


# ===========================================================================
# regime.py integration (get_current_regime uses macro_fetcher)
# ===========================================================================

class TestRegimeIntegration:
    @patch("terminal.macro_fetcher.get_macro_snapshot")
    def test_get_current_regime_with_data(self, mock_get):
        mock_get.return_value = MacroSnapshot(
            vix=14.0, spread_10y_2y=0.8, gdp_growth=3.0,
            us2y=4.0, us5y=4.1, us10y=4.2, us30y=4.5,
            fed_funds=4.3, cpi_yoy=2.5, unemployment=4.1,
            regime="RISK_ON", regime_confidence="high",
            regime_rationale="Low VIX, positive curve, strong GDP",
        )
        result = get_current_regime()
        assert result.regime == MarketRegime.RISK_ON
        assert result.confidence == "high"
        assert result.data_sources > 0

    @patch("terminal.macro_fetcher.get_macro_snapshot")
    def test_get_current_regime_fallback(self, mock_get):
        mock_get.return_value = None
        result = get_current_regime()
        assert result.regime == MarketRegime.NEUTRAL
        assert result.confidence == "low"
        assert result.data_sources == 0

    def test_regime_adjustment_values(self):
        assert get_regime_adjustment(MarketRegime.RISK_ON) == 1.0
        assert get_regime_adjustment(MarketRegime.NEUTRAL) == 1.0
        assert get_regime_adjustment(MarketRegime.RISK_OFF) == 0.7
        assert get_regime_adjustment(MarketRegime.CRISIS) == 0.4


# ===========================================================================
# Pipeline format_context with macro
# ===========================================================================

class TestPipelineFormatContext:
    def test_format_context_with_macro(self):
        from terminal.pipeline import DataPackage
        ms = MacroSnapshot(
            us10y=4.25, us10y_30d_chg_bp=35,
            vix=18.5, vix_regime="NORMAL",
            regime="NEUTRAL", regime_confidence="medium",
        )
        pkg = DataPackage(symbol="NVDA", macro=ms)
        ctx = pkg.format_context()
        assert "### Macro Environment" in ctx
        assert "US 10Y: 4.25%" in ctx
        assert "VIX: 18.5 (NORMAL)" in ctx

    def test_format_context_without_macro(self):
        from terminal.pipeline import DataPackage
        pkg = DataPackage(symbol="NVDA")
        ctx = pkg.format_context()
        assert "### Macro Environment" not in ctx


# ===========================================================================
# Pipeline calculate_position with regime
# ===========================================================================

class TestCalculatePositionRegime:
    @patch("terminal.regime.get_current_regime")
    @patch("terminal.regime.get_regime_adjustment")
    def test_risk_off_reduces_position(self, mock_adj, mock_regime):
        from terminal.pipeline import calculate_position
        mock_regime.return_value = RegimeAssessment(
            regime=MarketRegime.RISK_OFF,
            confidence="high",
            rationale="test",
            data_sources=10,
        )
        mock_adj.return_value = 0.7

        result = calculate_position(
            symbol="NVDA", dna="A", timing="A",
            total_capital=1_000_000, evidence_count=5,
        )
        assert "regime_adjustment" in result
        assert result["regime_adjustment"]["multiplier"] == 0.7
        assert result["pre_regime_position_pct"] > result["target_position_pct"]

    @patch("terminal.regime.get_current_regime")
    @patch("terminal.regime.get_regime_adjustment")
    def test_neutral_no_adjustment(self, mock_adj, mock_regime):
        from terminal.pipeline import calculate_position
        mock_regime.return_value = RegimeAssessment(
            regime=MarketRegime.NEUTRAL,
            confidence="medium",
            data_sources=8,
        )
        mock_adj.return_value = 1.0

        result = calculate_position(
            symbol="NVDA", dna="A", timing="A",
            total_capital=1_000_000, evidence_count=5,
        )
        assert "regime_adjustment" not in result

    @patch("terminal.regime.get_current_regime")
    @patch("terminal.regime.get_regime_adjustment")
    def test_apply_regime_false(self, mock_adj, mock_regime):
        from terminal.pipeline import calculate_position
        result = calculate_position(
            symbol="NVDA", dna="A", timing="A",
            total_capital=1_000_000, evidence_count=5,
            apply_regime=False,
        )
        mock_regime.assert_not_called()
        assert "regime_adjustment" not in result


# ===========================================================================
# Integration tests (require FRED_API_KEY)
# ===========================================================================

@pytest.mark.skipif(
    not os.getenv("FRED_API_KEY"),
    reason="FRED_API_KEY not set"
)
class TestIntegration:
    def test_fetch_macro_snapshot_e2e(self):
        """Full end-to-end FRED fetch."""
        snapshot = fetch_macro_snapshot()
        assert snapshot is not None
        assert snapshot.data_source_count > 0
        assert snapshot.regime in ("RISK_ON", "NEUTRAL", "RISK_OFF", "CRISIS")
        assert snapshot.fetched_at != ""
        # Should have at least some yield data
        assert snapshot.us10y is not None

    def test_get_macro_snapshot_caches(self):
        """get_macro_snapshot should return cached data on second call."""
        s1 = get_macro_snapshot()
        s2 = get_macro_snapshot()
        # Second call should hit cache (same fetched_at)
        assert s1.fetched_at == s2.fetched_at

    def test_snapshot_format_for_prompt_has_data(self):
        """Formatted prompt should contain real data."""
        snapshot = fetch_macro_snapshot()
        text = snapshot.format_for_prompt()
        assert "### Macro Environment" in text
        assert "US 10Y:" in text
        assert "Regime:" in text
