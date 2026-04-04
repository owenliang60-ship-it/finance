"""Tests for portfolio holdings manager (SQLite-backed)."""
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


class TestLoadAndGet:
    def test_empty_portfolio(self, manager):
        positions = manager.load_holdings()
        assert positions == []

    def test_add_and_load(self, manager):
        manager.add_position("NVDA", shares=100, avg_cost=135.0, date="2026-04-01")
        positions = manager.load_holdings()
        assert len(positions) == 1
        p = positions[0]
        assert p.symbol == "NVDA"
        assert p.shares == 100
        assert p.cost_basis == 135.0
        assert p.company_name == "NVIDIA"
        assert p.sector == "Technology"
        assert p.dna_rating == "S"
        assert p.timing_rating == "A"
        assert p.status == "OPEN"

    def test_get_position(self, manager):
        manager.add_position("NVDA", shares=100, avg_cost=135.0, date="2026-04-01")
        p = manager.get_position("NVDA")
        assert p is not None
        assert p.symbol == "NVDA"

    def test_get_nonexistent(self, manager):
        assert manager.get_position("FAKE") is None


class TestClosePosition:
    def test_close_and_reopen(self, manager):
        manager.add_position("NVDA", shares=100, avg_cost=135.0, date="2026-04-01")
        manager.close_position("NVDA", sell_price=150.0, date="2026-04-05")
        assert manager.get_position("NVDA") is None

        # Reopen
        manager.add_position("NVDA", shares=50, avg_cost=160.0, date="2026-04-06")
        p = manager.get_position("NVDA")
        assert p.shares == 50

    def test_close_calculates_pnl(self, manager):
        manager.add_position("NVDA", shares=100, avg_cost=135.0, date="2026-04-01")
        manager.close_position("NVDA", sell_price=150.0, date="2026-04-05")
        # realized_pnl = (150 - 135) * 100 = 1500
        holdings = manager._store.get_all_open_holdings()
        assert len(holdings) == 0  # No open


class TestNAV:
    def test_total_nav(self, manager):
        manager.add_position("NVDA", shares=100, avg_cost=135.0, date="2026-04-01")
        manager._store.set_cash(500000.0)
        prices = {"NVDA": 150.0}
        nav = manager.get_total_nav(prices)
        # NAV = 100 * 150 + 500000 = 515000
        assert nav == pytest.approx(515000.0)

    def test_weights(self, manager):
        manager.add_position("NVDA", shares=100, avg_cost=135.0, date="2026-04-01")
        manager.add_position("AAPL", shares=200, avg_cost=200.0, date="2026-04-01")
        manager._store.set_cash(100000.0)
        prices = {"NVDA": 150.0, "AAPL": 210.0}
        positions = manager.refresh_prices(prices)
        # NVDA: 15000, AAPL: 42000, cash: 100000, NAV: 157000
        nvda = [p for p in positions if p.symbol == "NVDA"][0]
        assert nvda.current_weight == pytest.approx(15000 / 157000, rel=1e-3)

    def test_summary(self, manager):
        manager.add_position("NVDA", shares=100, avg_cost=135.0, date="2026-04-01")
        manager._store.set_cash(500000.0)
        prices = {"NVDA": 150.0}
        summary = manager.get_portfolio_summary(prices)
        assert summary["total_nav"] == pytest.approx(515000.0)
        assert summary["invested_pct"] == pytest.approx(15000 / 515000, rel=1e-3)
        assert summary["cash_pct"] == pytest.approx(500000 / 515000, rel=1e-3)
        assert summary["total_positions"] == 1
