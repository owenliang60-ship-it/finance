"""Tests for src/data/extended_universe_manager.py."""
import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.data.extended_universe_manager import (
    get_extended_only_symbols,
    get_extended_symbols,
    refresh_extended_universe,
)


@pytest.fixture
def tmp_cache(tmp_path, monkeypatch):
    """Redirect EXTENDED_UNIVERSE_FILE to tmp_path."""
    cache_file = tmp_path / "extended_universe.json"
    monkeypatch.setattr(
        "src.data.extended_universe_manager.EXTENDED_UNIVERSE_FILE",
        cache_file,
    )
    return cache_file


class TestRefreshExtendedUniverse:
    def test_writes_cache_with_correct_format(self, tmp_cache):
        mock_client = MagicMock()
        mock_client.get_large_cap_stocks.return_value = [
            {"symbol": "AAPL"}, {"symbol": "NVDA"}, {"symbol": "XOM"},
        ]

        with patch("src.data.fmp_client.FMPClient", return_value=mock_client):
            symbols = refresh_extended_universe(min_mcap_b=10)

        assert symbols == ["AAPL", "NVDA", "XOM"]
        assert tmp_cache.exists()

        data = json.loads(tmp_cache.read_text())
        assert data["count"] == 3
        assert data["min_mcap_b"] == 10
        assert data["symbols"] == ["AAPL", "NVDA", "XOM"]
        assert "updated" in data

    def test_deduplicates_symbols(self, tmp_cache):
        mock_client = MagicMock()
        mock_client.get_large_cap_stocks.return_value = [
            {"symbol": "AAPL"}, {"symbol": "AAPL"}, {"symbol": "NVDA"},
        ]

        with patch("src.data.fmp_client.FMPClient", return_value=mock_client):
            symbols = refresh_extended_universe()

        assert symbols == ["AAPL", "NVDA"]

    def test_skips_entries_without_symbol(self, tmp_cache):
        mock_client = MagicMock()
        mock_client.get_large_cap_stocks.return_value = [
            {"symbol": "AAPL"}, {"name": "NoSymbol"}, {"symbol": ""},
        ]

        with patch("src.data.fmp_client.FMPClient", return_value=mock_client):
            symbols = refresh_extended_universe()

        assert symbols == ["AAPL"]


class TestGetExtendedSymbols:
    def test_returns_empty_when_no_cache(self, tmp_cache):
        assert get_extended_symbols() == []

    def test_returns_symbols_from_cache(self, tmp_cache):
        tmp_cache.write_text(json.dumps({
            "updated": "2026-03-28",
            "count": 2,
            "symbols": ["AAPL", "NVDA"],
        }))
        assert get_extended_symbols() == ["AAPL", "NVDA"]


class TestGetExtendedOnlySymbols:
    def test_excludes_pool_symbols(self, tmp_cache):
        tmp_cache.write_text(json.dumps({
            "updated": "2026-03-28",
            "count": 3,
            "symbols": ["AAPL", "NVDA", "XOM"],
        }))

        with patch(
            "src.data.pool_manager.get_symbols",
            return_value=["AAPL", "NVDA"],
        ):
            result = get_extended_only_symbols()

        assert result == ["XOM"]

    def test_returns_all_when_pool_empty(self, tmp_cache):
        tmp_cache.write_text(json.dumps({
            "updated": "2026-03-28",
            "count": 2,
            "symbols": ["XOM", "CVX"],
        }))

        with patch(
            "src.data.pool_manager.get_symbols",
            return_value=[],
        ):
            result = get_extended_only_symbols()

        assert result == ["CVX", "XOM"]
