"""Tests for scripts/broad_market_scan.py."""
import sys
from pathlib import Path

import pandas as pd

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import scripts.broad_market_scan as broad_market_scan
from scripts.broad_market_scan import (
    _deduplicate_quotes,
    format_broad_scan_report,
    load_price_frames,
    load_price_frames_from_market_db,
    normalize_downloaded_frames,
    update_streak_tracker,
)


class TestDeduplicateQuotes:
    def test_keep_largest_share_class(self):
        quotes = [
            {
                "symbol": "GOOG",
                "quoteType": "EQUITY",
                "longName": "Alphabet Inc.",
                "shortName": "Alphabet C",
                "marketCap": 3_700_000_000_000,
                "exchange": "NMS",
            },
            {
                "symbol": "GOOGL",
                "quoteType": "EQUITY",
                "longName": "Alphabet Inc.",
                "shortName": "Alphabet A",
                "marketCap": 3_710_000_000_000,
                "exchange": "NMS",
            },
        ]

        result = _deduplicate_quotes(quotes)

        assert list(result.keys()) == ["GOOGL"]
        assert result["GOOGL"]["marketCap"] == 3_710_000_000_000


class TestNormalizeDownloadedFrames:
    def test_extracts_per_ticker_close_and_volume(self):
        index = pd.date_range("2026-03-16", periods=3, freq="D")
        columns = pd.MultiIndex.from_tuples([
            ("Close", "AAPL"),
            ("Close", "MSFT"),
            ("Volume", "AAPL"),
            ("Volume", "MSFT"),
        ])
        data = pd.DataFrame(
            [
                [200.0, 300.0, 10_000, 20_000],
                [201.0, 301.0, 11_000, 21_000],
                [202.0, 302.0, 12_000, 22_000],
            ],
            index=index,
            columns=columns,
        )

        result = normalize_downloaded_frames(data, ["AAPL", "MSFT"])

        assert set(result.keys()) == {"AAPL", "MSFT"}
        assert list(result["AAPL"].columns) == ["close", "volume"]
        assert result["AAPL"]["close"].iloc[-1] == 202.0
        assert result["MSFT"]["volume"].iloc[0] == 20_000

    def test_drops_missing_rows_per_symbol(self):
        index = pd.date_range("2026-03-16", periods=3, freq="D")
        columns = pd.MultiIndex.from_tuples([
            ("Close", "AAPL"),
            ("Volume", "AAPL"),
        ])
        data = pd.DataFrame(
            [
                [200.0, 10_000],
                [201.0, None],
                [202.0, 12_000],
            ],
            index=index,
            columns=columns,
        )

        result = normalize_downloaded_frames(data, ["AAPL"])

        assert len(result["AAPL"]) == 2
        assert result["AAPL"].index.min() == index[0]
        assert result["AAPL"].index.max() == index[2]


class TestLoadPriceFramesFromMarketDb:
    def test_loads_recent_rows_sorted_ascending(self, monkeypatch):
        rows = [
            {"symbol": "AAPL", "date": "2026-01-03", "close": 103.0, "volume": 1300},
            {"symbol": "AAPL", "date": "2026-01-02", "close": 102.0, "volume": 1200},
            {"symbol": "AAPL", "date": "2026-01-01", "close": 101.0, "volume": 1100},
        ]

        class FakeStore:
            def get_daily_prices(self, symbol, limit=0):
                return rows[:limit]

        import src.data.market_store as market_store

        monkeypatch.setattr(market_store, "get_store", lambda: FakeStore())

        result = load_price_frames_from_market_db(["AAPL"], rows_needed=3)

        assert set(result.keys()) == {"AAPL"}
        assert list(result["AAPL"]["close"]) == [101.0, 102.0, 103.0]
        assert list(result["AAPL"].columns) == ["close", "volume"]

    def test_skips_symbols_without_enough_rows(self, monkeypatch):
        class FakeStore:
            def get_daily_prices(self, symbol, limit=0):
                return [{"symbol": symbol, "date": "2026-01-01", "close": 1.0, "volume": 100}]

        import src.data.market_store as market_store

        monkeypatch.setattr(market_store, "get_store", lambda: FakeStore())

        assert load_price_frames_from_market_db(["AAPL"], rows_needed=3) == {}

    def test_skips_symbols_not_fresh_to_latest_date(self, monkeypatch):
        rows_by_symbol = {
            "AAPL": [
                {"symbol": "AAPL", "date": "2026-01-03", "close": 103.0, "volume": 1300},
                {"symbol": "AAPL", "date": "2026-01-02", "close": 102.0, "volume": 1200},
            ],
            "MSFT": [
                {"symbol": "MSFT", "date": "2026-01-02", "close": 202.0, "volume": 2200},
                {"symbol": "MSFT", "date": "2026-01-01", "close": 201.0, "volume": 2100},
            ],
        }

        class FakeStore:
            def get_daily_prices(self, symbol, limit=0):
                return rows_by_symbol[symbol][:limit]

        import src.data.market_store as market_store

        monkeypatch.setattr(market_store, "get_store", lambda: FakeStore())

        result = load_price_frames_from_market_db(["AAPL", "MSFT"], rows_needed=2)

        assert set(result.keys()) == {"AAPL"}


