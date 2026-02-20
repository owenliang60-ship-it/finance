"""Tests for terminal.heptabase_sync — Heptabase card/journal formatting."""
import json
import pytest
from pathlib import Path
from unittest.mock import patch


@pytest.fixture
def company_dir(tmp_path):
    """Create a realistic MU company directory with test data."""
    sym_dir = tmp_path / "data" / "companies" / "TEST"
    for sub in ["memos", "analyses", "debates", "strategies", "trades"]:
        (sym_dir / sub).mkdir(parents=True)

    # OPRMS
    oprms = {
        "dna": "A",
        "dna_label": "猛将",
        "timing": "B",
        "timing_label": "正常波动",
        "timing_coeff": 0.35,
        "conviction_modifier": 0.7,
        "investment_bucket": "Cyclical Growth",
        "position_pct": 5.25,
        "evidence": ["Revenue +57% YoY", "HBM sold out"],
        "verdict": "HOLD",
        "verdict_rationale": "Strong thesis but overextended. Wait for pullback.",
        "analysis_depth": "alpha",
        "updated_at": "2026-02-09T17:00:00",
        "symbol": "TEST",
    }
    (sym_dir / "oprms.json").write_text(json.dumps(oprms))

    # Meta
    meta = {
        "company_name": "Test Corp",
        "sector": "Technology",
        "industry": "Semiconductors",
        "themes": ["AI Memory", "HBM"],
        "last_analysis_depth": "alpha",
        "last_analysis_date": "2026-02-09",
    }
    (sym_dir / "meta.json").write_text(json.dumps(meta))

    # Kill conditions
    kill = {
        "symbol": "TEST",
        "conditions": [
            {"description": "Gross margin 2Q decline", "metric": "gm", "threshold": "2 QoQ", "status": "active"},
            {"description": "Samsung HBM expansion >30%", "metric": "competitor", "threshold": ">30%", "status": "active"},
        ],
    }
    (sym_dir / "kill_conditions.json").write_text(json.dumps(kill))

    # Memo
    memo_text = "# TEST Analysis\n\nThis is the investment memo content.\n\n## Key Points\n- Point 1\n- Point 2"
    (sym_dir / "memos" / "20260209_170000_investment.md").write_text(memo_text)

    # Alpha
    alpha = {
        "red_team": {
            "attacks": [
                {"name": "Cycle Trap", "lethality": 9, "summary": "Classic peak margins."},
                {"name": "Capex Trap", "lethality": 8, "summary": "Heavy reinvestment."},
            ],
            "survival": "Thesis survives but needs exit signals.",
        },
        "cycle_pendulum": {
            "sentiment": {"score": 8, "label": "Euphoria", "evidence": "6mo +303%"},
            "business": {"score": 7, "label": "Mid-expansion", "evidence": "Rev +57%"},
            "technology": {"score": 4, "label": "Early-mid", "evidence": "HBM3E current"},
            "conclusion": "Mixed signals. Don't add at sentiment peak.",
        },
        "asymmetric_bet": {
            "scenarios": [
                {"name": "Bull", "probability": 0.25, "target": "$500-600", "return_range": "+15% to +37%"},
                {"name": "Base", "probability": 0.45, "target": "$380-480", "return_range": "-13% to +10%"},
                {"name": "Bear", "probability": 0.30, "target": "$200-300", "return_range": "-32% to -54%"},
            ],
            "expected_value": -0.0755,
            "asymmetry": "UNFAVORABLE",
            "conviction_modifier": 0.7,
        },
        "final_verdict": "HOLD",
        "symbol": "TEST",
        "debate_conviction_modifier": 1.15,
        "debate_final_action": "执行",
        "debate_key_disagreement": "周期风险 vs 非对称赌注",
    }
    (sym_dir / "analyses" / "20260209_170000_alpha.json").write_text(json.dumps(alpha))

    return tmp_path


@pytest.fixture
def _patch_root(company_dir):
    """Patch company_db paths to use tmp_path."""
    with patch("terminal.company_db._COMPANIES_DIR", company_dir / "data" / "companies"):
        yield


