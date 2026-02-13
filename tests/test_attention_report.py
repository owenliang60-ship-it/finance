"""Tests for terminal/attention_report.py — HTML report generation."""
import pytest
from pathlib import Path

from terminal.attention_store import AttentionStore
from terminal.attention_report import (
    _build_html,
    _build_ranking_table,
    _build_trend_chart,
    _build_keyword_table,
    _change_badge,
    _score_badge,
    _z_cell,
    _rank_change_indicator,
    generate_attention_report,
)


@pytest.fixture
def store(tmp_path):
    db_path = tmp_path / "test_report.db"
    s = AttentionStore(db_path=db_path)
    yield s
    s.close()


@pytest.fixture
def sample_rankings():
    return [
        {"ticker": "NVDA", "composite_score": 3.5, "reddit_zscore": 2.8,
         "news_zscore": 2.5, "trends_zscore": 1.2, "rank": 1, "week_start": "2026-02-10"},
        {"ticker": "AMD", "composite_score": 2.1, "reddit_zscore": 1.5,
         "news_zscore": 1.8, "trends_zscore": 0.8, "rank": 2, "week_start": "2026-02-10"},
        {"ticker": "MU", "composite_score": 1.5, "reddit_zscore": 1.0,
         "news_zscore": 1.2, "trends_zscore": 0.5, "rank": 3, "week_start": "2026-02-10"},
    ]


# ---- Helper functions ----

class TestHelpers:
    def test_change_badge_positive(self):
        badge = _change_badge(15.5)
        assert "+15.5%" in badge
        assert "change-up" in badge

    def test_change_badge_negative(self):
        badge = _change_badge(-8.3)
        assert "-8.3%" in badge
        assert "change-down" in badge

    def test_change_badge_zero(self):
        badge = _change_badge(0.0)
        assert "0%" in badge

    def test_change_badge_none(self):
        assert _change_badge(None) == ""

    def test_score_badge_hot(self):
        badge = _score_badge(2.5)
        assert "dc3545" in badge  # red

    def test_score_badge_dim(self):
        badge = _score_badge(0.2)
        assert "7a7265" in badge  # dim

    def test_z_cell_positive(self):
        cell = _z_cell(2.5)
        assert "2.50" in cell
        assert "dc3545" in cell  # red for high z

    def test_z_cell_zero(self):
        cell = _z_cell(0.0)
        assert "—" in cell  # em dash for zero

    def test_z_cell_negative(self):
        cell = _z_cell(-1.5)
        assert "-1.50" in cell
        assert "2563eb" in cell  # blue for negative

    def test_rank_change_new(self):
        result = _rank_change_indicator("NEW_TICKER", 5, {})
        assert "NEW" in result

    def test_rank_change_up(self):
        prev = {"NVDA": {"rank": 5}}
        result = _rank_change_indicator("NVDA", 2, prev)
        assert "+3" in result
        assert "rank-up" in result

    def test_rank_change_down(self):
        prev = {"NVDA": {"rank": 1}}
        result = _rank_change_indicator("NVDA", 4, prev)
        assert "-3" in result
        assert "rank-down" in result


# ---- Ranking Table ----

class TestRankingTable:
    def test_table_structure(self, sample_rankings):
        html = _build_ranking_table(sample_rankings, {})
        assert "<table" in html
        assert "<thead>" in html
        assert "<tbody>" in html
        assert "NVDA" in html
        assert "AMD" in html
        assert "MU" in html

    def test_table_has_all_columns(self, sample_rankings):
        html = _build_ranking_table(sample_rankings, {})
        assert "综合分" in html
        assert "Reddit Z" in html
        assert "News Z" in html
        assert "GT Z" in html


# ---- Trend Chart ----

class TestTrendChart:
    def test_chart_with_data(self):
        trend_data = {
            "NVDA": [
                {"week_start": "2026-01-06", "composite_score": 2.0},
                {"week_start": "2026-01-13", "composite_score": 2.5},
                {"week_start": "2026-01-20", "composite_score": 3.0},
            ],
            "AMD": [
                {"week_start": "2026-01-06", "composite_score": 1.0},
                {"week_start": "2026-01-13", "composite_score": 1.5},
                {"week_start": "2026-01-20", "composite_score": 1.8},
            ],
        }
        svg = _build_trend_chart(trend_data, ["NVDA", "AMD"])
        assert "<svg" in svg
        assert "NVDA" in svg
        assert "AMD" in svg
        assert "<path" in svg  # lines

    def test_chart_empty_data(self):
        result = _build_trend_chart({}, [])
        assert "No trend data" in result

    def test_chart_single_ticker(self):
        trend_data = {
            "NVDA": [
                {"week_start": "2026-01-06", "composite_score": 2.0},
                {"week_start": "2026-01-13", "composite_score": 2.5},
            ],
        }
        svg = _build_trend_chart(trend_data, ["NVDA"])
        assert "<svg" in svg
        assert "NVDA" in svg


# ---- Keyword Table ----

class TestKeywordTable:
    def test_keyword_table(self):
        data = [
            {"keyword": "AI chip", "category": "ai", "tickers": ["NVDA"],
             "current_score": 85, "prev_score": 60, "change_pct": 41.7},
            {"keyword": "DRAM", "category": "memory", "tickers": ["MU"],
             "current_score": 40, "prev_score": 50, "change_pct": -20.0},
        ]
        html = _build_keyword_table(data)
        assert "AI chip" in html
        assert "DRAM" in html
        assert "change-up" in html  # +41.7%
        assert "NVDA" in html


# ---- Full HTML Generation ----

class TestFullReport:
    def test_generate_report_creates_file(self, store, tmp_path, sample_rankings):
        # Populate some data
        store.save_keyword("AI chip", source="manual", tickers=["NVDA"], category="ai")
        store.save_reddit_mention("NVDA", "2026-02-10", "stocks", 30, 200)
        store.save_news_mention("NVDA", "2026-02-10", 50, 0.3)

        for r in sample_rankings:
            store.save_attention_snapshot(r["ticker"], r["week_start"], r)

        output_dir = tmp_path / "reports"
        path = generate_attention_report(
            "2026-02-10", sample_rankings, output_dir=output_dir,
        )
        assert path.exists()
        content = path.read_text()
        assert "注意力雷达" in content
        assert "CONFIDENTIAL" in content
        assert "NVDA" in content

    def test_full_html_structure(self, sample_rankings):
        stats = {
            "tickers_scanned": 120,
            "reddit_total": 5000,
            "reddit_prev": 4500,
            "reddit_change_pct": 11.1,
            "news_total": 3000,
            "news_prev": 2800,
            "news_change_pct": 7.1,
            "active_keywords": 15,
        }
        doc = _build_html(
            week_start="2026-02-10",
            rankings=sample_rankings,
            stats=stats,
            trend_data={},
            top5_tickers=["NVDA", "AMD", "MU"],
            keyword_data=[],
            new_tickers=["IONQ"],
            prev_map={},
        )
        assert "<!DOCTYPE html>" in doc
        assert "</html>" in doc
        assert "注意力雷达" in doc
        assert "CONFIDENTIAL" in doc
        assert "120" in doc  # tickers scanned
        assert "IONQ" in doc  # new discovery
        assert "NEW DISCOVERIES" in doc
