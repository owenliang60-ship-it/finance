"""Tests for T11 — event-driven `--fundamental` targets + all scopes through
the kernel (R6).

Two things under test:
  - `detect_earnings_targets` (src/data/fundamental_events.py): the
    earnings-window ∩ SM-eligible symbol picker for `--scope events`.
  - `run_fundamental_update` (scripts/update_data.py): the scope-agnostic
    driver that now routes `--scope core/base/events` through the T8 kernel
    instead of the legacy `update_all_fundamentals` direct-write path.
"""
import sqlite3
import tempfile
from pathlib import Path

import pytest

from src.data.fundamental_events import detect_earnings_targets
from src.data.market_store import MarketStore
from scripts.update_data import run_fundamental_update


# ---------------------------------------------------------------------------
# Fixtures / fake provider (mirrors tests/test_fundamental_collector.py)
# ---------------------------------------------------------------------------

def _income_rows():
    return [
        {"date": "2026-06-30", "symbol": "AAPL", "reportedCurrency": "USD",
         "filingDate": "2026-08-01", "acceptedDate": "2026-08-01 16:30:00",
         "fiscalYear": "2026", "period": "Q3", "revenue": 94036000000.0,
         "netIncome": 21448000000.0, "eps": 1.4},
        {"date": "2026-03-31", "symbol": "AAPL", "reportedCurrency": "USD",
         "filingDate": "2026-05-02", "acceptedDate": "2026-05-02 16:30:00",
         "fiscalYear": "2026", "period": "Q2", "revenue": 90753000000.0,
         "netIncome": 23636000000.0, "eps": 1.53},
    ]


def _full_responses():
    return {
        "profile": ([{"symbol": "AAPL", "companyName": "Apple Inc.",
                      "sector": "Technology", "isEtf": False, "isFund": False,
                      "isAdr": False}], "ok"),
        "income": (_income_rows(), "ok"),
        "balance": ([{"date": "2026-06-30", "symbol": "AAPL",
                      "totalAssets": 331612000000.0}], "ok"),
        "cashflow": ([{"date": "2026-06-30", "symbol": "AAPL",
                       "operatingCashFlow": 28858000000.0}], "ok"),
        "ratios": ([{"date": "2025-09-30", "symbol": "AAPL", "period": "FY",
                     "grossProfitMargin": 0.462}], "ok"),
    }


class FakeFMPClient:
    """Stands in for FMPClient.get_dataset_with_status (T4 interface)."""

    def __init__(self, responses):
        self.responses = responses
        self.calls = []

    def get_dataset_with_status(self, kind, symbol, limit=None):
        self.calls.append({"kind": kind, "symbol": symbol, "limit": limit})
        rows, status = self.responses[kind]
        return [dict(r) for r in rows], status


@pytest.fixture
def tmp_store(tmp_path):
    store = MarketStore(db_path=tmp_path / "test_market.db")
    yield store
    store.close()


@pytest.fixture
def fake_client_full():
    return FakeFMPClient(_full_responses())


def _sm_row(symbol, eligible=1, reason="ok", is_etf=0):
    return {
        "symbol": symbol, "cik": None, "company_name": symbol + " Inc.",
        "exchange": "NASDAQ", "is_etf": is_etf, "is_fund": 0, "is_adr": 0,
        "share_class_of": None, "eligible": eligible, "reason": reason,
        "updated_at": "2026-08-19T00:00:00Z",
    }


@pytest.fixture
def tmp_store_sm3(tmp_store):
    """AAPL/MSFT eligible; SOXX etf/blocked — three SM identities, no
    membership required (events targeting filters by SM eligibility alone,
    not `current_base_universe`)."""
    tmp_store.upsert_security_master([
        _sm_row("AAPL"),
        _sm_row("MSFT"),
        _sm_row("SOXX", eligible=0, reason="etf", is_etf=1),
    ])
    return tmp_store


def _seed_earnings(store, symbol, announce):
    store.replace_fmp_earnings(symbol, [{"announce_date": announce}])


def _dump_income(store, symbol):
    return store.get_income(symbol, limit=8)


def _legacy_update_income(store, symbol, client):
    """Old direct-write path: fetch + `MarketStore.upsert_income` (the
    `_bulk_upsert` -> `_prepare_upsert_rows` writer `fundamental_fetcher.
    update_income` used before the kernel existed). Baseline for the T11
    parity contract — both paths funnel through the same row-prep helper
    (T8), so this asserts that guarantee end-to-end rather than re-deriving
    it from the source."""
    rows, status = client.get_dataset_with_status("income", symbol, limit=8)
    if status == "ok" and rows:
        store.upsert_income(symbol, rows)


def _fresh_store():
    tmp_dir = tempfile.mkdtemp()
    return MarketStore(db_path=Path(tmp_dir) / "legacy_market.db")


# ---------------------------------------------------------------------------
# Brief Step 1 — mandatory tests
# ---------------------------------------------------------------------------

def test_events_window_filters(tmp_store_sm3):
    _seed_earnings(tmp_store_sm3, "AAPL", announce="2026-08-20")
    _seed_earnings(tmp_store_sm3, "MSFT", announce="2026-05-01")
    assert detect_earnings_targets(tmp_store_sm3, window_days=8, as_of="2026-08-24") == ["AAPL"]


def test_events_excludes_ineligible(tmp_store_sm3):
    _seed_earnings(tmp_store_sm3, "SOXX", announce="2026-08-20")   # SOXX 在 SM 里是 etf/blocked
    assert "SOXX" not in detect_earnings_targets(tmp_store_sm3, window_days=8, as_of="2026-08-24")


def test_scope_core_kernel_parity_with_legacy(tmp_store, fake_client_full):
    run_fundamental_update(scope="core", symbols=["AAPL"], store=tmp_store, client=fake_client_full)
    rows_kernel = _dump_income(tmp_store, "AAPL")

    legacy_store = _fresh_store()
    try:
        _legacy_update_income(legacy_store, "AAPL", fake_client_full)   # 旧路径基准
        assert rows_kernel == _dump_income(legacy_store, "AAPL")
    finally:
        legacy_store.close()


def test_scope_events_zero_targets_exit0(tmp_store_sm3, fake_client_full):
    assert run_fundamental_update(scope="events", store=tmp_store_sm3,
                                  client=fake_client_full, as_of="2026-08-24") == 0
