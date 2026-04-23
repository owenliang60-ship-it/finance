from unittest.mock import MagicMock

from portfolio.holdings.live_quote_provider import (
    QuoteResult,
    fetch_option_live_quotes,
    fetch_stock_live_quotes,
)


def test_stock_quote_success_captures_meta_and_price_field():
    mock_client = MagicMock()
    mock_client.get_stock_quote_with_meta.side_effect = [
        {
            "quote": {"last": 150.5, "mid": 150.4, "bid": 150.3, "ask": 150.5},
            "raw": {"s": "ok", "updated": ["2026-04-22T10:05:00-04:00"]},
            "headers": {"X-Api-Cost": "1", "X-Api-Quota-Remaining": "9999"},
        },
        {
            "quote": {"last": 800.1, "bid": 799.9, "ask": 800.1},
            "raw": {"s": "ok"},
            "headers": {},
        },
    ]

    result = fetch_stock_live_quotes(["AAPL", "NVDA"], client=mock_client)

    assert isinstance(result, QuoteResult)
    assert result.prices == {"AAPL": 150.4, "NVDA": 800.1}
    assert result.failed == []
    assert result.quote_meta["AAPL"]["price_field"] == "mid"
    assert result.quote_meta["NVDA"]["price_field"] == "last"
    assert result.request_count == 2
    assert result.credit_header_available is True
    assert result.credits_used == "1"
    assert result.credits_remaining == "9999"


def test_stock_quote_failure_records_symbol():
    mock_client = MagicMock()
    mock_client.get_stock_quote_with_meta.return_value = None

    result = fetch_stock_live_quotes(["AAPL"], client=mock_client)

    assert result.prices == {}
    assert result.failed == ["AAPL"]
    assert result.request_count == 1


def test_stock_quote_exception_caught():
    mock_client = MagicMock()
    mock_client.get_stock_quote_with_meta.side_effect = Exception("network err")

    result = fetch_stock_live_quotes(["AAPL"], client=mock_client)

    assert result.prices == {}
    assert result.failed == ["AAPL"]


def test_option_quote_success():
    mock_client = MagicMock()
    mock_client.get_options_quote_with_meta.return_value = {
        "quote": {"mid": 2.50, "last": 2.55, "bid": 2.45, "ask": 2.55},
        "raw": {"s": "ok", "updated": ["2026-04-22T10:05:00-04:00"]},
        "headers": {"X-Api-Cost": "1"},
    }
    positions = [{
        "symbol": "AAPL",
        "expiration": "2026-03-21",
        "strike": 200.0,
        "side": "CALL",
    }]

    result = fetch_option_live_quotes(positions, client=mock_client)

    key = ("AAPL", "2026-03-21", 200.0, "CALL")
    assert result.prices[key] == 2.50
    assert result.failed == []
    assert result.quote_meta[key]["price_field"] == "mid"
    assert result.quote_meta[key]["occ"] == "AAPL260321C00200000"
    mock_client.get_options_quote_with_meta.assert_called_once_with("AAPL260321C00200000")


def test_option_quote_failure_adds_to_failed():
    mock_client = MagicMock()
    mock_client.get_options_quote_with_meta.return_value = None
    positions = [{
        "symbol": "AAPL",
        "expiration": "2026-03-21",
        "strike": 200.0,
        "side": "CALL",
    }]

    result = fetch_option_live_quotes(positions, client=mock_client)

    assert result.prices == {}
    assert len(result.failed) == 1


def test_option_quote_invalid_occ_caught():
    mock_client = MagicMock()
    positions = [{
        "symbol": "AAPL",
        "expiration": "bad-date",
        "strike": 200.0,
        "side": "CALL",
    }]

    result = fetch_option_live_quotes(positions, client=mock_client)

    assert result.prices == {}
    assert len(result.failed) == 1
    mock_client.get_options_quote_with_meta.assert_not_called()
