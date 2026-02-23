"""
FMP (Financial Modeling Prep) tool wrappers.

Each tool wraps a method from src/data/fmp_client.py, following the FinanceTool protocol.
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

# Import the singleton FMP client
try:
    from src.data.fmp_client import fmp_client
    FMP_CLIENT_AVAILABLE = True
except ImportError:
    FMP_CLIENT_AVAILABLE = False
    fmp_client = None

logger = logging.getLogger(__name__)


class BaseFMPTool(FinanceTool):
    """Base class for all FMP tools - handles common logic."""

    def __init__(self):
        self._api_key_checked = False
        self._is_available = False

    def is_available(self) -> bool:
        """Check if FMP API key is present and client loaded."""
        if not self._api_key_checked:
            self._is_available = (
                FMP_CLIENT_AVAILABLE
                and bool(os.getenv("FMP_API_KEY"))
            )
            self._api_key_checked = True
        return self._is_available

    def _execute_client_method(self, method_name: str, **kwargs) -> Any:
        """
        Execute a method on the FMP client.

        Args:
            method_name: Name of the method on fmp_client
            **kwargs: Arguments to pass to the method

        Returns:
            Method result

        Raises:
            ToolExecutionError: If execution fails
        """
        if not self.is_available():
            raise ToolExecutionError(
                f"{self.metadata.name}: FMP API not available "
                "(missing API key or import failed)"
            )

        try:
            method = getattr(fmp_client, method_name)
            result = method(**kwargs)
            return result
        except Exception as e:
            logger.error(f"{self.metadata.name} failed: {e}")
            raise ToolExecutionError(f"FMP API call failed: {e}") from e


# ========== Market Data Tools ==========

class GetHistoricalPriceTool(BaseFMPTool):
    """Get historical daily price data for a symbol."""

    @property
    def metadata(self) -> ToolMetadata:
        return ToolMetadata(
            name="get_historical_price",
            category=ToolCategory.MARKET_DATA,
            description="Get historical daily OHLCV data (up to 5 years)",
            provider="FMP",
            requires_api_key=True,
            api_key_env_var="FMP_API_KEY",
        )

    def execute(self, symbol: str, years: int = 5) -> List[Dict]:
        """
        Execute: get historical price data.

        Args:
            symbol: Stock ticker symbol
            years: Number of years of history (default: 5)

        Returns:
            List of daily bars (most recent first)
        """
        return self._execute_client_method(
            "get_historical_price", symbol=symbol, years=years
        )


class GetQuoteTool(BaseFMPTool):
    """Get real-time quote for a symbol."""

    @property
    def metadata(self) -> ToolMetadata:
        return ToolMetadata(
            name="get_quote",
            category=ToolCategory.MARKET_DATA,
            description="Get real-time quote (price, volume, change)",
            provider="FMP",
            requires_api_key=True,
            api_key_env_var="FMP_API_KEY",
        )

    def execute(self, symbol: str) -> Optional[Dict]:
        """
        Execute: get real-time quote.

        Args:
            symbol: Stock ticker symbol

        Returns:
            Quote dict or None if not found
        """
        return self._execute_client_method("get_quote", symbol=symbol)


class GetScreenerPageTool(BaseFMPTool):
    """Get paginated screener results (entire market snapshot)."""

    @property
    def metadata(self) -> ToolMetadata:
        return ToolMetadata(
            name="get_screener_page",
            category=ToolCategory.MARKET_DATA,
            description="Get paginated market screener results with price/volume",
            provider="FMP",
            requires_api_key=True,
            api_key_env_var="FMP_API_KEY",
        )

    def execute(
        self,
        offset: int = 0,
        limit: int = 1000,
        volume_more_than: Optional[int] = None,
    ) -> List[Dict]:
        """
        Execute: get screener page.

        Args:
            offset: Pagination offset
            limit: Results per page
            volume_more_than: Optional volume filter

        Returns:
            List of stock snapshots
        """
        return self._execute_client_method(
            "get_screener_page",
            offset=offset,
            limit=limit,
            volume_more_than=volume_more_than,
        )


# ========== Company Info Tools ==========

class GetProfileTool(BaseFMPTool):
    """Get company profile (description, CEO, sector, etc.)."""

    @property
    def metadata(self) -> ToolMetadata:
        return ToolMetadata(
            name="get_profile",
            category=ToolCategory.COMPANY_INFO,
            description="Get company profile and description",
            provider="FMP",
            requires_api_key=True,
            api_key_env_var="FMP_API_KEY",
        )

    def execute(self, symbol: str) -> Optional[Dict]:
        """
        Execute: get company profile.

        Args:
            symbol: Stock ticker symbol

        Returns:
            Profile dict or None if not found
        """
        return self._execute_client_method("get_profile", symbol=symbol)


class GetLargeCapStocksTool(BaseFMPTool):
    """Get list of large-cap stocks above a market cap threshold."""

    @property
    def metadata(self) -> ToolMetadata:
        return ToolMetadata(
            name="get_large_cap_stocks",
            category=ToolCategory.COMPANY_INFO,
            description="Get stocks above a market cap threshold",
            provider="FMP",
            requires_api_key=True,
            api_key_env_var="FMP_API_KEY",
        )

    def execute(self, market_cap_threshold: int) -> List[Dict]:
        """
        Execute: get large-cap stocks.

        Args:
            market_cap_threshold: Minimum market cap in dollars

        Returns:
            List of stock info dicts
        """
        return self._execute_client_method(
            "get_large_cap_stocks", market_cap_threshold=market_cap_threshold
        )


# ========== Fundamentals Tools ==========

class GetRatiosTool(BaseFMPTool):
    """Get financial ratios (P/E, ROE, margins, etc.)."""

    @property
    def metadata(self) -> ToolMetadata:
        return ToolMetadata(
            name="get_ratios",
            category=ToolCategory.FUNDAMENTALS,
            description="Get financial ratios (quarterly)",
            provider="FMP",
            requires_api_key=True,
            api_key_env_var="FMP_API_KEY",
        )

    def execute(self, symbol: str, limit: int = 4) -> List[Dict]:
        """
        Execute: get financial ratios.

        Args:
            symbol: Stock ticker symbol
            limit: Number of quarters to retrieve

        Returns:
            List of ratio dicts (most recent first)
        """
        return self._execute_client_method("get_ratios", symbol=symbol, limit=limit)


class GetIncomeStatementTool(BaseFMPTool):
    """Get income statement."""

    @property
    def metadata(self) -> ToolMetadata:
        return ToolMetadata(
            name="get_income_statement",
            category=ToolCategory.FUNDAMENTALS,
            description="Get income statement (quarterly or annual)",
            provider="FMP",
            requires_api_key=True,
            api_key_env_var="FMP_API_KEY",
        )

    def execute(
        self, symbol: str, period: str = "quarter", limit: int = 8
    ) -> List[Dict]:
        """
        Execute: get income statement.

        Args:
            symbol: Stock ticker symbol
            period: 'quarter' or 'annual'
            limit: Number of periods to retrieve

        Returns:
            List of income statement dicts
        """
        return self._execute_client_method(
            "get_income_statement", symbol=symbol, period=period, limit=limit
        )


class GetBalanceSheetTool(BaseFMPTool):
    """Get balance sheet."""

    @property
    def metadata(self) -> ToolMetadata:
        return ToolMetadata(
            name="get_balance_sheet",
            category=ToolCategory.FUNDAMENTALS,
            description="Get balance sheet (quarterly or annual)",
            provider="FMP",
            requires_api_key=True,
            api_key_env_var="FMP_API_KEY",
        )

    def execute(
        self, symbol: str, period: str = "quarter", limit: int = 8
    ) -> List[Dict]:
        """
        Execute: get balance sheet.

        Args:
            symbol: Stock ticker symbol
            period: 'quarter' or 'annual'
            limit: Number of periods to retrieve

        Returns:
            List of balance sheet dicts
        """
        return self._execute_client_method(
            "get_balance_sheet", symbol=symbol, period=period, limit=limit
        )


class GetCashFlowTool(BaseFMPTool):
    """Get cash flow statement."""

    @property
    def metadata(self) -> ToolMetadata:
        return ToolMetadata(
            name="get_cash_flow",
            category=ToolCategory.FUNDAMENTALS,
            description="Get cash flow statement (quarterly or annual)",
            provider="FMP",
            requires_api_key=True,
            api_key_env_var="FMP_API_KEY",
        )

    def execute(
        self, symbol: str, period: str = "quarter", limit: int = 8
    ) -> List[Dict]:
        """
        Execute: get cash flow statement.

        Args:
            symbol: Stock ticker symbol
            period: 'quarter' or 'annual'
            limit: Number of periods to retrieve

        Returns:
            List of cash flow dicts
        """
        return self._execute_client_method(
            "get_cash_flow", symbol=symbol, period=period, limit=limit
        )


class GetKeyMetricsTool(BaseFMPTool):
    """Get key metrics (market cap, P/E, EPS, etc.)."""

    @property
    def metadata(self) -> ToolMetadata:
        return ToolMetadata(
            name="get_key_metrics",
            category=ToolCategory.FUNDAMENTALS,
            description="Get key financial metrics",
            provider="FMP",
            requires_api_key=True,
            api_key_env_var="FMP_API_KEY",
        )

    def execute(self, symbol: str, limit: int = 4) -> List[Dict]:
        """
        Execute: get key metrics.

        Args:
            symbol: Stock ticker symbol
            limit: Number of quarters to retrieve

        Returns:
            List of key metrics dicts
        """
        return self._execute_client_method("get_key_metrics", symbol=symbol, limit=limit)


class GetEarningsCalendarTool(BaseFMPTool):
    """Get earnings calendar for upcoming earnings dates."""

    @property
    def metadata(self) -> ToolMetadata:
        return ToolMetadata(
            name="get_earnings_calendar",
            category=ToolCategory.FUNDAMENTALS,
            description="Get upcoming earnings dates",
            provider="FMP",
            requires_api_key=True,
            api_key_env_var="FMP_API_KEY",
        )

    def execute(self, from_date: str = None, to_date: str = None) -> List[Dict]:
        """
        Execute: get earnings calendar.

        Args:
            from_date: Start date (YYYY-MM-DD), optional
            to_date: End date (YYYY-MM-DD), optional

        Returns:
            List of earnings events
        """
        return self._execute_client_method(
            "get_earnings_calendar", from_date=from_date, to_date=to_date
        )


class GetAnalystEstimatesTool(BaseFMPTool):
    """Get analyst earnings estimates."""

    @property
    def metadata(self) -> ToolMetadata:
        return ToolMetadata(
            name="get_analyst_estimates",
            category=ToolCategory.FUNDAMENTALS,
            description="Get analyst earnings estimates",
            provider="FMP",
            requires_api_key=True,
            api_key_env_var="FMP_API_KEY",
        )

    def execute(self, symbol: str, period: str = "quarter", limit: int = 4) -> List[Dict]:
        """
        Execute: get analyst estimates.

        Args:
            symbol: Stock ticker symbol
            period: 'quarter' or 'annual'
            limit: Number of periods to retrieve

        Returns:
            List of analyst estimate dicts
        """
        return self._execute_client_method(
            "get_analyst_estimates", symbol=symbol, period=period, limit=limit
        )


class GetInsiderTradesTool(BaseFMPTool):
    """Get insider trading activity."""

    @property
    def metadata(self) -> ToolMetadata:
        return ToolMetadata(
            name="get_insider_trades",
            category=ToolCategory.FUNDAMENTALS,
            description="Get insider trading records",
            provider="FMP",
            requires_api_key=True,
            api_key_env_var="FMP_API_KEY",
        )

    def execute(self, symbol: str, limit: int = 50) -> List[Dict]:
        """
        Execute: get insider trades.

        Args:
            symbol: Stock ticker symbol
            limit: Number of trades to retrieve

        Returns:
            List of insider trade dicts
        """
        return self._execute_client_method(
            "get_insider_trades", symbol=symbol, limit=limit
        )


class GetStockNewsTool(BaseFMPTool):
    """Get stock news."""

    @property
    def metadata(self) -> ToolMetadata:
        return ToolMetadata(
            name="get_stock_news",
            category=ToolCategory.NEWS,
            description="Get recent stock news",
            provider="FMP",
            requires_api_key=True,
            api_key_env_var="FMP_API_KEY",
        )

    def execute(self, tickers: str = None, limit: int = 50) -> List[Dict]:
        """
        Execute: get stock news.

        Args:
            tickers: Comma-separated ticker symbols (e.g., "AAPL,MSFT"), optional
            limit: Number of news items to retrieve

        Returns:
            List of news dicts
        """
        return self._execute_client_method(
            "get_stock_news", tickers=tickers, limit=limit
        )


# ========== Tool Factory ==========

def create_fmp_tools() -> List[FinanceTool]:
    """
    Factory function to create all FMP tools.

    Returns:
        List of FMP tool instances
    """
    return [
        # Market Data
        GetHistoricalPriceTool(),
        GetQuoteTool(),
        GetScreenerPageTool(),
        # Company Info
        GetProfileTool(),
        GetLargeCapStocksTool(),
        # Fundamentals
        GetRatiosTool(),
        GetIncomeStatementTool(),
        GetBalanceSheetTool(),
        GetCashFlowTool(),
        GetKeyMetricsTool(),
        GetAnalystEstimatesTool(),
        GetEarningsCalendarTool(),
        GetInsiderTradesTool(),
        # News
        GetStockNewsTool(),
    ]
