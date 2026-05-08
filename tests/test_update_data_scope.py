"""Test _resolve_target_symbols helper for --forward-estimates scope routing."""
from unittest.mock import patch
import pytest


@patch("src.data.extended_universe_manager.get_extended_only_symbols")
@patch("src.data.pool_manager.get_symbols")
def test_explicit_symbols_bypass_scope(mock_pool, mock_ext):
    from scripts.update_data import _resolve_target_symbols
    result = _resolve_target_symbols(scope="extended", symbols=["AAPL", "MSFT"])
    assert result == ["AAPL", "MSFT"]
    assert not mock_pool.called
    assert not mock_ext.called


@patch("src.data.extended_universe_manager.get_extended_only_symbols")
@patch("src.data.pool_manager.get_symbols")
def test_scope_core_uses_pool(mock_pool, mock_ext):
    from scripts.update_data import _resolve_target_symbols
    mock_pool.return_value = ["AAPL", "NVDA"]
    result = _resolve_target_symbols(scope="core", symbols=None)
    assert set(result) == {"AAPL", "NVDA"}
    assert mock_pool.called
    assert not mock_ext.called


@patch("src.data.extended_universe_manager.get_extended_only_symbols")
@patch("src.data.pool_manager.get_symbols")
def test_scope_extended_uses_extended_only(mock_pool, mock_ext):
    from scripts.update_data import _resolve_target_symbols
    mock_ext.return_value = ["EXT1", "EXT2", "EXT3"]
    result = _resolve_target_symbols(scope="extended", symbols=None)
    assert set(result) == {"EXT1", "EXT2", "EXT3"}
    assert not mock_pool.called
    assert mock_ext.called


@patch("src.data.extended_universe_manager.get_extended_only_symbols")
@patch("src.data.pool_manager.get_symbols")
def test_scope_all_returns_union_no_duplicates(mock_pool, mock_ext):
    from scripts.update_data import _resolve_target_symbols
    mock_pool.return_value = ["AAPL", "NVDA", "SHARED"]
    mock_ext.return_value = ["SHARED", "EXT1", "EXT2"]
    result = _resolve_target_symbols(scope="all", symbols=None)
    assert result == sorted({"AAPL", "NVDA", "SHARED", "EXT1", "EXT2"})


def test_invalid_scope_raises():
    from scripts.update_data import _resolve_target_symbols
    with pytest.raises(ValueError, match="scope"):
        _resolve_target_symbols(scope="garbage", symbols=None)
