"""Integration tests: exercise terminal entrypoints, not just PortfolioManager."""
import pytest
import json
import pandas as pd
from types import SimpleNamespace
from unittest.mock import patch
from terminal.company_store import CompanyStore
from portfolio.holdings.manager import PortfolioManager


def _mock_price_df(latest: float) -> pd.DataFrame:
    """get_price_df() returns descending rows; row 0 is latest."""
    return pd.DataFrame({
        "date": ["2026-04-03", "2026-04-02", "2026-04-01"],
        "close": [latest, latest - 1.0, latest - 2.0],
        "volume": [1_000_000, 900_000, 800_000],
    })


class _FreshnessReport:
    def __init__(self, symbol: str, level: str = "GREEN"):
        self.symbol = symbol
        self.level = SimpleNamespace(value=level)
        self.reasons = []

    def to_dict(self):
        return {"symbol": self.symbol, "level": self.level.value, "reasons": []}


@pytest.fixture
def store(tmp_path):
    db = tmp_path / "test.db"
    s = CompanyStore(db_path=db)
    s.upsert_company("NVDA", company_name="NVIDIA", sector="Technology")
    s.save_oprms_rating("NVDA", dna="S", timing="A", timing_coeff=0.9)
    s.insert_holding("NVDA", shares=100, avg_cost=135.0, open_date="2026-04-01")
    s.set_cash(500000.0)
    s.save_kill_conditions("NVDA", [{"description": "Revenue < $90B", "source_lens": "fundamental"}])
    yield s
    s.close()


class TestSummaryContract:
    """P2-8: get_portfolio_summary returns JSON-serializable payload."""

    def test_summary_has_total_nav_and_legacy_fields(self, store):
        mgr = PortfolioManager(store=store)
        prices = {"NVDA": 150.0}
        summary = mgr.get_portfolio_summary(prices)
        # New fields
        assert "total_nav" in summary
        assert summary["total_nav"] == pytest.approx(515000.0)
        assert summary["invested_pct"] + summary["cash_pct"] == pytest.approx(1.0, rel=1e-3)
        # Legacy fields preserved
        assert "total_value" in summary
        assert "total_cost" in summary
        assert "total_pnl" in summary
        assert "total_pnl_pct" in summary
        assert "by_bucket" in summary
        assert "by_dna" in summary

    def test_positions_are_dicts_not_objects(self, store):
        """P2-8: positions must be serialized as dict, not raw Position."""
        mgr = PortfolioManager(store=store)
        prices = {"NVDA": 150.0}
        summary = mgr.get_portfolio_summary(prices)
        assert isinstance(summary["positions"], list)
        if summary["positions"]:
            assert isinstance(summary["positions"][0], dict)
            assert "symbol" in summary["positions"][0]
        # Verify entire summary is JSON serializable
        json.dumps(summary)  # No exception = pass


class TestPortfolioStatusContract:
    """P2-7: portfolio_status() must return 3 sections: holdings, company_db, analysis_freshness."""

    def test_status_has_all_sections(self, store):
        """P2-11: directly call terminal.commands.portfolio_status()."""
        with patch("terminal.company_store.get_store", return_value=store), \
             patch("src.data.price_fetcher.get_price_df", return_value=_mock_price_df(150.0)), \
             patch("terminal.commands.list_all_companies", return_value=["NVDA"]), \
             patch("terminal.freshness.check_all_freshness", return_value=[_FreshnessReport("NVDA")]):
            from portfolio.holdings import manager as hm
            hm._default_mgr = None  # Reset singleton to pick up test store

            from terminal.commands import portfolio_status
            result = portfolio_status()

        assert result["has_holdings"] is True
        assert result["summary"]["total_nav"] == pytest.approx(515000.0)
        assert isinstance(result["summary"]["positions"], list)
        assert "company_db" in result
        assert "analysis_freshness" in result


class TestMonitorContract:
    def test_monitor_exposes_total_nav(self, store):
        """P1-10 + P2-11: directly call terminal.monitor.run_full_monitor()."""
        with patch("terminal.company_store.get_store", return_value=store), \
             patch("src.data.price_fetcher.get_price_df", return_value=_mock_price_df(150.0)), \
             patch("terminal.freshness.check_freshness", return_value=_FreshnessReport("NVDA")):
            from portfolio.holdings import manager as hm
            hm._default_mgr = None

            from terminal.monitor import run_full_monitor
            result = run_full_monitor()

        assert result["position_count"] == 1
        assert result["total_value"] == pytest.approx(15000.0)
        assert result["total_nav"] == pytest.approx(515000.0)
        assert "summary" in result


class TestNAVConsistency:
    def test_summary_and_refresh_agree_on_weights(self, store):
        """portfolio_summary and refresh_prices weights must match."""
        mgr = PortfolioManager(store=store)
        prices = {"NVDA": 150.0}
        summary = mgr.get_portfolio_summary(prices)
        positions = mgr.refresh_prices(prices)
        p = positions[0]
        expected_weight = (100 * 150.0) / 515000.0
        assert p.current_weight == pytest.approx(expected_weight, rel=1e-3)
        assert summary["positions"][0]["current_weight"] == pytest.approx(expected_weight, rel=1e-3)
