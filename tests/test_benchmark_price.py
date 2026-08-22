"""Tests for benchmark symbol integration in price updates."""
import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))


class TestBenchmarkConfig:
    def test_benchmark_symbols_exist(self):
        from config.settings import BENCHMARK_SYMBOLS
        assert isinstance(BENCHMARK_SYMBOLS, list)
        assert "SPY" in BENCHMARK_SYMBOLS
        assert "QQQ" in BENCHMARK_SYMBOLS

    def test_benchmark_symbols_count(self):
        from config.settings import BENCHMARK_SYMBOLS
        assert len(BENCHMARK_SYMBOLS) >= 2


class TestUpdateAllPricesIncludesBenchmarks:
    @patch("src.data.price_fetcher.fetch_and_update_price")
    @patch("src.data.price_fetcher.get_fmp_price_targets", return_value=["AAPL", "NVDA"])
    def test_benchmarks_added_to_symbols(self, mock_targets, mock_fetch):
        """update_all_prices() should include SPY and QQQ even if not in pool."""
        import pandas as pd
        mock_fetch.return_value = pd.DataFrame({"close": [100]})

        from src.data.price_fetcher import update_all_prices
        result = update_all_prices()

        # Collect all symbols that were fetched
        fetched_symbols = [call.args[0] for call in mock_fetch.call_args_list]
        assert "SPY" in fetched_symbols
        assert "QQQ" in fetched_symbols
        assert "AAPL" in fetched_symbols
        assert "NVDA" in fetched_symbols

    @patch("src.data.price_fetcher.fetch_and_update_price")
    @patch("src.data.price_fetcher.get_fmp_price_targets", return_value=["AAPL", "SPY"])
    def test_no_duplicate_when_benchmark_already_in_pool(self, mock_targets, mock_fetch):
        """If SPY is already in pool, it should not be fetched twice."""
        import pandas as pd
        mock_fetch.return_value = pd.DataFrame({"close": [100]})

        from src.data.price_fetcher import update_all_prices
        result = update_all_prices()

        fetched_symbols = [call.args[0] for call in mock_fetch.call_args_list]
        # SPY should appear exactly once (set deduplication)
        assert fetched_symbols.count("SPY") == 1
