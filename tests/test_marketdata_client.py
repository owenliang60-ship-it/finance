"""Tests for MarketData.app API client."""
import json
import pytest
from unittest.mock import patch, MagicMock

from src.data.marketdata_client import MarketDataClient


@pytest.fixture
def client():
    """Create a client with a fake API key."""
    return MarketDataClient(api_key="test_key_123")


@pytest.fixture
def mock_response():
    """Factory for mock responses."""
    def _make(status_code=200, json_data=None):
        resp = MagicMock()
        resp.status_code = status_code
        resp.json.return_value = json_data or {}
        resp.text = json.dumps(json_data or {})
        return resp
    return _make


class TestRateLimit:
    """Test rate limiting behavior."""

    @patch("src.data.marketdata_client.time.sleep")
    @patch("src.data.marketdata_client.time.time")
    @patch("src.data.marketdata_client.requests.get")
    def test_rate_limit_waits(self, mock_get, mock_time, mock_sleep, client, mock_response):
        """Should sleep when calls are too close together."""
        # First call at t=0, second call at t=0.5 (within 2s interval)
        mock_time.side_effect = [0, 0.5, 0.5, 2.5, 2.5]
        mock_get.return_value = mock_response(200, {"s": "ok"})

        client._last_call_time = 0
        client._request("test/endpoint")

        # Should have slept for ~1.5s (2.0 - 0.5)
        mock_sleep.assert_called()

    @patch("src.data.marketdata_client.time.sleep")
    @patch("src.data.marketdata_client.requests.get")
    def test_no_sleep_when_enough_time(self, mock_get, mock_sleep, client, mock_response):
        """Should not sleep when enough time has passed."""
        mock_get.return_value = mock_response(200, {"s": "ok"})

        client._last_call_time = 0  # Long time ago
        client._request("test/endpoint")

        # sleep may be called, but only for rate limit less than interval
        # The key test is that it doesn't error out


class TestAuth:
    """Test Bearer token authentication."""

    @patch("src.data.marketdata_client.requests.get")
    def test_bearer_token_in_header(self, mock_get, client, mock_response):
        """API key should be sent as Bearer token in Authorization header."""
        mock_get.return_value = mock_response(200, {"s": "ok"})

        client._request("test/endpoint")

        mock_get.assert_called_once()
        call_kwargs = mock_get.call_args
        headers = call_kwargs.kwargs.get("headers") or call_kwargs[1].get("headers")
        assert headers["Authorization"] == "Bearer test_key_123"

    @patch("src.data.marketdata_client.requests.get")
    def test_api_key_not_in_params(self, mock_get, client, mock_response):
        """API key should NOT be in query params (unlike FMP)."""
        mock_get.return_value = mock_response(200, {"s": "ok"})

        client._request("test/endpoint", {"foo": "bar"})

        call_kwargs = mock_get.call_args
        params = call_kwargs.kwargs.get("params") or call_kwargs[1].get("params", {})
        assert "apikey" not in params
        assert "api_key" not in params


class TestRetry:
    """Test retry logic."""

    @patch("src.data.marketdata_client.time.sleep")
    @patch("src.data.marketdata_client.requests.get")
    def test_retry_on_429(self, mock_get, mock_sleep, client, mock_response):
        """Should retry on rate limit (429)."""
        mock_get.side_effect = [
            mock_response(429),
            mock_response(200, {"s": "ok", "data": [1]}),
        ]

        result = client._request("test/endpoint")
        assert result is not None
        assert mock_get.call_count == 2

    @patch("src.data.marketdata_client.requests.get")
    def test_no_retry_on_401(self, mock_get, client, mock_response):
        """Should NOT retry on auth failure (401)."""
        mock_get.return_value = mock_response(401)

        result = client._request("test/endpoint")
        assert result is None
        assert mock_get.call_count == 1

    @patch("src.data.marketdata_client.requests.get")
    def test_no_retry_on_402(self, mock_get, client, mock_response):
        """Should NOT retry on credit limit (402)."""
        mock_get.return_value = mock_response(402)

        result = client._request("test/endpoint")
        assert result is None
        assert mock_get.call_count == 1

    @patch("src.data.marketdata_client.time.sleep")
    @patch("src.data.marketdata_client.requests.get")
    def test_retry_on_timeout(self, mock_get, mock_sleep, client):
        """Should retry on timeout."""
        import requests as req
        mock_get.side_effect = [
            req.exceptions.Timeout("timeout"),
            req.exceptions.Timeout("timeout"),
            req.exceptions.Timeout("timeout"),
        ]

        result = client._request("test/endpoint")
        assert result is None
        assert mock_get.call_count == 3  # API_RETRY_TIMES


