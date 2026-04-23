"""Tests for src/data/extended_price_fetcher.py."""
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import numpy as np
import pandas as pd
import pytest

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.data.extended_price_fetcher import (
    _normalize_ohlcv,
    update_extended_prices,
)


def _make_yf_multiindex_data(symbols, n_days=5):
    """Create a mock yf.download() MultiIndex DataFrame with OHLCV."""
    dates = pd.date_range("2026-03-20", periods=n_days, freq="D")
    tuples = []
    for field in ["Open", "High", "Low", "Close", "Volume"]:
        for sym in symbols:
            tuples.append((field, sym))
    columns = pd.MultiIndex.from_tuples(tuples)

    np.random.seed(42)
    data = np.random.rand(n_days, len(tuples)) * 100 + 50
    # Make Volume larger
    for i, (field, _) in enumerate(tuples):
        if field == "Volume":
            data[:, i] *= 1_000_000

    return pd.DataFrame(data, index=dates, columns=columns)


class TestNormalizeOhlcv:
    def test_extracts_all_five_fields(self):
        symbols = ["AAPL", "NVDA"]
        data = _make_yf_multiindex_data(symbols, n_days=3)
        result = _normalize_ohlcv(data, symbols)

        assert set(result.keys()) == {"AAPL", "NVDA"}
        for sym in symbols:
            df = result[sym]
            assert "date" in df.columns
            assert "open" in df.columns
            assert "high" in df.columns
            assert "low" in df.columns
            assert "close" in df.columns
            assert "volume" in df.columns
            assert len(df) == 3

    def test_handles_empty_dataframe(self):
        result = _normalize_ohlcv(pd.DataFrame(), ["AAPL"])
        assert result == {}

    def test_skips_symbols_without_data(self):
        symbols = ["AAPL", "FAKE"]
        # Only AAPL has data
        data = _make_yf_multiindex_data(["AAPL"], n_days=3)
        result = _normalize_ohlcv(data, symbols)

        assert "AAPL" in result
        assert "FAKE" not in result

    def test_date_format_is_yyyy_mm_dd(self):
        data = _make_yf_multiindex_data(["XOM"], n_days=2)
        result = _normalize_ohlcv(data, ["XOM"])

        dates = result["XOM"]["date"].tolist()
        assert dates[0] == "2026-03-20"
        assert dates[1] == "2026-03-21"


class TestUpdateExtendedPrices:
    def test_returns_stats_when_no_symbols(self):
        result = update_extended_prices(symbols=[])

        assert result["total"] == 0
        assert result["success"] == 0
        assert result["failed"] == []

    def test_full_backfill_mode(self):
        mock_store = MagicMock()
        mock_store.upsert_daily_prices_df.return_value = 252

        mock_frames = {
            "XOM": pd.DataFrame({
                "date": ["2026-03-20"],
                "open": [100.0],
                "high": [105.0],
                "low": [99.0],
                "close": [103.0],
                "volume": [1_000_000],
            }),
        }

        with patch("src.data.extended_universe_manager.get_extended_only_symbols",
                    return_value=["XOM"]), \
             patch("src.data.market_store.get_store",
                   return_value=mock_store), \
             patch("src.data.extended_price_fetcher._yf_download_ohlcv",
                   return_value=mock_frames):

            result = update_extended_prices(
                full_backfill=True, symbols=["XOM"],
            )

        assert result["success"] == 1
        assert result["rows_inserted"] == 252
        mock_store.upsert_daily_prices_df.assert_called_once()

    def test_incremental_mode_routes_correctly(self):
        mock_store = MagicMock()
        # XOM has data, CVX doesn't
        mock_store.get_daily_prices.side_effect = lambda sym, limit=0: (
            [{"date": "2026-03-27"}] if sym == "XOM" else []
        )
        mock_store.upsert_daily_prices_df.return_value = 5

        mock_frames = {
            "XOM": pd.DataFrame({"date": ["2026-03-27"], "close": [100]}),
            "CVX": pd.DataFrame({"date": ["2026-03-27"], "close": [80]}),
        }

        with patch("src.data.extended_universe_manager.get_extended_only_symbols",
                    return_value=["CVX", "XOM"]), \
             patch("src.data.market_store.get_store",
                   return_value=mock_store), \
             patch("src.data.extended_price_fetcher._yf_download_ohlcv",
                   return_value=mock_frames) as mock_dl:

            result = update_extended_prices(symbols=["CVX", "XOM"])

        assert result["success"] == 2
        # Should have been called twice: once for backfill (CVX), once for incremental (XOM)
        assert mock_dl.call_count == 2

    def test_handles_upsert_failure(self):
        mock_store = MagicMock()
        mock_store.upsert_daily_prices_df.side_effect = Exception("DB error")

        mock_frames = {
            "XOM": pd.DataFrame({"date": ["2026-03-27"], "close": [100]}),
        }

        with patch("src.data.extended_universe_manager.get_extended_only_symbols",
                    return_value=["XOM"]), \
             patch("src.data.market_store.get_store",
                   return_value=mock_store), \
             patch("src.data.extended_price_fetcher._yf_download_ohlcv",
                   return_value=mock_frames):

            result = update_extended_prices(
                full_backfill=True, symbols=["XOM"],
            )

        assert result["success"] == 0
        assert "XOM" in result["failed"]

    def test_incremental_start_date_for_existing_symbols(self):
        mock_store = MagicMock()
        mock_store.get_daily_prices.return_value = [{"date": "2026-03-27"}]
        mock_store.upsert_daily_prices_df.return_value = 5
        mock_frames = {"XOM": pd.DataFrame({"date": ["2026-03-27"], "close": [100]})}

        with patch("src.data.extended_universe_manager.get_extended_only_symbols", return_value=["XOM"]), \
             patch("src.data.market_store.get_store", return_value=mock_store), \
             patch("src.data.extended_price_fetcher._yf_download_ohlcv", return_value=mock_frames) as mock_dl:
            result = update_extended_prices(symbols=["XOM"], start_date="2026-03-20")

        assert result["success"] == 1
        mock_dl.assert_called_once_with(
            ["XOM"],
            period=None,
            start="2026-03-20",
        )
