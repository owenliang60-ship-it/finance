"""
MarketData.app tool wrappers for options data.

Each tool wraps a method from src/data/marketdata_client.py, following the FinanceTool protocol.
"""
import os
import logging
from typing import Any, Dict, List, Optional

from terminal.tools.protocol import (
    FinanceTool,
    ToolCategory,
    ToolMetadata,
    ToolExecutionError,
)

# Import the singleton MarketData client
try:
    from src.data.marketdata_client import marketdata_client
    MARKETDATA_CLIENT_AVAILABLE = True
except ImportError:
    MARKETDATA_CLIENT_AVAILABLE = False
    marketdata_client = None

logger = logging.getLogger(__name__)


class BaseMarketDataTool(FinanceTool):
    """Base class for all MarketData.app tools."""

    def __init__(self):
        self._api_key_checked = False
        self._is_available = False

    def is_available(self) -> bool:
        """Check if MarketData API key is present and client loaded."""
        if not self._api_key_checked:
            self._is_available = (
                MARKETDATA_CLIENT_AVAILABLE
                and bool(os.getenv("MARKETDATA_API_KEY"))
            )
            self._api_key_checked = True
        return self._is_available

    def _execute_client_method(self, method_name: str, **kwargs) -> Any:
        """Execute a method on the MarketData client.

        Args:
            method_name: Name of the method on marketdata_client
            **kwargs: Arguments to pass to the method

        Returns:
            Method result

        Raises:
            ToolExecutionError: If execution fails
        """
        if not self.is_available():
            raise ToolExecutionError(
                "{}: MarketData API not available "
                "(missing API key or import failed)".format(self.metadata.name)
            )

        try:
            method = getattr(marketdata_client, method_name)
            return method(**kwargs)
        except Exception as e:
            logger.error("%s failed: %s", self.metadata.name, e)
            raise ToolExecutionError(
                "MarketData API call failed: {}".format(e)
            ) from e


# ========== Options Tools ==========

class GetOptionsChainTool(BaseMarketDataTool):
    """Get options chain data for a symbol."""

    @property
    def metadata(self) -> ToolMetadata:
        return ToolMetadata(
            name="get_options_chain",
            category=ToolCategory.OPTIONS,
            description="Get options chain with strikes, Greeks, and IV",
            provider="MarketData",
            requires_api_key=True,
            api_key_env_var="MARKETDATA_API_KEY",
        )

    def execute(
        self,
        symbol: str,
        expiration: Optional[str] = None,
        dte_min: Optional[int] = None,
        dte_max: Optional[int] = None,
        strike_limit: Optional[int] = None,
        option_range: Optional[str] = None,
        side: Optional[str] = None,
    ) -> Optional[Dict]:
        """Execute: get options chain.

        Args:
            symbol: Stock ticker symbol
            expiration: Specific expiration date (YYYY-MM-DD)
            dte_min: Minimum days to expiration
            dte_max: Maximum days to expiration
            strike_limit: Limit strikes per expiration (saves credits)
            option_range: 'itm', 'otm', or 'atm'
            side: 'call' or 'put'

        Returns:
            Chain data dict with arrays of strike, bid, ask, iv, delta, etc.
        """
        return self._execute_client_method(
            "get_options_chain",
            symbol=symbol,
            expiration=expiration,
            dte_min=dte_min,
            dte_max=dte_max,
            strike_limit=strike_limit,
            option_range=option_range,
            side=side,
        )


class GetOptionsExpirationsTool(BaseMarketDataTool):
    """Get available options expiration dates for a symbol."""

    @property
    def metadata(self) -> ToolMetadata:
        return ToolMetadata(
            name="get_options_expirations",
            category=ToolCategory.OPTIONS,
            description="Get available options expiration dates",
            provider="MarketData",
            requires_api_key=True,
            api_key_env_var="MARKETDATA_API_KEY",
        )

    def execute(self, symbol: str) -> Optional[List[str]]:
        """Execute: get options expirations.

        Args:
            symbol: Stock ticker symbol

        Returns:
            List of expiration date strings
        """
        return self._execute_client_method(
            "get_options_expirations", symbol=symbol
        )


class GetOptionQuoteTool(BaseMarketDataTool):
    """Get quote for a specific option contract."""

    @property
    def metadata(self) -> ToolMetadata:
        return ToolMetadata(
            name="get_option_quote",
            category=ToolCategory.OPTIONS,
            description="Get quote for a specific option contract by OCC symbol",
            provider="MarketData",
            requires_api_key=True,
            api_key_env_var="MARKETDATA_API_KEY",
        )

    def execute(self, option_symbol: str) -> Optional[Dict]:
        """Execute: get option quote.

        Args:
            option_symbol: OCC standard option symbol (e.g. AAPL260321C00200000)

        Returns:
            Option quote dict
        """
        return self._execute_client_method(
            "get_options_quote", option_symbol=option_symbol
        )


# ========== Tool Factory ==========

def create_marketdata_tools() -> List[FinanceTool]:
    """Factory function to create all MarketData tools.

    Returns:
        List of MarketData tool instances
    """
    return [
        GetOptionsChainTool(),
        GetOptionsExpirationsTool(),
        GetOptionQuoteTool(),
    ]
