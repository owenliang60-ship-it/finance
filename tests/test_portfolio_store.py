"""Tests for CompanyStore kill conditions + checkpoint (ledger tables retired)."""
import pytest
from terminal.company_store import CompanyStore


@pytest.fixture
def store(tmp_path):
    db_path = tmp_path / "test_company.db"
    s = CompanyStore(db_path=db_path)
    # Seed a company for FK
    s.upsert_company("NVDA", company_name="NVIDIA", sector="Technology")
    yield s
    s.close()


class TestKillConditionsMigration:
    def test_save_reads_from_sqlite(self, store):
        """company_db facade should read from SQLite after migration."""
        from unittest.mock import patch
        import terminal.company_db as cdb

        # Seed company
        store.upsert_company("AAPL", company_name="Apple")

        # Save via CompanyStore (SQLite)
        store.save_kill_conditions("AAPL", [
            {"description": "Revenue < $90B", "source_lens": "fundamental"},
        ])

        # company_db.get_kill_conditions should return SQLite data
        with patch.object(cdb, "_get_store", return_value=store):
            conditions = cdb.get_kill_conditions("AAPL")
            assert len(conditions) >= 1
            assert any("Revenue" in c.get("description", "") for c in conditions)

    def test_save_via_facade_roundtrip(self, store, tmp_path):
        """save via company_db facade writes to SQLite and reads back."""
        from unittest.mock import patch
        import terminal.company_db as cdb

        store.upsert_company("MSFT", company_name="Microsoft")

        with patch.object(cdb, "_get_store", return_value=store), \
             patch.object(cdb, "_COMPANIES_DIR", tmp_path):
            cdb.save_kill_conditions("MSFT", [
                {"description": "Azure growth < 20%", "metric": "cloud"},
            ])
            conditions = cdb.get_kill_conditions("MSFT")
            assert len(conditions) >= 1
            assert any("Azure" in c.get("description", "") for c in conditions)


class TestCheckpoint:
    def test_checkpoint_company_db(self, store):
        """checkpoint should not raise."""
        store.checkpoint()  # No error = pass
