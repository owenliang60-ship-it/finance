"""Tests for social_sentiment table in MarketStore."""
import pytest
from pathlib import Path
from src.data.market_store import MarketStore


@pytest.fixture
def store(tmp_path):
    """Create a temporary MarketStore."""
    db_path = tmp_path / "test_market.db"
    return MarketStore(db_path=db_path)


def _make_rows(source="reddit", days=3, base_mentions=100):
    """Generate sample social sentiment rows."""
    rows = []
    for i in range(days):
        rows.append({
            "date": "2026-03-{:02d}".format(10 - i),
            "source": source,
            "buzz_score": 70.0 + i,
            "total_mentions": base_mentions + i * 10,
            "sentiment_score": 0.05 * (i + 1),
            "positive_count": 50 + i,
            "negative_count": 30 + i,
            "neutral_count": 20 + i,
            "bullish_pct": 35 + i,
            "bearish_pct": 25 - i,
            "trend": "rising" if i == 0 else "stable",
            "total_upvotes": 1000 + i * 100,
            "unique_posts": 80 + i,
            "subreddit_count": 10 if source == "reddit" else None,
            "is_validated": None if source == "reddit" else 1,
            "top_mentions": '["test snippet"]' if i == 0 else None,
            "top_subreddits": '[{"subreddit": "wsb"}]' if i == 0 and source == "reddit" else None,
            "period_days": 7,
            "created_at": "2026-03-10T07:00:00",
        })
    return rows


class TestUpsertSocialSentiment:

    def test_basic_upsert(self, store):
        rows = _make_rows(source="reddit", days=3)
        count = store.upsert_social_sentiment("NVDA", rows)
        assert count == 3

    def test_upsert_both_sources(self, store):
        reddit_rows = _make_rows(source="reddit", days=2)
        x_rows = _make_rows(source="x", days=2)
        store.upsert_social_sentiment("NVDA", reddit_rows)
        store.upsert_social_sentiment("NVDA", x_rows)

        all_rows = store.get_social_sentiment("NVDA", limit=10)
        assert len(all_rows) == 4  # 2 reddit + 2 x

    def test_upsert_replaces_on_conflict(self, store):
        rows = _make_rows(source="reddit", days=1)
        store.upsert_social_sentiment("NVDA", rows)

        # Update with different buzz
        rows[0]["buzz_score"] = 99.9
        store.upsert_social_sentiment("NVDA", rows)

        result = store.get_social_sentiment("NVDA", source="reddit", limit=1)
        assert len(result) == 1
        assert result[0]["buzz_score"] == 99.9

    def test_upsert_empty_rows(self, store):
        count = store.upsert_social_sentiment("NVDA", [])
        assert count == 0

    def test_upsert_skips_missing_date(self, store):
        rows = [{"source": "reddit", "buzz_score": 50, "created_at": "now"}]
        count = store.upsert_social_sentiment("NVDA", rows)
        assert count == 0

    def test_upsert_skips_missing_source(self, store):
        rows = [{"date": "2026-03-10", "buzz_score": 50, "created_at": "now"}]
        count = store.upsert_social_sentiment("NVDA", rows)
        assert count == 0

    def test_symbol_uppercased(self, store):
        rows = _make_rows(source="reddit", days=1)
        store.upsert_social_sentiment("nvda", rows)
        result = store.get_social_sentiment("NVDA")
        assert len(result) == 1
        assert result[0]["symbol"] == "NVDA"


class TestGetSocialSentiment:

    def test_get_with_source_filter(self, store):
        store.upsert_social_sentiment("NVDA", _make_rows("reddit", 3))
        store.upsert_social_sentiment("NVDA", _make_rows("x", 3))

        reddit_only = store.get_social_sentiment("NVDA", source="reddit")
        assert all(r["source"] == "reddit" for r in reddit_only)

        x_only = store.get_social_sentiment("NVDA", source="x")
        assert all(r["source"] == "x" for r in x_only)

    def test_get_ordered_newest_first(self, store):
        store.upsert_social_sentiment("NVDA", _make_rows("reddit", 3))
        rows = store.get_social_sentiment("NVDA", source="reddit")
        dates = [r["date"] for r in rows]
        assert dates == sorted(dates, reverse=True)

    def test_get_with_limit(self, store):
        store.upsert_social_sentiment("NVDA", _make_rows("reddit", 5))
        rows = store.get_social_sentiment("NVDA", source="reddit", limit=2)
        assert len(rows) == 2

    def test_get_nonexistent_symbol(self, store):
        rows = store.get_social_sentiment("ZZZZ")
        assert rows == []


class TestGetLatestSocialSentiment:

    def test_latest_returns_one(self, store):
        store.upsert_social_sentiment("NVDA", _make_rows("reddit", 3))
        latest = store.get_latest_social_sentiment("NVDA", source="reddit")
        assert latest is not None
        assert latest["date"] == "2026-03-10"

    def test_latest_no_data(self, store):
        assert store.get_latest_social_sentiment("ZZZZ") is None


class TestGetSocialSentimentBulk:

    def test_bulk_returns_dict(self, store):
        store.upsert_social_sentiment("NVDA", _make_rows("reddit", 2))
        store.upsert_social_sentiment("AAPL", _make_rows("reddit", 2))

        result = store.get_social_sentiment_bulk(["NVDA", "AAPL", "ZZZZ"])
        assert "NVDA" in result
        assert "AAPL" in result
        assert "ZZZZ" not in result

    def test_bulk_with_source_filter(self, store):
        store.upsert_social_sentiment("NVDA", _make_rows("reddit", 2))
        store.upsert_social_sentiment("NVDA", _make_rows("x", 2))

        result = store.get_social_sentiment_bulk(["NVDA"], source="reddit")
        assert all(r["source"] == "reddit" for r in result["NVDA"])


class TestSchemaIntegrity:

    def test_social_sentiment_in_stats(self, store):
        stats = store.get_stats()
        assert "social_sentiment" in stats
        assert stats["social_sentiment"] == 0

    def test_social_sentiment_in_valid_tables(self):
        from src.data.market_store import _VALID_TABLES
        assert "social_sentiment" in _VALID_TABLES

    def test_all_fields_stored(self, store):
        rows = _make_rows("reddit", 1)
        store.upsert_social_sentiment("NVDA", rows)
        result = store.get_social_sentiment("NVDA")[0]

        assert result["buzz_score"] == 70.0
        assert result["total_mentions"] == 100
        assert result["sentiment_score"] == 0.05
        assert result["positive_count"] == 50
        assert result["negative_count"] == 30
        assert result["neutral_count"] == 20
        assert result["bullish_pct"] == 35
        assert result["bearish_pct"] == 25
        assert result["trend"] == "rising"
        assert result["total_upvotes"] == 1000
        assert result["unique_posts"] == 80
        assert result["subreddit_count"] == 10
        assert result["top_mentions"] == '["test snippet"]'
        assert result["top_subreddits"] == '[{"subreddit": "wsb"}]'
        assert result["period_days"] == 7
        assert result["created_at"] == "2026-03-10T07:00:00"
