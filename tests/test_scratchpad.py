"""Tests for analysis scratchpad logging."""
import json
from pathlib import Path
import pytest

from terminal.scratchpad import (
    AnalysisScratchpad,
    read_scratchpad,
    list_scratchpads,
    get_latest_scratchpad,
)


def test_scratchpad_init(tmp_path, monkeypatch):
    """Test scratchpad initialization and query logging."""
    # Mock companies dir to use temp directory
    monkeypatch.setattr("terminal.scratchpad._COMPANIES_DIR", tmp_path)

    scratchpad = AnalysisScratchpad("NVDA", "full", "Custom analysis query")

    # Check log file created
    assert scratchpad.log_path.exists()
    assert scratchpad.symbol == "NVDA"
    assert scratchpad.depth == "full"

    # Check initial query logged
    events = read_scratchpad(scratchpad.log_path)
    assert len(events) == 1
    assert events[0]["type"] == "query"
    assert events[0]["symbol"] == "NVDA"
    assert events[0]["depth"] == "full"
    assert events[0]["query"] == "Custom analysis query"


def test_scratchpad_tool_call(tmp_path, monkeypatch):
    """Test tool call logging with result truncation."""
    monkeypatch.setattr("terminal.scratchpad._COMPANIES_DIR", tmp_path)

    scratchpad = AnalysisScratchpad("TSLA", "quick")

    # Small result
    scratchpad.log_tool_call(
        "FMPPriceTool",
        {"symbol": "TSLA", "period": "1d"},
        {"price": 250.5, "volume": 1000000},
    )

    # Large result (will be truncated)
    large_result = [{"data": "x" * 1000} for _ in range(10)]
    scratchpad.log_tool_call(
        "FMPFundamentalTool",
        {"symbol": "TSLA"},
        large_result,
    )

    events = read_scratchpad(scratchpad.log_path)
    tool_events = [e for e in events if e["type"] == "tool_call"]

    assert len(tool_events) == 2

    # First call not truncated
    assert tool_events[0]["tool"] == "FMPPriceTool"
    assert tool_events[0]["result"]["price"] == 250.5
    assert "_truncated" not in tool_events[0]["result"]

    # Second call truncated
    assert tool_events[1]["tool"] == "FMPFundamentalTool"
    assert tool_events[1]["result"]["_truncated"] is True
    assert tool_events[1]["result"]["_count"] == 10
    assert tool_events[1]["result_size"] > 5000


def test_scratchpad_reasoning(tmp_path, monkeypatch):
    """Test reasoning step logging with truncation."""
    monkeypatch.setattr("terminal.scratchpad._COMPANIES_DIR", tmp_path)

    scratchpad = AnalysisScratchpad("AAPL", "standard")

    # Short reasoning
    scratchpad.log_reasoning(
        "valuation_check",
        "PE ratio 25x vs industry 20x, premium justified by brand"
    )

    # Long reasoning (will be truncated)
    long_text = "A" * 1500
    scratchpad.log_reasoning("growth_analysis", long_text)

    events = read_scratchpad(scratchpad.log_path)
    reasoning_events = [e for e in events if e["type"] == "reasoning"]

    assert len(reasoning_events) == 2

    # First not truncated
    assert reasoning_events[0]["step"] == "valuation_check"
    assert "truncated" not in reasoning_events[0]["content"]

    # Second truncated
    assert reasoning_events[1]["step"] == "growth_analysis"
    assert len(reasoning_events[1]["content"]) <= 1050  # 1000 + truncation message
    assert "truncated" in reasoning_events[1]["content"]


def test_scratchpad_lens_complete(tmp_path, monkeypatch):
    """Test lens completion logging."""
    monkeypatch.setattr("terminal.scratchpad._COMPANIES_DIR", tmp_path)

    scratchpad = AnalysisScratchpad("MSFT", "full")

    scratchpad.log_lens_complete("growth_story", "/path/to/growth_lens.md")
    scratchpad.log_lens_complete("risk_assessment")  # No output path

    events = read_scratchpad(scratchpad.log_path)
    lens_events = [e for e in events if e["type"] == "lens_complete"]

    assert len(lens_events) == 2
    assert lens_events[0]["lens"] == "growth_story"
    assert lens_events[0]["output_path"] == "/path/to/growth_lens.md"
    assert lens_events[1]["lens"] == "risk_assessment"
    assert lens_events[1]["output_path"] is None


def test_scratchpad_final_rating(tmp_path, monkeypatch):
    """Test OPRMS rating logging."""
    monkeypatch.setattr("terminal.scratchpad._COMPANIES_DIR", tmp_path)

    scratchpad = AnalysisScratchpad("GOOGL", "full")

    oprms = {
        "dna": "A",
        "timing": "B",
        "timing_coeff": 0.5,
        "position_pct": 7.5,
        "evidence": ["Q4 earnings beat", "Cloud growth 30%"],
    }
    scratchpad.log_final_rating(oprms)

    events = read_scratchpad(scratchpad.log_path)
    rating_events = [e for e in events if e["type"] == "final_rating"]

    assert len(rating_events) == 1
    assert rating_events[0]["oprms"]["dna"] == "A"
    assert rating_events[0]["oprms"]["timing_coeff"] == 0.5


