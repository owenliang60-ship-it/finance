"""Tests for auto pool admission — analyzing a ticker outside the pool
automatically adds it to the pool and caches its fundamental data.

Tests:
1. ensure_in_pool(): adds missing ticker via FMP profile
2. ensure_in_pool(): skips if already in pool
3. ensure_fundamentals_cached(): fetches and caches all 5 data types
4. ensure_fundamentals_cached(): skips if already cached
5. collect_data() integration: auto-admits before data collection
6. ensure_in_pool(): handles FMP API failure gracefully
7. Pool history records auto-admission
"""
import json
import pytest
from datetime import datetime
from pathlib import Path
from unittest import mock

from src.data.pool_manager import (
    ensure_in_pool,
    get_stock_info,
    load_universe,
    save_universe,
    load_history,
    save_history,
    UNIVERSE_FILE,
    HISTORY_FILE,
)
from src.data.fundamental_fetcher import (
    ensure_fundamentals_cached,
    get_profile,
    get_ratios,
    get_income,
    get_balance_sheet,
    get_cash_flow,
    PROFILES_FILE,
    RATIOS_FILE,
    INCOME_FILE,
    BALANCE_SHEET_FILE,
    CASH_FLOW_FILE,
)


# ---------------------------------------------------------------------------
# Sample data
# ---------------------------------------------------------------------------

SAMPLE_PROFILE = {
    "symbol": "VRT",
    "companyName": "Vertiv Holdings Co",
    "mktCap": 45_000_000_000,
    "sector": "Industrials",
    "industry": "Electrical Equipment & Parts",
    "exchangeShortName": "NYSE",
    "country": "US",
    "ceo": "Giordano Albertazzi",
    "description": "Vertiv Holdings Co designs and builds digital infrastructure.",
    "beta": 1.8,
    "price": 120.0,
    "website": "https://www.vertiv.com",
    "ipoDate": "2020-02-07",
}

SAMPLE_RATIOS = [
    {"period": "Q3", "priceEarningsRatio": 55.0, "returnOnEquity": 0.35},
    {"period": "Q2", "priceEarningsRatio": 48.0, "returnOnEquity": 0.30},
]

SAMPLE_INCOME = [
    {"date": "2025-09-30", "revenue": 2_100_000_000, "netIncome": 250_000_000, "eps": 0.95},
]

SAMPLE_BALANCE_SHEET = [
    {"date": "2025-09-30", "totalAssets": 12_000_000_000},
]

SAMPLE_CASH_FLOW = [
    {"date": "2025-09-30", "operatingCashFlow": 400_000_000},
]

EXISTING_POOL = [
    {
        "symbol": "AAPL",
        "companyName": "Apple Inc",
        "marketCap": 3_500_000_000_000,
        "sector": "Technology",
        "industry": "Consumer Electronics",
        "exchange": "NASDAQ",
    },
    {
        "symbol": "NVDA",
        "companyName": "NVIDIA Corporation",
        "marketCap": 3_200_000_000_000,
        "sector": "Technology",
        "industry": "Semiconductors",
        "exchange": "NASDAQ",
    },
]


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _isolate_pool(tmp_path, monkeypatch):
    """Redirect pool and fundamental files to tmp_path."""
    pool_dir = tmp_path / "pool"
    pool_dir.mkdir()
    fund_dir = tmp_path / "fundamental"
    fund_dir.mkdir()

    # Pool files
    universe = pool_dir / "universe.json"
    history = pool_dir / "pool_history.json"
    universe.write_text(json.dumps(EXISTING_POOL))
    history.write_text("[]")

    monkeypatch.setattr("src.data.pool_manager.UNIVERSE_FILE", universe)
    monkeypatch.setattr("src.data.pool_manager.HISTORY_FILE", history)
    monkeypatch.setattr("src.data.pool_manager.POOL_DIR", pool_dir)

    # Fundamental files
    for fname in ["profiles.json", "ratios.json", "income.json",
                   "balance_sheet.json", "cash_flow.json"]:
        (fund_dir / fname).write_text("{}")

    monkeypatch.setattr("src.data.fundamental_fetcher.PROFILES_FILE", fund_dir / "profiles.json")
    monkeypatch.setattr("src.data.fundamental_fetcher.RATIOS_FILE", fund_dir / "ratios.json")
    monkeypatch.setattr("src.data.fundamental_fetcher.INCOME_FILE", fund_dir / "income.json")
    monkeypatch.setattr("src.data.fundamental_fetcher.BALANCE_SHEET_FILE", fund_dir / "balance_sheet.json")
    monkeypatch.setattr("src.data.fundamental_fetcher.CASH_FLOW_FILE", fund_dir / "cash_flow.json")


