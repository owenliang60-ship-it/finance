"""Tests for IV Tracker module."""
import pytest
import math
from unittest.mock import patch, MagicMock
from pathlib import Path

from terminal.company_store import CompanyStore
from terminal.options.iv_tracker import (
    compute_hv,
    get_iv_rank,
    get_iv_percentile,
    get_iv_history_summary,
    update_daily_iv,
)


@pytest.fixture
def store(tmp_path):
    """Create a fresh CompanyStore with temp DB."""
    db_path = tmp_path / "test.db"
    s = CompanyStore(db_path=db_path)
    s.upsert_company("AAPL", company_name="Apple")
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


class TestComputeHV:
    """Test historical volatility computation."""

    def test_compute_hv_basic(self, tmp_path):
        """Should compute HV from price CSV."""
        # Create a simple price CSV with known data
        csv_path = tmp_path / "AAPL.csv"
        # 32 days of close prices (need window+1)
        prices = [100 + i * 0.5 for i in range(32)]  # Slowly rising
        with open(csv_path, "w") as f:
            f.write("date,close\n")
            for i, p in enumerate(prices):
                f.write("2026-01-{:02d},{:.2f}\n".format(i + 1, p))

        with patch("terminal.options.iv_tracker.PRICE_DIR", tmp_path):
            hv = compute_hv("AAPL", window=30)
            assert hv is not None
            assert 0 < hv < 1  # Should be a reasonable annualized vol

    def test_compute_hv_insufficient_data(self, tmp_path):
        """Should return None with insufficient data."""
        csv_path = tmp_path / "AAPL.csv"
        with open(csv_path, "w") as f:
            f.write("date,close\n2026-01-01,100\n")

        with patch("terminal.options.iv_tracker.PRICE_DIR", tmp_path):
            hv = compute_hv("AAPL", window=30)
            assert hv is None

    def test_compute_hv_no_file(self, tmp_path):
        """Should return None when no price file."""
        with patch("terminal.options.iv_tracker.PRICE_DIR", tmp_path):
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
        # Current = 0.28, values below: 0.20, 0.25 = 2 out of 5 = 40%
        assert pctl == 40.0

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
        store.upsert_company("MSFT")
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
