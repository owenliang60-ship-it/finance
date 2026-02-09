"""Tests for FMP enrichment in pipeline.py (P0).

Tests DataPackage FMP fields, collect_data() FMP enrichment, and format_context() rendering.
"""
import pytest
from datetime import datetime
from unittest import mock

from terminal.pipeline import DataPackage, collect_data


# ---------------------------------------------------------------------------
# Sample FMP data
# ---------------------------------------------------------------------------

SAMPLE_ESTIMATES = [
    {
        "date": "2026-04-30",
        "estimatedEpsAvg": 0.88,
        "estimatedEpsLow": 0.75,
        "estimatedEpsHigh": 1.02,
        "estimatedRevenueAvg": 44_500_000_000,
    },
    {
        "date": "2026-01-31",
        "estimatedEpsAvg": 0.81,
        "estimatedEpsLow": 0.70,
        "estimatedEpsHigh": 0.95,
        "estimatedRevenueAvg": 39_200_000_000,
    },
]

SAMPLE_INSIDER_TRADES = [
    {
        "filingDate": "2026-01-15",
        "reportingName": "Jensen Huang",
        "transactionType": "S-Sale",
        "securitiesTransacted": 100_000,
        "price": 130.50,
    },
    {
        "transactionDate": "2026-01-10",
        "reportingName": "Colette Kress",
        "transactionType": "S-Sale",
        "securitiesTransacted": 50_000,
        "price": 128.00,
    },
]

SAMPLE_NEWS = [
    {
        "publishedDate": "2026-02-08T14:30:00.000Z",
        "title": "NVIDIA Announces New AI Chip Architecture",
    },
    {
        "publishedDate": "2026-02-07T09:00:00.000Z",
        "title": "NVIDIA Partners With Major Cloud Providers",
    },
]

SAMPLE_EARNINGS_CALENDAR = [
    {
        "symbol": "NVDA",
        "date": "2026-02-26",
        "epsEstimated": 0.88,
        "revenueEstimated": 44_500_000_000,
    },
    {
        "symbol": "AAPL",
        "date": "2026-02-20",
        "epsEstimated": 2.10,
        "revenueEstimated": 95_000_000_000,
    },
]


# ---------------------------------------------------------------------------
# TestDataPackageFMPFields
# ---------------------------------------------------------------------------

class TestDataPackageFMPFields:
    """Test DataPackage FMP field defaults and creation."""

    def test_default_values(self):
        pkg = DataPackage(symbol="NVDA")
        assert pkg.analyst_estimates is None
        assert pkg.earnings_calendar is None
        assert pkg.insider_trades == []
        assert pkg.news == []

    def test_creation_with_data(self):
        pkg = DataPackage(
            symbol="NVDA",
            analyst_estimates=SAMPLE_ESTIMATES,
            earnings_calendar=SAMPLE_EARNINGS_CALENDAR[:1],
            insider_trades=SAMPLE_INSIDER_TRADES,
            news=SAMPLE_NEWS,
        )
        assert len(pkg.analyst_estimates) == 2
        assert len(pkg.earnings_calendar) == 1
        assert len(pkg.insider_trades) == 2
        assert len(pkg.news) == 2


# ---------------------------------------------------------------------------
# TestFormatContextFMPEnrichment
# ---------------------------------------------------------------------------

