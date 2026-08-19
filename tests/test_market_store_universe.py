"""Tests for MarketStore universe storage tables + transaction boundary (T1)."""
import pytest, sqlite3

from src.data.market_store import MarketStore


@pytest.fixture
def tmp_store(tmp_path):
    """Create a fresh MarketStore backed by a temp DB."""
    db_path = tmp_path / "test_market.db"
    s = MarketStore(db_path=db_path)
    yield s
    s.close()


NEW_TABLES = ["security_master", "extended_membership", "coverage_status",
              "company_profile", "fundamental_vintage",
              "fundamental_backfill_runs", "fundamental_backfill_jobs"]

def test_new_tables_created(tmp_store):
    conn = sqlite3.connect(tmp_store.db_path)
    names = {r[0] for r in conn.execute("select name from sqlite_master where type='table'")}
    assert set(NEW_TABLES) <= names

def test_transaction_rolls_back_multi_table_on_error(tmp_store):
    with pytest.raises(RuntimeError):
        with tmp_store.transaction() as conn:
            conn.execute("insert into coverage_status(symbol, dataset, status, detail, updated_at) "
                         "values ('A','income_quarterly','ok',NULL,'2026-08-20')")   # 具名列（表共 9 列）
            conn.execute("insert into company_profile(symbol, payload, updated_at) "
                         "values ('A','{}','2026-08-20')")
            raise RuntimeError("boom")
    conn = sqlite3.connect(tmp_store.db_path)
    assert conn.execute("select count(*) from coverage_status").fetchone()[0] == 0
    assert conn.execute("select count(*) from company_profile").fetchone()[0] == 0
