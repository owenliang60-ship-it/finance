"""Tests for CompanyStore options tables (iv_daily + options_snapshots)."""
import pytest
import tempfile
from pathlib import Path
from datetime import datetime, timedelta

from terminal.company_store import CompanyStore


@pytest.fixture
def store(tmp_path):
    """Create a fresh CompanyStore with temp DB."""
    db_path = tmp_path / "test_company.db"
    s = CompanyStore(db_path=db_path)
    # Seed a company for FK references
    s.upsert_company("AAPL", company_name="Apple Inc.", sector="Technology")
    s.upsert_company("MSFT", company_name="Microsoft Corp.", sector="Technology")
    return s


class TestIvDaily:
    """Test iv_daily table CRUD."""

    def test_save_and_get_latest(self, store):
        """Should save and retrieve latest IV record."""
        store.save_iv_daily(
            "AAPL", "2026-02-20",
            iv_30d=0.28, iv_60d=0.30, hv_30d=0.22,
            put_call_ratio=0.85, total_volume=150000, total_oi=5000000,
        )

        result = store.get_latest_iv("AAPL")
        assert result is not None
        assert result["symbol"] == "AAPL"
        assert result["date"] == "2026-02-20"
        assert result["iv_30d"] == 0.28
        assert result["hv_30d"] == 0.22
        assert result["put_call_ratio"] == 0.85

    def test_upsert_on_same_date(self, store):
        """Should update existing record on same symbol+date."""
        store.save_iv_daily("AAPL", "2026-02-20", iv_30d=0.28)
        store.save_iv_daily("AAPL", "2026-02-20", iv_30d=0.32)

        result = store.get_latest_iv("AAPL")
        assert result["iv_30d"] == 0.32

        # Should only have one record
        history = store.get_iv_history("AAPL")
        assert len(history) == 1

    def test_get_iv_history_order(self, store):
        """Should return history newest first."""
        for i in range(5):
            store.save_iv_daily(
                "AAPL", "2026-02-{:02d}".format(20 + i),
                iv_30d=0.25 + i * 0.01,
            )

        history = store.get_iv_history("AAPL")
        assert len(history) == 5
        assert history[0]["date"] == "2026-02-24"
        assert history[-1]["date"] == "2026-02-20"

    def test_get_iv_history_limit(self, store):
        """Should respect limit parameter."""
        for i in range(10):
            store.save_iv_daily(
                "AAPL", "2026-02-{:02d}".format(10 + i),
                iv_30d=0.25,
            )

        history = store.get_iv_history("AAPL", limit=3)
        assert len(history) == 3

    def test_symbol_case_insensitive(self, store):
        """Should normalize symbol to uppercase."""
        store.save_iv_daily("aapl", "2026-02-20", iv_30d=0.28)

        result = store.get_latest_iv("AAPL")
        assert result is not None
        assert result["iv_30d"] == 0.28

    def test_no_iv_data(self, store):
        """Should return None/empty for unknown symbol."""
        assert store.get_latest_iv("UNKNOWN") is None
        assert store.get_iv_history("UNKNOWN") == []

    def test_partial_iv_data(self, store):
        """Should handle None fields gracefully."""
        store.save_iv_daily("AAPL", "2026-02-20", iv_30d=0.28)

        result = store.get_latest_iv("AAPL")
        assert result["iv_30d"] == 0.28
        assert result["iv_60d"] is None
        assert result["hv_30d"] is None

    def test_multiple_symbols(self, store):
        """Should keep data separate per symbol."""
        store.save_iv_daily("AAPL", "2026-02-20", iv_30d=0.28)
        store.save_iv_daily("MSFT", "2026-02-20", iv_30d=0.35)

        aapl = store.get_latest_iv("AAPL")
        msft = store.get_latest_iv("MSFT")
        assert aapl["iv_30d"] == 0.28
        assert msft["iv_30d"] == 0.35