class TestPrepareSync:
    """Tests for prepare_heptabase_sync()."""

    def test_full_data(self, _patch_root):
        from terminal.heptabase_sync import prepare_heptabase_sync

        result = prepare_heptabase_sync("TEST")
        assert result["has_data"] is True
        assert result["symbol"] == "TEST"
        assert len(result["card_content"]) > 0
        assert len(result["journal_entry"]) > 0

    def test_card_has_h1_title(self, _patch_root):
        from terminal.heptabase_sync import prepare_heptabase_sync

        result = prepare_heptabase_sync("TEST")
        first_line = result["card_content"].split("\n")[0]
        assert first_line.startswith("# TEST (Test Corp)")

    def test_card_has_oprms(self, _patch_root):
        from terminal.heptabase_sync import prepare_heptabase_sync

        card = prepare_heptabase_sync("TEST")["card_content"]
        assert "DNA=A" in card
        assert "Timing=B" in card
        assert "5.25%" in card

    def test_card_has_concept_links(self, _patch_root):
        from terminal.heptabase_sync import prepare_heptabase_sync

        card = prepare_heptabase_sync("TEST")["card_content"]
        assert "[[OPRMS]]" in card
        assert "[[Technology]]" in card
        assert "[[AI Memory]]" in card
        assert "[[HBM]]" in card

    def test_card_includes_memo_content(self, _patch_root):
        from terminal.heptabase_sync import prepare_heptabase_sync

        card = prepare_heptabase_sync("TEST")["card_content"]
        assert "This is the investment memo content." in card
        assert "Point 1" in card

    def test_card_has_kill_conditions(self, _patch_root):
        from terminal.heptabase_sync import prepare_heptabase_sync

        card = prepare_heptabase_sync("TEST")["card_content"]
        assert "Kill Conditions" in card
        assert "Gross margin 2Q decline" in card
        assert "Samsung HBM expansion" in card

    def test_card_has_alpha_sections(self, _patch_root):
        from terminal.heptabase_sync import prepare_heptabase_sync

        card = prepare_heptabase_sync("TEST")["card_content"]
        assert "Red Team" in card
        assert "Cycle Trap" in card
        assert "Cycle Pendulum" in card
        assert "Euphoria" in card
        assert "Asymmetric Bet" in card
        assert "-7.5%" in card  # EV (-0.0755 → -7.5%)

    def test_card_has_debate_section(self, _patch_root):
        from terminal.heptabase_sync import prepare_heptabase_sync

        card = prepare_heptabase_sync("TEST")["card_content"]
        assert "Alpha Debate" in card
        assert "1.15" in card
        assert "执行" in card
        assert "周期风险 vs 非对称赌注" in card

    def test_journal_format_brevity(self, _patch_root):
        from terminal.heptabase_sync import prepare_heptabase_sync

        journal = prepare_heptabase_sync("TEST")["journal_entry"]
        assert len(journal) < 500
        assert "TEST" in journal
        assert "A/B" in journal
        assert "HOLD" in journal

    def test_journal_has_depth(self, _patch_root):
        from terminal.heptabase_sync import prepare_heptabase_sync

        journal = prepare_heptabase_sync("TEST")["journal_entry"]
        assert "alpha" in journal


class TestMinimalData:
    """Tests for sync with minimal data (only oprms, no memo/alpha)."""

    def test_oprms_only(self, company_dir):
        """Only oprms.json exists — should produce a simplified card."""
        # Remove memo, alpha, kill conditions, keep only oprms + meta
        sym_dir = company_dir / "data" / "companies" / "MINI"
        for sub in ["memos", "analyses", "debates", "strategies", "trades"]:
            (sym_dir / sub).mkdir(parents=True)

        oprms = {
            "dna": "B", "dna_label": "黑马", "timing": "C", "timing_label": "垃圾时间",
            "timing_coeff": 0.2, "position_pct": 1.4,
            "verdict": "PASS", "verdict_rationale": "Not yet.",
            "analysis_depth": "standard",
            "updated_at": "2026-02-09T12:00:00", "symbol": "MINI",
        }
        (sym_dir / "oprms.json").write_text(json.dumps(oprms))
        (sym_dir / "meta.json").write_text(json.dumps({"company_name": "Mini Inc"}))

        with patch("terminal.company_db._COMPANIES_DIR", company_dir / "data" / "companies"):
            from terminal.heptabase_sync import prepare_heptabase_sync

            result = prepare_heptabase_sync("MINI")
            assert result["has_data"] is True
            card = result["card_content"]
            assert "# MINI (Mini Inc)" in card
            assert "DNA=B" in card
            # No alpha sections
            assert "Red Team" not in card
            # No kill conditions section header
            assert "Kill Conditions" not in card


class TestNoData:
    """Tests for sync with no data at all."""

    def test_no_oprms(self, company_dir):
        """No oprms → has_data=False, empty content."""
        with patch("terminal.company_db._COMPANIES_DIR", company_dir / "data" / "companies"):
            from terminal.heptabase_sync import prepare_heptabase_sync

            result = prepare_heptabase_sync("NONEXISTENT")
            assert result["has_data"] is False
            assert result["card_content"] == ""
            assert result["journal_entry"] == ""
