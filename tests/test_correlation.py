"""Tests for src/analysis/correlation.py"""
import json
import sys
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pandas as pd
import pytest

# Ensure project root is on path
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))


def _make_price_df(dates, closes):
    """Helper: create a price DataFrame in descending order (matching get_price_df format)."""
    df = pd.DataFrame({
        "date": pd.to_datetime(dates),
        "open": closes,
        "high": closes,
        "low": closes,
        "close": closes,
        "volume": [1000] * len(closes),
        "change": [0.0] * len(closes),
        "changePercent": [0.0] * len(closes),
    })
    return df.sort_values("date", ascending=False).reset_index(drop=True)


# Store price DataFrames for mock routing by symbol
_mock_prices = {}


def _mock_get_price_df(symbol, days=None, max_age_days=0):
    """Mock get_price_df that returns from _mock_prices dict."""
    df = _mock_prices.get(symbol)
    if df is None:
        return None
    if days:
        return df.head(days)
    return df


class TestLoadPriceReturns:
    def test_returns_none_when_no_data(self):
        with patch("src.data.price_fetcher.load_price_cache", return_value=None), \
             patch("src.data.price_fetcher.fetch_and_update_price", return_value=None):
            from src.analysis.correlation import load_price_returns
            result = load_price_returns("MISSING")
            assert result is None

    def test_loads_returns(self):
        dates = pd.date_range("2025-01-01", periods=30, freq="B").strftime("%Y-%m-%d").tolist()
        closes = list(range(100, 130))
        _mock_prices["AAPL"] = _make_price_df(dates, closes)

        with patch("src.data.price_fetcher.load_price_cache", side_effect=lambda s: _mock_prices.get(s)):
            from src.analysis.correlation import load_price_returns
            ret = load_price_returns("AAPL", window=20)
            assert ret is not None
            assert isinstance(ret, pd.Series)
            assert ret.name == "AAPL"
            assert len(ret) <= 20
            # Returns should be positive (prices are monotonically increasing)
            assert (ret > 0).all()


class TestComputeCorrelationMatrix:
    def test_returns_empty_with_insufficient_data(self):
        dates = pd.date_range("2025-01-01", periods=30, freq="B").strftime("%Y-%m-%d").tolist()
        _mock_prices.clear()
        _mock_prices["ONLY"] = _make_price_df(dates, list(range(100, 130)))

        with patch("src.data.price_fetcher.load_price_cache", side_effect=lambda s: _mock_prices.get(s)):
            from src.analysis.correlation import compute_correlation_matrix
            result = compute_correlation_matrix(["ONLY"], window=25)
            assert result == {}

    def test_computes_matrix_for_two_symbols(self):
        np.random.seed(42)
        dates = pd.date_range("2025-01-01", periods=130, freq="B").strftime("%Y-%m-%d").tolist()
        base = np.cumsum(np.random.randn(130)) + 100
        _mock_prices.clear()
        _mock_prices["AAA"] = _make_price_df(dates, base.tolist())
        _mock_prices["BBB"] = _make_price_df(dates, (base + np.random.randn(130) * 0.5).tolist())

        with patch("src.data.price_fetcher.load_price_cache", side_effect=lambda s: _mock_prices.get(s)):
            from src.analysis.correlation import compute_correlation_matrix
            result = compute_correlation_matrix(["AAA", "BBB"], window=120)
            assert "AAA" in result
            assert "BBB" in result
            assert result["AAA"]["AAA"] == 1.0
            assert result["BBB"]["BBB"] == 1.0
            assert result["AAA"]["BBB"] > 0.5

    def test_skips_symbols_with_too_few_points(self):
        np.random.seed(42)
        dates_long = pd.date_range("2025-01-01", periods=130, freq="B").strftime("%Y-%m-%d").tolist()
        dates_short = pd.date_range("2025-01-01", periods=10, freq="B").strftime("%Y-%m-%d").tolist()
        _mock_prices.clear()
        _mock_prices["LONG1"] = _make_price_df(dates_long, np.cumsum(np.random.randn(130) + 100).tolist())
        _mock_prices["LONG2"] = _make_price_df(dates_long, np.cumsum(np.random.randn(130) + 100).tolist())
        _mock_prices["SHORT"] = _make_price_df(dates_short, list(range(100, 110)))

        with patch("src.data.price_fetcher.load_price_cache", side_effect=lambda s: _mock_prices.get(s)):
            from src.analysis.correlation import compute_correlation_matrix
            result = compute_correlation_matrix(["LONG1", "LONG2", "SHORT"], window=120)
            assert "SHORT" not in result
            assert "LONG1" in result
            assert "LONG2" in result