def test_scratchpad_helpers(tmp_path, monkeypatch):
    """Test helper functions for listing/reading scratchpads."""
    monkeypatch.setattr("terminal.scratchpad._COMPANIES_DIR", tmp_path)

    # Create multiple scratchpads
    s1 = AnalysisScratchpad("AMZN", "quick")
    s2 = AnalysisScratchpad("AMZN", "full")
    s3 = AnalysisScratchpad("AMZN", "standard")

    # List all scratchpads
    logs = list_scratchpads("AMZN")
    assert len(logs) == 3
    # Should be sorted newest first (but timing may be same second, so just check all exist)
    log_paths = {s1.log_path, s2.log_path, s3.log_path}
    assert set(logs) == log_paths

    # Get latest (should be one of them)
    latest = get_latest_scratchpad("AMZN")
    assert latest in log_paths

    # Non-existent symbol
    assert list_scratchpads("UNKNOWN") == []
    assert get_latest_scratchpad("UNKNOWN") is None


def test_scratchpad_unique_filenames(tmp_path, monkeypatch):
    """Test that different queries generate unique filenames."""
    monkeypatch.setattr("terminal.scratchpad._COMPANIES_DIR", tmp_path)

    s1 = AnalysisScratchpad("META", "full", "Earnings analysis")
    s2 = AnalysisScratchpad("META", "full", "Valuation check")

    # Different hashes due to different queries
    assert s1.log_path != s2.log_path
    assert s1.hash != s2.hash

    # Both files should exist
    assert s1.log_path.exists()
    assert s2.log_path.exists()


def test_scratchpad_malformed_json(tmp_path, monkeypatch):
    """Test graceful handling of malformed JSONL."""
    monkeypatch.setattr("terminal.scratchpad._COMPANIES_DIR", tmp_path)

    scratchpad = AnalysisScratchpad("NFLX", "quick")

    # Write valid event
    scratchpad.log_reasoning("step1", "Valid reasoning")

    # Manually write malformed line
    with open(scratchpad.log_path, "a") as f:
        f.write("{ invalid json\n")
        f.write(json.dumps({"type": "reasoning", "step": "step2", "content": "Valid again"}) + "\n")

    # Should skip malformed line but keep valid ones
    events = read_scratchpad(scratchpad.log_path)
    reasoning_events = [e for e in events if e["type"] == "reasoning"]
    assert len(reasoning_events) == 2  # Initial + step2 (skips malformed)


def test_scratchpad_debate_round(tmp_path, monkeypatch):
    """Test debate round logging."""
    monkeypatch.setattr("terminal.scratchpad._COMPANIES_DIR", tmp_path)

    scratchpad = AnalysisScratchpad("NVDA", "deep")

    scratchpad.log_debate_round(1, "索罗斯", "行动", "反身性证据强烈，时间窗口正在关闭")
    scratchpad.log_debate_round(1, "马克斯", "等待", "周期位置不支持行动，等待成本低")

    events = read_scratchpad(scratchpad.log_path)
    debate_events = [e for e in events if e["type"] == "debate_round"]

    assert len(debate_events) == 2
    assert debate_events[0]["round"] == 1
    assert debate_events[0]["persona"] == "索罗斯"
    assert debate_events[0]["stance"] == "行动"
    assert "反身性" in debate_events[0]["key_argument"]
    assert debate_events[1]["persona"] == "马克斯"


def test_scratchpad_debate_synthesis(tmp_path, monkeypatch):
    """Test debate synthesis logging."""
    monkeypatch.setattr("terminal.scratchpad._COMPANIES_DIR", tmp_path)

    scratchpad = AnalysisScratchpad("TSLA", "deep")

    scratchpad.log_debate_synthesis(
        final_conviction_modifier=1.2,
        final_action="执行",
        key_disagreement="周期位置 vs 反身性时间窗口",
    )

    events = read_scratchpad(scratchpad.log_path)
    synth_events = [e for e in events if e["type"] == "debate_synthesis"]

    assert len(synth_events) == 1
    assert synth_events[0]["conviction_modifier"] == 1.2
    assert synth_events[0]["action"] == "执行"
    assert "周期" in synth_events[0]["key_disagreement"]


def test_scratchpad_memory_retrieval(tmp_path, monkeypatch):
    """Test memory retrieval logging."""
    monkeypatch.setattr("terminal.scratchpad._COMPANIES_DIR", tmp_path)

    scratchpad = AnalysisScratchpad("AAPL", "deep")

    scratchpad.log_memory_retrieval(
        source="same_ticker",
        query="AAPL",
        matches_count=3,
        relevance="Found 3 prior analyses, most recent 2 weeks ago",
    )

    events = read_scratchpad(scratchpad.log_path)
    mem_events = [e for e in events if e["type"] == "memory_retrieval"]

    assert len(mem_events) == 1
    assert mem_events[0]["source"] == "same_ticker"
    assert mem_events[0]["matches_count"] == 3
    assert "2 weeks" in mem_events[0]["relevance"]
