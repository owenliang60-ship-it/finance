"""T20 B4 — eligible_extended is opt-in; legacy defaults stay unchanged."""
from unittest.mock import MagicMock, patch

from backtest.adapters.us_stocks import USStocksAdapter


def test_eligible_extended_option_uses_current_base_universe():
    adapter = USStocksAdapter(universe="eligible_extended")
    with patch("src.data.universe_resolver.current_base_universe",
               return_value=["AAPL", "MSFT"]):
        assert adapter._discover_symbols() == ["AAPL", "MSFT"]


def test_bare_adapter_keeps_market_db_all_default():
    adapter = USStocksAdapter()
    store = MagicMock()
    store.get_symbols.return_value = ["AAPL", "MSFT", "SPY", "QQQ", "^VIX"]
    with patch("backtest.adapters.us_stocks._get_market_store",
               return_value=store):
        assert adapter._discover_symbols() == ["AAPL", "MSFT"]
    store.get_symbols.assert_called_once_with("daily_price")
