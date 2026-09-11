"""Matrix #8 — `get_extended_only_symbols()` becomes a deprecated forwarder.

It now returns whatever `extended_price_fetcher.get_yfinance_price_targets()`
returns (matrix #7 semantics) and warns; the body is deleted at Stop G phase 2.
"""
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.data.extended_universe_manager import get_extended_only_symbols


TARGETS = ["AMD", "CVX", "XOM"]


def test_forwards_to_get_yfinance_price_targets():
    with patch("src.data.extended_price_fetcher.get_yfinance_price_targets",
               return_value=list(TARGETS)) as mock_targets:
        with pytest.warns(DeprecationWarning):
            result = get_extended_only_symbols()

    assert result == TARGETS
    assert mock_targets.called


def test_warning_names_the_replacement():
    with patch("src.data.extended_price_fetcher.get_yfinance_price_targets",
               return_value=[]):
        with pytest.warns(DeprecationWarning, match="get_yfinance_price_targets"):
            get_extended_only_symbols()


def test_return_is_equivalent_to_the_legacy_list_pre_bootstrap():
    """Until membership is bootstrapped the forwarder still yields exactly the
    pre-migration extended−core list, so no caller changes behaviour on merge."""
    extended = ["AAPL", "AMD", "CVX", "MSFT", "XOM"]
    core = ["AAPL", "MSFT"]

    with patch("src.data.universe_resolver.current_base_universe",
               side_effect=RuntimeError("extended_membership empty — run bootstrap first")), \
         patch("src.data.extended_universe_manager.get_extended_symbols",
               return_value=list(extended)), \
         patch("src.data.pool_manager.get_symbols", return_value=list(core)):
        with pytest.warns(DeprecationWarning):
            result = get_extended_only_symbols()

    assert result == sorted(set(extended) - set(core))