class TestFormatContextFMPEnrichment:
    """Test format_context() renders FMP sections correctly."""

    def test_analyst_consensus_section(self):
        pkg = DataPackage(symbol="NVDA", analyst_estimates=SAMPLE_ESTIMATES)
        ctx = pkg.format_context()
        assert "### Analyst Consensus" in ctx
        assert "2026-04-30" in ctx
        assert "EPS 0.75/0.88/1.02" in ctx
        assert "$44.5B" in ctx

    def test_earnings_calendar_section(self):
        pkg = DataPackage(
            symbol="NVDA",
            earnings_calendar=[SAMPLE_EARNINGS_CALENDAR[0]],
        )
        ctx = pkg.format_context()
        assert "### Upcoming Earnings" in ctx
        assert "2026-02-26" in ctx
        assert "0.88" in ctx

    def test_insider_trades_section(self):
        pkg = DataPackage(symbol="NVDA", insider_trades=SAMPLE_INSIDER_TRADES)
        ctx = pkg.format_context()
        assert "### Recent Insider Activity" in ctx
        assert "Jensen Huang" in ctx
        assert "S-Sale" in ctx
        assert "100,000" in ctx

    def test_news_section(self):
        pkg = DataPackage(symbol="NVDA", news=SAMPLE_NEWS)
        ctx = pkg.format_context()
        assert "### Recent News" in ctx
        assert "NVIDIA Announces New AI Chip Architecture" in ctx
        # Date should be truncated at T
        assert "2026-02-08" in ctx
        assert "T14:30" not in ctx

    def test_no_data_no_sections(self):
        pkg = DataPackage(symbol="NVDA")
        ctx = pkg.format_context()
        assert "### Analyst Consensus" not in ctx
        assert "### Upcoming Earnings" not in ctx
        assert "### Recent Insider Activity" not in ctx
        assert "### Recent News" not in ctx

    def test_insider_trades_sorted_by_value(self):
        """Insider trades should be sorted by transaction value (descending)."""
        pkg = DataPackage(symbol="NVDA", insider_trades=SAMPLE_INSIDER_TRADES)
        ctx = pkg.format_context()
        # Jensen Huang trade ($13.05M) should appear before Colette Kress ($6.4M)
        jensen_pos = ctx.index("Jensen Huang")
        colette_pos = ctx.index("Colette Kress")
        assert jensen_pos < colette_pos

    def test_news_date_without_timestamp(self):
        """News with date that has no T separator should render as-is."""
        pkg = DataPackage(
            symbol="NVDA",
            news=[{"publishedDate": "2026-02-08", "title": "Test"}],
        )
        ctx = pkg.format_context()
        assert "2026-02-08" in ctx


# ---------------------------------------------------------------------------
# TestCollectDataFMPEnrichment
# ---------------------------------------------------------------------------

