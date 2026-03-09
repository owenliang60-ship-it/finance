"""Tests for yfinance forward estimates client."""
import pytest
from unittest import mock
from datetime import date

import pandas as pd

from src.data.yfinance_client import YFinanceClient


# Sample DataFrames matching yfinance output
SAMPLE_EARNINGS_EST = pd.DataFrame({
    "avg": [1.95, 1.72, 8.50, 9.31],
    "low": [1.85, 1.59, 8.15, 8.36],
    "high": [2.16, 1.86, 8.97, 10.19],
    "yearAgoEps": [1.65, 1.57, 7.46, 8.50],
    "numberOfAnalysts": [29, 27, 38, 40],
    "growth": [0.1846, 0.0986, 0.1390, 0.0959],
}, index=pd.Index(["0q", "+1q", "0y", "+1y"], name="period"))

SAMPLE_REVENUE_EST = pd.DataFrame({
    "avg": [109_079_879_710, 101_642_789_290, 465_024_459_540, 495_001_546_900],
    "low": [105_000_000_000, 95_980_000_000, 448_737_000_000, 454_827_000_000],
    "high": [115_000_000_000, 108_000_000_000, 480_000_000_000, 540_000_000_000],
    "yearAgoRevenue": [95_359_000_000, 94_036_000_000, 416_161_000_000, 465_024_459_540],
    "numberOfAnalysts": [32, 29, 38, 40],
    "growth": [0.1439, 0.0809, 0.1174, 0.0645],
}, index=pd.Index(["0q", "+1q", "0y", "+1y"], name="period"))

SAMPLE_GROWTH_EST = pd.DataFrame({
    "stockTrend": [0.185, 0.099, 0.139, 0.093, None],
    "indexTrend": [0.133, 0.112, 0.151, 0.162, 0.122],
}, index=pd.Index(["0q", "+1q", "0y", "+1y", "LTG"], name="period"))

SAMPLE_PRICE_TARGETS = {
    "current": 257.46,
    "high": 350.0,
    "low": 205.0,
    "mean": 292.15,
    "median": 300.0,
}

SAMPLE_EPS_TREND = pd.DataFrame({
    "current": [1.95454, 1.72488, 8.49731, 9.31197],
    "7daysAgo": [1.95289, 1.73246, 8.50696, 9.32731],
    "30daysAgo": [1.94679, 1.73226, 8.47850, 9.29309],
    "60daysAgo": [1.84245, 1.70764, 8.26396, 9.13429],
    "90daysAgo": [1.84290, 1.70809, 8.25726, 9.10952],
}, index=pd.Index(["0q", "+1q", "0y", "+1y"], name="period"))

SAMPLE_EPS_REVISIONS = pd.DataFrame({
    "upLast7days": [0, 0, 1, 2],
    "upLast30days": [25, 14, 35, 27],
    "downLast30days": [1, 11, 0, 6],
    "downLast7Days": [0, 0, 0, 0],
}, index=pd.Index(["0q", "+1q", "0y", "+1y"], name="period"))


class TestYFinanceClient:

    def _mock_ticker(self):
        """Create a mock yfinance Ticker with all 6 properties."""
        ticker = mock.MagicMock()
        ticker.earnings_estimate = SAMPLE_EARNINGS_EST
        ticker.revenue_estimate = SAMPLE_REVENUE_EST
        ticker.growth_estimates = SAMPLE_GROWTH_EST
        ticker.analyst_price_targets = SAMPLE_PRICE_TARGETS
        ticker.eps_trend = SAMPLE_EPS_TREND
        ticker.eps_revisions = SAMPLE_EPS_REVISIONS
        return ticker

    @mock.patch("src.data.yfinance_client.yf.Ticker")
    def test_get_forward_estimates_returns_rows(self, mock_ticker_cls):
        mock_ticker_cls.return_value = self._mock_ticker()
        client = YFinanceClient()
        estimates, metadata = client.get_forward_estimates("AAPL")

        assert len(estimates) == 4
        row_0q = next(r for r in estimates if r["period"] == "0q")
        assert row_0q["eps_avg"] == 1.95
        assert row_0q["eps_num_analysts"] == 29
        assert row_0q["rev_avg"] == 109_079_879_710
        assert row_0q["eps_rev_up_30d"] == 25
        assert row_0q["eps_trend_current"] == pytest.approx(1.95454)
        assert row_0q["growth_stock"] == pytest.approx(0.185)

    @mock.patch("src.data.yfinance_client.yf.Ticker")
    def test_get_forward_estimates_metadata(self, mock_ticker_cls):
        mock_ticker_cls.return_value = self._mock_ticker()
        client = YFinanceClient()
        _, metadata = client.get_forward_estimates("AAPL")

        assert metadata["price_target_current"] == 257.46
        assert metadata["price_target_high"] == 350.0
        assert metadata["price_target_median"] == 300.0

    @mock.patch("src.data.yfinance_client.yf.Ticker")
    def test_handles_none_earnings_estimate(self, mock_ticker_cls):
        ticker = self._mock_ticker()
        ticker.earnings_estimate = None
        mock_ticker_cls.return_value = ticker
        client = YFinanceClient()
        estimates, metadata = client.get_forward_estimates("AAPL")

        # Should still return rows (from revenue_estimate index)
        assert len(estimates) == 4
        row = next(r for r in estimates if r["period"] == "0q")
        assert row.get("eps_avg") is None  # no earnings data → key absent
        assert row["rev_avg"] == 109_079_879_710

    @mock.patch("src.data.yfinance_client.yf.Ticker")
    def test_handles_all_none(self, mock_ticker_cls):
        ticker = mock.MagicMock()
        ticker.earnings_estimate = None
        ticker.revenue_estimate = None
        ticker.growth_estimates = None
        ticker.analyst_price_targets = None
        ticker.eps_trend = None
        ticker.eps_revisions = None
        mock_ticker_cls.return_value = ticker
        client = YFinanceClient()
        estimates, metadata = client.get_forward_estimates("AAPL")

        assert estimates == []
        assert metadata == {}

    @mock.patch("src.data.yfinance_client.yf.Ticker")
    def test_handles_empty_dataframes(self, mock_ticker_cls):
        ticker = mock.MagicMock()
        ticker.earnings_estimate = pd.DataFrame()
        ticker.revenue_estimate = pd.DataFrame()
        ticker.growth_estimates = pd.DataFrame()
        ticker.analyst_price_targets = {}
        ticker.eps_trend = pd.DataFrame()
        ticker.eps_revisions = pd.DataFrame()
        mock_ticker_cls.return_value = ticker
        client = YFinanceClient()
        estimates, metadata = client.get_forward_estimates("AAPL")

        assert estimates == []
        assert metadata == {}

    @mock.patch("src.data.yfinance_client.yf.Ticker")
    def test_nan_values_become_none(self, mock_ticker_cls):
        """NaN values in DataFrames should be converted to None for SQLite."""
        ticker = self._mock_ticker()
        mock_ticker_cls.return_value = ticker
        client = YFinanceClient()
        estimates, _ = client.get_forward_estimates("AAPL")

        # Check NaN handling — all float values should be actual numbers (not NaN)
        for row in estimates:
            for v in row.values():
                if isinstance(v, float):
                    assert v == v  # NaN != NaN
