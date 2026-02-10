"""Tests for analysis freshness system — staleness detection, timing refresh, evolution."""
import json
import sys
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from terminal.freshness import (
    AnalysisContext,
    FreshnessLevel,
    FreshnessReport,
    build_analysis_context,
    check_freshness,
    check_all_freshness,
    prepare_timing_refresh_prompt,
    apply_timing_refresh,
    get_evolution_timeline,
    format_evolution_text,
    YELLOW_DAYS,
    RED_DAYS,
    YELLOW_PRICE_PCT,
    RED_PRICE_PCT,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_oprms(
    dna="S",
    timing="B",
    timing_coeff=0.5,
    days_ago=5,
    price=400.0,
    regime="RISK_ON",
    earnings_date="2025-10-28",
    source="pipeline",
    with_context=True,
):
    """Build a mock OPRMS dict with analysis_context."""
    analyzed_at = (datetime.now() - timedelta(days=days_ago)).isoformat()
    oprms = {
        "dna": dna,
        "timing": timing,
        "timing_coeff": timing_coeff,
        "evidence": ["source1", "source2", "source3"],
        "investment_bucket": "core",
        "updated_at": analyzed_at,
        "symbol": "TEST",
    }
    if with_context:
        oprms["analysis_context"] = {
            "analyzed_at": analyzed_at,
            "price_at_analysis": price,
            "price_date": (datetime.now() - timedelta(days=days_ago)).strftime("%Y-%m-%d"),
            "regime": regime,
            "vix": 18.5,
            "fundamental_date": "2025-09-30",
            "earnings_date": earnings_date,
            "depth": "full",
            "source": source,
            "evidence_count": 3,
        }
    return oprms


def _make_data_package(
    symbol="TEST",
    price=400.0,
    regime="RISK_ON",
    vix=18.5,
    income_date="2025-09-30",
    earnings_date="2025-10-28",
):
    """Build a mock DataPackage for testing build_analysis_context."""
    pkg = MagicMock()
    pkg.price = {"latest_close": price, "latest_date": "2026-02-10"}
    pkg.latest_price = price
    pkg.macro = MagicMock()
    pkg.macro.regime = regime
    pkg.macro.vix = vix
    pkg.income = [{"date": income_date}]
    pkg.earnings_calendar = [{"date": earnings_date}]
    pkg.company_record = MagicMock()
    pkg.company_record.oprms = {"evidence": ["a", "b", "c"]}
    return pkg


# ---------------------------------------------------------------------------
# 1. AnalysisContext serialization
# ---------------------------------------------------------------------------

class TestAnalysisContext:
    """AnalysisContext to_dict / from_dict round-trip."""

    def test_round_trip(self):
        ctx = AnalysisContext(
            analyzed_at="2026-02-10T12:00:00",
            price_at_analysis=413.6,
            price_date="2026-02-10",
            regime="RISK_ON",
            vix=18.5,
            fundamental_date="2025-09-30",
            earnings_date="2025-10-28",
            depth="full",
            source="pipeline",
            evidence_count=5,
        )
        d = ctx.to_dict()
        restored = AnalysisContext.from_dict(d)
        assert restored.analyzed_at == ctx.analyzed_at
        assert restored.price_at_analysis == 413.6
        assert restored.regime == "RISK_ON"
        assert restored.evidence_count == 5

    def test_from_dict_ignores_unknown(self):
        data = {
            "analyzed_at": "2026-02-10",
            "unknown_field": "should_be_ignored",
            "price_at_analysis": 100.0,
        }
        ctx = AnalysisContext.from_dict(data)
        assert ctx.analyzed_at == "2026-02-10"
        assert ctx.price_at_analysis == 100.0
        assert not hasattr(ctx, "unknown_field")

    def test_defaults(self):
        ctx = AnalysisContext()
        d = ctx.to_dict()
        assert d["analyzed_at"] == ""
        assert d["price_at_analysis"] is None
        assert d["evidence_count"] == 0


# ---------------------------------------------------------------------------
# 2. build_analysis_context
# ---------------------------------------------------------------------------

class TestBuildAnalysisContext:
    def test_extracts_all_fields(self):
        pkg = _make_data_package()
        result = build_analysis_context(pkg, depth="full", source="pipeline")

        assert result["depth"] == "full"
        assert result["source"] == "pipeline"
        assert result["price_at_analysis"] == 400.0
        assert result["price_date"] == "2026-02-10"
        assert result["regime"] == "RISK_ON"
        assert result["vix"] == 18.5
        assert result["fundamental_date"] == "2025-09-30"
        assert result["earnings_date"] == "2025-10-28"
        assert result["evidence_count"] == 3

    def test_handles_missing_data(self):
        pkg = MagicMock()
        pkg.price = None
        pkg.latest_price = None
        pkg.macro = None
        pkg.income = []
        pkg.earnings_calendar = []
        pkg.company_record = None

        result = build_analysis_context(pkg, depth="quick", source="test")
        assert result["depth"] == "quick"
        assert result["price_at_analysis"] is None
        assert result["regime"] == ""
        assert result["fundamental_date"] == ""
        assert result["earnings_date"] == ""


# ---------------------------------------------------------------------------
# 3. check_freshness — GREEN
# ---------------------------------------------------------------------------

class TestFreshnessGreen:
    """Analysis is fresh: <14 days, price ±5%, same regime."""

    @patch("terminal.freshness._get_latest_earnings_date", return_value="2025-10-28")
    @patch("terminal.freshness._get_current_regime", return_value="RISK_ON")
    @patch("terminal.freshness._get_current_price", return_value=410.0)
    @patch("terminal.freshness.get_oprms")
    def test_green_all_stable(self, mock_oprms, mock_price, mock_regime, mock_earn):
        mock_oprms.return_value = _make_oprms(days_ago=5, price=400.0, regime="RISK_ON")
        report = check_freshness("TEST")
        assert report.level == FreshnessLevel.GREEN
        assert report.days_since_analysis == 5
        assert abs(report.price_change_pct - 2.5) < 0.1
        assert not report.regime_changed

    @patch("terminal.freshness._get_latest_earnings_date", return_value=None)
    @patch("terminal.freshness._get_current_regime", return_value=None)
    @patch("terminal.freshness._get_current_price", return_value=None)
    @patch("terminal.freshness.get_oprms")
    def test_green_missing_current_data(self, mock_oprms, mock_price, mock_regime, mock_earn):
        """If we can't get current data, don't escalate unnecessarily."""
        mock_oprms.return_value = _make_oprms(days_ago=3)
        report = check_freshness("TEST")
        assert report.level == FreshnessLevel.GREEN
        assert report.price_change_pct is None


# ---------------------------------------------------------------------------
# 4. check_freshness — YELLOW
# ---------------------------------------------------------------------------

class TestFreshnessYellow:
    """Partial changes: 14+ days, ±10% price, or regime shift."""

    @patch("terminal.freshness._get_latest_earnings_date", return_value="2025-10-28")
    @patch("terminal.freshness._get_current_regime", return_value="RISK_ON")
    @patch("terminal.freshness._get_current_price", return_value=405.0)
    @patch("terminal.freshness.get_oprms")
    def test_yellow_age(self, mock_oprms, mock_price, mock_regime, mock_earn):
        mock_oprms.return_value = _make_oprms(days_ago=16, price=400.0)
        report = check_freshness("TEST")
        assert report.level == FreshnessLevel.YELLOW
        assert any("16 days old" in r for r in report.reasons)

    @patch("terminal.freshness._get_latest_earnings_date", return_value="2025-10-28")
    @patch("terminal.freshness._get_current_regime", return_value="RISK_ON")
    @patch("terminal.freshness._get_current_price", return_value=445.0)
    @patch("terminal.freshness.get_oprms")
    def test_yellow_price(self, mock_oprms, mock_price, mock_regime, mock_earn):
        """Price moved +11.25% → YELLOW."""
        mock_oprms.return_value = _make_oprms(days_ago=5, price=400.0)
        report = check_freshness("TEST")
        assert report.level == FreshnessLevel.YELLOW
        assert any("Price changed" in r for r in report.reasons)

    @patch("terminal.freshness._get_latest_earnings_date", return_value="2025-10-28")
    @patch("terminal.freshness._get_current_regime", return_value="RISK_OFF")
    @patch("terminal.freshness._get_current_price", return_value=400.0)
    @patch("terminal.freshness.get_oprms")
    def test_yellow_regime_change(self, mock_oprms, mock_price, mock_regime, mock_earn):
        mock_oprms.return_value = _make_oprms(days_ago=5, price=400.0, regime="RISK_ON")
        report = check_freshness("TEST")
        assert report.level == FreshnessLevel.YELLOW
        assert report.regime_changed
        assert any("Regime changed" in r for r in report.reasons)


# ---------------------------------------------------------------------------
# 5. check_freshness — RED
# ---------------------------------------------------------------------------

class TestFreshnessRed:
    """Major changes: 30+ days, ±20% price, new earnings, no context."""

    @patch("terminal.freshness._get_latest_earnings_date", return_value="2025-10-28")
    @patch("terminal.freshness._get_current_regime", return_value="RISK_ON")
    @patch("terminal.freshness._get_current_price", return_value=400.0)
    @patch("terminal.freshness.get_oprms")
    def test_red_age(self, mock_oprms, mock_price, mock_regime, mock_earn):
        mock_oprms.return_value = _make_oprms(days_ago=35, price=400.0)
        report = check_freshness("TEST")
        assert report.level == FreshnessLevel.RED
        assert any("35 days old" in r for r in report.reasons)

    @patch("terminal.freshness._get_latest_earnings_date", return_value="2025-10-28")
    @patch("terminal.freshness._get_current_regime", return_value="RISK_ON")
    @patch("terminal.freshness._get_current_price", return_value=500.0)
    @patch("terminal.freshness.get_oprms")
    def test_red_price(self, mock_oprms, mock_price, mock_regime, mock_earn):
        """Price moved +25% → RED."""
        mock_oprms.return_value = _make_oprms(days_ago=5, price=400.0)
        report = check_freshness("TEST")
        assert report.level == FreshnessLevel.RED
        assert any("Price changed" in r for r in report.reasons)

    @patch("terminal.freshness._get_latest_earnings_date", return_value="2026-01-28")
    @patch("terminal.freshness._get_current_regime", return_value="RISK_ON")
    @patch("terminal.freshness._get_current_price", return_value=405.0)
    @patch("terminal.freshness.get_oprms")
    def test_red_new_earnings(self, mock_oprms, mock_price, mock_regime, mock_earn):
        mock_oprms.return_value = _make_oprms(
            days_ago=5, price=400.0, earnings_date="2025-10-28"
        )
        report = check_freshness("TEST")
        assert report.level == FreshnessLevel.RED
        assert report.new_earnings_available
        assert any("New earnings" in r for r in report.reasons)

    @patch("terminal.freshness.get_oprms")
    def test_red_no_context(self, mock_oprms):
        """Legacy OPRMS with no analysis_context → RED."""
        mock_oprms.return_value = _make_oprms(with_context=False)
        report = check_freshness("TEST")
        assert report.level == FreshnessLevel.RED
        assert any("No analysis context" in r for r in report.reasons)

    @patch("terminal.freshness.get_oprms")
    def test_red_no_oprms(self, mock_oprms):
        mock_oprms.return_value = None
        report = check_freshness("TEST")
        assert report.level == FreshnessLevel.RED
        assert any("No OPRMS" in r for r in report.reasons)


# ---------------------------------------------------------------------------
# 6. check_all_freshness — sorted by severity
# ---------------------------------------------------------------------------

class TestCheckAllFreshness:
    @patch("terminal.freshness.check_freshness")
    @patch("terminal.freshness.get_oprms")
    @patch("terminal.freshness.list_all_companies")
    def test_sorted_by_severity(self, mock_list, mock_oprms, mock_check):
        mock_list.return_value = ["AAPL", "MSFT", "GOOG"]
        mock_oprms.side_effect = [
            {"dna": "S"},  # AAPL has OPRMS
            {"dna": "A"},  # MSFT has OPRMS
            {"dna": "B"},  # GOOG has OPRMS
        ]
        # AAPL=GREEN, MSFT=RED, GOOG=YELLOW
        mock_check.side_effect = [
            FreshnessReport(symbol="AAPL", level=FreshnessLevel.GREEN),
            FreshnessReport(symbol="MSFT", level=FreshnessLevel.RED),
            FreshnessReport(symbol="GOOG", level=FreshnessLevel.YELLOW),
        ]
        reports = check_all_freshness()
        assert len(reports) == 3
        assert reports[0].level == FreshnessLevel.RED
        assert reports[0].symbol == "MSFT"
        assert reports[1].level == FreshnessLevel.YELLOW
        assert reports[2].level == FreshnessLevel.GREEN

    @patch("terminal.freshness.get_oprms")
    @patch("terminal.freshness.list_all_companies")
    def test_skips_companies_without_oprms(self, mock_list, mock_oprms):
        mock_list.return_value = ["AAPL", "NORATING"]
        mock_oprms.side_effect = [
            {"dna": "S"},  # AAPL has OPRMS
            None,          # NORATING has no OPRMS
        ]
        with patch("terminal.freshness.check_freshness") as mock_check:
            mock_check.return_value = FreshnessReport(
                symbol="AAPL", level=FreshnessLevel.GREEN
            )
            reports = check_all_freshness()
            assert len(reports) == 1
            assert reports[0].symbol == "AAPL"


# ---------------------------------------------------------------------------
# 7. Timing refresh prompt
# ---------------------------------------------------------------------------

class TestTimingRefreshPrompt:
    @patch("terminal.freshness._get_current_regime", return_value="RISK_ON")
    @patch("terminal.freshness._get_current_price", return_value=420.0)
    @patch("terminal.freshness.get_kill_conditions", return_value=[])
    @patch("terminal.freshness.get_oprms")
    def test_prompt_contains_key_data(self, mock_oprms, mock_kc, mock_price, mock_regime):
        mock_oprms.return_value = _make_oprms(dna="S", timing="B", timing_coeff=0.5, price=400.0)
        result = prepare_timing_refresh_prompt("TEST")
        assert result is not None
        assert "TEST" in result["prompt"]
        assert "DNA: S" in result["prompt"]
        assert result["context"]["current_price"] == 420.0
        assert result["context"]["dna"] == "S"
        assert result["context"]["old_timing"] == "B"

    @patch("terminal.freshness.get_oprms")
    def test_no_oprms_returns_none(self, mock_oprms):
        mock_oprms.return_value = None
        result = prepare_timing_refresh_prompt("NOEXIST")
        assert result is None


# ---------------------------------------------------------------------------
# 8. apply_timing_refresh — preserves DNA
# ---------------------------------------------------------------------------

class TestApplyTimingRefresh:
    @patch("terminal.freshness.get_macro_snapshot", create=True)
    @patch("terminal.freshness._get_current_regime", return_value="RISK_ON")
    @patch("terminal.freshness._get_current_price", return_value=420.0)
    @patch("terminal.freshness.save_oprms")
    @patch("terminal.freshness.get_oprms")
    def test_preserves_dna_updates_timing(
        self, mock_get, mock_save, mock_price, mock_regime, mock_macro
    ):
        mock_get.return_value = _make_oprms(dna="S", timing="B", timing_coeff=0.5)

        # Mock the macro snapshot import inside apply_timing_refresh
        mock_snap = MagicMock()
        mock_snap.vix = 20.0
        mock_macro.return_value = mock_snap

        refresh = {
            "timing": "A",
            "timing_coeff": 0.85,
            "rationale": "Price breakout confirmed",
        }

        with patch("terminal.freshness.get_macro_snapshot", return_value=mock_snap):
            apply_timing_refresh("TEST", refresh)

        # Verify save was called
        mock_save.assert_called_once()
        saved = mock_save.call_args[0][1]

        # DNA preserved
        assert saved["dna"] == "S"
        # Timing updated
        assert saved["timing"] == "A"
        assert saved["timing_coeff"] == 0.85
        # Context updated
        assert saved["analysis_context"]["source"] == "timing_refresh"
        assert saved["analysis_context"]["price_at_analysis"] == 420.0

    @patch("terminal.freshness.get_oprms")
    def test_raises_on_no_oprms(self, mock_get):
        mock_get.return_value = None
        with pytest.raises(ValueError, match="No OPRMS found"):
            apply_timing_refresh("TEST", {"timing": "A", "timing_coeff": 0.8})


# ---------------------------------------------------------------------------
# 9. Evolution timeline
# ---------------------------------------------------------------------------

class TestEvolutionTimeline:
    @patch("terminal.freshness.get_oprms_history")
    def test_timeline_with_history(self, mock_history):
        t1 = "2026-02-09T12:00:00"
        t2 = "2026-02-10T12:00:00"
        mock_history.return_value = [
            {
                "dna": "S", "timing": "B", "timing_coeff": 0.55,
                "updated_at": t1, "symbol": "MSFT",
                "analysis_context": {
                    "price_at_analysis": 413.6,
                    "regime": "RISK_ON",
                    "source": "pipeline",
                },
            },
            {
                "dna": "S", "timing": "B", "timing_coeff": 0.5,
                "updated_at": t2, "symbol": "MSFT",
                "analysis_context": {
                    "price_at_analysis": 418.6,
                    "regime": "RISK_ON",
                    "source": "deep_analysis_v2",
                },
            },
        ]
        result = get_evolution_timeline("MSFT")
        assert result["symbol"] == "MSFT"
        assert len(result["timeline"]) == 2

        # First entry: no delta
        first = result["timeline"][0]
        assert first["dna"] == "S"
        assert first["delta"]["timing_coeff_delta"] is None

        # Second entry: has delta
        second = result["timeline"][1]
        assert second["timing_coeff"] == 0.5
        assert second["delta"]["timing_coeff_delta"] == -0.05
        assert second["delta"]["price_change_pct"] is not None

        # Summary
        assert result["summary"]["dna_stable"] is True
        assert result["summary"]["timing_changes"] == 1
        assert result["summary"]["total_entries"] == 2

    @patch("terminal.freshness.get_oprms_history")
    def test_empty_history(self, mock_history):
        mock_history.return_value = []
        result = get_evolution_timeline("NEW")
        assert result["timeline"] == []
        assert result["summary"]["dna_stable"] is True

    @patch("terminal.freshness.get_oprms_history")
    def test_dna_change_detected(self, mock_history):
        mock_history.return_value = [
            {"dna": "B", "timing": "C", "timing_coeff": 0.2, "updated_at": "2026-01-01"},
            {"dna": "S", "timing": "A", "timing_coeff": 0.9, "updated_at": "2026-02-01"},
        ]
        result = get_evolution_timeline("TEST")
        assert result["summary"]["dna_stable"] is False


# ---------------------------------------------------------------------------
# 10. format_evolution_text — markdown output
# ---------------------------------------------------------------------------

class TestFormatEvolutionText:
    @patch("terminal.freshness.get_oprms_history")
    def test_markdown_table(self, mock_history):
        mock_history.return_value = [
            {
                "dna": "S", "timing": "B", "timing_coeff": 0.55,
                "updated_at": "2026-02-09T12:00:00",
                "analysis_context": {"price_at_analysis": 413.6, "regime": "RISK_ON", "source": "pipeline"},
            },
        ]
        timeline = get_evolution_timeline("MSFT")
        text = format_evolution_text(timeline)
        assert "MSFT" in text
        assert "DNA" in text
        assert "Timing" in text
        assert "|" in text  # markdown table
        assert "$413.6" in text

    def test_empty_timeline(self):
        data = {"symbol": "NEW", "timeline": [], "summary": {}}
        text = format_evolution_text(data)
        assert "无评级历史" in text


# ---------------------------------------------------------------------------
# 11. FreshnessReport.to_dict
# ---------------------------------------------------------------------------

class TestFreshnessReportDict:
    def test_to_dict(self):
        report = FreshnessReport(
            symbol="AAPL",
            level=FreshnessLevel.YELLOW,
            days_since_analysis=16,
            reasons=["too old"],
            price_change_pct=5.2,
            regime_changed=True,
        )
        d = report.to_dict()
        assert d["symbol"] == "AAPL"
        assert d["level"] == "YELLOW"
        assert d["days_since_analysis"] == 16
        assert d["regime_changed"] is True


# ---------------------------------------------------------------------------
# 12. Threshold constants are sane
# ---------------------------------------------------------------------------

class TestThresholds:
    def test_yellow_before_red(self):
        assert YELLOW_DAYS < RED_DAYS
        assert YELLOW_PRICE_PCT < RED_PRICE_PCT

    def test_values(self):
        assert YELLOW_DAYS == 14
        assert RED_DAYS == 30
        assert YELLOW_PRICE_PCT == 10.0
        assert RED_PRICE_PCT == 20.0
