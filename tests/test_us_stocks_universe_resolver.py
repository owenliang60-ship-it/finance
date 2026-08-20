"""T20 B4 — eligible_extended is opt-in; legacy defaults stay unchanged."""
import json
import sqlite3
import warnings
from unittest.mock import MagicMock, patch

import pytest

from backtest.adapters.us_stocks import USStocksAdapter


def test_eligible_extended_option_uses_current_base_universe():
    adapter = USStocksAdapter(universe="eligible_extended")
    with patch("src.data.universe_resolver.current_base_universe",
               return_value=["AAPL", "MSFT"]):
        assert adapter._discover_symbols() == ["AAPL", "MSFT"]


def test_eligible_extended_propagates_resolver_failure():
    adapter = USStocksAdapter(universe="eligible_extended")
    with patch("src.data.universe_resolver.current_base_universe",
               side_effect=sqlite3.DatabaseError("database disk image malformed")):
        with pytest.raises(sqlite3.DatabaseError):
            adapter._discover_symbols()


def test_bare_adapter_keeps_market_db_all_default():
    adapter = USStocksAdapter()
    store = MagicMock()
    store.get_symbols.return_value = ["AAPL", "MSFT", "SPY", "QQQ", "^VIX"]
    with patch("backtest.adapters.us_stocks._get_market_store",
               return_value=store):
        assert adapter._discover_symbols() == ["AAPL", "MSFT"]
    store.get_symbols.assert_called_once_with("daily_price")


def test_pool_selector_reads_archived_frozen_core_with_warning(tmp_path):
    archived = tmp_path / "data" / "pool" / "archive"
    archived.mkdir(parents=True)
    (archived / "universe.json").write_text(
        json.dumps([{"symbol": "aapl"}, {"symbol": "MSFT"}]),
        encoding="utf-8",
    )
    adapter = USStocksAdapter(universe="pool")

    with patch("backtest.adapters.us_stocks.resolve_shared_data_root",
               return_value=tmp_path), \
         warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        symbols = adapter._discover_symbols()

    assert symbols == ["AAPL", "MSFT"]
    assert any("frozen core pool" in str(w.message).lower() for w in caught)
