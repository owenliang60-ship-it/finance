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
    """Test that load_price_cache() reads from market.db."""

    def test_reads_mdb_when_data_exists(self):
        """When market.db has data, returns it."""
        from src.data.price_fetcher import load_price_cache

        fake_df = _make_price_df(5)
        fake_store = MagicMock()
        fake_store.get_daily_prices_df.return_value = fake_df

        with patch("src.data.market_store.get_store", return_value=fake_store):
            result = load_price_cache("AAPL")

        assert result is not None
        assert len(result) == 5

    def test_returns_none_when_db_empty(self):
        """When market.db returns None, returns None."""
        from src.data.price_fetcher import load_price_cache

        fake_store = MagicMock()
        fake_store.get_daily_prices_df.return_value = None

        with patch("src.data.market_store.get_store", return_value=fake_store):
            result = load_price_cache("AAPL")

        assert result is None

    def test_returns_none_when_db_error(self):
        """When market.db raises exception, returns None."""
        from src.data.price_fetcher import load_price_cache

        with patch("src.data.market_store.get_store", side_effect=Exception("DB error")):
            result = load_price_cache("AAPL")

        assert result is None

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
# Tests for save_price_cache() — market.db only (P4: CSV retired)
# ---------------------------------------------------------------------------

class TestSavePriceCacheWritePath:
    """Test that save_price_cache writes to market.db."""

    def test_db_write_failure_propagates(self):
        """When market.db write fails, exception should propagate."""
        from src.data.price_fetcher import save_price_cache

        df = _make_price_df(3)
        fake_store = MagicMock()
        fake_store.upsert_daily_prices_df.side_effect = Exception("DB write error")

        with patch("src.data.market_store.get_store", return_value=fake_store):
            with pytest.raises(Exception, match="DB write error"):
                save_price_cache("AAPL", df)

    def test_db_write_succeeds(self):
        """Normal case: market.db write succeeds."""
        from src.data.price_fetcher import save_price_cache

        df = _make_price_df(3)
        fake_store = MagicMock()
        fake_store.upsert_daily_prices_df.return_value = 3

        with patch("src.data.market_store.get_store", return_value=fake_store):
            save_price_cache("AAPL", df)

        fake_store.upsert_daily_prices_df.assert_called_once()
