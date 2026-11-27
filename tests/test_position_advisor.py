"""position_advisor reads current position from the read-only Google Sheet."""
import logging
from types import SimpleNamespace
from unittest.mock import patch

import pytest


@pytest.fixture(autouse=True)
def _no_company_db():
    with patch("terminal.commands.get_oprms", return_value=None), \
         patch("terminal.commands.get_kill_conditions", return_value=[]):
        yield


def _book(*holdings):
    return SimpleNamespace(holdings=list(holdings))


def test_position_from_sheet():
    from terminal.commands import position_advisor
    h = SimpleNamespace(symbol="AAA", market="US", shares=10.0,
                        cost_per_share=5.0, sheet_price=6.0,
                        market_value=60.0, category="Value", is_leaps=False)
    with patch("portfolio.holdings.sheet_book.load_sheet_book",
               return_value=_book(h)):
        result = position_advisor("aaa")
    pos = result["current_position"]
    assert pos["symbol"] == "AAA"
    assert pos["shares"] == 10.0
    assert pos["cost_per_share"] == 5.0
    assert pos["source"] == "google_sheet"


def test_not_held_returns_none():
    from terminal.commands import position_advisor
    with patch("portfolio.holdings.sheet_book.load_sheet_book",
               return_value=_book()):
        assert position_advisor("ZZZ")["current_position"] is None


def test_sheet_failure_returns_none_with_warning(caplog):
    from terminal.commands import position_advisor
    from portfolio.holdings.sheet_book import SheetBookError
    with patch("portfolio.holdings.sheet_book.load_sheet_book",
               side_effect=SheetBookError("secret-id")), \
         caplog.at_level(logging.WARNING, logger="terminal.commands"):
        result = position_advisor("AAA")
    assert result["current_position"] is None
    assert "Google Sheet unavailable" in caplog.text
    assert "secret-id" not in caplog.text


def test_retired_commands_removed():
    import terminal
    import terminal.commands as cmds
    assert not hasattr(cmds, "portfolio_status")
    assert not hasattr(cmds, "run_monitor")
    assert not hasattr(terminal, "portfolio_status")
