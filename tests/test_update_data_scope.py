"""Test _resolve_target_symbols helper for --forward-estimates scope routing."""
import re
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
    assert result == ["AAPL", "NVDA"]
    assert mock_pool.called
    assert not mock_ext.called


@patch("src.data.extended_universe_manager.get_extended_only_symbols")
@patch("src.data.pool_manager.get_symbols")
def test_scope_extended_uses_extended_only(mock_pool, mock_ext):
    from scripts.update_data import _resolve_target_symbols
    mock_ext.return_value = ["EXT1", "EXT2", "EXT3"]
    result = _resolve_target_symbols(scope="extended", symbols=None)
    assert result == ["EXT1", "EXT2", "EXT3"]
    assert not mock_pool.called
    assert mock_ext.called


@patch("src.data.overlays.load_overlay_tier", return_value=[])
@patch("src.data.extended_universe_manager.get_extended_only_symbols")
@patch("src.data.pool_manager.get_symbols")
def test_scope_all_returns_union_no_duplicates(mock_pool, mock_ext, _mock_overlay):
    from scripts.update_data import _resolve_target_symbols
    mock_pool.return_value = ["AAPL", "NVDA", "SHARED"]
    mock_ext.return_value = ["SHARED", "EXT1", "EXT2"]
    result = _resolve_target_symbols(scope="all", symbols=None)
    assert result == sorted({"AAPL", "NVDA", "SHARED", "EXT1", "EXT2"})


@patch("src.data.overlays.load_overlay_tier")
@patch("src.data.extended_universe_manager.get_extended_only_symbols")
@patch("src.data.pool_manager.get_symbols")
def test_scope_all_keeps_overlay_symbols_excluded_from_price_forwarder(
        mock_pool, mock_ext, mock_overlay):
    """B2 review C2: get_extended_only_symbols is a price-tier complement,
    so scope=all must add the overlay tier back for yfinance forward estimates."""
    from scripts.update_data import _resolve_target_symbols

    mock_pool.return_value = ["AAPL"]
    mock_ext.return_value = ["MSFT"]
    mock_overlay.return_value = ["CVX", "SPY"]

    result = _resolve_target_symbols(scope="all", symbols=None)

    assert result == ["AAPL", "CVX", "MSFT", "SPY"]


def test_invalid_scope_raises():
    from scripts.update_data import _resolve_target_symbols
    with pytest.raises(ValueError, match="scope"):
        _resolve_target_symbols(scope="garbage", symbols=None)


# ---------------------------------------------------------------------------
# T11: base/events scopes added for --fundamental (R6)
# ---------------------------------------------------------------------------

@patch("src.data.universe_resolver.current_base_universe")
def test_scope_base_uses_current_base_universe(mock_base):
    from scripts.update_data import _resolve_target_symbols
    mock_base.return_value = ["AAA", "BBB"]
    dummy_store = object()
    result = _resolve_target_symbols(scope="base", symbols=None, store=dummy_store)
    assert result == ["AAA", "BBB"]
    mock_base.assert_called_once_with(store=dummy_store)


@patch("src.data.fundamental_events.detect_earnings_targets")
def test_scope_events_uses_detect_earnings_targets(mock_detect):
    from scripts.update_data import _resolve_target_symbols
    mock_detect.return_value = ["AAPL", "MSFT"]
    dummy_store = object()
    result = _resolve_target_symbols(scope="events", symbols=None,
                                     store=dummy_store, as_of="2026-08-24")
    assert result == ["AAPL", "MSFT"]
    mock_detect.assert_called_once_with(dummy_store, as_of="2026-08-24")


@patch("src.data.fundamental_events.detect_earnings_targets")
def test_scope_events_defaults_as_of_to_today(mock_detect):
    """No explicit as_of -> today (UTC), never left None (would blow up the
    window-arithmetic inside detect_earnings_targets)."""
    from scripts.update_data import _resolve_target_symbols
    mock_detect.return_value = []
    dummy_store = object()
    _resolve_target_symbols(scope="events", symbols=None, store=dummy_store, as_of=None)
    called_as_of = mock_detect.call_args.kwargs["as_of"]
    assert re.match(r"^\d{4}-\d{2}-\d{2}$", called_as_of)


def test_scope_choices_include_base_and_events():
    from scripts.update_data import FUNDAMENTAL_SCOPE_CHOICES
    assert set(FUNDAMENTAL_SCOPE_CHOICES) == {"core", "extended", "all", "base", "events"}
    assert FUNDAMENTAL_SCOPE_CHOICES[0] == "core"           # default stays core
