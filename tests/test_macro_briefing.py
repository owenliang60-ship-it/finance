"""
Tests for macro briefing: cross-asset signal detection + briefing prompt generation.
"""
import json
import pytest

from terminal.macro_snapshot import MacroSnapshot
from terminal.macro_briefing import (
    CrossAssetSignal,
    detect_signals,
    generate_briefing_prompt,
    _detect_carry_trade_unwind,
    _detect_credit_stress,
    _detect_liquidity_drain,
    _detect_reflation,
    _detect_risk_rally,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _snapshot(**kwargs) -> MacroSnapshot:
    """Create a MacroSnapshot with given overrides."""
    return MacroSnapshot(**kwargs)


# ===========================================================================
# TestCrossAssetSignalDetection
# ===========================================================================

class TestCarryTradeUnwind:
    def test_strong_signal(self):
        """BOJ rate > 0.25 AND USDJPY dropped > 5 → STRONG."""
        s = _snapshot(japan_rate=0.5, usdjpy=145.0, usdjpy_30d_chg=-6.0)
        sig = _detect_carry_trade_unwind(s)
        assert sig.fired is True
        assert sig.strength == "STRONG"
        assert len(sig.evidence) >= 2

    def test_moderate_signal(self):
        """BOJ rate > 0 AND USDJPY dropped > 2 → MODERATE."""
        s = _snapshot(japan_rate=0.1, usdjpy=150.0, usdjpy_30d_chg=-3.0)
        sig = _detect_carry_trade_unwind(s)
        assert sig.fired is True
        assert sig.strength == "MODERATE"

    def test_not_fired_no_boj_hike(self):
        """BOJ rate at 0 → not fired."""
        s = _snapshot(japan_rate=0.0, usdjpy=150.0, usdjpy_30d_chg=-3.0)
        sig = _detect_carry_trade_unwind(s)
        assert sig.fired is False

    def test_not_fired_yen_weakening(self):
        """BOJ hiking but yen weakening → not fired."""
        s = _snapshot(japan_rate=0.25, usdjpy=155.0, usdjpy_30d_chg=2.0)
        sig = _detect_carry_trade_unwind(s)
        assert sig.fired is False

    def test_not_fired_no_data(self):
        """Missing data → not fired, no crash."""
        s = _snapshot()
        sig = _detect_carry_trade_unwind(s)
        assert sig.fired is False
        assert sig.name == "carry_trade_unwind"


class TestCreditStress:
    def test_hy_wide(self):
        """HY > 4% → fired."""
        s = _snapshot(hy_spread=4.5)
        sig = _detect_credit_stress(s)
        assert sig.fired is True
        assert "4.50%" in sig.evidence[0] or "450bp" in sig.evidence[0]

    def test_hy_spike(self):
        """HY 30d change > 0.5pp → fired."""
        s = _snapshot(hy_spread=3.2, hy_spread_30d_chg=0.6)
        sig = _detect_credit_stress(s)
        assert sig.fired is True

    def test_hy_wide_and_spiking(self):
        """Both wide AND spiking → STRONG."""
        s = _snapshot(hy_spread=4.5, hy_spread_30d_chg=0.8)
        sig = _detect_credit_stress(s)
        assert sig.fired is True
        assert sig.strength == "STRONG"

    def test_normal(self):
        """HY < 3.5, no spike → not fired."""
        s = _snapshot(hy_spread=3.0, hy_spread_30d_chg=0.1)
        sig = _detect_credit_stress(s)
        assert sig.fired is False

    def test_no_data(self):
        s = _snapshot()
        sig = _detect_credit_stress(s)
        assert sig.fired is False


class TestLiquidityDrain:
    def test_fired(self):
        """Fed BS shrinking + DXY strengthening → fired."""
        s = _snapshot(
            fed_bs_30d_chg_pct=-0.8,
            dxy_trend="STRENGTHENING",
            dxy_30d_chg=2.5,
        )
        sig = _detect_liquidity_drain(s)
        assert sig.fired is True
        assert sig.strength == "MODERATE"

    def test_strong(self):
        """Fed BS shrinking > 1% → STRONG."""
        s = _snapshot(
            fed_bs_30d_chg_pct=-1.5,
            dxy_trend="STRENGTHENING",
        )
        sig = _detect_liquidity_drain(s)
        assert sig.fired is True
        assert sig.strength == "STRONG"

    def test_not_fired_stable_bs(self):
        """Fed BS stable → not fired."""
        s = _snapshot(
            fed_bs_30d_chg_pct=-0.2,
            dxy_trend="STRENGTHENING",
        )
        sig = _detect_liquidity_drain(s)
        assert sig.fired is False

    def test_not_fired_weak_dollar(self):
        """DXY weakening → not fired (even if BS shrinking)."""
        s = _snapshot(
            fed_bs_30d_chg_pct=-1.0,
            dxy_trend="WEAKENING",
        )
        sig = _detect_liquidity_drain(s)
        assert sig.fired is False


class TestReflation:
    def test_fired(self):
        """CPI > 3 + 10Y up > 20bp + GDP > 2 → fired."""
        s = _snapshot(cpi_yoy=3.5, us10y_30d_chg_bp=30, gdp_growth=2.5)
        sig = _detect_reflation(s)
        assert sig.fired is True
        assert sig.strength == "MODERATE"

    def test_strong(self):
        """CPI > 4 + 10Y up > 40bp → STRONG."""
        s = _snapshot(cpi_yoy=4.5, us10y_30d_chg_bp=50, gdp_growth=3.0)
        sig = _detect_reflation(s)
        assert sig.fired is True
        assert sig.strength == "STRONG"

    def test_no_growth(self):
        """GDP < 2 → not fired."""
        s = _snapshot(cpi_yoy=3.5, us10y_30d_chg_bp=30, gdp_growth=1.5)
        sig = _detect_reflation(s)
        assert sig.fired is False

    def test_cpi_low(self):
        """CPI < 3 → not fired."""
        s = _snapshot(cpi_yoy=2.5, us10y_30d_chg_bp=30, gdp_growth=2.5)
        sig = _detect_reflation(s)
        assert sig.fired is False


class TestRiskRally:
    def test_fired(self):
        """VIX < 15 + curve steep + GDP > 2 → fired."""
        s = _snapshot(vix=13.0, spread_10y_2y=0.8, gdp_growth=2.5)
        sig = _detect_risk_rally(s)
        assert sig.fired is True
        assert sig.strength == "MODERATE"

    def test_strong_low_vix(self):
        """VIX < 12 → STRONG."""
        s = _snapshot(vix=11.0, spread_10y_2y=1.0, gdp_growth=3.0)
        sig = _detect_risk_rally(s)
        assert sig.fired is True
        assert sig.strength == "STRONG"

    def test_high_vix(self):
        """VIX > 15 → not fired."""
        s = _snapshot(vix=18.0, spread_10y_2y=0.8, gdp_growth=2.5)
        sig = _detect_risk_rally(s)
        assert sig.fired is False

    def test_inverted_curve(self):
        """Flat/inverted curve → not fired."""
        s = _snapshot(vix=13.0, spread_10y_2y=0.2, gdp_growth=2.5)
        sig = _detect_risk_rally(s)
        assert sig.fired is False


class TestDetectSignals:
    def test_multiple_signals_can_fire(self):
        """Both carry trade + credit stress fire simultaneously."""
        s = _snapshot(
            japan_rate=0.5, usdjpy=145.0, usdjpy_30d_chg=-6.0,
            hy_spread=5.0,
        )
        signals = detect_signals(s)
        fired = [sig for sig in signals if sig.fired]
        assert len(fired) == 2
        names = {sig.name for sig in fired}
        assert "carry_trade_unwind" in names
        assert "credit_stress" in names

    def test_no_data_graceful(self):
        """Empty MacroSnapshot → 0 fired, no crash."""
        s = _snapshot()
        signals = detect_signals(s)
        assert len(signals) == 5  # all 5 detectors run
        fired = [sig for sig in signals if sig.fired]
        assert len(fired) == 0

    def test_partial_data(self):
        """Only VIX + HY available → only relevant signals checked."""
        s = _snapshot(vix=20.0, hy_spread=4.5)
        signals = detect_signals(s)
        fired = [sig for sig in signals if sig.fired]
        # Only credit_stress should fire
        assert len(fired) == 1
        assert fired[0].name == "credit_stress"

    def test_returns_all_signals(self):
        """detect_signals returns all 5 signals regardless of fired status."""
        s = _snapshot()
        signals = detect_signals(s)
        assert len(signals) == 5
        names = {sig.name for sig in signals}
        assert names == {
            "carry_trade_unwind", "credit_stress", "liquidity_drain",
            "reflation", "risk_rally",
        }


# ===========================================================================
# TestBriefingPrompt
# ===========================================================================

class TestBriefingPrompt:
    def test_prompt_includes_snapshot(self):
        """Prompt should contain format_for_prompt() output."""
        s = _snapshot(vix=20.0, regime="NEUTRAL")
        signals = detect_signals(s)
        prompt = generate_briefing_prompt(s, signals)
        assert "VIX: 20.0" in prompt
        assert "Regime: NEUTRAL" in prompt

    def test_prompt_includes_fired_signals(self):
        """Active signals should appear in the prompt."""
        s = _snapshot(hy_spread=5.0)
        signals = detect_signals(s)
        prompt = generate_briefing_prompt(s, signals)
        assert "Credit Stress" in prompt
        assert "STRONG" in prompt or "MODERATE" in prompt

    def test_prompt_no_signals(self):
        """Valid prompt even when 0 signals fire."""
        s = _snapshot(vix=20.0)
        signals = detect_signals(s)
        prompt = generate_briefing_prompt(s, signals)
        assert "No strong cross-asset signals" in prompt
        assert "宏观数据快照" in prompt

    def test_prompt_structure(self):
        """Prompt contains required sections."""
        s = _snapshot(vix=15.0, regime="RISK_ON")
        signals = detect_signals(s)
        prompt = generate_briefing_prompt(s, signals)
        assert "宏观数据快照" in prompt
        assert "跨资产信号" in prompt
        assert "任务" in prompt
        assert "输出格式" in prompt
        assert "TAILWIND" in prompt
        assert "交易台行动指引" in prompt


# ===========================================================================
# TestMacroSnapshotNewFields
# ===========================================================================

class TestMacroSnapshotNewFields:
    def test_usdjpy_trend_field(self):
        s = _snapshot(usdjpy_30d_chg=-3.5)
        assert s.usdjpy_30d_chg == -3.5

    def test_hy_spread_trend_field(self):
        s = _snapshot(hy_spread_30d_chg=0.6)
        assert s.hy_spread_30d_chg == 0.6

    def test_fed_bs_trend_field(self):
        s = _snapshot(fed_bs_30d_chg_pct=-0.8)
        assert s.fed_bs_30d_chg_pct == -0.8

    def test_defaults_none(self):
        """New fields default to None."""
        s = _snapshot()
        assert s.usdjpy_30d_chg is None
        assert s.hy_spread_30d_chg is None
        assert s.fed_bs_30d_chg_pct is None

    def test_serialization_roundtrip(self):
        """New fields survive to_json/from_json roundtrip."""
        s = _snapshot(
            usdjpy_30d_chg=-3.5,
            hy_spread_30d_chg=0.6,
            fed_bs_30d_chg_pct=-0.8,
        )
        json_str = s.to_json()
        restored = MacroSnapshot.from_json(json_str)
        assert restored.usdjpy_30d_chg == -3.5
        assert restored.hy_spread_30d_chg == 0.6
        assert restored.fed_bs_30d_chg_pct == -0.8

    def test_from_dict_ignores_unknown(self):
        """from_dict still ignores unknown fields (backward compat)."""
        data = {"usdjpy_30d_chg": -2.0, "unknown_field": 42}
        s = MacroSnapshot.from_dict(data)
        assert s.usdjpy_30d_chg == -2.0


# ===========================================================================
# TestPipelineIntegration
# ===========================================================================

class TestPipelineIntegration:
    def test_briefing_in_context(self):
        """macro_briefing shows in format_context()."""
        from terminal.pipeline import DataPackage
        pkg = DataPackage(symbol="TEST")
        pkg.macro_briefing = "Market is trading goldilocks narrative."
        ctx = pkg.format_context()
        assert "### Macro Briefing" in ctx
        assert "goldilocks narrative" in ctx

    def test_no_briefing_in_context(self):
        """No macro_briefing → no section in format_context()."""
        from terminal.pipeline import DataPackage
        pkg = DataPackage(symbol="TEST")
        ctx = pkg.format_context()
        assert "### Macro Briefing" not in ctx

    def test_data_package_has_new_fields(self):
        """DataPackage has macro_briefing and macro_signals fields."""
        from terminal.pipeline import DataPackage
        pkg = DataPackage(symbol="TEST")
        assert pkg.macro_briefing is None
        assert pkg.macro_signals == []

    def test_signals_dict_format(self):
        """macro_signals stores dicts (serialized from CrossAssetSignal)."""
        from terminal.pipeline import DataPackage
        pkg = DataPackage(symbol="TEST")
        pkg.macro_signals = [
            {"name": "credit_stress", "label": "Credit Stress",
             "fired": True, "strength": "STRONG", "evidence": ["HY at 5%"]}
        ]
        assert pkg.macro_signals[0]["name"] == "credit_stress"
        assert pkg.macro_signals[0]["fired"] is True
