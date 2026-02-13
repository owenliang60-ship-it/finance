"""Tests for terminal/attention_store.py — SQLite CRUD."""
import json
import tempfile
from pathlib import Path

import pytest

from terminal.attention_store import AttentionStore


@pytest.fixture
def store(tmp_path):
    """Create a fresh AttentionStore with temp DB."""
    db_path = tmp_path / "test_attention.db"
    s = AttentionStore(db_path=db_path)
    yield s
    s.close()


# ---- Theme Keywords ----

class TestKeywords:
    def test_save_and_get_keyword(self, store):
        store.save_keyword("AI chip", source="manual", tickers=["NVDA", "AMD"], category="ai_chip")
        kws = store.get_keywords()
        assert len(kws) == 1
        assert kws[0]["keyword"] == "AI chip"
        assert kws[0]["source"] == "manual"
        assert kws[0]["related_tickers"] == ["NVDA", "AMD"]
        assert kws[0]["category"] == "ai_chip"

    def test_upsert_keyword(self, store):
        store.save_keyword("DRAM price", source="manual", tickers=["MU"])
        store.save_keyword("DRAM price", source="reddit_auto", tickers=["MU", "WDC"])
        kws = store.get_keywords()
        assert len(kws) == 1
        assert kws[0]["source"] == "reddit_auto"
        assert kws[0]["related_tickers"] == ["MU", "WDC"]

    def test_deactivate_keyword(self, store):
        store.save_keyword("old keyword", source="manual")
        assert store.deactivate_keyword("old keyword") is True
        active = store.get_keywords(active_only=True)
        assert len(active) == 0
        all_kws = store.get_keywords(active_only=False)
        assert len(all_kws) == 1

    def test_seed_keywords(self, store):
        seed = {
            "memory": {"keywords": ["DRAM price", "HBM"], "tickers": ["MU"]},
            "ai": {"keywords": ["AI chip"], "tickers": ["NVDA"]},
        }
        count = store.seed_keywords(seed)
        assert count == 3
        kws = store.get_keywords()
        assert len(kws) == 3


# ---- Google Trends ----

class TestGoogleTrends:
    def test_save_and_get_trends(self, store):
        store.save_trends_data("AI chip", "2026-01-06", 85, 1.2)
        store.save_trends_data("AI chip", "2026-01-13", 90, 1.3)
        history = store.get_trends_history("AI chip", weeks=10)
        assert len(history) == 2
        assert history[0]["week_start"] == "2026-01-13"  # newest first
        assert history[0]["interest_score"] == 90

    def test_upsert_trends(self, store):
        store.save_trends_data("DRAM", "2026-01-06", 50, 0.8)
        store.save_trends_data("DRAM", "2026-01-06", 55, 0.9)
        history = store.get_trends_history("DRAM")
        assert len(history) == 1
        assert history[0]["interest_score"] == 55

    def test_save_trends_batch(self, store):
        records = [
            {"keyword": "DRAM", "week_start": "2026-01-06", "interest_score": 50, "anchor_ratio": 0.8},
            {"keyword": "DRAM", "week_start": "2026-01-13", "interest_score": 55, "anchor_ratio": 0.9},
            {"keyword": "HBM", "week_start": "2026-01-06", "interest_score": 70, "anchor_ratio": 1.1},
        ]
        count = store.save_trends_batch(records)
        assert count == 3
        assert len(store.get_trends_history("DRAM")) == 2
        assert len(store.get_trends_history("HBM")) == 1


# ---- Reddit Mentions ----

class TestRedditMentions:
    def test_save_and_get_reddit(self, store):
        store.save_reddit_mention("NVDA", "2026-02-10", "wallstreetbets", 42, 200)
        store.save_reddit_mention("NVDA", "2026-02-10", "stocks", 15, 200)
        history = store.get_reddit_history("NVDA", days=30)
        assert len(history) == 1  # grouped by date
        assert history[0]["total_mentions"] == 57

    def test_upsert_reddit(self, store):
        store.save_reddit_mention("AMD", "2026-02-10", "stocks", 10, 200)
        store.save_reddit_mention("AMD", "2026-02-10", "stocks", 15, 200)
        history = store.get_reddit_history("AMD")
        assert history[0]["total_mentions"] == 15  # updated, not duplicated

    def test_save_reddit_batch(self, store):
        records = [
            {"ticker": "NVDA", "scan_date": "2026-02-10", "subreddit": "wsb", "mention_count": 30, "sample_posts": 200},
            {"ticker": "AMD", "scan_date": "2026-02-10", "subreddit": "wsb", "mention_count": 20, "sample_posts": 200},
        ]
        count = store.save_reddit_batch(records)
        assert count == 2

    def test_get_reddit_daily_totals(self, store):
        store.save_reddit_mention("NVDA", "2026-02-10", "wsb", 30, 200)
        store.save_reddit_mention("NVDA", "2026-02-10", "stocks", 10, 200)
        store.save_reddit_mention("AMD", "2026-02-10", "wsb", 20, 200)
        totals = store.get_reddit_daily_totals("2026-02-10")
        assert len(totals) == 2
        assert totals[0]["ticker"] == "NVDA"  # 40 > 20
        assert totals[0]["total_mentions"] == 40

    def test_ticker_uppercased(self, store):
        store.save_reddit_mention("nvda", "2026-02-10", "stocks", 5, 100)
        history = store.get_reddit_history("NVDA")
        assert len(history) == 1


