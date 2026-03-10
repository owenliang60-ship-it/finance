"""Adanos Social Sentiment API client.

Fetches Reddit and X/Twitter sentiment data per ticker.
API docs: https://api.adanos.org/docs

Each source returns:
- Aggregate metrics: buzz_score, mentions, sentiment_score, bullish/bearish_pct
- Daily trend: per-day breakdown of mentions + sentiment
- Top mentions: 10 most-engaged posts with text snippets

Usage:
    from src.data.adanos_client import adanos_client
    data = adanos_client.get_stock_sentiment("NVDA", source="reddit", days=7)
"""
import json
import logging
import time
from datetime import datetime
from typing import Any, Dict, List, Optional

import requests

from config.settings import (
    ADANOS_API_KEY,
    ADANOS_BASE_URL,
    ADANOS_CALL_INTERVAL,
    ADANOS_REQUEST_DAYS,
    API_RETRY_TIMES,
    API_TIMEOUT,
)

logger = logging.getLogger(__name__)

# Source config: endpoint prefix + field mappings for normalization
_SOURCE_CONFIG = {
    "reddit": {
        "prefix": "/reddit/stocks/v1",
        "unique_posts_field": "unique_posts",
        "top_mentions_field": "top_mentions",
    },
    "x": {
        "prefix": "/x/stocks/v1",
        "unique_posts_field": "unique_tweets",
        "top_mentions_field": "top_tweets",
    },
}


