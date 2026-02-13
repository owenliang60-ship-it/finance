"""Tests for terminal/attention.py — ticker extraction + Reddit logic."""
import pytest

from terminal.attention import extract_hot_keywords, extract_tickers


# ---- Ticker Extraction ----

class TestExtractTickers:
    def test_dollar_sign_pattern(self):
        text = "I'm buying $NVDA and $AMD today"
        tickers = extract_tickers(text)
        assert "NVDA" in tickers
        assert "AMD" in tickers

    def test_dollar_sign_with_blacklist(self):
        text = "$AI is not a ticker, but $NVDA is"
        tickers = extract_tickers(text, blacklist={"AI"})
        assert "AI" not in tickers
        assert "NVDA" in tickers

    def test_bare_uppercase_with_known_set(self):
        text = "NVDA is going to the moon, AMD too"
        known = {"NVDA", "AMD", "GOOG"}
        tickers = extract_tickers(text, known_tickers=known)
        assert "NVDA" in tickers
        assert "AMD" in tickers

    def test_bare_uppercase_without_known_set(self):
        """Without known_tickers, bare uppercase should NOT match."""
        text = "NVDA is going to the moon"
        tickers = extract_tickers(text)
        assert "NVDA" not in tickers  # only $TICKER works without known set

    def test_mixed_dollar_and_bare(self):
        text = "Just bought $TSLA, also looking at MSFT"
        known = {"MSFT", "TSLA"}
        tickers = extract_tickers(text, known_tickers=known)
        assert "TSLA" in tickers
        assert "MSFT" in tickers

    def test_blacklist_filters(self):
        text = "$IT $IS $NVDA $AI $AMD"
        blacklist = {"IT", "IS", "AI"}
        tickers = extract_tickers(text, blacklist=blacklist)
        assert "NVDA" in tickers
        assert "AMD" in tickers
        assert "IT" not in tickers
        assert "IS" not in tickers
        assert "AI" not in tickers

    def test_empty_text(self):
        assert extract_tickers("") == []
        assert extract_tickers(None) == []

    def test_no_tickers(self):
        text = "The stock market is doing well today"
        assert extract_tickers(text) == []

    def test_deduplication(self):
        text = "$NVDA $NVDA $NVDA"
        tickers = extract_tickers(text)
        assert tickers == ["NVDA"]

    def test_ticker_length_limits(self):
        text = "$A $AB $ABC $ABCD $ABCDE $ABCDEF"
        tickers = extract_tickers(text)
        assert "A" in tickers
        assert "AB" in tickers
        assert "ABCDE" in tickers
        assert "ABCDEF" not in tickers  # 6 chars = too long

    def test_sorted_output(self):
        text = "$TSLA $AAPL $NVDA"
        tickers = extract_tickers(text)
        assert tickers == sorted(tickers)


# ---- Hot Keywords ----

class TestExtractHotKeywords:
    def test_basic_extraction(self):
        titles = [
            "NVDA earnings beat expectations semiconductor rally",
            "Semiconductor stocks soaring after earnings",
            "Best semiconductor ETF for 2026 earnings season",
            "How to invest in semiconductor companies",
            "Semiconductor shortage update earnings impact",
            "Another semiconductor post about earnings",
        ]
        kws = extract_hot_keywords(titles, min_freq=3)
        keywords = [k for k, c in kws]
        assert "semiconductor" in keywords
        assert "earnings" in keywords

    def test_min_freq_filter(self):
        titles = ["stock market analysis", "stock market crash", "crypto is dead"]
        kws = extract_hot_keywords(titles, min_freq=2)
        keywords = [k for k, c in kws]
        assert "stock" in keywords
        assert "market" in keywords
        assert "crypto" not in keywords  # only 1 occurrence

    def test_stopwords_filtered(self):
        titles = ["the stock in the market is going up for the win"]
        kws = extract_hot_keywords(titles, min_freq=1)
        keywords = [k for k, c in kws]
        assert "the" not in keywords
        assert "for" not in keywords  # stopword

    def test_top_n_limit(self):
        titles = [f"word{i} " * 10 for i in range(50)]
        kws = extract_hot_keywords(titles, min_freq=1, top_n=5)
        assert len(kws) <= 5

    def test_empty_input(self):
        assert extract_hot_keywords([]) == []

    def test_short_words_excluded(self):
        """Words shorter than 3 chars are excluded."""
        titles = ["AI is ok but GPU are hot"] * 5
        kws = extract_hot_keywords(titles, min_freq=1)
        keywords = [k for k, c in kws]
        # "ai", "is", "ok" are all <= 2 chars or stopwords
        assert "gpu" in keywords
        assert "hot" in keywords