class TestOptionsSnapshots:
    """Test options_snapshots table CRUD."""

    def _sample_contracts(self):
        """Create sample option contracts."""
        return [
            {
                "expiration": "2026-03-21",
                "strike": 200.0,
                "side": "call",
                "bid": 8.50,
                "ask": 8.80,
                "mid": 8.65,
                "last": 8.70,
                "volume": 1500,
                "open_interest": 25000,
                "iv": 0.28,
                "delta": 0.55,
                "gamma": 0.03,
                "theta": -0.15,
                "vega": 0.25,
                "dte": 25,
                "in_the_money": True,
                "underlying_price": 202.50,
            },
            {
                "expiration": "2026-03-21",
                "strike": 200.0,
                "side": "put",
                "bid": 6.00,
                "ask": 6.30,
                "mid": 6.15,
                "last": 6.10,
                "volume": 800,
                "open_interest": 18000,
                "iv": 0.30,
                "delta": -0.45,
                "gamma": 0.03,
                "theta": -0.14,
                "vega": 0.24,
                "dte": 25,
                "in_the_money": False,
                "underlying_price": 202.50,
            },
            {
                "expiration": "2026-03-21",
                "strike": 210.0,
                "side": "call",
                "bid": 4.00,
                "ask": 4.30,
                "mid": 4.15,
                "last": 4.10,
                "volume": 2000,
                "open_interest": 30000,
                "iv": 0.32,
                "delta": 0.35,
                "gamma": 0.025,
                "theta": -0.12,
                "vega": 0.22,
                "dte": 25,
                "in_the_money": False,
                "underlying_price": 202.50,
            },
        ]

    def test_save_and_get_snapshot(self, store):
        """Should save contracts and retrieve them."""
        contracts = self._sample_contracts()
        count = store.save_options_snapshot("AAPL", "2026-02-24", contracts)
        assert count == 3

        result = store.get_options_snapshot("AAPL", "2026-02-24")
        assert len(result) == 3

    def test_get_snapshot_latest(self, store):
        """Should return latest snapshot when date not specified."""
        contracts = self._sample_contracts()
        store.save_options_snapshot("AAPL", "2026-02-23", contracts)
        store.save_options_snapshot("AAPL", "2026-02-24", contracts)

        result = store.get_options_snapshot("AAPL")
        assert len(result) == 3
        assert all(r["snapshot_date"] == "2026-02-24" for r in result)

    def test_filter_by_expiration(self, store):
        """Should filter by expiration date."""
        contracts = self._sample_contracts()
        # Add a contract with different expiration
        contracts.append({
            "expiration": "2026-04-17",
            "strike": 200.0,
            "side": "call",
            "bid": 12.00,
            "ask": 12.50,
            "mid": 12.25,
            "dte": 52,
            "underlying_price": 202.50,
        })
        store.save_options_snapshot("AAPL", "2026-02-24", contracts)

        result = store.get_options_snapshot(
            "AAPL", "2026-02-24", expiration="2026-03-21"
        )
        assert len(result) == 3
        assert all(r["expiration"] == "2026-03-21" for r in result)

    def test_filter_by_side(self, store):
        """Should filter by call/put side."""
        contracts = self._sample_contracts()
        store.save_options_snapshot("AAPL", "2026-02-24", contracts)

        calls = store.get_options_snapshot("AAPL", "2026-02-24", side="call")
        puts = store.get_options_snapshot("AAPL", "2026-02-24", side="put")

        assert len(calls) == 2  # Two call contracts
        assert len(puts) == 1   # One put contract
        assert all(r["side"] == "call" for r in calls)
        assert all(r["side"] == "put" for r in puts)

    def test_in_the_money_stored_as_int(self, store):
        """Should store in_the_money as 0/1 integer."""
        contracts = self._sample_contracts()
        store.save_options_snapshot("AAPL", "2026-02-24", contracts)

        result = store.get_options_snapshot("AAPL", "2026-02-24")
        itm_contract = [r for r in result if r["strike"] == 200.0 and r["side"] == "call"][0]
        otm_contract = [r for r in result if r["strike"] == 200.0 and r["side"] == "put"][0]

        assert itm_contract["in_the_money"] == 1
        assert otm_contract["in_the_money"] == 0

    def test_empty_snapshot(self, store):
        """Should return empty list for unknown symbol."""
        result = store.get_options_snapshot("UNKNOWN")
        assert result == []

    def test_cleanup_old_snapshots(self, store):
        """Should delete snapshots older than retain_days."""
        contracts = self._sample_contracts()

        # Save old and recent snapshots
        old_date = (datetime.now() - timedelta(days=10)).strftime("%Y-%m-%d")
        recent_date = datetime.now().strftime("%Y-%m-%d")

        store.save_options_snapshot("AAPL", old_date, contracts)
        store.save_options_snapshot("AAPL", recent_date, contracts)

        deleted = store.cleanup_old_snapshots(retain_days=7)
        assert deleted == 3  # 3 old contracts deleted

        # Recent ones should remain
        result = store.get_options_snapshot("AAPL", recent_date)
        assert len(result) == 3

    def test_cleanup_no_old_snapshots(self, store):
        """Should return 0 when nothing to clean."""
        contracts = self._sample_contracts()
        store.save_options_snapshot("AAPL", "2099-12-31", contracts)

        deleted = store.cleanup_old_snapshots(retain_days=7)
        assert deleted == 0

    def test_order_by_expiration_strike_side(self, store):
        """Results should be ordered by expiration, strike, side."""
        contracts = self._sample_contracts()
        store.save_options_snapshot("AAPL", "2026-02-24", contracts)

        result = store.get_options_snapshot("AAPL", "2026-02-24")
        # All same expiration, so ordered by strike then side
        assert result[0]["strike"] == 200.0
        assert result[0]["side"] == "call"
        assert result[1]["strike"] == 200.0
        assert result[1]["side"] == "put"
        assert result[2]["strike"] == 210.0


class TestSchemaCreation:
    """Test that new tables are created properly."""

    def test_tables_exist(self, store):
        """Both new tables should exist after init."""
        conn = store._get_conn()
        tables = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        assert "iv_daily" in tables
        assert "options_snapshots" in tables

    def test_iv_daily_unique_constraint(self, store):
        """UNIQUE(symbol, date) should prevent duplicates (upsert handles it)."""
        store.save_iv_daily("AAPL", "2026-02-20", iv_30d=0.28)
        store.save_iv_daily("AAPL", "2026-02-20", iv_30d=0.32)

        history = store.get_iv_history("AAPL")
        assert len(history) == 1
        assert history[0]["iv_30d"] == 0.32

    def test_indexes_exist(self, store):
        """Should have proper indexes for performance."""
        conn = store._get_conn()
        indexes = {
            row[1]
            for row in conn.execute("PRAGMA index_list(iv_daily)").fetchall()
        }
        # At least the unique constraint index should exist
        assert len(indexes) >= 1
