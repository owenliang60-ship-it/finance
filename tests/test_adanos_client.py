"""Tests for Adanos social sentiment API client."""
import json
from unittest.mock import MagicMock, patch

import pytest

from src.data.adanos_client import AdanosClient, _SOURCE_CONFIG


# --- Fixtures ---

SAMPLE_REDDIT_RESPONSE = {
    "ticker": "NVDA",
    "company_name": "NVIDIA Corp",
    "found": True,
    "buzz_score": 76.7,
    "total_mentions": 750,
    "sentiment_score": 0.034,
    "positive_count": 506,
    "negative_count": 408,
    "neutral_count": 615,
    "total_upvotes": 5249,
    "unique_posts": 418,
    "subreddit_count": 29,
    "trend": "rising",
    "bullish_pct": 33,
    "bearish_pct": 27,
    "period_days": 7,
    "top_subreddits": [
        {"subreddit": "wallstreetbets", "count": 133},
        {"subreddit": "stocks", "count": 44},
    ],
    "daily_trend": [
        {"date": "2026-03-08", "mentions": 113, "sentiment": 0.032, "buzz_score": 78.0},
        {"date": "2026-03-07", "mentions": 87, "sentiment": 0.047, "buzz_score": 74.6},
        {"date": "2026-03-06", "mentions": 135, "sentiment": 0.069, "buzz_score": 80.3},
    ],
    "top_mentions": [
        {"text_snippet": "NVDA is great", "sentiment_score": 0.5, "upvotes": 10,
         "subreddit": "stocks", "created_utc": "2026-03-08T12:00:00"},
    ],
}

SAMPLE_X_RESPONSE = {
    "ticker": "NVDA",
    "company_name": "NVIDIA Corp",
    "found": True,
    "buzz_score": 84.2,
    "total_mentions": 2503,
    "sentiment_score": 0.177,
    "positive_count": 1324,
    "negative_count": 412,
    "neutral_count": 767,
    "total_upvotes": 56775,
    "unique_tweets": 2503,
    "trend": "rising",
    "bullish_pct": 53,
    "bearish_pct": 16,
    "period_days": 7,
    "is_validated": True,
    "daily_trend": [
        {"date": "2026-03-08", "mentions": 296, "sentiment": 0.152, "buzz_score": 86.5},
        {"date": "2026-03-07", "mentions": 286, "sentiment": 0.196, "buzz_score": 86.0},
    ],
    "top_tweets": [
        {"text_snippet": "NVDA rally!", "sentiment_score": 0.4, "likes": 1000,
         "author": "trader1", "created_at": "2026-03-08T14:00:00Z"},
    ],
}


@pytest.fixture
def client():
    return AdanosClient(api_key="test_key")


# --- Tests ---

class TestAdanosClient:

    def test_init(self, client):
        assert client.api_key == "test_key"
        assert client.base_url == "https://api.adanos.org"

    def test_invalid_source_raises(self, client):
        with pytest.raises(ValueError, match="Invalid source"):
            client.get_stock_sentiment("NVDA", source="tiktok")

    @patch("src.data.adanos_client.requests.get")
    def test_get_stock_sentiment_reddit(self, mock_get, client):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = SAMPLE_REDDIT_RESPONSE
        mock_get.return_value = mock_resp

        result = client.get_stock_sentiment("NVDA", source="reddit", days=7)

        assert result is not None
        assert result["ticker"] == "NVDA"
        assert result["buzz_score"] == 76.7
        assert result["total_mentions"] == 750
        assert result["bullish_pct"] == 33
        assert len(result["daily_trend"]) == 3

        # Verify endpoint
        call_url = mock_get.call_args[0][0]
        assert "/reddit/stocks/v1/stock/NVDA" in call_url

    @patch("src.data.adanos_client.requests.get")
    def test_get_stock_sentiment_x(self, mock_get, client):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = SAMPLE_X_RESPONSE
        mock_get.return_value = mock_resp

        result = client.get_stock_sentiment("NVDA", source="x", days=7)

        assert result is not None
        assert result["buzz_score"] == 84.2
        assert result["unique_tweets"] == 2503

        call_url = mock_get.call_args[0][0]
        assert "/x/stocks/v1/stock/NVDA" in call_url

    @patch("src.data.adanos_client.requests.get")
    def test_not_found_returns_none(self, mock_get, client):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"ticker": "ZZZZ", "found": False}
        mock_get.return_value = mock_resp

        result = client.get_stock_sentiment("ZZZZ", source="reddit")
        assert result is None

    @patch("src.data.adanos_client.requests.get")
    def test_auth_error_returns_none(self, mock_get, client):
        mock_resp = MagicMock()
        mock_resp.status_code = 401
        mock_resp.text = "Unauthorized"
        mock_get.return_value = mock_resp

        result = client.get_stock_sentiment("NVDA", source="reddit")
        assert result is None

    def test_no_api_key_returns_none(self):
        client = AdanosClient(api_key="")
        result = client.get_stock_sentiment("NVDA", source="reddit")
        assert result is None