# ---- News Mentions ----

class TestNewsMentions:
    def test_save_and_get_news(self, store):
        store.save_news_mention("AAPL", "2026-02-10", 25, 0.3, "finnhub")
        history = store.get_news_history("AAPL")
        assert len(history) == 1
        assert history[0]["article_count"] == 25
        assert abs(history[0]["avg_sentiment"] - 0.3) < 0.01

    def test_upsert_news(self, store):
        store.save_news_mention("GOOG", "2026-02-10", 10, 0.1, "finnhub")
        store.save_news_mention("GOOG", "2026-02-10", 15, 0.2, "finnhub")
        history = store.get_news_history("GOOG")
        assert len(history) == 1
        assert history[0]["article_count"] == 15

    def test_save_news_batch(self, store):
        records = [
            {"ticker": "NVDA", "scan_date": "2026-02-10", "article_count": 50, "avg_sentiment": 0.4},
            {"ticker": "AMD", "scan_date": "2026-02-10", "article_count": 20, "avg_sentiment": 0.1},
        ]
        count = store.save_news_batch(records)
        assert count == 2

    def test_get_news_daily_totals(self, store):
        store.save_news_mention("NVDA", "2026-02-10", 50, 0.3, "finnhub")
        store.save_news_mention("AMD", "2026-02-10", 20, 0.1, "finnhub")
        totals = store.get_news_daily_totals("2026-02-10")
        assert len(totals) == 2
        assert totals[0]["ticker"] == "NVDA"


# ---- Attention Snapshots ----

class TestSnapshots:
    def test_save_and_get_snapshot(self, store):
        scores = {
            "reddit_zscore": 2.5,
            "news_zscore": 1.8,
            "trends_zscore": 1.2,
            "composite_score": 1.85,
            "rank": 1,
        }
        store.save_attention_snapshot("NVDA", "2026-02-10", scores)
        ranking = store.get_weekly_ranking("2026-02-10")
        assert len(ranking) == 1
        assert ranking[0]["ticker"] == "NVDA"
        assert abs(ranking[0]["composite_score"] - 1.85) < 0.01

    def test_weekly_ranking_order(self, store):
        for i, (ticker, score) in enumerate([("NVDA", 3.0), ("AMD", 2.0), ("MU", 1.0)]):
            store.save_attention_snapshot(ticker, "2026-02-10", {
                "composite_score": score, "rank": i + 1,
            })
        ranking = store.get_weekly_ranking("2026-02-10")
        assert [r["ticker"] for r in ranking] == ["NVDA", "AMD", "MU"]

    def test_get_ticker_history(self, store):
        for week in ["2026-01-06", "2026-01-13", "2026-01-20"]:
            store.save_attention_snapshot("NVDA", week, {"composite_score": 2.0, "rank": 1})
        history = store.get_ticker_history("NVDA", weeks=10)
        assert len(history) == 3
        assert history[0]["week_start"] == "2026-01-20"

    def test_save_snapshots_batch(self, store):
        records = [
            {"ticker": "NVDA", "week_start": "2026-02-10", "composite_score": 3.0, "rank": 1},
            {"ticker": "AMD", "week_start": "2026-02-10", "composite_score": 2.0, "rank": 2},
        ]
        count = store.save_snapshots_batch(records)
        assert count == 2

    def test_get_all_weeks(self, store):
        store.save_attention_snapshot("NVDA", "2026-01-06", {"composite_score": 1.0, "rank": 1})
        store.save_attention_snapshot("NVDA", "2026-01-13", {"composite_score": 2.0, "rank": 1})
        weeks = store.get_all_weeks()
        assert weeks == ["2026-01-13", "2026-01-06"]

    def test_get_new_discoveries(self, store):
        # Week 1: NVDA, AMD
        store.save_attention_snapshot("NVDA", "2026-01-06", {"composite_score": 2.0, "rank": 1})
        store.save_attention_snapshot("AMD", "2026-01-06", {"composite_score": 1.0, "rank": 2})
        # Week 2: NVDA, AMD, MU (new)
        store.save_attention_snapshot("NVDA", "2026-01-13", {"composite_score": 2.5, "rank": 1})
        store.save_attention_snapshot("AMD", "2026-01-13", {"composite_score": 1.5, "rank": 2})
        store.save_attention_snapshot("MU", "2026-01-13", {"composite_score": 1.0, "rank": 3})
        new = store.get_new_discoveries("2026-01-13")
        assert new == ["MU"]


# ---- Stats ----

class TestStats:
    def test_get_stats(self, store):
        store.save_keyword("AI chip", source="manual")
        store.save_trends_data("AI chip", "2026-01-06", 85, 1.2)
        store.save_reddit_mention("NVDA", "2026-02-10", "wsb", 10, 200)
        store.save_news_mention("NVDA", "2026-02-10", 25, 0.3)
        store.save_attention_snapshot("NVDA", "2026-02-10", {"composite_score": 2.0, "rank": 1})
        stats = store.get_stats()
        assert stats["active_keywords"] == 1
        assert stats["gt_data_points"] == 1
        assert stats["reddit_scan_days"] == 1
        assert stats["news_scan_days"] == 1
        assert stats["snapshot_weeks"] == 1
        assert stats["tickers_tracked"] == 1
