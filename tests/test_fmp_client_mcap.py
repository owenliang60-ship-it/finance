"""FMP historical market cap 方法测试（mock HTTP）"""
import pytest
from unittest.mock import patch
from src.data.fmp_client import FMPClient


@pytest.fixture
def client():
    return FMPClient(api_key="test_key")


def test_get_historical_market_cap_success(client):
    mock_data = [
        {"symbol": "AAPL", "date": "2024-01-02", "marketCap": 3000000000000},
        {"symbol": "AAPL", "date": "2024-01-03", "marketCap": 3050000000000},
    ]
    with patch.object(client, '_request', return_value=mock_data):
        result = client.get_historical_market_cap("AAPL", "2024-01-01", "2024-01-10")
    assert len(result) == 2
    assert result[0]["market_cap"] == 3000000000000
    assert result[0]["date"] == "2024-01-02"
    assert result[0]["symbol"] == "AAPL"


def test_get_historical_market_cap_empty(client):
    with patch.object(client, '_request', return_value=[]):
        result = client.get_historical_market_cap("ZZZZ", "2024-01-01", "2024-01-10")
    assert result == []


def test_get_historical_market_cap_none_response(client):
    with patch.object(client, '_request', return_value=None):
        result = client.get_historical_market_cap("AAPL", "2024-01-01", "2024-01-10")
    assert result == []


def test_get_historical_market_cap_calls_stable_endpoint(client):
    """确认调用的是 stable 端点路径，symbol 作为 query param"""
    with patch.object(client, '_request', return_value=[]) as mock_req:
        client.get_historical_market_cap("AAPL", "2024-01-01", "2024-12-31")
    mock_req.assert_called_once_with(
        "historical-market-capitalization",
        {"symbol": "AAPL", "from": "2024-01-01", "to": "2024-12-31"},
    )


# === Phase A1: screener limit truncation regression tests ===


def test_get_large_cap_stocks_passes_default_limit():
    """A1 regression: default limit must be 5000 to cover full $10B+ universe."""
    from src.data.fmp_client import FMPClient, SCREENER_DEFAULT_LIMIT
    client = FMPClient(api_key="fake")
    with patch.object(client, "_request", return_value=[]) as m:
        client.get_large_cap_stocks(market_cap_threshold=10_000_000_000)
    assert SCREENER_DEFAULT_LIMIT == 5000, \
        "anchor: 5000 covers ~2.8x of $10B+ universe (1797 as of 2026-05-21)"
    called_params = m.call_args[0][1]
    assert called_params["limit"] == 5000, \
        "screener call must explicitly pass limit, else FMP defaults to 1000"