class TestGetSentimentRows:

    @patch("src.data.adanos_client.requests.get")
    def test_reddit_rows_expanded(self, mock_get, client):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = SAMPLE_REDDIT_RESPONSE
        mock_get.return_value = mock_resp

        rows = client.get_sentiment_rows("NVDA", source="reddit", days=7)

        assert len(rows) == 3  # 3 days in daily_trend
        assert rows[0]["date"] == "2026-03-08"
        assert rows[0]["source"] == "reddit"
        assert rows[0]["buzz_score"] == 78.0  # day-level buzz
        assert rows[0]["total_mentions"] == 113
        assert rows[0]["sentiment_score"] == 0.032
        assert rows[0]["bullish_pct"] == 33  # aggregate level
        assert rows[0]["subreddit_count"] == 29
        assert rows[0]["created_at"] is not None

        # top_mentions JSON only on first (latest) day
        assert rows[0]["top_mentions"] is not None
        parsed = json.loads(rows[0]["top_mentions"])
        assert isinstance(parsed, list)
        assert rows[1]["top_mentions"] is None
        assert rows[2]["top_mentions"] is None

        # top_subreddits JSON only on first day
        assert rows[0]["top_subreddits"] is not None
        assert rows[1]["top_subreddits"] is None

    @patch("src.data.adanos_client.requests.get")
    def test_x_rows_field_mapping(self, mock_get, client):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = SAMPLE_X_RESPONSE
        mock_get.return_value = mock_resp

        rows = client.get_sentiment_rows("NVDA", source="x", days=7)

        assert len(rows) == 2
        assert rows[0]["source"] == "x"
        # unique_tweets → unique_posts
        assert rows[0]["unique_posts"] == 2503
        # is_validated mapped
        assert rows[0]["is_validated"] == 1
        # subreddit_count should be None for X
        assert rows[0]["subreddit_count"] is None
        # top_subreddits should be None for X
        assert rows[0]["top_subreddits"] is None

    @patch("src.data.adanos_client.requests.get")
    def test_api_failure_returns_empty(self, mock_get, client):
        mock_resp = MagicMock()
        mock_resp.status_code = 500
        mock_get.return_value = mock_resp

        rows = client.get_sentiment_rows("NVDA", source="reddit")
        assert rows == []

    @patch("src.data.adanos_client.requests.get")
    def test_empty_daily_trend_returns_empty(self, mock_get, client):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "ticker": "NVDA", "found": True,
            "buzz_score": 50, "daily_trend": [],
        }
        mock_get.return_value = mock_resp

        rows = client.get_sentiment_rows("NVDA", source="reddit")
        assert rows == []


class TestGetTrending:

    @patch("src.data.adanos_client.requests.get")
    def test_trending_returns_list(self, mock_get, client):
        trending_data = [
            {"ticker": "NVDA", "buzz_score": 77.8, "mentions": 765},
            {"ticker": "AAPL", "buzz_score": 70.1, "mentions": 500},
        ]
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = trending_data
        mock_get.return_value = mock_resp

        result = client.get_trending(source="reddit", days=7, limit=5)
        assert len(result) == 2
        assert result[0]["ticker"] == "NVDA"

    @patch("src.data.adanos_client.requests.get")
    def test_trending_failure_returns_empty(self, mock_get, client):
        mock_resp = MagicMock()
        mock_resp.status_code = 500
        mock_get.return_value = mock_resp

        result = client.get_trending(source="x")
        assert result == []
