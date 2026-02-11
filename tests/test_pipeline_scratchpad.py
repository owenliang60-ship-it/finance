"""Tests for scratchpad integration in pipeline and commands."""
import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock

from terminal.pipeline import collect_data
from terminal.commands import analyze_ticker
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


def test_analyze_ticker_creates_scratchpad(tmp_path, monkeypatch, mock_data_sources):
    """Test analyze_ticker creates scratchpad automatically."""
    monkeypatch.setattr("terminal.scratchpad._COMPANIES_DIR", tmp_path)

    result = analyze_ticker("TEST", depth="quick")

    # Should return scratchpad path
    assert "scratchpad_path" in result
    scratchpad_path = Path(result["scratchpad_path"])
    assert scratchpad_path.exists()

    # Verify scratchpad content
    events = read_scratchpad(scratchpad_path)
    assert len(events) > 0

    # First event should be query
    assert events[0]["type"] == "query"
    assert events[0]["symbol"] == "TEST"
    assert events[0]["depth"] == "quick"


def test_analyze_ticker_logs_phases(tmp_path, monkeypatch, mock_data_sources):
    """Test analyze_ticker logs phase transitions."""
    monkeypatch.setattr("terminal.scratchpad._COMPANIES_DIR", tmp_path)

    result = analyze_ticker("TEST", depth="standard")

    scratchpad_path = Path(result["scratchpad_path"])
    events = read_scratchpad(scratchpad_path)

    reasoning_steps = [e["step"] for e in events if e["type"] == "reasoning"]

    # Should have phase transitions
    assert "phase_1_start" in reasoning_steps
    assert "phase_1_complete" in reasoning_steps
    assert "phase_2_start" in reasoning_steps
    assert "phase_2_complete" in reasoning_steps


def test_analyze_ticker_full_depth(tmp_path, monkeypatch, mock_data_sources):
    """Test analyze_ticker with full depth logs all phases."""
    monkeypatch.setattr("terminal.scratchpad._COMPANIES_DIR", tmp_path)

    result = analyze_ticker("TEST", depth="full")

    scratchpad_path = Path(result["scratchpad_path"])
    events = read_scratchpad(scratchpad_path)

    reasoning_steps = [e["step"] for e in events if e["type"] == "reasoning"]

    # Should have all three phases
    assert "phase_1_start" in reasoning_steps
    assert "phase_1_complete" in reasoning_steps
    assert "phase_2_start" in reasoning_steps
    assert "phase_2_complete" in reasoning_steps
    assert "phase_3_start" in reasoning_steps
    assert "phase_3_complete" in reasoning_steps


def test_analyze_ticker_logs_errors(tmp_path, monkeypatch):
    """Test analyze_ticker logs errors before raising."""
    monkeypatch.setattr("terminal.scratchpad._COMPANIES_DIR", tmp_path)

    # Mock collect_data to fail
    def failing_collect_data(*args, **kwargs):
        raise RuntimeError("Data collection failed")

    monkeypatch.setattr(
        "terminal.commands.collect_data",
        failing_collect_data
    )

    with pytest.raises(RuntimeError, match="Data collection failed"):
        analyze_ticker("TEST", depth="quick")

    # Find the scratchpad (should exist despite error)
    scratchpad_dir = tmp_path / "TEST" / "scratchpad"
    assert scratchpad_dir.exists()

    logs = list(scratchpad_dir.glob("*.jsonl"))
    assert len(logs) > 0

    # Check error was logged
    events = read_scratchpad(logs[0])
    error_events = [e for e in events if e["type"] == "reasoning" and e["step"] == "error"]

    assert len(error_events) > 0
    assert "Data collection failed" in error_events[0]["content"]


def test_scratchpad_path_in_result(tmp_path, monkeypatch, mock_data_sources):
    """Test scratchpad path is included in result for all depths."""
    monkeypatch.setattr("terminal.scratchpad._COMPANIES_DIR", tmp_path)

    for depth in ["quick", "standard", "full"]:
        result = analyze_ticker("TEST", depth=depth)

        assert "scratchpad_path" in result
        assert Path(result["scratchpad_path"]).exists()


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


def test_analyze_ticker_standard_has_five_lenses_no_macro_briefing(
    tmp_path, monkeypatch, mock_data_sources
):
    """Standard depth returns 5 lens prompts but no macro_briefing_prompt."""
    monkeypatch.setattr("terminal.scratchpad._COMPANIES_DIR", tmp_path)

    # Mock macro snapshot (raw data still used by lenses)
    mock_snapshot = MagicMock()
    mock_snapshot.regime = "NEUTRAL"
    mock_snapshot.data_source_count = 10
    mock_snapshot.vix = 18.0
    mock_snapshot.format_for_prompt.return_value = "Mock macro data"
    mock_snapshot.japan_rate = None
    mock_snapshot.usdjpy_30d_chg = None
    mock_snapshot.hy_spread = None
    mock_snapshot.hy_spread_30d_chg = None
    mock_snapshot.fed_bs_30d_chg_pct = None
    mock_snapshot.dxy_trend = None
    mock_snapshot.dxy_30d_chg = None
    mock_snapshot.cpi_yoy = None
    mock_snapshot.us10y_30d_chg_bp = None
    mock_snapshot.gdp_growth = None
    mock_snapshot.spread_10y_2y = None

    monkeypatch.setattr(
        "terminal.macro_fetcher.get_macro_snapshot",
        lambda: mock_snapshot
    )

    result = analyze_ticker("TEST", depth="standard")

    # macro_briefing_prompt no longer in result (moved to /macro skill)
    assert "macro_briefing_prompt" not in result

    # 5 lens prompts (no Macro-Tactical)
    assert "lens_prompts" in result
    assert len(result["lens_prompts"]) == 5
    lens_names = [p["lens_name"] for p in result["lens_prompts"]]
    assert "Macro-Tactical" not in lens_names