class AdanosClient:
    """Thin client for Adanos Social Sentiment API."""

    def __init__(self, api_key: str = ADANOS_API_KEY):
        self.api_key = api_key
        self.base_url = ADANOS_BASE_URL
        self._last_call_time = 0.0

    def _rate_limit(self) -> None:
        """Enforce minimum interval between API calls."""
        elapsed = time.time() - self._last_call_time
        if elapsed < ADANOS_CALL_INTERVAL:
            time.sleep(ADANOS_CALL_INTERVAL - elapsed)
        self._last_call_time = time.time()

    def _request(self, endpoint: str, params: Optional[Dict] = None) -> Optional[Dict]:
        """Make authenticated GET request with retry logic.

        Returns parsed JSON dict on success, None on failure.
        """
        if not self.api_key:
            logger.error("ADANOS_API_KEY not configured")
            return None

        url = "{}{}".format(self.base_url, endpoint)
        headers = {"X-API-Key": self.api_key}

        for attempt in range(1, API_RETRY_TIMES + 1):
            self._rate_limit()
            try:
                resp = requests.get(
                    url,
                    headers=headers,
                    params=params,
                    timeout=API_TIMEOUT,
                )

                if resp.status_code == 200:
                    return resp.json()

                if resp.status_code == 429:
                    wait = attempt * 5
                    logger.warning(
                        "Adanos rate limited (429), waiting %ds (attempt %d/%d)",
                        wait, attempt, API_RETRY_TIMES,
                    )
                    time.sleep(wait)
                    continue

                if resp.status_code in (401, 403):
                    logger.error("Adanos auth error %d: %s", resp.status_code, resp.text)
                    return None

                logger.warning(
                    "Adanos %s returned %d (attempt %d/%d)",
                    endpoint, resp.status_code, attempt, API_RETRY_TIMES,
                )

            except requests.exceptions.Timeout:
                logger.warning(
                    "Adanos timeout for %s (attempt %d/%d)",
                    endpoint, attempt, API_RETRY_TIMES,
                )
            except requests.exceptions.RequestException as e:
                logger.warning(
                    "Adanos request error for %s: %s (attempt %d/%d)",
                    endpoint, e, attempt, API_RETRY_TIMES,
                )

        return None

    def get_stock_sentiment(
        self,
        symbol: str,
        source: str = "reddit",
        days: int = ADANOS_REQUEST_DAYS,
    ) -> Optional[Dict[str, Any]]:
        """Fetch sentiment data for a single ticker from one source.

        Args:
            symbol: Stock ticker (e.g. "NVDA")
            source: "reddit" or "x"
            days: Lookback window (1-90 for Hobby tier)

        Returns:
            Raw API response dict with all fields, or None on failure.
        """
        if source not in _SOURCE_CONFIG:
            raise ValueError("Invalid source: {}. Must be 'reddit' or 'x'".format(source))

        config = _SOURCE_CONFIG[source]
        endpoint = "{}/stock/{}".format(config["prefix"], symbol.upper())
        data = self._request(endpoint, params={"days": days})

        if data is None:
            return None

        if not data.get("found", False):
            logger.debug("Adanos: %s not found on %s", symbol, source)
            return None

        return data

    def get_sentiment_rows(
        self,
        symbol: str,
        source: str = "reddit",
        days: int = ADANOS_REQUEST_DAYS,
    ) -> List[Dict[str, Any]]:
        """Fetch sentiment and expand daily_trend into DB-ready rows.

        Each row in daily_trend becomes one DB row with the aggregate-level
        fields (buzz_score, bullish_pct, etc.) carried forward, plus the
        day-specific mentions and sentiment.

        Returns:
            List of dicts ready for market_store.upsert_social_sentiment().
            Empty list on failure or no data.
        """
        data = self.get_stock_sentiment(symbol, source=source, days=days)
        if data is None:
            return []

        config = _SOURCE_CONFIG[source]
        now = datetime.now().isoformat()
        daily_trend = data.get("daily_trend", [])

        if not daily_trend:
            return []

        # Serialize top_mentions/top_tweets → JSON string
        top_mentions_raw = data.get(config["top_mentions_field"], [])
        top_mentions_json = json.dumps(top_mentions_raw, ensure_ascii=False) if top_mentions_raw else None

        # Serialize top_subreddits → JSON string (Reddit only)
        top_subreddits_raw = data.get("top_subreddits", [])
        top_subreddits_json = json.dumps(top_subreddits_raw, ensure_ascii=False) if top_subreddits_raw else None

        rows = []
        # Find actual latest date (API ordering not guaranteed across sources)
        latest_date = max(
            (d.get("date", "") for d in daily_trend),
            default=None,
        )
        for day in daily_trend:
            date_str = day.get("date")
            if not date_str:
                continue

            is_latest = (date_str == latest_date)
            row = {
                "date": date_str,
                "source": source,
                # Per-day fields (from daily_trend array)
                "buzz_score": day.get("buzz_score") if day.get("buzz_score") is not None else data.get("buzz_score"),
                "total_mentions": day.get("mentions"),
                "sentiment_score": day.get("sentiment"),
                # Period-level aggregates (only on latest day to avoid
                # misleading downstream consumers — these cover the full
                # request period, not individual days)
                "positive_count": data.get("positive_count") if is_latest else None,
                "negative_count": data.get("negative_count") if is_latest else None,
                "neutral_count": data.get("neutral_count") if is_latest else None,
                "bullish_pct": data.get("bullish_pct") if is_latest else None,
                "bearish_pct": data.get("bearish_pct") if is_latest else None,
                "trend": data.get("trend") if is_latest else None,
                "total_upvotes": data.get("total_upvotes") if is_latest else None,
                # Source-specific (period-level)
                "unique_posts": data.get(config["unique_posts_field"]) if is_latest else None,
                "subreddit_count": data.get("subreddit_count") if is_latest else None,
                "is_validated": (1 if data.get("is_validated") else (0 if source == "x" else None)) if is_latest else None,
                # JSON blobs (only on latest day to avoid redundancy)
                "top_mentions": top_mentions_json if is_latest else None,
                "top_subreddits": top_subreddits_json if is_latest else None,
                # Metadata
                "period_days": data.get("period_days", days),
                "created_at": now,
            }
            rows.append(row)

        return rows

    def get_trending(
        self,
        source: str = "reddit",
        days: int = 7,
        limit: int = 20,
    ) -> List[Dict[str, Any]]:
        """Fetch trending stocks for a source.

        Returns list of trending stock dicts, or empty list on failure.
        """
        if source not in _SOURCE_CONFIG:
            raise ValueError("Invalid source: {}".format(source))

        config = _SOURCE_CONFIG[source]
        endpoint = "{}/trending".format(config["prefix"])
        data = self._request(endpoint, params={"days": days, "limit": limit})

        if data is None:
            return []

        return data if isinstance(data, list) else []


# Module-level singleton
adanos_client = AdanosClient()
