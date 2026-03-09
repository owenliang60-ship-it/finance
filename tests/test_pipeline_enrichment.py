"""Tests for pipeline.py enrichment (forward estimates + FMP).

Tests DataPackage fields, collect_data() enrichment, and format_context() rendering.
"""
import pytest
from datetime import datetime
from unittest import mock

from terminal.pipeline import DataPackage, collect_data


# ---------------------------------------------------------------------------
# Sample data
# ---------------------------------------------------------------------------

SAMPLE_FORWARD_ESTIMATES = [
    {"date": "2026-03-09", "period": "0q",
     "eps_avg": 1.95, "eps_low": 1.85, "eps_high": 2.16,
     "eps_num_analysts": 29, "eps_growth": 0.1846,
     "rev_avg": 109_079_879_710, "rev_num_analysts": 32, "rev_growth": 0.1439,
     "growth_stock": 0.185, "growth_index": 0.133,
     "eps_trend_current": 1.95454, "eps_trend_90d": 1.84290,
     "eps_rev_up_30d": 25, "eps_rev_down_30d": 1},
    {"date": "2026-03-09", "period": "+1q",
     "eps_avg": 1.72, "eps_low": 1.59, "eps_high": 1.86,
     "eps_num_analysts": 27, "eps_growth": 0.0986,
     "rev_avg": 101_642_789_290, "rev_num_analysts": 29, "rev_growth": 0.0809},
]

SAMPLE_FORWARD_METADATA = {
    "date": "2026-03-09",
    "price_target_current": 257.46,
    "price_target_high": 350.0,
    "price_target_low": 205.0,
    "price_target_mean": 292.15,
    "price_target_median": 300.0,
}

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