# ---------------------------------------------------------------------------
# Tests: ensure_in_pool
# ---------------------------------------------------------------------------

class TestEnsureInPool:
    """Tests for pool_manager.ensure_in_pool()."""

    def test_adds_missing_ticker(self):
        """Ticker not in pool → FMP fetch → added to universe.json."""
        with mock.patch("src.data.pool_manager.fmp_client") as mock_fmp:
            mock_fmp.get_profile.return_value = SAMPLE_PROFILE

            result = ensure_in_pool("VRT")

        assert result["symbol"] == "VRT"
        assert result["companyName"] == "Vertiv Holdings Co"
        assert result["source"] == "analysis"
        assert result["marketCap"] == 45_000_000_000

        # Verify in universe
        pool = load_universe()
        symbols = [s["symbol"] for s in pool]
        assert "VRT" in symbols
        assert len(pool) == 3  # AAPL + NVDA + VRT

    def test_skips_existing_ticker(self):
        """Ticker already in pool → no API call, returns existing info."""
        with mock.patch("src.data.pool_manager.fmp_client") as mock_fmp:
            result = ensure_in_pool("AAPL")

        mock_fmp.get_profile.assert_not_called()
        assert result["symbol"] == "AAPL"
        assert result["companyName"] == "Apple Inc"

    def test_case_insensitive(self):
        """Lowercase ticker gets uppercased."""
        with mock.patch("src.data.pool_manager.fmp_client") as mock_fmp:
            mock_fmp.get_profile.return_value = SAMPLE_PROFILE
            result = ensure_in_pool("vrt")

        assert result["symbol"] == "VRT"

    def test_api_failure_returns_empty(self):
        """FMP API returns None → empty dict, pool unchanged."""
        with mock.patch("src.data.pool_manager.fmp_client") as mock_fmp:
            mock_fmp.get_profile.return_value = None
            result = ensure_in_pool("VRT")

        assert result == {}
        pool = load_universe()
        assert len(pool) == 2  # unchanged

    def test_records_history(self):
        """Auto-admission records entry in pool history."""
        with mock.patch("src.data.pool_manager.fmp_client") as mock_fmp:
            mock_fmp.get_profile.return_value = SAMPLE_PROFILE
            ensure_in_pool("VRT")

        history = load_history()
        assert len(history) == 1
        assert history[0]["entered"] == ["VRT"]
        assert history[0]["reason"] == "auto-admitted via analysis"


# ---------------------------------------------------------------------------
# Tests: ensure_fundamentals_cached
# ---------------------------------------------------------------------------

