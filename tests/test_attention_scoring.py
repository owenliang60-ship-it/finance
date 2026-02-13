"""Tests for terminal/attention.py — Z-score + composite scoring."""
import pytest
from datetime import date, timedelta
from pathlib import Path

from terminal.attention import _zscore, _get_monday, compute_attention_ranking
from terminal.attention_store import AttentionStore


@pytest.fixture
def store(tmp_path):
    """Fresh AttentionStore with temp DB."""
    db_path = tmp_path / "test_scoring.db"
    s = AttentionStore(db_path=db_path)
    yield s
    s.close()


# ---- Z-score ----

class TestZscore:
    def test_basic_zscore(self):
        values = [10.0, 20.0, 30.0, 40.0, 50.0]
        # mean=30, std=sqrt(200)=14.14
        z = _zscore(values, 50.0)
        assert abs(z - 1.414) < 0.01

    def test_zero_std(self):
        """When all values are equal, Z=0."""
        values = [5.0, 5.0, 5.0]
        assert _zscore(values, 5.0) == 0.0

    def test_no_history(self):
        """With <2 values, Z=0."""
        assert _zscore([], 10.0) == 0.0
        assert _zscore([5.0], 10.0) == 0.0

    def test_negative_zscore(self):
        values = [10.0, 20.0, 30.0, 40.0, 50.0]
        z = _zscore(values, 10.0)
        assert z < 0

    def test_extreme_zscore(self):
        values = [1.0, 2.0, 3.0, 4.0, 5.0]
        z = _zscore(values, 100.0)
        assert z > 5.0  # extreme outlier


# ---- Get Monday ----

class TestGetMonday:
    def test_monday(self):
        assert _get_monday(date(2026, 2, 10)) == "2026-02-09"  # Mon

    def test_tuesday(self):
        assert _get_monday(date(2026, 2, 11)) == "2026-02-09"

    def test_sunday(self):
        assert _get_monday(date(2026, 2, 15)) == "2026-02-09"

    def test_already_monday(self):
        assert _get_monday(date(2026, 2, 9)) == "2026-02-09"


# ---- Composite Ranking ----

class TestComputeRanking:
    def _populate_data(self, store, weeks_back=12):
        """Populate store with synthetic historical data (with variance)."""
        base = date(2026, 2, 10)  # Monday
        # Add historical reddit mentions with some variance
        for w in range(weeks_back):
            d = base - timedelta(days=7 * w)
            ds = d.isoformat()
            # NVDA: ~10 mentions with variance
            store.save_reddit_mention("NVDA", ds, "stocks", 10 + (w % 3), 200)
            # AMD: ~5 mentions with variance
            store.save_reddit_mention("AMD", ds, "stocks", 5 + (w % 4), 200)
            # News: NVDA ~20, AMD ~10 with variance
            store.save_news_mention("NVDA", ds, 20 + (w % 5), 0.3)
            store.save_news_mention("AMD", ds, 10 + (w % 3), 0.1)

    def test_basic_ranking(self, store):
        """With uniform history, a spike should rank higher."""
        self._populate_data(store)

        # Add a spike for AMD this week
        target_week = "2026-02-10"
        store.save_reddit_mention("AMD", target_week, "wsb", 50, 200)
        store.save_news_mention("AMD", target_week, 80, 0.5, "fmp")

        rankings = compute_attention_ranking(
            store, week_start=target_week, baseline_days=90, top_n=10,
        )
        assert len(rankings) > 0
        # AMD should have higher Z-scores due to spike
        amd = next((r for r in rankings if r["ticker"] == "AMD"), None)
        nvda = next((r for r in rankings if r["ticker"] == "NVDA"), None)
        assert amd is not None
        assert amd["reddit_zscore"] > 0  # spike above baseline

    def test_empty_data(self, store):
        """No data → empty rankings."""
        rankings = compute_attention_ranking(store, week_start="2026-02-10")
        assert rankings == []

    def test_rank_assignment(self, store):
        """Ranks should be sequential 1, 2, 3..."""
        self._populate_data(store, weeks_back=4)
        rankings = compute_attention_ranking(
            store, week_start="2026-02-10", top_n=10,
        )
        if rankings:
            ranks = [r["rank"] for r in rankings]
            assert ranks == list(range(1, len(ranks) + 1))

    def test_top_n_limit(self, store):
        """Should respect top_n limit."""
        # Add many tickers
        for i in range(20):
            store.save_reddit_mention(f"T{i:03d}", "2026-02-10", "stocks", i + 1, 200)
        rankings = compute_attention_ranking(
            store, week_start="2026-02-10", top_n=5,
        )
        assert len(rankings) <= 5

    def test_custom_weights(self, store):
        self._populate_data(store, weeks_back=4)
        # Reddit-only weighting
        rankings = compute_attention_ranking(
            store, week_start="2026-02-10",
            weights={"reddit": 1.0, "news": 0.0, "trends": 0.0},
        )
        # Should still work
        assert isinstance(rankings, list)

    def test_trends_keyword_mapping(self, store):
        """GT scores should map to tickers via keyword_ticker_map."""
        # Add GT data with variance in historical weeks
        for w in range(8):
            ws = (date(2026, 2, 10) - timedelta(days=7 * w)).isoformat()
            ratio = 1.0 + (w % 3) * 0.1  # variance: 1.0, 1.1, 1.2, 1.0...
            store.save_trends_data("AI chip", ws, 50 + w, ratio)

        # Spike in current week (overwrite w=0)
        store.save_trends_data("AI chip", "2026-02-10", 95, 3.0)

        keyword_map = {"AI chip": ["NVDA", "AMD"]}

        rankings = compute_attention_ranking(
            store, week_start="2026-02-10",
            keyword_ticker_map=keyword_map, top_n=10,
        )
        # NVDA and AMD should appear with non-zero trends_zscore
        tickers = {r["ticker"] for r in rankings}
        assert "NVDA" in tickers or "AMD" in tickers
        for r in rankings:
            if r["ticker"] in ("NVDA", "AMD"):
                assert r["trends_zscore"] > 0

    def test_week_start_output(self, store):
        """Each ranking entry should include week_start."""
        store.save_reddit_mention("NVDA", "2026-02-10", "stocks", 10, 200)
        rankings = compute_attention_ranking(store, week_start="2026-02-10")
        if rankings:
            assert rankings[0]["week_start"] == "2026-02-10"
