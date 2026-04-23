"""Tests for src.data.broad_universe_manager."""

import json
from pathlib import Path

import pytest

from src.data.market_store import MarketStore


@pytest.fixture
def manager_env(tmp_path, monkeypatch):
    import src.data.broad_universe_manager as bum

    db_path = tmp_path / "market.db"
    seed_path = tmp_path / "pool" / "broad_universe_seed.json"
    final_path = tmp_path / "pool" / "broad_universe.json"
    scans_dir = tmp_path / "scans"
    scans_dir.mkdir(parents=True, exist_ok=True)

    monkeypatch.setattr(bum, "MARKET_DB_PATH", db_path)
    monkeypatch.setattr(bum, "BROAD_UNIVERSE_SEED_FILE", seed_path)
    monkeypatch.setattr(bum, "BROAD_UNIVERSE_FILE", final_path)
    monkeypatch.setattr(bum, "SCANS_DIR", scans_dir)
    monkeypatch.setattr(bum, "BROAD_UNIVERSE_SEED_MIN_COUNT", 1)
    monkeypatch.setattr(bum, "BROAD_UNIVERSE_SEED_MAX_COUNT", 20)
    monkeypatch.setattr(bum, "BROAD_UNIVERSE_MIN_COUNT", 1)
    monkeypatch.setattr(bum, "BROAD_UNIVERSE_MAX_COUNT", 20)

    store = MarketStore(db_path)
    yield bum, store, scans_dir
    store.close()


def test_build_seed_dedupes_across_sources(manager_env, monkeypatch):
    bum, store, scans_dir = manager_env
    monkeypatch.setattr(bum, "_fetch_yfscreen_dedup", lambda _min_mcap: {"AAPL", "MSFT"})
    scans_dir.joinpath("broad_scan_tracker.json").write_text(json.dumps({"NVDA": {}, "_meta": {}}))
    store.upsert_daily_prices(
        "MSFT",
        [{"date": "2024-01-02", "close": 100, "open": 100, "high": 100, "low": 100, "volume": 1}],
    )
    store.upsert_daily_prices(
        "TSLA",
        [{"date": "2024-01-02", "close": 100, "open": 100, "high": 100, "low": 100, "volume": 1}],
    )

    symbols = bum.build_over_inclusive_seed()

    assert symbols == ["AAPL", "MSFT", "NVDA", "TSLA"]
    payload = json.loads(bum.BROAD_UNIVERSE_SEED_FILE.read_text())
    assert payload["source_breakdown"]["existing_price"] == 1
    assert payload["source_breakdown"]["broadscan"] == 1


def test_finalize_filters_by_max_historical_mcap(manager_env, monkeypatch):
    bum, store, _ = manager_env
    monkeypatch.setattr(bum, "_fetch_yfscreen_dedup", lambda _min_mcap: {"AAPL", "MSFT", "SMCI"})
    bum.build_over_inclusive_seed()
    store.upsert_historical_market_cap(
        "AAPL",
        [{"date": "2024-01-02", "market_cap": 2_000_000_000}],
    )
    store.upsert_historical_market_cap(
        "MSFT",
        [{"date": "2024-01-02", "market_cap": 900_000_000}],
    )
    store.upsert_historical_market_cap(
        "SMCI",
        [{"date": "2024-01-02", "market_cap": 5_000_000_000}],
    )

    result = bum.finalize_broad_universe(min_mcap_usd=1_000_000_000)

    assert result["symbols"] == ["AAPL", "SMCI"]
    assert result["metadata"]["AAPL"]["max_hist_mcap_usd"] == 2_000_000_000


def test_get_new_symbols_vs_price(manager_env, monkeypatch):
    bum, store, _ = manager_env
    monkeypatch.setattr(bum, "_PRICE_SUFFICIENT_ROWS", 1)
    monkeypatch.setattr(bum, "_fetch_yfscreen_dedup", lambda _min_mcap: {"AAPL", "MSFT"})
    bum.build_over_inclusive_seed()
    store.upsert_historical_market_cap("AAPL", [{"date": "2024-01-02", "market_cap": 2_000_000_000}])
    store.upsert_historical_market_cap("MSFT", [{"date": "2024-01-02", "market_cap": 2_000_000_000}])
    bum.finalize_broad_universe()
    store.upsert_daily_prices(
        "AAPL",
        [{"date": "2024-01-02", "close": 100, "open": 100, "high": 100, "low": 100, "volume": 1}],
    )

    assert bum.get_new_symbols_vs_price() == ["MSFT"]


def test_get_new_symbols_vs_price_treats_partial_history_as_new(manager_env, monkeypatch):
    bum, store, _ = manager_env
    monkeypatch.setattr(bum, "_PRICE_SUFFICIENT_ROWS", 2)
    monkeypatch.setattr(bum, "_fetch_yfscreen_dedup", lambda _min_mcap: {"AAPL", "MSFT"})
    bum.build_over_inclusive_seed()
    store.upsert_historical_market_cap("AAPL", [{"date": "2024-01-02", "market_cap": 2_000_000_000}])
    store.upsert_historical_market_cap("MSFT", [{"date": "2024-01-02", "market_cap": 2_000_000_000}])
    bum.finalize_broad_universe()
    store.upsert_daily_prices(
        "AAPL",
        [{"date": "2024-01-02", "close": 100, "open": 100, "high": 100, "low": 100, "volume": 1}],
    )
    store.upsert_daily_prices(
        "MSFT",
        [
            {"date": "2024-01-02", "close": 100, "open": 100, "high": 100, "low": 100, "volume": 1},
            {"date": "2024-01-03", "close": 101, "open": 101, "high": 101, "low": 101, "volume": 1},
        ],
    )

    assert bum.get_new_symbols_vs_price() == ["AAPL"]
