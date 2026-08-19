"""Tests for terminal.dashboard — HTML Dashboard generator."""
import logging
import re
import pytest
from pathlib import Path
from unittest.mock import patch

from terminal.company_store import CompanyStore
from terminal.dashboard import generate_dashboard

# Symbols to return from mocked get_symbols()
_MOCK_POOL_SYMBOLS = ["NVDA", "MSFT", "GOOG"]


@pytest.fixture
def populated_store(tmp_path):
    """Create a store with sample data for dashboard testing."""
    db_path = tmp_path / "test.db"
    store = CompanyStore(db_path=db_path)

    # Add companies
    store.upsert_company("NVDA", company_name="NVIDIA", sector="Technology",
                         exchange="NASDAQ")
    store.upsert_company("MSFT", company_name="Microsoft", sector="Technology",
                         exchange="NASDAQ")
    store.upsert_company("GOOG", company_name="Alphabet", sector="Technology",
                         exchange="NASDAQ")
    store.upsert_company("AAPL", company_name="Apple", sector="Technology",
                         exchange="NASDAQ")

    # Add OPRMS ratings
    store.save_oprms_rating("NVDA", dna="S", timing="A", timing_coeff=0.9,
                            verdict="BUY", position_pct=22.5,
                            investment_bucket="Long-term Compounder",
                            evidence=["AI leader", "GPU monopoly"])
    store.save_oprms_rating("MSFT", dna="S", timing="B", timing_coeff=0.5,
                            verdict="HOLD", position_pct=12.5,
                            investment_bucket="Long-term Compounder")
    store.save_oprms_rating("GOOG", dna="A", timing="B", timing_coeff=0.5,
                            verdict="BUY", position_pct=7.5,
                            investment_bucket="Catalyst-Driven Long")

    # Add analyses
    store.save_analysis("NVDA", {
        "analysis_date": "2026-02-13",
        "debate_verdict": "BUY — 高信心",
        "executive_summary": "NVIDIA dominates AI compute",
        "report_path": "/tmp/nvda_report.md",
        "html_report_path": "/tmp/nvda_report.html",
    })

    yield store, tmp_path
    store.close()


class TestDashboard:
    def test_generates_html_file(self, populated_store):
        store, tmp_path = populated_store
        output = tmp_path / "dashboard.html"

        import terminal.dashboard as mod
        original = mod.get_store
        mod.get_store = lambda: store

        try:
            with patch(
                "src.data.universe_resolver.current_base_universe",
                return_value=_MOCK_POOL_SYMBOLS,
            ):
                path = generate_dashboard(output_path=output)
            assert path.exists()
            assert path.suffix == ".html"
        finally:
            mod.get_store = original

    def test_contains_company_data(self, populated_store):
        store, tmp_path = populated_store
        output = tmp_path / "dashboard.html"

        import terminal.dashboard as mod
        original = mod.get_store
        mod.get_store = lambda: store

        try:
            with patch(
                "src.data.universe_resolver.current_base_universe",
                return_value=_MOCK_POOL_SYMBOLS,
            ):
                path = generate_dashboard(output_path=output)
            content = path.read_text(encoding="utf-8")

            # Company names
            assert "NVDA" in content
            assert "MSFT" in content
            assert "GOOG" in content
            assert "AAPL" in content

            # Stats
            assert "Total" in content
            assert "Rated" in content

            # OPRMS data
            assert "BUY" in content
            assert "HOLD" in content

            # HTML structure
            assert "<!DOCTYPE html>" in content
            assert "company-table" in content
        finally:
            mod.get_store = original

    def test_filter_javascript(self, populated_store):
        store, tmp_path = populated_store
        output = tmp_path / "dashboard.html"

        import terminal.dashboard as mod
        original = mod.get_store
        mod.get_store = lambda: store

        try:
            with patch(
                "src.data.universe_resolver.current_base_universe",
                return_value=_MOCK_POOL_SYMBOLS,
            ):
                path = generate_dashboard(output_path=output)
            content = path.read_text(encoding="utf-8")
            assert "filterTable" in content
            assert "sortTable" in content
        finally:
            mod.get_store = original

    def test_report_links(self, populated_store):
        store, tmp_path = populated_store
        output = tmp_path / "dashboard.html"

        import terminal.dashboard as mod
        original = mod.get_store
        mod.get_store = lambda: store

        try:
            with patch(
                "src.data.universe_resolver.current_base_universe",
                return_value=_MOCK_POOL_SYMBOLS,
            ):
                path = generate_dashboard(output_path=output)
            content = path.read_text(encoding="utf-8")
            assert "nvda_report.html" in content
            assert "View" in content
        finally:
            mod.get_store = original

    def test_empty_database(self, tmp_path):
        db_path = tmp_path / "empty.db"
        store = CompanyStore(db_path=db_path)
        output = tmp_path / "dashboard.html"

        import terminal.dashboard as mod
        original = mod.get_store
        mod.get_store = lambda: store

        try:
            with patch(
                "src.data.universe_resolver.current_base_universe",
                return_value=[],
            ):
                path = generate_dashboard(output_path=output)
            assert path.exists()
            content = path.read_text(encoding="utf-8")
            assert "0" in content  # Total should be 0
        finally:
            mod.get_store = original
            store.close()

    def test_pool_stat_reflects_resolver_eligible_count(self, populated_store):
        """Matrix #3 (dashboard.py:166): Pool stat card displays resolver
        eligible count, not the raw pool_manager.get_symbols() count."""
        store, tmp_path = populated_store
        output = tmp_path / "dashboard.html"

        import terminal.dashboard as mod
        original = mod.get_store
        mod.get_store = lambda: store

        try:
            with patch(
                "src.data.universe_resolver.current_base_universe",
                return_value=_MOCK_POOL_SYMBOLS,
            ):
                path = generate_dashboard(output_path=output)
            content = path.read_text(encoding="utf-8")
            match = re.search(r'Pool</div><div class="stat-value">(\d+)</div>', content)
            assert match is not None, content
            assert int(match.group(1)) == len(_MOCK_POOL_SYMBOLS)
        finally:
            mod.get_store = original

    def test_pool_stat_falls_back_to_legacy_pool_when_resolver_raises(self, populated_store, caplog):
        """Matrix #3 (dashboard.py:166): display context must never crash —
        resolver failure falls back to pool_manager.get_symbols() with a
        logged warning from terminal.dashboard specifically (not just the
        get_stats() fallback from company_store, which uses a distinct
        symbol count here to guard against that false-positive)."""
        store, tmp_path = populated_store
        output = tmp_path / "dashboard.html"

        import terminal.dashboard as mod
        original = mod.get_store
        mod.get_store = lambda: store

        def boom():
            raise RuntimeError("extended_membership empty — run bootstrap first")

        legacy_symbols = ["A", "B", "C", "D", "E"]
        try:
            with patch("src.data.universe_resolver.current_base_universe", boom), \
                 patch("src.data.pool_manager.get_symbols", return_value=legacy_symbols):
                with caplog.at_level(logging.WARNING):
                    path = generate_dashboard(output_path=output)
            content = path.read_text(encoding="utf-8")
            match = re.search(r'Pool</div><div class="stat-value">(\d+)</div>', content)
            assert match is not None, content
            assert int(match.group(1)) == len(legacy_symbols)
            assert any(
                rec.name == "terminal.dashboard"
                and "current_base_universe unavailable" in rec.message
                for rec in caplog.records
            )
        finally:
            mod.get_store = original
