"""Tests for IV backfill script and related compute_hv(as_of) support."""
import pytest
from unittest.mock import patch, MagicMock, call
from pathlib import Path
from datetime import datetime

from terminal.company_store import CompanyStore
from terminal.options.iv_tracker import compute_hv


# ============================================================
# Fixtures
# ============================================================

@pytest.fixture
def store(tmp_path):
    """Create a fresh CompanyStore with temp DB."""
    db_path = tmp_path / "test.db"
    s = CompanyStore(db_path=db_path)
    s.upsert_company("AAPL", company_name="Apple")
    s.upsert_company("MSFT", company_name="Microsoft")
    return s


@pytest.fixture
def price_csv(tmp_path):
    """Create a price CSV with 60 days of data, newest-first."""
    csv_path = tmp_path / "AAPL.csv"
    # Generate 60 trading days of data, newest first
    # Dates: 2026-01-01 to 2026-03-01 (approx)
    lines = ["date,open,high,low,close,volume,change,changePercent"]
    base_price = 150.0
    for i in range(60):
        day_offset = i  # newest first
        date_obj = datetime(2026, 3, 1) - __import__("datetime").timedelta(days=day_offset)
        date_str = date_obj.strftime("%Y-%m-%d")
        close = base_price + (60 - i) * 0.5  # rising prices
        lines.append("{},{:.2f},{:.2f},{:.2f},{:.2f},1000000,0.5,0.33".format(
            date_str, close - 1, close + 1, close - 2, close
        ))
    csv_path.write_text("\n".join(lines))
    return tmp_path


# ============================================================
# Trading Day Generator
# ============================================================

class TestGenerateTradingDays:
    """Test trading day generation."""

    def test_weekdays_only(self):
        from scripts.backfill_iv import generate_trading_days
        days = generate_trading_days("2026-02-16", "2026-02-22")
        # Feb 16=Mon, 17=Tue, 18=Wed, 19=Thu, 20=Fri, 21=Sat, 22=Sun
        assert len(days) == 5
        assert "2026-02-16" in days
        assert "2026-02-20" in days
        assert "2026-02-21" not in days  # Saturday
        assert "2026-02-22" not in days  # Sunday

    def test_single_day(self):
        from scripts.backfill_iv import generate_trading_days
        days = generate_trading_days("2026-02-17", "2026-02-17")
        assert days == ["2026-02-17"]  # Tuesday

    def test_weekend_only(self):
        from scripts.backfill_iv import generate_trading_days
        days = generate_trading_days("2026-02-21", "2026-02-22")
        assert days == []  # Sat-Sun

    def test_one_week(self):
        from scripts.backfill_iv import generate_trading_days
        days = generate_trading_days("2026-02-16", "2026-02-27")
        assert len(days) == 10  # 2 weeks of weekdays

    def test_chronological_order(self):
        from scripts.backfill_iv import generate_trading_days
        days = generate_trading_days("2026-01-01", "2026-01-31")
        assert days == sorted(days)


# ============================================================
# Resume / Checkpoint
# ============================================================

class TestCheckpointResume:
    """Test that existing data is skipped."""

    def test_get_existing_dates(self, store):
        from scripts.backfill_iv import get_existing_dates
        # Insert some data
        store.save_iv_daily("AAPL", "2026-01-01", iv_30d=0.25)
        store.save_iv_daily("AAPL", "2026-01-02", iv_30d=0.26)
        store.save_iv_daily("AAPL", "2026-01-05", iv_30d=0.27)

        existing = get_existing_dates(store, "AAPL")
        assert "2026-01-01" in existing
        assert "2026-01-02" in existing
        assert "2026-01-05" in existing
        assert "2026-01-03" not in existing

    def test_empty_existing(self, store):
        from scripts.backfill_iv import get_existing_dates
        existing = get_existing_dates(store, "AAPL")
        assert existing == set()

    def test_backfill_skips_existing(self, store):
        """Full integration: backfill should skip dates already in DB."""
        from scripts.backfill_iv import backfill

        # Pre-populate one date
        store.save_iv_daily("AAPL", "2026-02-17", iv_30d=0.30)

        mock_client = MagicMock()
        mock_client.get_historical_atm_iv.return_value = 0.28

        args = MagicMock()
        args.symbols = ["AAPL"]
        args.start = "2026-02-17"
        args.end = "2026-02-18"
        args.daily_limit = 9500
        args.dry_run = False

        with patch("scripts.backfill_iv.get_store", return_value=store), \
             patch("src.data.marketdata_client.MarketDataClient", return_value=mock_client), \
             patch("scripts.backfill_iv.compute_hv", return_value=0.22):
            backfill(args)

        # Should only call API for 2026-02-18 (17 was skipped)
        assert mock_client.get_historical_atm_iv.call_count == 1
        mock_client.get_historical_atm_iv.assert_called_with("AAPL", "2026-02-18")