class TestCacheRoundTrip:
    def test_save_and_load(self, tmp_path):
        cache_dir = tmp_path / "correlation"
        cache_file = cache_dir / "matrix.json"

        with patch("src.analysis.correlation.CORRELATION_CACHE_DIR", cache_dir), \
             patch("src.analysis.correlation.CORRELATION_CACHE_FILE", cache_file):
            from src.analysis.correlation import save_correlation_cache, load_correlation_cache

            matrix = {"AAPL": {"AAPL": 1.0, "NVDA": 0.85}, "NVDA": {"AAPL": 0.85, "NVDA": 1.0}}
            save_correlation_cache(matrix)

            assert cache_file.exists()

            loaded = load_correlation_cache()
            assert loaded == matrix

    def test_load_returns_none_when_no_cache(self, tmp_path):
        cache_file = tmp_path / "correlation" / "matrix.json"

        with patch("src.analysis.correlation.CORRELATION_CACHE_FILE", cache_file):
            from src.analysis.correlation import load_correlation_cache
            assert load_correlation_cache() is None


class TestGetCorrelationMatrix:
    def test_uses_cache_when_available(self, tmp_path):
        cached_matrix = {"AAPL": {"AAPL": 1.0, "NVDA": 0.9}, "NVDA": {"AAPL": 0.9, "NVDA": 1.0}}

        with patch("src.analysis.correlation.load_correlation_cache", return_value=cached_matrix), \
             patch("src.analysis.correlation.compute_correlation_matrix") as mock_compute:
            from src.analysis.correlation import get_correlation_matrix
            result = get_correlation_matrix(["AAPL", "NVDA"], use_cache=True)
            assert result == cached_matrix
            mock_compute.assert_not_called()

    def test_recomputes_when_cache_missing_symbols(self, tmp_path):
        cached_matrix = {"AAPL": {"AAPL": 1.0}}
        new_matrix = {"AAPL": {"AAPL": 1.0, "NVDA": 0.9}, "NVDA": {"AAPL": 0.9, "NVDA": 1.0}}

        with patch("src.analysis.correlation.load_correlation_cache", return_value=cached_matrix), \
             patch("src.analysis.correlation.compute_correlation_matrix", return_value=new_matrix), \
             patch("src.analysis.correlation.save_correlation_cache"):
            from src.analysis.correlation import get_correlation_matrix
            result = get_correlation_matrix(["AAPL", "NVDA"], use_cache=True)
            assert result == new_matrix

    def test_skips_cache_when_disabled(self, tmp_path):
        new_matrix = {"AAPL": {"AAPL": 1.0, "NVDA": 0.9}, "NVDA": {"AAPL": 0.9, "NVDA": 1.0}}

        with patch("src.analysis.correlation.load_correlation_cache") as mock_load, \
             patch("src.analysis.correlation.compute_correlation_matrix", return_value=new_matrix), \
             patch("src.analysis.correlation.save_correlation_cache"):
            from src.analysis.correlation import get_correlation_matrix
            result = get_correlation_matrix(["AAPL", "NVDA"], use_cache=False)
            mock_load.assert_not_called()
            assert result == new_matrix