SAMPLE_ANALYST_RECOMMENDATIONS = [
    # Two grades from same firm — should deduplicate (keep first = latest)
    {"gradingCompany": "Morgan Stanley", "newGrade": "Overweight", "date": "2026-02-01"},
    {"gradingCompany": "Morgan Stanley", "newGrade": "Equal-Weight", "date": "2025-11-01"},
    # Different firms
    {"gradingCompany": "Goldman Sachs", "newGrade": "Buy", "date": "2026-01-15"},
    {"gradingCompany": "JP Morgan", "newGrade": "Neutral", "date": "2026-01-10"},
    {"gradingCompany": "Barclays", "newGrade": "Underweight", "date": "2025-12-20"},
    {"gradingCompany": "Citi", "newGrade": "Strong Buy", "date": "2026-02-05"},
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
# TestDataPackageFields
# ---------------------------------------------------------------------------

class TestDataPackageFields:
    """Test DataPackage field defaults and creation."""

    def test_default_values(self):
        pkg = DataPackage(symbol="NVDA")
        assert pkg.forward_estimates is None
        assert pkg.forward_metadata is None
        assert pkg.analyst_recommendations is None
        assert pkg.earnings_calendar is None
        assert pkg.insider_trades == []
        assert pkg.news == []

    def test_creation_with_forward_data(self):
        pkg = DataPackage(
            symbol="NVDA",
            forward_estimates=SAMPLE_FORWARD_ESTIMATES,
            forward_metadata=SAMPLE_FORWARD_METADATA,
            analyst_recommendations=SAMPLE_ANALYST_RECOMMENDATIONS,
            earnings_calendar=SAMPLE_EARNINGS_CALENDAR[:1],
            insider_trades=SAMPLE_INSIDER_TRADES,
            news=SAMPLE_NEWS,
        )
        assert len(pkg.forward_estimates) == 2
        assert pkg.forward_metadata["price_target_mean"] == 292.15
        assert len(pkg.analyst_recommendations) == 6
        assert len(pkg.earnings_calendar) == 1
        assert len(pkg.insider_trades) == 2
        assert len(pkg.news) == 2


# ---------------------------------------------------------------------------
# TestFormatContextForwardEstimates
# ---------------------------------------------------------------------------

class TestFormatContextForwardEstimates:
    """Test format_context() renders forward estimates sections correctly."""

    def test_forward_estimates_consensus_table(self):
        pkg = DataPackage(symbol="NVDA", forward_estimates=SAMPLE_FORWARD_ESTIMATES)
        ctx = pkg.format_context()
        assert "### Forward Estimates (Consensus)" in ctx
        assert "| 0q |" in ctx
        assert "1.85/1.95/2.16" in ctx
        assert "$109.1B" in ctx
        assert "+18.5%" in ctx

    def test_estimate_momentum_section(self):
        pkg = DataPackage(symbol="NVDA", forward_estimates=SAMPLE_FORWARD_ESTIMATES)
        ctx = pkg.format_context()
        assert "### Estimate Momentum" in ctx
        assert "25 up / 1 down" in ctx
        assert "EPS Drift" in ctx
        assert "outgrowing market" in ctx

    def test_price_targets_section(self):
        pkg = DataPackage(
            symbol="NVDA",
            forward_estimates=SAMPLE_FORWARD_ESTIMATES,
            forward_metadata=SAMPLE_FORWARD_METADATA,
        )
        ctx = pkg.format_context()
        assert "### Analyst Price Targets" in ctx
        assert "$257.46" in ctx
        assert "$292.15" in ctx
        assert "Implied Upside: +13.5%" in ctx

    def test_no_forward_estimates_no_sections(self):
        pkg = DataPackage(symbol="NVDA")
        ctx = pkg.format_context()
        assert "### Forward Estimates" not in ctx
        assert "### Estimate Momentum" not in ctx
        assert "### Analyst Price Targets" not in ctx


# ---------------------------------------------------------------------------
# TestFormatContextFMPEnrichment (unchanged sections)
# ---------------------------------------------------------------------------

class TestFormatContextFMPEnrichment:
    """Test format_context() renders FMP sections correctly (insider, news, ratings, earnings)."""

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

    def test_analyst_rating_distribution_section(self):
        """Analyst rating distribution should render with firm dedup and bucketing."""
        pkg = DataPackage(symbol="NVDA", analyst_recommendations=SAMPLE_ANALYST_RECOMMENDATIONS)
        ctx = pkg.format_context()
        assert "### Analyst Rating Distribution" in ctx
        assert "Buy/Outperform: 3" in ctx
        assert "Hold/Neutral: 1" in ctx
        assert "Sell/Underperform: 1" in ctx
        assert "Total Analysts: 5" in ctx

    def test_analyst_rating_distribution_percentages(self):
        """Percentages should be computed correctly."""
        pkg = DataPackage(symbol="NVDA", analyst_recommendations=SAMPLE_ANALYST_RECOMMENDATIONS)
        ctx = pkg.format_context()
        assert "60%" in ctx
        assert "20%" in ctx

    def test_analyst_rating_no_data_no_section(self):
        """No analyst_recommendations should produce no section."""
        pkg = DataPackage(symbol="NVDA", analyst_recommendations=None)
        ctx = pkg.format_context()
        assert "### Analyst Rating Distribution" not in ctx

    def test_no_data_no_sections(self):
        pkg = DataPackage(symbol="NVDA")
        ctx = pkg.format_context()
        assert "### Forward Estimates" not in ctx
        assert "### Estimate Momentum" not in ctx
        assert "### Analyst Price Targets" not in ctx
        assert "### Analyst Rating Distribution" not in ctx
        assert "### Upcoming Earnings" not in ctx
        assert "### Recent Insider Activity" not in ctx
        assert "### Recent News" not in ctx

    def test_insider_trades_sorted_by_value(self):
        """Insider trades should be sorted by transaction value (descending)."""
        pkg = DataPackage(symbol="NVDA", insider_trades=SAMPLE_INSIDER_TRADES)
        ctx = pkg.format_context()
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
# TestCollectDataEnrichment
# ---------------------------------------------------------------------------

class TestCollectDataEnrichment:
    """Test collect_data() enrichment integration."""

    def _make_mock_registry(self, results=None):
        """Create a mock registry that returns configured results."""
        if results is None:
            results = {
                "get_analyst_recommendations": SAMPLE_ANALYST_RECOMMENDATIONS,
                "get_insider_trades": SAMPLE_INSIDER_TRADES,
                "get_stock_news": SAMPLE_NEWS,
                "get_earnings_calendar": SAMPLE_EARNINGS_CALENDAR,
            }

        mock_registry = mock.MagicMock()

        def execute_side_effect(tool_name, **kwargs):
            return results.get(tool_name, [])

        mock_registry.execute.side_effect = execute_side_effect
        return mock_registry

    @mock.patch("terminal.pipeline.get_company_record")
    @mock.patch("terminal.macro_fetcher.get_macro_snapshot")
    @mock.patch("src.indicators.engine.run_indicators")
    @mock.patch("src.data.data_query.get_stock_data")
    @mock.patch("terminal.tools.registry.get_registry")
    @mock.patch("src.data.yfinance_client.yfinance_client")
    @mock.patch("src.data.market_store.get_store")
    def test_forward_estimates_from_yfinance_fallback(
        self, mock_get_store, mock_yf, mock_get_registry, mock_stock,
        mock_indicators, mock_macro, mock_company
    ):
        """When market.db has no data, yfinance live fallback should be used."""
        # market.db returns empty
        mock_store = mock.MagicMock()
        mock_store.get_latest_forward_estimates.return_value = []
        mock_store.get_latest_forward_metadata.return_value = None
        mock_get_store.return_value = mock_store

        # yfinance returns data
        mock_yf.get_forward_estimates.return_value = (
            SAMPLE_FORWARD_ESTIMATES, SAMPLE_FORWARD_METADATA
        )

        mock_registry = self._make_mock_registry()
        mock_get_registry.return_value = mock_registry
        mock_stock.return_value = {}
        mock_indicators.return_value = {}
        mock_macro.return_value = None
        mock_company.return_value = None

        pkg = collect_data("NVDA")

        assert pkg.forward_estimates == SAMPLE_FORWARD_ESTIMATES
        assert pkg.forward_metadata == SAMPLE_FORWARD_METADATA

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

        assert pkg.analyst_recommendations is None
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
            "get_insider_trades": SAMPLE_INSIDER_TRADES,
            "get_stock_news": SAMPLE_NEWS,
            "get_earnings_calendar": SAMPLE_EARNINGS_CALENDAR,
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
        assert pkg.news == SAMPLE_NEWS
