"""Tests for terminal.company_store watchlist (T16): add_to_watchlist / get_watchlist.

company.db `watchlist` table is LOCAL-exclusive write (P3 ownership) —
synced to cloud only via the existing `sync_to_cloud.sh --push`. Cloud writes
are forbidden so a stray local push never silently overwrites a cloud-side
addition (R3-m5/R4-P2-1). The table is created lazily by add_to_watchlist()
(NOT part of the base _SCHEMA) so a fresh store has no watchlist table until
first use — see tests/test_overlays.py::test_load_watchlist_missing_table_returns_empty.
"""
import pytest

from terminal.company_store import CompanyStore


@pytest.fixture
def store(tmp_path):
    db_path = tmp_path / "test_company.db"
    s = CompanyStore(db_path=db_path)
    yield s
    s.close()


class TestAddToWatchlist:
    def test_add_and_get(self, store):
        store.add_to_watchlist("NEWCO", source="analysis")
        assert "NEWCO" in store.get_watchlist()

    def test_idempotent(self, store):
        store.add_to_watchlist("NEWCO", source="analysis")
        store.add_to_watchlist("NEWCO", source="analysis")
        assert store.get_watchlist().count("NEWCO") == 1

    def test_case_insensitive(self, store):
        store.add_to_watchlist("newco", source="analysis")
        assert store.get_watchlist() == ["NEWCO"]

    def test_records_source_and_added_at(self, store):
        store.add_to_watchlist("NEWCO", source="analysis", added_at="2026-08-19T00:00:00")
        conn = store._get_conn()
        row = conn.execute(
            "SELECT source, added_at FROM watchlist WHERE symbol='NEWCO'"
        ).fetchone()
        assert row["source"] == "analysis"
        assert row["added_at"] == "2026-08-19T00:00:00"

    def test_cloud_write_forbidden(self, store, monkeypatch):
        monkeypatch.setattr("config.settings.IS_CLOUD", True)
        with pytest.raises(RuntimeError):
            store.add_to_watchlist("NEWCO", source="analysis")
        # Guard must fire before any row is written.
        monkeypatch.setattr("config.settings.IS_CLOUD", False)
        assert store.get_watchlist() == []


class TestGetWatchlist:
    def test_empty_when_table_absent(self, store):
        # fresh store: add_to_watchlist() never called -> table not created yet
        assert store.get_watchlist() == []

    def test_returns_sorted_uppercase(self, store):
        store.add_to_watchlist("tsla")
        store.add_to_watchlist("AAPL")
        assert store.get_watchlist() == ["AAPL", "TSLA"]
