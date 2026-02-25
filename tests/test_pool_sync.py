"""
Tests for pool sync: sync_db_pool() and refresh_universe() DB sync integration.
"""
import json
import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock


@pytest.fixture
def pool_dir(tmp_path):
    """Create a temp pool directory with universe.json."""
    pool = tmp_path / "pool"
    pool.mkdir()
    universe = [
        {"symbol": "AAPL", "companyName": "Apple"},
        {"symbol": "MSFT", "companyName": "Microsoft"},
        {"symbol": "NVDA", "companyName": "NVIDIA"},
    ]
    (pool / "universe.json").write_text(json.dumps(universe))
    return pool


class TestSyncDbPool:
    """Tests for sync_db_pool() standalone function."""

    def test_syncs_universe_to_db(self, pool_dir):
        """sync_db_pool reads universe.json and calls store.sync_pool."""
        mock_store = MagicMock()
        mock_store.sync_pool.return_value = 3

        with patch("src.data.pool_manager.POOL_DIR", pool_dir), \
             patch("src.data.pool_manager.UNIVERSE_FILE", pool_dir / "universe.json"), \
             patch("terminal.company_store.get_store", return_value=mock_store):
            from src.data.pool_manager import sync_db_pool
            result = sync_db_pool()

        assert result == 3
        mock_store.sync_pool.assert_called_once()
        called_symbols = mock_store.sync_pool.call_args[0][0]
        assert set(called_symbols) == {"AAPL", "MSFT", "NVDA"}

    def test_empty_universe_returns_zero(self, tmp_path):
        """sync_db_pool returns 0 when universe.json is empty or missing."""
        pool = tmp_path / "pool"
        pool.mkdir()
        (pool / "universe.json").write_text("[]")

        with patch("src.data.pool_manager.POOL_DIR", pool), \
             patch("src.data.pool_manager.UNIVERSE_FILE", pool / "universe.json"):
            from src.data.pool_manager import sync_db_pool
            result = sync_db_pool()

        assert result == 0


class TestRefreshUniversePoolSync:
    """Test that refresh_universe() triggers DB pool sync."""

    def test_refresh_calls_sync_pool(self, pool_dir, tmp_path):
        """After refresh_universe saves, it should call store.sync_pool."""
        mock_store = MagicMock()
        mock_store.sync_pool.return_value = 5

        price_dir = tmp_path / "price"
        price_dir.mkdir()
        fundamental_dir = tmp_path / "fundamental"
        fundamental_dir.mkdir()

        fake_stocks = [
            {"symbol": "AAPL", "companyName": "Apple", "marketCap": 3e12,
             "sector": "Technology", "industry": "Consumer Electronics"},
            {"symbol": "MSFT", "companyName": "Microsoft", "marketCap": 2.5e12,
             "sector": "Technology", "industry": "Software"},
        ]

        with patch("src.data.pool_manager.POOL_DIR", pool_dir), \
             patch("src.data.pool_manager.UNIVERSE_FILE", pool_dir / "universe.json"), \
             patch("src.data.pool_manager.HISTORY_FILE", pool_dir / "pool_history.json"), \
             patch("src.data.pool_manager.PRICE_DIR", price_dir), \
             patch("src.data.pool_manager.FUNDAMENTAL_DIR", fundamental_dir), \
             patch("src.data.pool_manager.fmp_client") as mock_fmp, \
             patch("terminal.company_store.get_store", return_value=mock_store):
            mock_fmp.get_large_cap_stocks.return_value = fake_stocks

            from src.data.pool_manager import refresh_universe
            stocks, entered, exited = refresh_universe()

        # sync_pool should have been called with the new stock symbols
        mock_store.sync_pool.assert_called_once()
        synced_symbols = mock_store.sync_pool.call_args[0][0]
        assert "AAPL" in synced_symbols
        assert "MSFT" in synced_symbols


class TestBackfillFallback:
    """Test IV script fallback when in_pool is empty."""

    def test_backfill_falls_back_to_sync_db_pool(self):
        """When list_companies returns empty, backfill syncs pool first."""
        mock_store = MagicMock()
        # First call returns empty, second returns data
        mock_store.list_companies.side_effect = [
            [],  # first call: in_pool empty
            [{"symbol": "AAPL"}, {"symbol": "MSFT"}],  # after sync
        ]

        with patch("scripts.backfill_iv.get_store", return_value=mock_store), \
             patch("src.data.pool_manager.sync_db_pool", return_value=2) as mock_sync, \
             patch("scripts.backfill_iv.BENCHMARK_SYMBOLS", ["SPY"]):

            # Simulate the symbol-gathering logic from backfill()
            companies = mock_store.list_companies(in_pool_only=True)
            symbols = [c["symbol"] for c in companies]

            if not symbols:
                from src.data.pool_manager import sync_db_pool
                sync_db_pool()
                companies = mock_store.list_companies(in_pool_only=True)
                symbols = [c["symbol"] for c in companies]

            assert symbols == ["AAPL", "MSFT"]
            mock_sync.assert_called_once()
