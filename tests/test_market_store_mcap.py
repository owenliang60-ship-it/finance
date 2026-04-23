"""historical_market_cap 表的 CRUD 测试"""
import pytest
from src.data.market_store import MarketStore


@pytest.fixture
def store(tmp_path):
    return MarketStore(tmp_path / "test.db")


def test_upsert_and_query_mcap(store):
    rows = [
        {"symbol": "AAPL", "date": "2024-01-02", "market_cap": 3_000_000_000_000},
        {"symbol": "AAPL", "date": "2024-01-03", "market_cap": 3_050_000_000_000},
    ]
    count = store.upsert_historical_market_cap("AAPL", rows)
    assert count == 2

    cap = store.get_market_cap_at("AAPL", "2024-01-02")
    assert cap == 3_000_000_000_000


def test_get_market_cap_at_returns_nearest_before(store):
    """非交易日应返回最近的前一个交易日市值"""
    rows = [
        {"symbol": "AAPL", "date": "2024-01-02", "market_cap": 3_000_000_000_000},
        {"symbol": "AAPL", "date": "2024-01-05", "market_cap": 3_100_000_000_000},
    ]
    store.upsert_historical_market_cap("AAPL", rows)
    cap = store.get_market_cap_at("AAPL", "2024-01-04")
    assert cap == 3_000_000_000_000


def test_get_market_cap_at_missing_symbol(store):
    cap = store.get_market_cap_at("ZZZZ", "2024-01-02")
    assert cap is None


def test_bulk_market_caps_at_date(store):
    """批量查某日多个 symbol 的市值"""
    store.upsert_historical_market_cap("AAPL", [
        {"symbol": "AAPL", "date": "2024-01-02", "market_cap": 3_000_000_000_000},
    ])
    store.upsert_historical_market_cap("MSFT", [
        {"symbol": "MSFT", "date": "2024-01-02", "market_cap": 2_800_000_000_000},
    ])
    result = store.get_bulk_market_caps_at("2024-01-02")
    assert result["AAPL"] == 3_000_000_000_000
    assert result["MSFT"] == 2_800_000_000_000


def test_upsert_idempotent(store):
    rows = [{"symbol": "AAPL", "date": "2024-01-02", "market_cap": 3_000_000_000_000}]
    store.upsert_historical_market_cap("AAPL", rows)
    rows[0]["market_cap"] = 3_100_000_000_000
    store.upsert_historical_market_cap("AAPL", rows)
    cap = store.get_market_cap_at("AAPL", "2024-01-02")
    assert cap == 3_100_000_000_000


def test_list_symbols_in_historical_market_cap(store):
    store.upsert_historical_market_cap(
        "AAPL", [{"symbol": "AAPL", "date": "2024-01-02", "market_cap": 1}]
    )
    store.upsert_historical_market_cap(
        "MSFT", [{"symbol": "MSFT", "date": "2024-01-02", "market_cap": 1}]
    )

    assert store.list_symbols_in_historical_market_cap() == ["AAPL", "MSFT"]


def test_get_symbols_with_market_cap_at_uses_latest_as_of_snapshot(store):
    store.upsert_historical_market_cap(
        "AAPL",
        [
            {"symbol": "AAPL", "date": "2024-01-02", "market_cap": 3_000_000_000},
            {"symbol": "AAPL", "date": "2024-06-01", "market_cap": 500_000_000},
        ],
    )
    store.upsert_historical_market_cap(
        "MSFT",
        [{"symbol": "MSFT", "date": "2024-06-01", "market_cap": 2_000_000_000}],
    )

    result = store.get_symbols_with_market_cap_at("2024-07-01", 1_000_000_000, freshness_days=90)

    assert result == ["MSFT"]


def test_get_symbols_with_market_cap_at_excludes_stale_rows(store):
    store.upsert_historical_market_cap(
        "OLD",
        [{"symbol": "OLD", "date": "2024-01-02", "market_cap": 3_000_000_000}],
    )
    store.upsert_historical_market_cap(
        "NEW",
        [{"symbol": "NEW", "date": "2024-06-15", "market_cap": 3_000_000_000}],
    )

    result = store.get_symbols_with_market_cap_at("2024-07-01", 1_000_000_000, freshness_days=30)

    assert result == ["NEW"]