class TestCollectDataFMPEnrichment:
    """Test collect_data() FMP enrichment integration."""

    def _make_mock_registry(self, results=None):
        """Create a mock registry that returns configured results."""
        if results is None:
            results = {
                "get_analyst_estimates": {"success": True, "data": SAMPLE_ESTIMATES},
                "get_insider_trades": {"success": True, "data": SAMPLE_INSIDER_TRADES},
                "get_stock_news": {"success": True, "data": SAMPLE_NEWS},
                "get_earnings_calendar": {"success": True, "data": SAMPLE_EARNINGS_CALENDAR},
            }

        mock_registry = mock.MagicMock()

        def execute_side_effect(tool_name, **kwargs):
            return results.get(tool_name, {"success": False})

        mock_registry.execute.side_effect = execute_side_effect
        return mock_registry

    @mock.patch("terminal.pipeline.get_company_record")
    @mock.patch("terminal.macro_fetcher.get_macro_snapshot")
    @mock.patch("src.indicators.engine.run_indicators")
    @mock.patch("src.data.data_query.get_stock_data")
    @mock.patch("terminal.tools.registry.get_registry")
    def test_all_fmp_fields_populated(
        self, mock_get_registry, mock_stock, mock_indicators,
        mock_macro, mock_company
    ):
        """All 4 FMP fields should be populated when registry works."""
        mock_registry = self._make_mock_registry()
        mock_get_registry.return_value = mock_registry
        mock_stock.return_value = {}
        mock_indicators.return_value = {}
        mock_macro.return_value = None
        mock_company.return_value = None

        pkg = collect_data("NVDA")

        assert pkg.analyst_estimates == SAMPLE_ESTIMATES
        assert pkg.insider_trades == SAMPLE_INSIDER_TRADES
        assert pkg.news == SAMPLE_NEWS
        # earnings_calendar is filtered by symbol
        assert len(pkg.earnings_calendar) == 1
        assert pkg.earnings_calendar[0]["symbol"] == "NVDA"

    @mock.patch("terminal.pipeline.get_company_record")
    @mock.patch("terminal.macro_fetcher.get_macro_snapshot")
    @mock.patch("src.indicators.engine.run_indicators")
    @mock.patch("src.data.data_query.get_stock_data")
    @mock.patch("terminal.tools.registry.get_registry")
    def test_registry_unavailable_graceful(
        self, mock_get_registry, mock_stock, mock_indicators,
        mock_macro, mock_company
    ):
        """When registry fails to load, FMP fields stay at defaults."""
        mock_get_registry.side_effect = ImportError("no registry")
        mock_stock.return_value = {}
        mock_indicators.return_value = {}
        mock_macro.return_value = None
        mock_company.return_value = None

        pkg = collect_data("NVDA")

        assert pkg.analyst_estimates is None
        assert pkg.earnings_calendar is None
        assert pkg.insider_trades == []
        assert pkg.news == []

    @mock.patch("terminal.pipeline.get_company_record")
    @mock.patch("terminal.macro_fetcher.get_macro_snapshot")
    @mock.patch("src.indicators.engine.run_indicators")
    @mock.patch("src.data.data_query.get_stock_data")
    @mock.patch("terminal.tools.registry.get_registry")
    def test_individual_tool_failure_graceful(
        self, mock_get_registry, mock_stock, mock_indicators,
        mock_macro, mock_company
    ):
        """If one FMP tool fails, others should still work."""
        results = {
            "get_analyst_estimates": {"success": True, "data": SAMPLE_ESTIMATES},
            "get_insider_trades": {"success": True, "data": SAMPLE_INSIDER_TRADES},
            "get_stock_news": {"success": True, "data": SAMPLE_NEWS},
            "get_earnings_calendar": {"success": True, "data": SAMPLE_EARNINGS_CALENDAR},
        }
        mock_registry = self._make_mock_registry(results)
        # Make insider trades raise
        original_side_effect = mock_registry.execute.side_effect

        def patched_execute(tool_name, **kwargs):
            if tool_name == "get_insider_trades":
                raise ConnectionError("API timeout")
            return original_side_effect(tool_name, **kwargs)

        mock_registry.execute.side_effect = patched_execute
        mock_get_registry.return_value = mock_registry
        mock_stock.return_value = {}
        mock_indicators.return_value = {}
        mock_macro.return_value = None
        mock_company.return_value = None

        pkg = collect_data("NVDA")

        # Insider trades should remain default (empty list) due to error
        assert pkg.insider_trades == []
        # Others should still be populated
        assert pkg.analyst_estimates == SAMPLE_ESTIMATES
        assert pkg.news == SAMPLE_NEWS

    @mock.patch("terminal.pipeline.get_company_record")
    @mock.patch("terminal.macro_fetcher.get_macro_snapshot")
    @mock.patch("src.indicators.engine.run_indicators")
    @mock.patch("src.data.data_query.get_stock_data")
    @mock.patch("terminal.tools.registry.get_registry")
    def test_tool_returns_failure(
        self, mock_get_registry, mock_stock, mock_indicators,
        mock_macro, mock_company
    ):
        """If a tool returns success=False, the field should stay at default."""
        results = {
            "get_analyst_estimates": {"success": False, "error": "not found"},
            "get_insider_trades": {"success": True, "data": SAMPLE_INSIDER_TRADES},
            "get_stock_news": {"success": True, "data": SAMPLE_NEWS},
            "get_earnings_calendar": {"success": True, "data": SAMPLE_EARNINGS_CALENDAR},
        }
        mock_registry = self._make_mock_registry(results)
        mock_get_registry.return_value = mock_registry
        mock_stock.return_value = {}
        mock_indicators.return_value = {}
        mock_macro.return_value = None
        mock_company.return_value = None

        pkg = collect_data("NVDA")

        # analyst_estimates should remain None (success=False)
        assert pkg.analyst_estimates is None
        # Others should be populated
        assert pkg.insider_trades == SAMPLE_INSIDER_TRADES
        assert pkg.news == SAMPLE_NEWS
