"""Tests for terminal.pipeline.ensure_tracked (T16).

Replaces the legacy `ensure_in_pool` auto-admit call in collect_data():
analysis-triggered tracking now writes only to the company.db `watchlist`
table and NEVER touches data/pool/universe.json — that write path is now
exclusively owned by the screener pool-refresh cron (R2/R5/R13).
"""
from unittest.mock import patch

import pytest

from terminal.company_store import CompanyStore
from terminal.pipeline import ensure_tracked


@pytest.fixture
def tmp_company_store(tmp_path):
    db_path = tmp_path / "test_company.db"
    s = CompanyStore(db_path=db_path)
    yield s
    s.close()


class TestEnsureTracked:
    def test_writes_watchlist(self, tmp_company_store):
        with patch("terminal.company_store.get_store", return_value=tmp_company_store):
            ensure_tracked("NEWCO")
        assert "NEWCO" in tmp_company_store.get_watchlist()

    def test_universe_json_byte_identical(self, tmp_company_store):
        from src.data.pool_manager import UNIVERSE_FILE

        before = UNIVERSE_FILE.read_bytes() if UNIVERSE_FILE.exists() else None
        with patch("terminal.company_store.get_store", return_value=tmp_company_store):
            ensure_tracked("NEWCO")
        after = UNIVERSE_FILE.read_bytes() if UNIVERSE_FILE.exists() else None
        assert before == after

    def test_idempotent(self, tmp_company_store):
        with patch("terminal.company_store.get_store", return_value=tmp_company_store):
            ensure_tracked("NEWCO")
            ensure_tracked("NEWCO")
        assert tmp_company_store.get_watchlist().count("NEWCO") == 1

    def test_cloud_returns_false_without_raising(self, tmp_company_store, monkeypatch):
        monkeypatch.setattr("config.settings.IS_CLOUD", True)
        with patch("terminal.company_store.get_store", return_value=tmp_company_store):
            result = ensure_tracked("NEWCO")
        assert result is False
        monkeypatch.setattr("config.settings.IS_CLOUD", False)
        assert tmp_company_store.get_watchlist() == []
