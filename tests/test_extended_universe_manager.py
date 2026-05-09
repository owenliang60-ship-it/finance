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
            symbols = refresh_extended_universe(min_mcap_b=10, min_count_floor=0)

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
            symbols = refresh_extended_universe(min_count_floor=0)

        assert symbols == ["AAPL", "NVDA"]

    def test_skips_entries_without_symbol(self, tmp_cache):
        mock_client = MagicMock()
        mock_client.get_large_cap_stocks.return_value = [
            {"symbol": "AAPL"}, {"name": "NoSymbol"}, {"symbol": ""},
        ]

        with patch("src.data.fmp_client.FMPClient", return_value=mock_client):
            symbols = refresh_extended_universe(min_count_floor=0)

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


class TestRefreshFloorGuard:
    """P0 floor guard: FMP empty/partial returns must NOT overwrite cache.

    Background: Boss review v1 -> v2 caught fmp_client.get_large_cap_stocks()
    silent-fail mode (returns [] on API error). Without guard,
    refresh_extended_universe() would write count:0 / symbols:[] and exit 0,
    corrupting the production cache. Guard raises RuntimeError before
    _write_cache() and preserves whatever was on disk.
    """

    @pytest.fixture
    def populated_cache(self, tmp_cache):
        """Pre-populate cache with 548 fake symbols (= 5/9 actual count)."""
        tmp_cache.write_text(json.dumps({
            "updated": "2026-04-25",
            "min_mcap_b": 10,
            "count": 548,
            "symbols": [f"SYM{i}" for i in range(548)],
        }))
        return tmp_cache

    def test_aborts_when_fmp_returns_empty(self, populated_cache):
        """FMP API silent failure (returns []) -> raise + old cache intact."""
        mock_client = MagicMock()
        mock_client.get_large_cap_stocks.return_value = []

        with patch("src.data.fmp_client.FMPClient", return_value=mock_client):
            with pytest.raises(RuntimeError, match="below floor"):
                refresh_extended_universe()

        data = json.loads(populated_cache.read_text())
        assert data["updated"] == "2026-04-25"
        assert data["count"] == 548

    def test_aborts_when_fmp_returns_partial(self, populated_cache):
        """FMP API jitter (returns 100 < floor 400) -> raise + old cache intact."""
        mock_client = MagicMock()
        mock_client.get_large_cap_stocks.return_value = [
            {"symbol": f"SYM{i}"} for i in range(100)
        ]

        with patch("src.data.fmp_client.FMPClient", return_value=mock_client):
            with pytest.raises(RuntimeError, match="below floor"):
                refresh_extended_universe()

        data = json.loads(populated_cache.read_text())
        assert data["count"] == 548

    def test_writes_when_above_floor(self, populated_cache):
        """FMP normal return (500 >= floor 400) -> writes new cache."""
        mock_client = MagicMock()
        mock_client.get_large_cap_stocks.return_value = [
            {"symbol": f"NEW{i}"} for i in range(500)
        ]

        with patch("src.data.fmp_client.FMPClient", return_value=mock_client):
            symbols = refresh_extended_universe()

        assert len(symbols) == 500
        data = json.loads(populated_cache.read_text())
        assert data["count"] == 500
        assert data["symbols"][0] == "NEW0"
        assert data["updated"] != "2026-04-25"

    def test_respects_explicit_floor_override(self, populated_cache):
        """Explicit min_count_floor=50 allows tiny refresh through (test/dev path)."""
        mock_client = MagicMock()
        mock_client.get_large_cap_stocks.return_value = [
            {"symbol": f"TINY{i}"} for i in range(60)
        ]

        with patch("src.data.fmp_client.FMPClient", return_value=mock_client):
            symbols = refresh_extended_universe(min_count_floor=50)

        assert len(symbols) == 60
        data = json.loads(populated_cache.read_text())
        assert data["count"] == 60
