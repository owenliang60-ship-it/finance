"""Tests for terminal.dashboard — HTML Dashboard generator."""
import pytest
from pathlib import Path

from terminal.company_store import CompanyStore
from terminal.dashboard import generate_dashboard


@pytest.fixture
def populated_store(tmp_path):
    """Create a store with sample data for dashboard testing."""
    db_path = tmp_path / "test.db"
    store = CompanyStore(db_path=db_path)

    # Add companies
    store.upsert_company("NVDA", company_name="NVIDIA", sector="Technology",
                         exchange="NASDAQ", in_pool=True)
    store.upsert_company("MSFT", company_name="Microsoft", sector="Technology",
                         exchange="NASDAQ", in_pool=True)
    store.upsert_company("GOOG", company_name="Alphabet", sector="Technology",
                         exchange="NASDAQ", in_pool=True)
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

        # Monkey-patch get_store to return our test store
        import terminal.dashboard as mod
        original = mod.get_store
        mod.get_store = lambda: store

        try:
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
            path = generate_dashboard(output_path=output)
            assert path.exists()
            content = path.read_text(encoding="utf-8")
            assert "0" in content  # Total should be 0
        finally:
            mod.get_store = original
            store.close()
