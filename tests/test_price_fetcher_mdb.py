"""Tests for P2.1 — market.db read path for price data."""
import sys
from datetime import datetime, timedelta
from pathlib import Path
from unittest import mock
from unittest.mock import patch, MagicMock

import pandas as pd
import pytest

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.data.price_fetcher import PRICE_COLUMNS


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_price_df(n: int = 5, start_close: float = 100.0) -> pd.DataFrame:
    """Create a multi-row price DataFrame sorted descending (newest first)."""
    dates = [datetime.now() - timedelta(days=i) for i in range(n)]
    return pd.DataFrame({
        "date": pd.to_datetime(dates),
        "open": [start_close - 1 + i for i in range(n)],
        "high": [start_close + 1 + i for i in range(n)],
        "low": [start_close - 2 + i for i in range(n)],
        "close": [start_close + i for i in range(n)],
        "volume": [1_000_000 + i * 100 for i in range(n)],
        "change": [0.5 + i * 0.1 for i in range(n)],
        "changePercent": [0.5 + i * 0.1 for i in range(n)],
    }).sort_values("date", ascending=False).reset_index(drop=True)


def _make_db_rows(n: int = 5, start_close: float = 100.0):
    """Create rows as returned by MarketStore.get_daily_prices() (list of dicts)."""
    rows = []
    for i in range(n):
        date = (datetime.now() - timedelta(days=i)).strftime("%Y-%m-%d")
        rows.append({
            "symbol": "AAPL",
            "date": date,
            "open": start_close - 1 + i,
            "high": start_close + 1 + i,
            "low": start_close - 2 + i,
            "close": start_close + i,
            "volume": 1_000_000 + i * 100,
            "change": 0.5 + i * 0.1,
            "change_pct": 0.5 + i * 0.1,
        })
    # DB rows are returned descending already
    return rows


# ---------------------------------------------------------------------------
# Tests for MarketStore.get_daily_prices_df()
# ---------------------------------------------------------------------------

class TestGetDailyPricesDf:
    """Test the new get_daily_prices_df() method on MarketStore."""

    def test_columns_match_price_columns(self, tmp_path):
        """Returned columns must exactly match PRICE_COLUMNS."""
        from src.data.market_store import MarketStore

        store = MarketStore(db_path=tmp_path / "test.db")
        rows = _make_db_rows(5)
        store.upsert_daily_prices("AAPL", [
            {
                "date": r["date"], "open": r["open"], "high": r["high"],
                "low": r["low"], "close": r["close"], "volume": r["volume"],
                "change": r["change"], "changePercent": r["change_pct"],
            }
            for r in rows
        ])

        df = store.get_daily_prices_df("AAPL")
        assert df is not None
        assert list(df.columns) == PRICE_COLUMNS

    def test_sort_order_descending(self, tmp_path):
        """Data must be sorted by date descending (newest first)."""
        from src.data.market_store import MarketStore

        store = MarketStore(db_path=tmp_path / "test.db")
        rows = _make_db_rows(10)
        store.upsert_daily_prices("AAPL", [
            {
                "date": r["date"], "open": r["open"], "high": r["high"],
                "low": r["low"], "close": r["close"], "volume": r["volume"],
                "change": r["change"], "changePercent": r["change_pct"],
            }
            for r in rows
        ])

        df = store.get_daily_prices_df("AAPL")
        dates = df["date"].tolist()
        assert dates == sorted(dates, reverse=True)

    def test_date_dtype_datetime64(self, tmp_path):
        """date column must be datetime64[ns]."""
        from src.data.market_store import MarketStore

        store = MarketStore(db_path=tmp_path / "test.db")
        rows = _make_db_rows(3)
        store.upsert_daily_prices("AAPL", [
            {
                "date": r["date"], "open": r["open"], "high": r["high"],
                "low": r["low"], "close": r["close"], "volume": r["volume"],
                "change": r["change"], "changePercent": r["change_pct"],
            }
            for r in rows
        ])

        df = store.get_daily_prices_df("AAPL")
        assert pd.api.types.is_datetime64_any_dtype(df["date"])

    def test_empty_symbol_returns_none(self, tmp_path):
        """No data for symbol → returns None."""
        from src.data.market_store import MarketStore

        store = MarketStore(db_path=tmp_path / "test.db")
        result = store.get_daily_prices_df("ZZZZ")
        assert result is None


# ---------------------------------------------------------------------------
# Tests for load_price_cache() routing
# ---------------------------------------------------------------------------

