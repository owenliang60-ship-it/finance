"""
Tests for pool sync: refresh_universe() integration.

Note: sync_db_pool() and store.sync_pool() have been retired (P1.1).
Pool membership is now solely determined by pool_manager.get_symbols().
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


class TestRefreshUniversePoolSync:
    """Test that refresh_universe() saves to universe.json (single source of truth)."""

    def test_refresh_saves_universe(self, pool_dir, tmp_path):
        """After refresh_universe saves, universe.json is updated."""
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
             patch("src.data.pool_manager.fmp_client") as mock_fmp:
            mock_fmp.get_large_cap_stocks.return_value = fake_stocks

            from src.data.pool_manager import refresh_universe
            stocks, entered, exited = refresh_universe()

        # universe.json should reflect the new pool
        saved = json.loads((pool_dir / "universe.json").read_text())
        saved_symbols = {s["symbol"] for s in saved}
        assert "AAPL" in saved_symbols
        assert "MSFT" in saved_symbols
