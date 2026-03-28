"""Tests for scripts/rs_universe_scan.py"""
import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

import pandas as pd
import pytest

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from scripts.rs_universe_scan import (
    format_rs_report,
    format_console_report,
    fetch_universe,
    load_price_data,
)


class TestFormatRsReport:
    """Telegram 报告格式化"""

    def test_basic_formatting(self):
        rs_b = pd.DataFrame([
            {"symbol": "NVDA", "rs_rank": 99, "z_3m": 2.31, "z_1m": 1.87, "z_1w": 1.54},
            {"symbol": "PLTR", "rs_rank": 97, "z_3m": 2.10, "z_1m": 1.92, "z_1w": 0.84},
        ])
        rs_c = pd.DataFrame([
            {"symbol": "PLTR", "rs_rank": 99, "clenow_63d": 0.94, "clenow_21d": 0.87, "clenow_10d": 0.72},
        ])
        result = format_rs_report(rs_b, rs_c, 500, 840)
        assert "RS Universe Scan" in result
        assert "500只" in result
        assert "14m" in result
        assert "NVDA" in result
        assert "PLTR" in result
        assert "Method B" in result
        assert "Method C" in result

    def test_sort_order(self):
        """Top stocks should be sorted by rs_rank descending"""
        rs_b = pd.DataFrame([
            {"symbol": "AAPL", "rs_rank": 50, "z_3m": 0.5, "z_1m": 0.3, "z_1w": 0.1},
            {"symbol": "NVDA", "rs_rank": 99, "z_3m": 2.3, "z_1m": 1.8, "z_1w": 1.5},
            {"symbol": "INTC", "rs_rank": 5,  "z_3m": -1.8, "z_1m": -1.5, "z_1w": -0.9},
        ])
        rs_c = pd.DataFrame()
        result = format_rs_report(rs_b, rs_c, 3, 60)
        lines = result.split("\n")
        data_lines = [l for l in lines if l.strip().startswith(("1 ", " 1 "))]
        assert "NVDA" in data_lines[0]

    def test_empty_dataframes(self):
        rs_b = pd.DataFrame()
        rs_c = pd.DataFrame()
        result = format_rs_report(rs_b, rs_c, 0, 0)
        assert "RS Universe Scan" in result

    def test_under_4096_chars(self):
        """Report should fit in one Telegram message"""
        rows = [{"symbol": "SYM{}".format(i), "rs_rank": 99 - i,
                 "z_3m": 2.0 - i * 0.1, "z_1m": 1.5 - i * 0.05, "z_1w": 1.0 - i * 0.03}
                for i in range(25)]
        rs_b = pd.DataFrame(rows)
        rs_c = pd.DataFrame()
        result = format_rs_report(rs_b, rs_c, 500, 840)
        assert len(result) < 4096


class TestFormatConsoleReport:
    """控制台输出格式化"""

    def test_basic_formatting(self):
        rs_b = pd.DataFrame([
            {"symbol": "NVDA", "rs_rank": 99, "z_3m": 2.31, "z_1m": 1.87, "z_1w": 1.54},
        ])
        rs_c = pd.DataFrame([
            {"symbol": "NVDA", "rs_rank": 99, "clenow_63d": 0.94, "clenow_21d": 0.87, "clenow_10d": 0.72},
        ])
        result = format_console_report(rs_b, rs_c)
        assert "Method B" in result
        assert "Method C" in result
        assert "NVDA" in result

    def test_sort_order(self):
        """Console report should also sort by rs_rank"""
        rs_b = pd.DataFrame([
            {"symbol": "INTC", "rs_rank": 5,  "z_3m": -1.0, "z_1m": -0.8, "z_1w": -0.5},
            {"symbol": "NVDA", "rs_rank": 99, "z_3m": 2.3,  "z_1m": 1.8,  "z_1w": 1.5},
        ])
        rs_c = pd.DataFrame()
        result = format_console_report(rs_b, rs_c)
        lines = result.split("\n")
        # Find the first numbered data line
        data_lines = [l for l in lines if l.strip().startswith("1 ")]
        assert len(data_lines) >= 1
        assert "NVDA" in data_lines[0]


class TestFetchUniverse:
    """Universe 获取"""

    def test_fetch_and_deduplicate(self):
        mock_client = MagicMock()
        mock_client.get_large_cap_stocks.return_value = [
            {"symbol": "AAPL"}, {"symbol": "MSFT"}, {"symbol": "AAPL"},
        ]
        result = fetch_universe(mock_client, 10)
        assert result == ["AAPL", "MSFT"]
        mock_client.get_large_cap_stocks.assert_called_once_with(10_000_000_000)

    def test_empty_result(self):
        mock_client = MagicMock()
        mock_client.get_large_cap_stocks.return_value = []
        result = fetch_universe(mock_client, 10)
        assert result == []


class TestLoadPriceData:
    """价格数据加载"""

    @patch("src.data.market_store.get_store")
    @patch("scripts.rs_universe_scan.get_price_df")
    def test_cached_symbols_use_cache(self, mock_get_price_df, mock_get_store):
        """缓存中的股票应该用本地缓存（池内+扩展池）"""
        mock_store = MagicMock()
        mock_store.get_symbols.return_value = ["AAPL", "MSFT"]
        mock_get_store.return_value = mock_store
        mock_df = pd.DataFrame({"date": ["2026-01-01"], "close": [200.0]})
        mock_get_price_df.return_value = mock_df

        mock_client = MagicMock()
        result = load_price_data(["AAPL"], mock_client)

        assert "AAPL" in result
        mock_get_price_df.assert_called_once_with("AAPL", max_age_days=0)
        # Should NOT call API for cached symbols
        mock_client.get_historical_price_range.assert_not_called()

    @patch("src.data.market_store.get_store")
    @patch("scripts.rs_universe_scan.get_price_df")
    def test_uncached_symbols_use_api(self, mock_get_price_df, mock_get_store):
        """缓存中没有的股票应该调 API"""
        mock_store = MagicMock()
        mock_store.get_symbols.return_value = ["AAPL"]  # PLTR not cached
        mock_get_store.return_value = mock_store

        mock_client = MagicMock()
        mock_client.get_historical_price_range.return_value = [
            {"date": "2026-01-01", "close": 50.0},
            {"date": "2026-01-02", "close": 51.0},
        ]

        result = load_price_data(["PLTR"], mock_client)

        assert "PLTR" in result
        mock_client.get_historical_price_range.assert_called_once()
        mock_get_price_df.assert_not_called()

    @patch("src.data.market_store.get_store")
    @patch("scripts.rs_universe_scan.get_price_df")
    def test_mixed_cached_and_uncached(self, mock_get_price_df, mock_get_store):
        """混合: 缓存+未缓存"""
        mock_store = MagicMock()
        mock_store.get_symbols.return_value = ["AAPL"]
        mock_get_store.return_value = mock_store
        mock_df = pd.DataFrame({"date": ["2026-01-01"], "close": [200.0]})
        mock_get_price_df.return_value = mock_df

        mock_client = MagicMock()
        mock_client.get_historical_price_range.return_value = [
            {"date": "2026-01-01", "close": 50.0},
        ]

        result = load_price_data(["AAPL", "PLTR"], mock_client)

        assert "AAPL" in result
        assert "PLTR" in result
        mock_get_price_df.assert_called_once()
        mock_client.get_historical_price_range.assert_called_once()
