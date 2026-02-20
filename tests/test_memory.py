"""Tests for terminal.memory — Agent Memory system."""
import json
import pytest
from pathlib import Path
from unittest.mock import MagicMock, patch

from terminal.memory import (
    extract_situation_summary,
    store_situation,
    retrieve_same_ticker_experiences,
    format_past_experiences,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _write_research_files(research_dir, include_debate=False):
    """Create realistic research files for memory extraction."""
    (research_dir / "data_context.md").write_text(
        "### Company: NVIDIA (NVDA)\n"
        "- Sector: Technology\n- Market Cap: $3200B\n\n"
        "**Price**: Latest: $880.50\n\n"
        "**Regime: RISK_ON**\n",
        encoding="utf-8",
    )
    (research_dir / "oprms.md").write_text(
        "### OPRMS 评级 — NVDA\n\n"
        "**资产基因 (DNA)**: S — 圣杯\n"
        "- 仓位上限: 25%\n\n"
        "**时机系数 (Timing)**: A — 趋势确立\n"
        "- 系数: 0.9\n",
        encoding="utf-8",
    )
    (research_dir / "memo.md").write_text(
        "## 投资备忘录\n\n"
        "## 1. 执行摘要 (Executive Summary)\n\n"
        "NVIDIA is the undisputed leader in AI infrastructure, "
        "with data center revenue growing 150% YoY.\n\n"
        "## 2. 变异观点\nMarket underrates CUDA ecosystem lock-in.\n",
        encoding="utf-8",
    )
    (research_dir / "alpha_red_team.md").write_text(
        "## 红队试炼\n\n"
        "Attack 1: Data center capex cycle reversal risk\n"
        "Attack 2: AMD MI400 competitive threat\n"
        "Attack 3: Customer ASIC development danger\n",
        encoding="utf-8",
    )
    (research_dir / "alpha_cycle.md").write_text(
        "## 周期钟摆\n\n"
        "Sentiment at 8/10 — toward_greed.\n"
        "Business cycle: expansion.\n",
        encoding="utf-8",
    )
    (research_dir / "alpha_bet.md").write_text(
        "## 非对称赌注\n\n"
        "conviction_modifier: 1.2\n"
        "最终行动: 执行\n"
        "Core insight: AI infrastructure spend is durable.\n",
        encoding="utf-8",
    )
    if include_debate:
        (research_dir / "alpha_debate.md").write_text(
            "## 终极辩论 — NVDA\n\n"
            "核心分歧: 周期顶部 vs 结构性增长\n\n"
            "conviction_modifier: 1.1\n"
            "最终行动: 执行\n",
            encoding="utf-8",
        )


# ===========================================================================
# TestExtractSituationSummary
# ===========================================================================

class TestExtractSituationSummary:
    def test_extracts_regime(self, tmp_path):
        _write_research_files(tmp_path)
        result = extract_situation_summary("NVDA", tmp_path)
        assert result["regime"] == "RISK_ON"

    def test_extracts_price(self, tmp_path):
        _write_research_files(tmp_path)
        result = extract_situation_summary("NVDA", tmp_path)
        assert result["price"] == 880.50

    def test_extracts_oprms_snapshot(self, tmp_path):
        _write_research_files(tmp_path)
        result = extract_situation_summary("NVDA", tmp_path)
        oprms = result["oprms_snapshot"]
        assert oprms["dna"] == "S"
        assert oprms["timing"] == "A"
        assert oprms["coeff"] == 0.9

    def test_extracts_thesis_summary(self, tmp_path):
        _write_research_files(tmp_path)
        result = extract_situation_summary("NVDA", tmp_path)
        assert "NVIDIA" in result["thesis_summary"]
        assert len(result["thesis_summary"]) <= 200

    def test_extracts_key_risks(self, tmp_path):
        _write_research_files(tmp_path)
        result = extract_situation_summary("NVDA", tmp_path)
        assert len(result["key_risks"]) >= 2
        assert any("capex" in r.lower() or "cycle" in r.lower()
                    for r in result["key_risks"])

    def test_extracts_cycle_position(self, tmp_path):
        _write_research_files(tmp_path)
        result = extract_situation_summary("NVDA", tmp_path)
        cycle = result["cycle_position"]
        assert cycle["score"] == 8
        assert cycle["direction"] == "toward_greed"

    def test_extracts_action_from_bet(self, tmp_path):
        _write_research_files(tmp_path)
        result = extract_situation_summary("NVDA", tmp_path)
        assert result["action"] == "执行"
        assert result["conviction_modifier"] == 1.2

    def test_debate_overrides_bet(self, tmp_path):
        _write_research_files(tmp_path, include_debate=True)
        result = extract_situation_summary("NVDA", tmp_path)
        # debate has conviction_modifier 1.1, bet has 1.2
        # debate should win
        assert result["conviction_modifier"] == 1.1
        assert result["debate_key_disagreement"] is not None
        assert "周期" in result["debate_key_disagreement"]

    def test_returns_none_for_empty_dir(self, tmp_path):
        result = extract_situation_summary("NVDA", tmp_path)
        assert result is None

    def test_symbol_uppercased(self, tmp_path):
        _write_research_files(tmp_path)
        result = extract_situation_summary("nvda", tmp_path)
        assert result["symbol"] == "NVDA"

    def test_handles_partial_files(self, tmp_path):
        """Works with just oprms.md."""
        (tmp_path / "oprms.md").write_text(
            "DNA: A\nTiming: B\n系数: 0.5\n",
            encoding="utf-8",
        )
        result = extract_situation_summary("TEST", tmp_path)
        assert result is not None
        assert result["oprms_snapshot"]["dna"] == "A"


# ===========================================================================
# TestStoreSituation
# ===========================================================================

class TestStoreSituation:
    def test_calls_update_situation_summary(self):
        mock_store = MagicMock()
        situation = {"symbol": "NVDA", "regime": "RISK_ON", "price": 880.5}
        store_situation("NVDA", situation, store=mock_store)
        mock_store.update_situation_summary.assert_called_once()
        call_args = mock_store.update_situation_summary.call_args
        assert call_args[0][0] == "NVDA"
        # Verify JSON is valid
        json.loads(call_args[0][1])

    def test_uppercases_symbol(self):
        mock_store = MagicMock()
        store_situation("nvda", {"symbol": "nvda"}, store=mock_store)
        assert mock_store.update_situation_summary.call_args[0][0] == "NVDA"


# ===========================================================================
# TestRetrieveSameTickerExperiences
# ===========================================================================

class TestRetrieveSameTickerExperiences:
    def test_parses_situation_json(self):
        mock_store = MagicMock()
        situation = {"regime": "RISK_ON", "price": 880.5}
        mock_store.get_analyses_with_memory.return_value = [
            {
                "id": 1,
                "symbol": "NVDA",
                "analysis_date": "2026-02-01",
                "situation_summary": json.dumps(situation),
            }
        ]
        results = retrieve_same_ticker_experiences("NVDA", store=mock_store)
        assert len(results) == 1
        assert results[0]["situation_parsed"]["regime"] == "RISK_ON"

    def test_handles_invalid_json(self):
        mock_store = MagicMock()
        mock_store.get_analyses_with_memory.return_value = [
            {
                "id": 1,
                "symbol": "NVDA",
                "analysis_date": "2026-02-01",
                "situation_summary": "not json",
            }
        ]
        results = retrieve_same_ticker_experiences("NVDA", store=mock_store)
        assert results[0]["situation_parsed"] is None

    def test_empty_results(self):
        mock_store = MagicMock()
        mock_store.get_analyses_with_memory.return_value = []
        results = retrieve_same_ticker_experiences("AAPL", store=mock_store)
        assert results == []


# ===========================================================================
# TestFormatPastExperiences
# ===========================================================================

class TestFormatPastExperiences:
    def test_empty_returns_empty_string(self):
        assert format_past_experiences([]) == ""

    def test_formats_single_experience(self):
        experiences = [
            {
                "analysis_date": "2026-01-15",
                "situation_parsed": {
                    "symbol": "NVDA",
                    "price": 800.0,
                    "regime": "RISK_ON",
                    "oprms_snapshot": {"dna": "S", "timing": "A", "coeff": 0.9},
                    "thesis_summary": "AI infrastructure leader",
                    "action": "执行",
                    "conviction_modifier": 1.2,
                    "key_risks": ["capex cycle risk", "competition"],
                    "cycle_position": {"score": 7, "direction": "toward_greed"},
                    "debate_key_disagreement": "估值 vs 成长",
                },
            }
        ]
        result = format_past_experiences(experiences)
        assert "历史经验回顾" in result
        assert "2026-01-15" in result
        assert "$800.0" in result
        assert "RISK_ON" in result
        assert "DNA=S" in result
        assert "AI infrastructure" in result
        assert "执行" in result
        assert "capex" in result
        assert "7/10" in result
        assert "估值 vs 成长" in result

    def test_formats_multiple_experiences(self):
        experiences = [
            {
                "analysis_date": "2026-02-10",
                "situation_parsed": {
                    "price": 900.0, "regime": "RISK_ON",
                    "oprms_snapshot": {}, "thesis_summary": "Recent",
                    "action": "执行", "conviction_modifier": 1.1,
                },
            },
            {
                "analysis_date": "2026-01-05",
                "situation_parsed": {
                    "price": 700.0, "regime": "NEUTRAL",
                    "oprms_snapshot": {}, "thesis_summary": "Older",
                    "action": "搁置", "conviction_modifier": 0.8,
                },
            },
        ]
        result = format_past_experiences(experiences)
        assert "历史分析 #1" in result
        assert "历史分析 #2" in result
        assert "2026-02-10" in result
        assert "2026-01-05" in result

    def test_handles_missing_parsed_situation(self):
        experiences = [
            {
                "analysis_date": "2026-01-15",
                "situation_parsed": None,
            }
        ]
        result = format_past_experiences(experiences)
        assert "历史分析 #1" in result
        # Should not crash
        assert "N/A" in result