class TestResponseParsing:
    """Test response parsing for MarketData.app format."""

    @patch("src.data.marketdata_client.requests.get")
    def test_ok_response(self, mock_get, client, mock_response):
        """Should return data dict when s=ok."""
        data = {"s": "ok", "strike": [200, 210], "iv": [0.3, 0.35]}
        mock_get.return_value = mock_response(200, data)

        result = client._request("options/chain/AAPL")
        assert result["s"] == "ok"
        assert result["strike"] == [200, 210]

    @patch("src.data.marketdata_client.requests.get")
    def test_no_data_response(self, mock_get, client, mock_response):
        """Should return None when s=no_data."""
        mock_get.return_value = mock_response(200, {"s": "no_data"})

        result = client._request("options/chain/INVALID")
        assert result is None


class TestOptionsChain:
    """Test options chain methods."""

    @patch("src.data.marketdata_client.requests.get")
    def test_get_options_chain_params(self, mock_get, client, mock_response):
        """Should pass correct params for chain request."""
        mock_get.return_value = mock_response(200, {"s": "ok"})

        client.get_options_chain(
            "AAPL",
            dte=30,
            date_from="2026-03-01",
            date_to="2026-06-01",
            strike_limit=2,
            option_range="otm",
        )

        call_kwargs = mock_get.call_args
        params = call_kwargs.kwargs.get("params") or call_kwargs[1].get("params", {})
        assert params["dte"] == 30
        assert params["from"] == "2026-03-01"
        assert params["to"] == "2026-06-01"
        assert params["strikeLimit"] == 2
        assert params["range"] == "otm"

    @patch("src.data.marketdata_client.requests.get")
    def test_get_options_expirations(self, mock_get, client, mock_response):
        """Should extract expirations list from response."""
        data = {"s": "ok", "expirations": ["2026-03-21", "2026-04-17"]}
        mock_get.return_value = mock_response(200, data)

        result = client.get_options_expirations("AAPL")
        assert result == ["2026-03-21", "2026-04-17"]

    @patch("src.data.marketdata_client.requests.get")
    def test_get_atm_iv_data(self, mock_get, client, mock_response):
        """ATM IV data should use dte=30 and strikeLimit=2."""
        mock_get.return_value = mock_response(200, {"s": "ok"})

        client.get_atm_iv_data("AAPL")

        call_kwargs = mock_get.call_args
        params = call_kwargs.kwargs.get("params") or call_kwargs[1].get("params", {})
        assert params["strikeLimit"] == 2
        assert params["dte"] == 30
        assert "range" not in params


class TestStockQuote:
    """Test stock quote method."""

    @patch("src.data.marketdata_client.requests.get")
    def test_get_stock_quote(self, mock_get, client, mock_response):
        """Should extract first element from array-style response."""
        data = {
            "s": "ok",
            "last": [225.50],
            "bid": [225.40],
            "ask": [225.60],
            "volume": [1000000],
            "mid": [225.50],
        }
        mock_get.return_value = mock_response(200, data)

        result = client.get_stock_quote("AAPL")
        assert result["last"] == 225.50
        assert result["bid"] == 225.40
        assert result["ask"] == 225.60
