"""Tests for portfolio holdings manager (enrichment only; ledger retired)."""
import pytest
from terminal.company_store import CompanyStore


@pytest.fixture
def store(tmp_path):
    db_path = tmp_path / "test_company.db"
    s = CompanyStore(db_path=db_path)
    # Seed companies + OPRMS
    s.upsert_company("NVDA", company_name="NVIDIA", sector="Technology", industry="Semiconductors")
    s.upsert_company("AAPL", company_name="Apple", sector="Technology", industry="Consumer Electronics")
    s.save_oprms_rating("NVDA", dna="S", timing="A", timing_coeff=0.9)
    s.save_oprms_rating("AAPL", dna="A", timing="B", timing_coeff=0.5)
    yield s
    s.close()


@pytest.fixture
def manager(store):
    """Create a manager backed by the test store."""
    from portfolio.holdings.manager import PortfolioManager
    return PortfolioManager(store=store)


class TestEnrichHoldingRow:
    """Public wrapper used by sheet-sourced callers (PI)."""

    def test_wraps_enrich_with_company_metadata(self, tmp_path):
        from terminal.company_store import CompanyStore
        from portfolio.holdings.manager import PortfolioManager

        store = CompanyStore(db_path=tmp_path / "t.db")
        store.upsert_company("NVDA", company_name="NVIDIA", sector="Technology")
        mgr = PortfolioManager(store=store)
        row = {"symbol": "NVDA", "avg_cost": 10.0, "shares": 100,
               "open_date": "", "position_id": None, "status": "OPEN"}
        pos = mgr.enrich_holding_row(row)
        assert pos.symbol == "NVDA"
        assert pos.company_name == "NVIDIA"
        assert pos.sector == "Technology"
        assert pos.cost_basis == 10.0
        assert pos.shares == 100
        store.close()

    def test_unknown_symbol_best_effort(self, tmp_path):
        from terminal.company_store import CompanyStore
        from portfolio.holdings.manager import PortfolioManager

        store = CompanyStore(db_path=tmp_path / "t.db")
        mgr = PortfolioManager(store=store)
        row = {"symbol": "0001", "avg_cost": 4.5, "shares": 200,
               "open_date": "", "position_id": None, "status": "OPEN"}
        pos = mgr.enrich_holding_row(row)
        assert pos.symbol == "0001"
        assert pos.sector == ""  # not in companies table -> empty (PI falls back to Category)
        store.close()


class TestRetiredLedgerApi:
    def test_ledger_methods_removed(self):
        from portfolio.holdings import manager as hm
        for name in ("load_holdings", "get_position", "add_position",
                     "close_position", "execute_trade", "execute_option_trade",
                     "get_option_market_value", "get_portfolio_summary",
                     "get_total_nav", "refresh_prices"):
            assert not hasattr(hm.PortfolioManager, name), name
        for name in ("load_holdings", "save_holdings", "add_position",
                     "update_position", "remove_position", "get_position",
                     "get_portfolio_value"):
            assert not hasattr(hm, name), name

    def test_target_weight_still_available(self):
        from portfolio.holdings import calculate_target_weight
        assert calculate_target_weight("S", "A") > 0