class TestLoadPriceFrames:
    def test_uses_market_db_without_download_when_coverage_is_sufficient(self, monkeypatch):
        frame = pd.DataFrame(
            {"close": [1.0, 2.0], "volume": [100.0, 200.0]},
            index=pd.date_range("2026-01-01", periods=2),
        )

        monkeypatch.setattr(
            broad_market_scan,
            "load_price_frames_from_market_db",
            lambda symbols, rows_needed=121: {symbol: frame for symbol in symbols},
        )
        monkeypatch.setattr(
            broad_market_scan,
            "download_price_frames",
            lambda _symbols: (_ for _ in ()).throw(AssertionError("should not download")),
        )

        result = load_price_frames(["AAPL", "MSFT"])

        assert set(result.keys()) == {"AAPL", "MSFT"}

    def test_falls_back_to_download_when_db_coverage_is_low(self, monkeypatch):
        frame = pd.DataFrame(
            {"close": [1.0, 2.0], "volume": [100.0, 200.0]},
            index=pd.date_range("2026-01-01", periods=2),
        )
        requested = []

        monkeypatch.setattr(
            broad_market_scan,
            "load_price_frames_from_market_db",
            lambda _symbols, rows_needed=121: {"AAPL": frame},
        )

        def fake_download(symbols, period="6mo"):
            requested.extend(symbols)
            return {symbol: frame for symbol in symbols}

        monkeypatch.setattr(broad_market_scan, "download_price_frames", fake_download)

        result = load_price_frames(["AAPL", "MSFT"])

        assert requested == ["MSFT"]
        assert set(result.keys()) == {"AAPL", "MSFT"}


class TestUpdateStreakTracker:
    def test_extends_streak_when_last_seen_matches_previous_scan(self):
        tracker = {
            "_meta": {"last_scan_date": "2026-03-21"},
            "RKLB": {
                "first_seen": "2026-03-19",
                "last_seen": "2026-03-21",
                "consecutive_days": 2,
                "appearances": 2,
                "max_rvol": 4.1,
                "max_return": 8.5,
            },
        }
        candidates = [{"symbol": "RKLB", "rvol": 4.8, "return_pct": 9.2}]

        updated = update_streak_tracker(tracker, candidates, "2026-03-24")

        assert updated["RKLB"]["consecutive_days"] == 3
        assert updated["RKLB"]["appearances"] == 3
        assert updated["RKLB"]["last_seen"] == "2026-03-24"
        assert updated["_meta"]["last_scan_date"] == "2026-03-24"

    def test_resets_streak_when_symbol_missed_previous_scan(self):
        tracker = {
            "_meta": {"last_scan_date": "2026-03-21"},
            "OKLO": {
                "first_seen": "2026-03-19",
                "last_seen": "2026-03-20",
                "consecutive_days": 2,
                "appearances": 2,
                "max_rvol": 5.0,
                "max_return": 7.0,
            },
        }
        candidates = [{"symbol": "OKLO", "rvol": 5.1, "return_pct": 8.3}]

        updated = update_streak_tracker(tracker, candidates, "2026-03-24")

        assert updated["OKLO"]["consecutive_days"] == 1
        assert updated["OKLO"]["appearances"] == 3
        assert updated["OKLO"]["first_seen"] == "2026-03-19"

    def test_cleans_up_stale_records(self):
        tracker = {
            "_meta": {"last_scan_date": "2026-03-21"},
            "OLD": {
                "first_seen": "2026-01-01",
                "last_seen": "2026-01-15",
                "consecutive_days": 1,
                "appearances": 1,
                "max_rvol": 3.1,
                "max_return": 4.0,
            },
        }

        updated = update_streak_tracker(tracker, [], "2026-03-24")

        assert "OLD" not in updated
        assert updated["_meta"]["last_scan_date"] == "2026-03-24"


class TestFormatBroadScanReport:
    def test_formats_sections_and_stats(self):
        candidates = [
            {"symbol": "RKLB", "rvol": 4.8, "return_pct": 9.2, "consecutive_days": 3, "marketCap": 9_200_000_000},
            {"symbol": "OKLO", "rvol": 5.1, "return_pct": 8.3, "consecutive_days": 1, "marketCap": 6_800_000_000},
        ]

        report = format_broad_scan_report(
            candidates=candidates,
            symbols_scanned=1372,
            triggered_total=14,
            outside_total=9,
            scan_date="2026-03-24",
            min_mcap_b=5.0,
        )

        assert "RKLB" in report
        assert "OKLO" in report
        assert "连续3天" in report
        assert "首次" in report
        assert "市值≥$50亿" in report
        assert "扫描 1,372只 | 触发 14只 | 池外 9只" in report
