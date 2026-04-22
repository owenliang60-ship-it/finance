"""Integration tests for the extended_true delisted overlay universe."""

from unittest.mock import patch

from backtest.adapters.us_stocks import USStocksAdapter


def test_extended_true_discovers_active_plus_delisted_overlay():
    adapter = USStocksAdapter(universe="extended_true")

    with patch(
        "src.data.delisted_universe_manager.get_extended_true_symbols",
        return_value=["AAPL", "NVDA", "TWTR"],
    ):
        symbols = adapter._discover_symbols()

    assert symbols == ["AAPL", "NVDA", "TWTR"]


def test_extended_universe_still_uses_active_symbols_only():
    adapter = USStocksAdapter(universe="extended")

    with patch(
        "src.data.extended_universe_manager.get_extended_symbols",
        return_value=["AAPL", "NVDA"],
    ):
        symbols = adapter._discover_symbols()

    assert symbols == ["AAPL", "NVDA"]
