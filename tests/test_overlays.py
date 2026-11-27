"""Tests for T7 overlay loaders (R6, R13): holdings / watchlist / benchmarks."""
import pytest

from terminal.company_store import CompanyStore
from src.data.overlays import load_holdings, load_watchlist, load_benchmarks


@pytest.fixture
def tmp_company_store(tmp_path):
    db_path = tmp_path / "test_company.db"
    return CompanyStore(db_path=db_path)


def _seed_company(store, symbol, name="Test Co"):
    conn = store._get_conn()
    conn.execute(
        "INSERT INTO companies (symbol, company_name, updated_at) VALUES (?, ?, '2026-08-19')",
        (symbol, name),
    )


class _FakeBook:
    def __init__(self, symbols):
        self._symbols = symbols

    def quotable_us_symbols(self):
        return list(self._symbols)


def test_load_holdings_reads_sheet_quotable_us(monkeypatch):
    import portfolio.holdings.sheet_book as sb
    monkeypatch.setattr(sb, "load_sheet_book", lambda: _FakeBook(["AAA", "BBB"]))
    assert load_holdings() == ["AAA", "BBB"]


def test_load_holdings_ignores_company_db(monkeypatch, tmp_company_store):
    import portfolio.holdings.sheet_book as sb
    monkeypatch.setattr(sb, "load_sheet_book", lambda: _FakeBook(["AAA"]))
    # store arg kept for signature compatibility only
    assert load_holdings(store=tmp_company_store) == ["AAA"]


@pytest.mark.parametrize("exc", [None, RuntimeError("boom")])
def test_load_holdings_sheet_failure_returns_empty_with_warning(
        monkeypatch, caplog, exc):
    import logging
    import portfolio.holdings.sheet_book as sb

    def boom():
        raise exc if exc is not None else sb.SheetBookError("secret-id-xyz")

    monkeypatch.setattr(sb, "load_sheet_book", boom)
    with caplog.at_level(logging.WARNING, logger="src.data.overlays"):
        assert load_holdings() == []
    assert "Google Sheet unavailable" in caplog.text
    assert "secret-id-xyz" not in caplog.text


def test_load_watchlist_missing_table_returns_empty(tmp_company_store):
    # fresh CompanyStore has no `watchlist` table yet (created in T16)
    assert load_watchlist(store=tmp_company_store) == []


def test_load_watchlist_reads_existing_table(tmp_company_store):
    conn = tmp_company_store._get_conn()
    conn.execute("CREATE TABLE watchlist (symbol TEXT PRIMARY KEY)")
    conn.execute("INSERT INTO watchlist (symbol) VALUES ('SMCI')")
    assert load_watchlist(store=tmp_company_store) == ["SMCI"]


def test_load_benchmarks_returns_settings_symbols():
    from config import settings
    assert load_benchmarks() == list(settings.BENCHMARK_SYMBOLS)
