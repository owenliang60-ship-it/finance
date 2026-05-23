"""Behavior tests for terminal/tools/fmp_tools.py wrappers."""
from unittest.mock import patch

from terminal.tools.fmp_tools import GetLargeCapStocksTool
from src.data.fmp_client import SCREENER_DEFAULT_LIMIT


def test_get_large_cap_stocks_tool_passes_default_limit():
    """Tool default execute() must pass SCREENER_DEFAULT_LIMIT through to client."""
    tool = GetLargeCapStocksTool()
    with patch.object(tool, "_execute_client_method", return_value=[]) as m:
        tool.execute(market_cap_threshold=10_000_000_000)
    m.assert_called_once_with(
        "get_large_cap_stocks",
        market_cap_threshold=10_000_000_000,
        limit=SCREENER_DEFAULT_LIMIT,
    )


def test_get_large_cap_stocks_tool_passes_custom_limit():
    """Caller-provided limit must be forwarded verbatim."""
    tool = GetLargeCapStocksTool()
    with patch.object(tool, "_execute_client_method", return_value=[]) as m:
        tool.execute(market_cap_threshold=100_000_000_000, limit=123)
    m.assert_called_once_with(
        "get_large_cap_stocks",
        market_cap_threshold=100_000_000_000,
        limit=123,
    )
