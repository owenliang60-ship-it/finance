"""Tests for IV Tracker module."""
import pytest
import math
from unittest.mock import patch, MagicMock
from pathlib import Path

from src.data.market_store import MarketStore
from terminal.options.iv_tracker import (
    compute_hv,
    get_iv_rank,
    get_iv_percentile,
    get_iv_history_summary,
    update_daily_iv,
)


@pytest.fixture
def store(tmp_path):
    """Create a fresh MarketStore with temp DB."""
    db_path = tmp_path / "test_market.db"
    s = MarketStore(db_path=db_path)
    return s


@pytest.fixture
def store_with_iv(store):
    """Store pre-loaded with IV history (30 data points)."""
    # Simulate IV history: a range from 0.20 to 0.40
    for i in range(30):
        iv = 0.20 + i * 0.007  # 0.20, 0.207, ..., ~0.40
        store.save_iv_daily(
            "AAPL",
            "2026-01-{:02d}".format(i + 1) if i < 28 else "2026-02-{:02d}".format(i - 27),
            iv_30d=round(iv, 4),
            hv_30d=round(iv * 0.8, 4),
        )
    return store


def _make_hv_price_df(n, start_price=100.0, increment=0.5):
    """Create a price DataFrame for HV tests (descending order)."""
    import pandas as pd
    from datetime import datetime, timedelta
    dates = [datetime(2026, 1, 1) + timedelta(days=i) for i in range(n)]
    prices = [start_price + i * increment for i in range(n)]
    df = pd.DataFrame({
        "date": pd.to_datetime(dates),
        "open": prices,
        "high": prices,
        "low": prices,
        "close": prices,
        "volume": [1000] * n,
        "change": [0.0] * n,
        "changePercent": [0.0] * n,
    })
    return df.sort_values("date", ascending=False).reset_index(drop=True)


class TestComputeHV:
    """Test historical volatility computation."""

    def test_compute_hv_basic(self):
        """Should compute HV from price data."""
        df = _make_hv_price_df(32)

        with patch("src.data.price_fetcher.load_price_cache", return_value=df):
            hv = compute_hv("AAPL", window=30)
            assert hv is not None
            assert 0 < hv < 1  # Should be a reasonable annualized vol

    def test_compute_hv_insufficient_data(self):
        """Should return None with insufficient data."""
        df = _make_hv_price_df(1)

        with patch("src.data.price_fetcher.load_price_cache", return_value=df):
            hv = compute_hv("AAPL", window=30)
            assert hv is None

    def test_compute_hv_no_data(self):
        """Should return None when no price data."""
        with patch("src.data.price_fetcher.load_price_cache", return_value=None), \
             patch("src.data.price_fetcher.fetch_and_update_price", return_value=None):
            hv = compute_hv("NOFILE", window=30)
            assert hv is None


class TestIVRank:
    """Test IV Rank calculation."""

    def test_iv_rank_at_high(self, store_with_iv):
        """IV at 52-week high should give rank ~100."""
        # Current IV is the last one saved (highest)
        rank = get_iv_rank("AAPL", store_with_iv)
        assert rank is not None
        assert rank > 90

    def test_iv_rank_at_low(self, store):
        """IV at 52-week low should give rank ~0."""
        # Save data where current IV is the lowest
        for i in range(10):
            store.save_iv_daily(
                "AAPL",
                "2026-01-{:02d}".format(i + 1),
                iv_30d=0.40 - i * 0.02,  # Decreasing: current is lowest
            )
        rank = get_iv_rank("AAPL", store)
        assert rank is not None
        assert rank < 10

    def test_iv_rank_midpoint(self, store):
        """IV at midpoint should give rank ~50."""
        # Save: 0.20, 0.30, 0.40 → current = 0.30
        store.save_iv_daily("AAPL", "2026-01-01", iv_30d=0.20)
        store.save_iv_daily("AAPL", "2026-01-02", iv_30d=0.40)
        store.save_iv_daily("AAPL", "2026-01-03", iv_30d=0.30)

        rank = get_iv_rank("AAPL", store)
        assert rank == 50.0  # (0.30 - 0.20) / (0.40 - 0.20) * 100

    def test_iv_rank_no_data(self, store):
        """Should return None when no IV data."""
        rank = get_iv_rank("AAPL", store)
        assert rank is None

    def test_iv_rank_single_point(self, store):
        """Should return None with only one data point."""
        store.save_iv_daily("AAPL", "2026-01-01", iv_30d=0.30)
        rank = get_iv_rank("AAPL", store)
        assert rank is None

    def test_iv_rank_flat_iv(self, store):
        """Should return 50 when IV is flat (no range)."""
        for i in range(5):
            store.save_iv_daily(
                "AAPL", "2026-01-{:02d}".format(i + 1), iv_30d=0.30
            )
        rank = get_iv_rank("AAPL", store)
        assert rank == 50.0


