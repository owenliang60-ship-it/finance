"""Tests for MarketData.app tool wrappers."""
import pytest
from unittest.mock import patch, MagicMock

from terminal.tools.marketdata_tools import (
    BaseMarketDataTool,
    GetOptionsChainTool,
    GetOptionsExpirationsTool,
    GetOptionQuoteTool,
    create_marketdata_tools,
)
from terminal.tools.protocol import ToolCategory, ToolExecutionError


class TestBaseMarketDataTool:
    """Test base tool class."""

    def test_unavailable_without_api_key(self):
        """Should be unavailable when API key is missing."""
        tool = GetOptionsChainTool()
        with patch.dict("os.environ", {}, clear=True):
            tool._api_key_checked = False
            assert not tool.is_available()

    @patch("terminal.tools.marketdata_tools.MARKETDATA_CLIENT_AVAILABLE", True)
    def test_available_with_api_key(self):
        """Should be available when API key is set."""
        tool = GetOptionsChainTool()
        tool._api_key_checked = False
        with patch.dict("os.environ", {"MARKETDATA_API_KEY": "test_key"}):
            assert tool.is_available()


class TestGetOptionsChainTool:
    """Test options chain tool."""

    def test_metadata(self):
        tool = GetOptionsChainTool()
        assert tool.metadata.name == "get_options_chain"
        assert tool.metadata.category == ToolCategory.OPTIONS
        assert tool.metadata.provider == "MarketData"
        assert tool.metadata.requires_api_key is True
        assert tool.metadata.api_key_env_var == "MARKETDATA_API_KEY"

    @patch("terminal.tools.marketdata_tools.marketdata_client")
    @patch("terminal.tools.marketdata_tools.MARKETDATA_CLIENT_AVAILABLE", True)
    def test_execute(self, mock_client):
        """Should delegate to client method."""
        mock_client.get_options_chain.return_value = {"s": "ok", "strike": [200]}
        tool = GetOptionsChainTool()
        tool._api_key_checked = True
        tool._is_available = True

        result = tool.execute("AAPL", dte=30)

        mock_client.get_options_chain.assert_called_once_with(
            symbol="AAPL",
            expiration=None,
            dte=30,
            date_from=None,
            date_to=None,
            strike_limit=None,
            option_range=None,
            side=None,
        )
        assert result["strike"] == [200]


class TestGetOptionsExpirationsTool:
    """Test expirations tool."""

    def test_metadata(self):
        tool = GetOptionsExpirationsTool()
        assert tool.metadata.name == "get_options_expirations"
        assert tool.metadata.category == ToolCategory.OPTIONS

    @patch("terminal.tools.marketdata_tools.marketdata_client")
    def test_execute(self, mock_client):
        mock_client.get_options_expirations.return_value = ["2026-03-21", "2026-04-17"]
        tool = GetOptionsExpirationsTool()
        tool._api_key_checked = True
        tool._is_available = True

        result = tool.execute("AAPL")
        assert result == ["2026-03-21", "2026-04-17"]


class TestGetOptionQuoteTool:
    """Test option quote tool."""

    def test_metadata(self):
        tool = GetOptionQuoteTool()
        assert tool.metadata.name == "get_options_quote"
        assert tool.metadata.category == ToolCategory.OPTIONS

    @patch("terminal.tools.marketdata_tools.marketdata_client")
    def test_execute(self, mock_client):
        mock_client.get_options_quote.return_value = {"s": "ok", "bid": [8.50]}
        tool = GetOptionQuoteTool()
        tool._api_key_checked = True
        tool._is_available = True

        result = tool.execute("AAPL260321C00200000")
        mock_client.get_options_quote.assert_called_once_with(
            option_symbol="AAPL260321C00200000"
        )

    def test_execute_unavailable(self):
        """Should raise ToolExecutionError when unavailable."""
        tool = GetOptionQuoteTool()
        tool._api_key_checked = True
        tool._is_available = False

        with pytest.raises(ToolExecutionError):
            tool.execute("AAPL260321C00200000")


class TestFactory:
    """Test tool factory."""

    def test_create_marketdata_tools(self):
        tools = create_marketdata_tools()
        assert len(tools) == 3

        names = {t.metadata.name for t in tools}
        assert "get_options_chain" in names
        assert "get_options_expirations" in names
        assert "get_options_quote" in names

    def test_all_tools_have_options_category(self):
        tools = create_marketdata_tools()
        for tool in tools:
            assert tool.metadata.category == ToolCategory.OPTIONS