class TestEnsureFundamentalsCached:
    """Tests for fundamental_fetcher.ensure_fundamentals_cached()."""

    def test_fetches_all_when_empty(self):
        """All 5 data types fetched and cached when empty."""
        with mock.patch("src.data.fundamental_fetcher.fetch_profile", return_value=SAMPLE_PROFILE), \
             mock.patch("src.data.fundamental_fetcher.fetch_ratios", return_value=SAMPLE_RATIOS), \
             mock.patch("src.data.fundamental_fetcher.fetch_income", return_value=SAMPLE_INCOME), \
             mock.patch("src.data.fundamental_fetcher.fetch_balance_sheet", return_value=SAMPLE_BALANCE_SHEET), \
             mock.patch("src.data.fundamental_fetcher.fetch_cash_flow", return_value=SAMPLE_CASH_FLOW):

            result = ensure_fundamentals_cached("VRT")

        assert result is True
        assert get_profile("VRT") is not None
        assert len(get_ratios("VRT")) == 2
        assert len(get_income("VRT")) == 1
        assert len(get_balance_sheet("VRT")) == 1
        assert len(get_cash_flow("VRT")) == 1

    def test_skips_already_cached(self):
        """If data already cached, no fetch calls made."""
        # Pre-populate cache via module-level references (monkeypatched paths)
        import src.data.fundamental_fetcher as ff
        ff._save_json(ff.PROFILES_FILE, {"VRT": SAMPLE_PROFILE})
        ff._save_json(ff.RATIOS_FILE, {"VRT": SAMPLE_RATIOS})
        ff._save_json(ff.INCOME_FILE, {"VRT": SAMPLE_INCOME})
        ff._save_json(ff.BALANCE_SHEET_FILE, {"VRT": SAMPLE_BALANCE_SHEET})
        ff._save_json(ff.CASH_FLOW_FILE, {"VRT": SAMPLE_CASH_FLOW})

        with mock.patch("src.data.fundamental_fetcher.fetch_profile") as mock_prof, \
             mock.patch("src.data.fundamental_fetcher.fetch_ratios") as mock_rat, \
             mock.patch("src.data.fundamental_fetcher.fetch_income") as mock_inc, \
             mock.patch("src.data.fundamental_fetcher.fetch_balance_sheet") as mock_bs, \
             mock.patch("src.data.fundamental_fetcher.fetch_cash_flow") as mock_cf:

            result = ensure_fundamentals_cached("VRT")

        # None of the fetch functions should be called
        mock_prof.assert_not_called()
        mock_rat.assert_not_called()
        mock_inc.assert_not_called()
        mock_bs.assert_not_called()
        mock_cf.assert_not_called()

    def test_partial_cache_fills_gaps(self):
        """Only missing data types are fetched."""
        # Pre-populate only profile
        from src.data.fundamental_fetcher import _save_json, PROFILES_FILE
        _save_json(PROFILES_FILE, {"VRT": SAMPLE_PROFILE})

        with mock.patch("src.data.fundamental_fetcher.fetch_profile") as mock_prof, \
             mock.patch("src.data.fundamental_fetcher.fetch_ratios", return_value=SAMPLE_RATIOS), \
             mock.patch("src.data.fundamental_fetcher.fetch_income", return_value=SAMPLE_INCOME), \
             mock.patch("src.data.fundamental_fetcher.fetch_balance_sheet", return_value=SAMPLE_BALANCE_SHEET), \
             mock.patch("src.data.fundamental_fetcher.fetch_cash_flow", return_value=SAMPLE_CASH_FLOW):

            ensure_fundamentals_cached("VRT")

        # Profile should NOT be fetched (already cached)
        mock_prof.assert_not_called()
        # Others should be fetched
        assert len(get_ratios("VRT")) == 2


# ---------------------------------------------------------------------------
# Tests: collect_data integration
# ---------------------------------------------------------------------------

class TestCollectDataAutoAdmit:
    """Integration test: collect_data() auto-admits non-pool tickers."""

    def test_auto_admits_in_collect_data(self):
        """collect_data() calls ensure_in_pool + ensure_fundamentals_cached."""
        with mock.patch("src.data.pool_manager.fmp_client") as mock_fmp, \
             mock.patch("src.data.fundamental_fetcher.fetch_profile", return_value=SAMPLE_PROFILE), \
             mock.patch("src.data.fundamental_fetcher.fetch_ratios", return_value=SAMPLE_RATIOS), \
             mock.patch("src.data.fundamental_fetcher.fetch_income", return_value=SAMPLE_INCOME), \
             mock.patch("src.data.fundamental_fetcher.fetch_balance_sheet", return_value=SAMPLE_BALANCE_SHEET), \
             mock.patch("src.data.fundamental_fetcher.fetch_cash_flow", return_value=SAMPLE_CASH_FLOW), \
             mock.patch("src.data.price_fetcher.fmp_client") as mock_price_fmp, \
             mock.patch("terminal.macro_fetcher.get_macro_snapshot", return_value=None):

            mock_fmp.get_profile.return_value = SAMPLE_PROFILE
            mock_price_fmp.get_historical_price.return_value = [
                {"date": "2026-02-07", "open": 119, "high": 121, "low": 118,
                 "close": 120, "volume": 5_000_000, "change": 1.0, "changePercent": 0.84},
            ]

            from terminal.pipeline import collect_data
            pkg = collect_data("VRT", price_days=5)

        # VRT should now be in pool
        pool = load_universe()
        symbols = [s["symbol"] for s in pool]
        assert "VRT" in symbols

        # DataPackage should have data
        assert pkg.symbol == "VRT"