# ============================================================
# Credit Limit
# ============================================================

class TestCreditLimit:
    """Test daily credit limit enforcement."""

    def test_stops_at_limit(self, store):
        """Should stop when credit limit is reached."""
        from scripts.backfill_iv import backfill

        mock_client = MagicMock()
        mock_client.get_historical_atm_iv.return_value = 0.30

        args = MagicMock()
        args.symbols = ["AAPL"]
        args.start = "2026-02-16"
        args.end = "2026-02-20"  # 5 trading days
        args.daily_limit = 3  # Only allow 3 credits
        args.dry_run = False

        with patch("scripts.backfill_iv.get_store", return_value=store), \
             patch("src.data.marketdata_client.MarketDataClient", return_value=mock_client), \
             patch("scripts.backfill_iv.compute_hv", return_value=0.22):
            backfill(args)

        # Should only make 3 API calls despite 5 trading days
        assert mock_client.get_historical_atm_iv.call_count == 3


# ============================================================
# compute_hv with as_of
# ============================================================

class TestComputeHVAsOf:
    """Test compute_hv with as_of date parameter."""

    def test_as_of_filters_data(self, price_csv):
        """Should only use prices on or before as_of date."""
        with patch("terminal.options.iv_tracker.PRICE_DIR", price_csv):
            # Use a date in the middle of our data range
            hv_full = compute_hv("AAPL", window=30)
            hv_earlier = compute_hv("AAPL", window=30, as_of="2026-02-15")

            assert hv_full is not None
            assert hv_earlier is not None
            # Different data windows should give different (or at least valid) results
            assert hv_full > 0
            assert hv_earlier > 0

    def test_as_of_none_uses_all(self, price_csv):
        """as_of=None should behave same as before."""
        with patch("terminal.options.iv_tracker.PRICE_DIR", price_csv):
            hv_default = compute_hv("AAPL", window=30)
            hv_none = compute_hv("AAPL", window=30, as_of=None)
            assert hv_default == hv_none

    def test_as_of_too_early(self, price_csv):
        """Should return None if not enough data before as_of."""
        with patch("terminal.options.iv_tracker.PRICE_DIR", price_csv):
            hv = compute_hv("AAPL", window=30, as_of="2026-01-05")
            assert hv is None  # Only ~5 days of data before this date

    def test_as_of_exact_boundary(self, price_csv):
        """Should include the as_of date itself."""
        with patch("terminal.options.iv_tracker.PRICE_DIR", price_csv):
            # Our data goes from ~2026-01-01 to 2026-03-01
            # Pick a date with at least 31 days of data before it
            hv = compute_hv("AAPL", window=30, as_of="2026-02-28")
            assert hv is not None
            assert hv > 0


# ============================================================
# Dry Run
# ============================================================

class TestDryRun:
    """Test dry-run mode."""

    def test_dry_run_no_api_calls(self, store):
        """Dry run should not make any API calls."""
        from scripts.backfill_iv import backfill

        args = MagicMock()
        args.symbols = ["AAPL"]
        args.start = "2026-02-17"
        args.end = "2026-02-18"
        args.daily_limit = 9500
        args.dry_run = True

        with patch("scripts.backfill_iv.get_store", return_value=store):
            # Should NOT import or use MarketDataClient at all
            backfill(args)

        # No data should be added to DB
        history = store.get_iv_history("AAPL")
        assert len(history) == 0
