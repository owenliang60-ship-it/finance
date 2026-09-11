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


def test_load_holdings_returns_open_positions(tmp_company_store):
    _seed_company(tmp_company_store, "AAPL")
    conn = tmp_company_store._get_conn()
    conn.execute(
        "INSERT INTO holdings (symbol, shares, avg_cost, open_date, status, last_updated) "
        "VALUES ('AAPL', 10, 100.0, '2026-08-01', 'OPEN', '2026-08-19')"
    )
    assert load_holdings(store=tmp_company_store) == ["AAPL"]


def test_load_holdings_excludes_closed_positions(tmp_company_store):
    _seed_company(tmp_company_store, "MSFT")
    conn = tmp_company_store._get_conn()
    conn.execute(
        "INSERT INTO holdings (symbol, shares, avg_cost, open_date, status, close_date, last_updated) "
        "VALUES ('MSFT', 5, 200.0, '2026-08-01', 'CLOSED', '2026-08-10', '2026-08-19')"
    )
    assert load_holdings(store=tmp_company_store) == []


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
