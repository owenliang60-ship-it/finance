"""Tests for scratchpad integration in pipeline and commands."""
import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock

from terminal.pipeline import collect_data
from terminal.scratchpad import AnalysisScratchpad, read_scratchpad


@pytest.fixture
def mock_data_sources(monkeypatch):
    """Mock all external data sources."""
    # Mock get_stock_data (imported inside collect_data)
    # DataPackage expects price to be dict with "latest_close" key
    mock_stock = {
        "info": {"symbol": "TEST", "name": "Test Company"},
        "profile": {"sector": "Technology"},
        "fundamentals": {"revenue": 1000000},
        "ratios": [{"pe": 25}],
        "income": [{"netIncome": 100000}],
        "price": {"latest_close": 100, "data": [{"close": 100, "date": "2026-01-01"}]},
    }
    monkeypatch.setattr(
        "src.data.data_query.get_stock_data",
        lambda *args, **kwargs: mock_stock
    )

    # Mock run_indicators (imported inside collect_data)
    # format_context() expects indicators with nested dict structure
    mock_indicators = {
        "pmarp": {"current": 50, "signal": "neutral"},
        "rvol": {"current": 1.5, "signal": "normal"}
    }
    monkeypatch.setattr(
        "src.indicators.engine.run_indicators",
        lambda *args: mock_indicators
    )

    # Mock get_company_record (imported at module level)
    mock_record = MagicMock()
    mock_record.has_data = False
    monkeypatch.setattr(
        "terminal.pipeline.get_company_record",
        lambda *args: mock_record
    )


def test_collect_data_without_scratchpad(mock_data_sources):
    """Test collect_data works without scratchpad (backward compatibility)."""
    data_pkg = collect_data("TEST", price_days=60)

    assert data_pkg.symbol == "TEST"
    assert data_pkg.info is not None
    assert data_pkg.profile is not None
    assert data_pkg.price is not None


def test_collect_data_with_scratchpad(tmp_path, monkeypatch, mock_data_sources):
    """Test collect_data logs to scratchpad when provided."""
    monkeypatch.setattr("terminal.scratchpad._COMPANIES_DIR", tmp_path)

    scratchpad = AnalysisScratchpad("TEST", "quick")
    data_pkg = collect_data("TEST", price_days=60, scratchpad=scratchpad)

    # Verify data collection
    assert data_pkg.symbol == "TEST"

    # Verify scratchpad logging
    events = read_scratchpad(scratchpad.log_path)

    # Should have: query + data_collection_start + tool_call(get_stock_data) +
    #              tool_call(run_indicators) + data_collection_complete
    assert len(events) >= 5

    # Check event types
    event_types = [e["type"] for e in events]
    assert "query" in event_types
    assert "tool_call" in event_types
    assert "reasoning" in event_types

    # Check specific reasoning steps
    reasoning_steps = [e["step"] for e in events if e["type"] == "reasoning"]
    assert "data_collection_start" in reasoning_steps
    assert "data_collection_complete" in reasoning_steps

    # Check tool calls
    tool_calls = [e["tool"] for e in events if e["type"] == "tool_call"]
    assert "get_stock_data" in tool_calls
    assert "run_indicators" in tool_calls


def test_collect_data_logs_errors(tmp_path, monkeypatch):
    """Test collect_data logs errors to scratchpad."""
    monkeypatch.setattr("terminal.scratchpad._COMPANIES_DIR", tmp_path)

    # Mock get_stock_data to fail
    def failing_get_stock_data(*args, **kwargs):
        raise RuntimeError("API failure")

    monkeypatch.setattr(
        "src.data.data_query.get_stock_data",
        failing_get_stock_data
    )

    # Mock other dependencies
    monkeypatch.setattr(
        "src.indicators.engine.run_indicators",
        lambda *args: {}
    )
    monkeypatch.setattr(
        "terminal.pipeline.get_company_record",
        lambda *args: MagicMock(has_data=False)
    )

    scratchpad = AnalysisScratchpad("TEST", "quick")
    data_pkg = collect_data("TEST", scratchpad=scratchpad)

    # Should still return a data package (graceful degradation)
    assert data_pkg.symbol == "TEST"

    # Check error was logged
    events = read_scratchpad(scratchpad.log_path)
    error_events = [e for e in events if e["type"] == "reasoning" and e["step"] == "error"]

    assert len(error_events) > 0
    assert "API failure" in error_events[0]["content"]


def test_backward_compatibility_collect_data(mock_data_sources):
    """Test collect_data without scratchpad parameter still works."""
    # Should not raise any errors
    data_pkg = collect_data("TEST")

    assert data_pkg.symbol == "TEST"
    assert data_pkg.price is not None


# ---------------------------------------------------------------------------
# Macro briefing removed from pipeline (now standalone /macro skill)
# ---------------------------------------------------------------------------

def test_lenses_are_five_not_six():
    """Only 5 lenses exist (no Macro-Tactical)."""
    from knowledge.philosophies.base import get_all_lenses
    lenses = get_all_lenses()
    assert len(lenses) == 5
    lens_names = [l.name for l in lenses]
    assert "Macro-Tactical" not in lens_names