class TestIVPercentile:
    """Test IV Percentile calculation."""

    def test_iv_percentile_at_high(self, store_with_iv):
        """Highest IV should have high percentile."""
        pctl = get_iv_percentile("AAPL", store_with_iv)
        assert pctl is not None
        assert pctl > 90

    def test_iv_percentile_at_low(self, store):
        """Lowest IV should have low percentile."""
        for i in range(10):
            store.save_iv_daily(
                "AAPL",
                "2026-01-{:02d}".format(i + 1),
                iv_30d=0.40 - i * 0.02,  # Current is lowest
            )
        pctl = get_iv_percentile("AAPL", store)
        assert pctl is not None
        assert pctl < 10

    def test_iv_percentile_known(self, store):
        """Test with known values."""
        # 5 values: 0.20, 0.25, 0.30, 0.35, 0.28 (current)
        store.save_iv_daily("AAPL", "2026-01-01", iv_30d=0.20)
        store.save_iv_daily("AAPL", "2026-01-02", iv_30d=0.25)
        store.save_iv_daily("AAPL", "2026-01-03", iv_30d=0.30)
        store.save_iv_daily("AAPL", "2026-01-04", iv_30d=0.35)
        store.save_iv_daily("AAPL", "2026-01-05", iv_30d=0.28)

        pctl = get_iv_percentile("AAPL", store)
        # Current = 0.28 (history[0]), comparison set = [0.35, 0.30, 0.25, 0.20]
        # Below: 0.20, 0.25 = 2 out of 4 = 50%
        assert pctl == 50.0

    def test_iv_percentile_no_data(self, store):
        """Should return None when no data."""
        assert get_iv_percentile("AAPL", store) is None


class TestIVHistorySummary:
    """Test combined IV summary."""

    def test_full_summary(self, store_with_iv):
        """Should return complete summary."""
        summary = get_iv_history_summary("AAPL", store_with_iv)
        assert summary is not None
        assert "current_iv" in summary
        assert "iv_rank" in summary
        assert "iv_percentile" in summary
        assert "iv_52w_high" in summary
        assert "iv_52w_low" in summary
        assert "data_days" in summary
        assert summary["data_days"] == 30
        assert summary["symbol"] == "AAPL"

    def test_rv_iv_ratio(self, store_with_iv):
        """Should compute RV/IV ratio."""
        summary = get_iv_history_summary("AAPL", store_with_iv)
        assert summary["rv_iv_ratio"] is not None
        # HV was set to 0.8× IV, so ratio should be ~0.8
        assert abs(summary["rv_iv_ratio"] - 0.8) < 0.1

    def test_no_data(self, store):
        """Should return None when no data."""
        assert get_iv_history_summary("AAPL", store) is None


class TestUpdateDailyIV:
    """Test batch IV update."""

    def test_successful_update(self, store):
        """Should process symbols and store IV."""
        mock_client = MagicMock()
        mock_client.get_atm_iv_data.return_value = {
            "s": "ok",
            "iv": [0.28, 0.30],
            "volume": [1000, 500],
            "openInterest": [10000, 8000],
        }

        with patch("terminal.options.iv_tracker.compute_hv", return_value=0.22):
            result = update_daily_iv(["AAPL"], store, client=mock_client)

        assert result["success_count"] == 1
        assert result["fail_count"] == 0

        # Verify stored in DB
        latest = store.get_latest_iv("AAPL")
        assert latest is not None
        assert latest["iv_30d"] == 0.29  # Average of 0.28 and 0.30

    def test_no_data_response(self, store):
        """Should handle no_data response gracefully."""
        mock_client = MagicMock()
        mock_client.get_atm_iv_data.return_value = {"s": "no_data"}

        result = update_daily_iv(["AAPL"], store, client=mock_client)
        assert result["fail_count"] == 1
        assert "no data" in result["errors"][0]

    def test_mixed_results(self, store):
        """Should handle mix of success and failure."""
        mock_client = MagicMock()
        mock_client.get_atm_iv_data.side_effect = [
            {"s": "ok", "iv": [0.28]},
            None,  # MSFT fails
        ]

        with patch("terminal.options.iv_tracker.compute_hv", return_value=None):
            result = update_daily_iv(["AAPL", "MSFT"], store, client=mock_client)

        assert result["success_count"] == 1
        assert result["fail_count"] == 1
        assert result["total"] == 2

    def test_null_iv_values(self, store):
        """Should handle null IV values in response."""
        mock_client = MagicMock()
        mock_client.get_atm_iv_data.return_value = {
            "s": "ok",
            "iv": [None, None],
        }

        result = update_daily_iv(["AAPL"], store, client=mock_client)
        assert result["fail_count"] == 1
