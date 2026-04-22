"""Tests for delisted-company helper on FMPClient."""

import pytest
from unittest.mock import patch

from src.data.fmp_client import FMPClient


@pytest.fixture
def client():
    return FMPClient(api_key="test_key")


def test_get_delisted_companies_success(client):
    mock_data = [
        {"symbol": "TWTR", "companyName": "Twitter, Inc.", "delistedDate": "2022-10-28"},
        {"symbol": "ATVI", "companyName": "Activision Blizzard, Inc.", "delistedDate": "2023-10-20"},
    ]
    with patch.object(client, "_request", return_value=mock_data):
        result = client.get_delisted_companies(page=0, limit=100)

    assert len(result) == 2
    assert result[0]["symbol"] == "TWTR"
    assert result[1]["symbol"] == "ATVI"


def test_get_delisted_companies_empty(client):
    with patch.object(client, "_request", return_value=[]):
        result = client.get_delisted_companies(page=0, limit=100)

    assert result == []


def test_get_delisted_companies_none_response(client):
    with patch.object(client, "_request", return_value=None):
        result = client.get_delisted_companies(page=1, limit=100)

    assert result == []


def test_get_delisted_companies_calls_stable_endpoint(client):
    with patch.object(client, "_request", return_value=[]) as mock_req:
        client.get_delisted_companies(page=1, limit=50)

    mock_req.assert_called_once_with(
        "delisted-companies",
        {"page": 1, "limit": 50},
    )
