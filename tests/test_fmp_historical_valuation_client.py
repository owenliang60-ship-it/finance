"""FMP historical basket valuation endpoint contracts."""
import json
from pathlib import Path
from unittest.mock import patch

import pytest

from src.data.fmp_client import FMPClient, FMPResponseError


FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def client():
    return FMPClient(api_key="test_key", call_interval=0)


def _fixture(name):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def test_get_fund_disclosure_dates_contract(client):
    data = _fixture("fmp_fund_disclosure_dates_soxx.json")
    with patch.object(client, "_request", return_value=data) as request:
        assert client.get_fund_disclosure_dates("soxx") == data
    request.assert_called_once_with(
        "funds/disclosure-dates", {"symbol": "SOXX"})


def test_get_fund_disclosure_contract(client):
    data = _fixture("fmp_fund_disclosure_soxx_2025q3.json")
    with patch.object(client, "_request", return_value=data) as request:
        assert client.get_fund_disclosure("soxx", 2025, 3) == data
    request.assert_called_once_with(
        "funds/disclosure",
        {"symbol": "SOXX", "year": 2025, "quarter": 3},
    )


@pytest.mark.parametrize(
    "symbol,from_date,to_date,fixture",
    [
        ("eurusd", "2026-07-01", "2026-07-03", "fmp_fx_eurusd_sample.json"),
        ("twdusd", "2026-07-01", "2026-07-03", "fmp_fx_twdusd_sample.json"),
    ],
)
def test_get_historical_fx_contract(client, symbol, from_date, to_date, fixture):
    data = _fixture(fixture)
    with patch.object(client, "_request", return_value=data) as request:
        assert client.get_historical_fx(symbol, from_date, to_date) == data
    request.assert_called_once_with(
        "historical-price-eod/full",
        {"symbol": symbol.upper(), "from": from_date, "to": to_date},
    )


def test_get_stock_splits_contract(client):
    data = _fixture("fmp_splits_klac.json")
    with patch.object(client, "_request", return_value=data) as request:
        assert client.get_stock_splits("klac") == data
    request.assert_called_once_with("splits", {"symbol": "KLAC"})


@pytest.mark.parametrize(
    "method,args",
    [
        ("get_fund_disclosure_dates", ("SOXX",)),
        ("get_fund_disclosure", ("SOXX", 2025, 3)),
        ("get_historical_fx", ("EURUSD", "2026-07-01", "2026-07-03")),
        ("get_stock_splits", ("KLAC",)),
    ],
)
@pytest.mark.parametrize("payload", [None, {"Error Message": "denied"}, "bad"])
def test_historical_methods_reject_non_list_payload(client, method, args, payload):
    with patch.object(client, "_request", return_value=payload):
        with pytest.raises(FMPResponseError) as error:
            getattr(client, method)(*args)
    message = str(error.value)
    assert "test_key" not in message
    assert "apikey" not in message.lower()
    assert "http" not in message.lower()


@pytest.mark.parametrize(
    "method,args",
    [
        ("get_fund_disclosure_dates", ("",)),
        ("get_fund_disclosure", ("SOXX", 1899, 1)),
        ("get_fund_disclosure", ("SOXX", 2025, 0)),
        ("get_fund_disclosure", ("SOXX", 2025, 5)),
        ("get_historical_fx", ("EURUSD", "not-a-date", "2026-07-03")),
        ("get_historical_fx", ("EURUSD", "2026-07-04", "2026-07-03")),
    ],
)
def test_validation_fails_before_network(client, method, args):
    with patch.object(client, "_request") as request:
        with pytest.raises(ValueError):
            getattr(client, method)(*args)
    request.assert_not_called()


@pytest.mark.parametrize(
    "row",
    [
        {"symbol": "KLAC", "numerator": 10, "denominator": 1},
        {"symbol": "KLAC", "date": "2026-06-12", "numerator": 0,
         "denominator": 1},
        {"symbol": "KLAC", "date": "2026-06-12", "numerator": 10,
         "denominator": -1},
    ],
)
def test_stock_splits_reject_malformed_rows(client, row):
    with patch.object(client, "_request", return_value=[row]):
        with pytest.raises(FMPResponseError):
            client.get_stock_splits("KLAC")


def test_valid_empty_lists_are_preserved(client):
    calls = [
        (client.get_fund_disclosure_dates, ("SOXX",)),
        (client.get_fund_disclosure, ("SOXX", 2025, 3)),
        (client.get_historical_fx, ("EURUSD", "2026-07-01", "2026-07-03")),
        (client.get_stock_splits, ("KLAC",)),
    ]
    for method, args in calls:
        with patch.object(client, "_request", return_value=[]):
            assert method(*args) == []