class TestLoadPriceCacheRouting:
    """Test that load_price_cache() reads from market.db with CSV fallback."""

    @patch("src.data.price_fetcher._load_price_cache_csv")
    def test_reads_mdb_when_data_exists(self, mock_csv):
        """When market.db has data, should NOT fall back to CSV."""
        from src.data.price_fetcher import load_price_cache

        fake_df = _make_price_df(5)
        fake_store = MagicMock()
        fake_store.get_daily_prices_df.return_value = fake_df

        with patch("src.data.market_store.get_store", return_value=fake_store):
            result = load_price_cache("AAPL")

        assert result is not None
        assert len(result) == 5
        mock_csv.assert_not_called()

    @patch("src.data.price_fetcher._load_price_cache_csv")
    def test_fallback_csv_when_db_empty(self, mock_csv):
        """When market.db returns None, should fall back to CSV."""
        from src.data.price_fetcher import load_price_cache

        csv_df = _make_price_df(3)
        mock_csv.return_value = csv_df

        fake_store = MagicMock()
        fake_store.get_daily_prices_df.return_value = None

        with patch("src.data.market_store.get_store", return_value=fake_store):
            result = load_price_cache("AAPL")

        mock_csv.assert_called_once_with("AAPL")
        assert result is not None
        assert len(result) == 3

    @patch("src.data.price_fetcher._load_price_cache_csv")
    def test_fallback_csv_when_db_error(self, mock_csv):
        """When market.db raises exception, should fall back to CSV."""
        from src.data.price_fetcher import load_price_cache

        csv_df = _make_price_df(2)
        mock_csv.return_value = csv_df

        with patch("src.data.market_store.get_store", side_effect=Exception("DB error")):
            result = load_price_cache("AAPL")

        mock_csv.assert_called_once_with("AAPL")
        assert result is not None

    def test_get_price_df_via_mdb(self):
        """End-to-end: get_price_df() routes through load_price_cache → market.db."""
        from src.data.price_fetcher import get_price_df

        fake_df = _make_price_df(10)
        fake_store = MagicMock()
        fake_store.get_daily_prices_df.return_value = fake_df

        with patch("src.data.market_store.get_store", return_value=fake_store):
            result = get_price_df("AAPL", days=5, max_age_days=0)

        assert result is not None
        assert len(result) == 5


# ---------------------------------------------------------------------------
# Tests for save_price_cache() write path flip (P2.3)
# ---------------------------------------------------------------------------

class TestSavePriceCacheWritePath:
    """Test that save_price_cache writes to market.db first, CSV second."""

    def test_db_write_failure_propagates(self):
        """When market.db write fails, exception should propagate."""
        from src.data.price_fetcher import save_price_cache

        df = _make_price_df(3)
        fake_store = MagicMock()
        fake_store.upsert_daily_prices_df.side_effect = Exception("DB write error")

        with patch("src.data.market_store.get_store", return_value=fake_store):
            with pytest.raises(Exception, match="DB write error"):
                save_price_cache("AAPL", df)

    def test_csv_write_failure_silent(self, tmp_path):
        """When CSV write fails, should log warning but not raise."""
        from src.data.price_fetcher import save_price_cache

        df = _make_price_df(3)
        fake_store = MagicMock()
        fake_store.upsert_daily_prices_df.return_value = 3

        with patch("src.data.market_store.get_store", return_value=fake_store), \
             patch("src.data.price_fetcher.PRICE_DIR", tmp_path / "nonexistent" / "deep"):
            # CSV write will fail because parent dir doesn't exist
            # (we patched PRICE_DIR.mkdir to not be called first)
            # Actually PRICE_DIR.mkdir is called, let's make to_csv fail
            pass

        # Simpler approach: mock to_csv to raise
        with patch("src.data.market_store.get_store", return_value=fake_store), \
             patch("pandas.DataFrame.to_csv", side_effect=OSError("disk full")):
            # Should NOT raise despite CSV write failure
            save_price_cache("AAPL", df)

        # DB write should have been called
        fake_store.upsert_daily_prices_df.assert_called()

    def test_both_writes_succeed(self, tmp_path):
        """Normal case: both market.db and CSV writes succeed."""
        from src.data.price_fetcher import save_price_cache

        df = _make_price_df(3)
        fake_store = MagicMock()
        fake_store.upsert_daily_prices_df.return_value = 3

        with patch("src.data.market_store.get_store", return_value=fake_store), \
             patch("src.data.price_fetcher.PRICE_DIR", tmp_path):
            save_price_cache("AAPL", df)

        # DB was called
        fake_store.upsert_daily_prices_df.assert_called_once()
        # CSV was written
        assert (tmp_path / "AAPL.csv").exists()
