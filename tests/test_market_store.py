"""Tests for MarketStore (src/data/market_store.py)."""
import sqlite3
import tempfile
from pathlib import Path

import pandas as pd
import pytest

from src.data.market_store import MarketStore, _camel_to_snake, get_store


@pytest.fixture
def store(tmp_path):
    """Create a fresh MarketStore backed by a temp DB."""
    db_path = tmp_path / "test_market.db"
    s = MarketStore(db_path=db_path)
    yield s
    s.close()


# ---------------------------------------------------------------------------
# camelCase → snake_case
# ---------------------------------------------------------------------------

class TestCamelToSnake:
    def test_simple(self):
        assert _camel_to_snake("netIncome") == "net_income"

    def test_consecutive_caps(self):
        assert _camel_to_snake("ebitda") == "ebitda"
        # All-caps acronyms lowercase without underscores (regex only splits at case transitions)
        assert _camel_to_snake("EBITDA") == "ebitda"

    def test_already_snake(self):
        assert _camel_to_snake("net_income") == "net_income"

    def test_multi_word(self):
        assert _camel_to_snake("totalStockholdersEquity") == "total_stockholders_equity"

    def test_abbreviation(self):
        assert _camel_to_snake("epsDiluted") == "eps_diluted"

    def test_change_percent(self):
        assert _camel_to_snake("changePercent") == "change_percent"


# ---------------------------------------------------------------------------
# Daily Price
# ---------------------------------------------------------------------------

class TestDailyPrice:
    def _sample_rows(self):
        return [
            {"date": "2024-01-02", "open": 100.0, "high": 105.0,
             "low": 99.0, "close": 104.0, "volume": 1000000,
             "change": 4.0, "changePercent": 4.0},
            {"date": "2024-01-03", "open": 104.0, "high": 106.0,
             "low": 103.0, "close": 105.5, "volume": 800000,
             "change": 1.5, "changePercent": 1.44},
        ]

    def test_upsert_and_get(self, store):
        count = store.upsert_daily_prices("AAPL", self._sample_rows())
        assert count == 2

        rows = store.get_daily_prices("AAPL")
        assert len(rows) == 2
        assert rows[0]["symbol"] == "AAPL"
        assert rows[0]["close"] == 105.5  # Most recent first

    def test_idempotent(self, store):
        store.upsert_daily_prices("AAPL", self._sample_rows())
        store.upsert_daily_prices("AAPL", self._sample_rows())
        rows = store.get_daily_prices("AAPL")
        assert len(rows) == 2  # No duplicates

    def test_date_range(self, store):
        store.upsert_daily_prices("AAPL", self._sample_rows())
        rows = store.get_daily_prices("AAPL", start_date="2024-01-03")
        assert len(rows) == 1
        assert rows[0]["date"] == "2024-01-03"

    def test_limit(self, store):
        store.upsert_daily_prices("AAPL", self._sample_rows())
        rows = store.get_daily_prices("AAPL", limit=1)
        assert len(rows) == 1

    def test_dataframe_upsert(self, store):
        df = pd.DataFrame({
            "date": pd.to_datetime(["2024-01-02", "2024-01-03"]),
            "open": [100.0, 104.0],
            "high": [105.0, 106.0],
            "low": [99.0, 103.0],
            "close": [104.0, 105.5],
            "volume": [1000000, 800000],
            "change": [4.0, 1.5],
            "changePercent": [4.0, 1.44],
        })
        count = store.upsert_daily_prices_df("AAPL", df)
        assert count == 2

        rows = store.get_daily_prices("AAPL")
        assert len(rows) == 2
        # change_pct should be mapped
        assert rows[0]["change_pct"] is not None

    def test_empty_df(self, store):
        count = store.upsert_daily_prices_df("AAPL", pd.DataFrame())
        assert count == 0

    def test_none_df(self, store):
        count = store.upsert_daily_prices_df("AAPL", None)
        assert count == 0

    def test_symbol_case_insensitive(self, store):
        store.upsert_daily_prices("aapl", self._sample_rows())
        rows = store.get_daily_prices("AAPL")
        assert len(rows) == 2


# ---------------------------------------------------------------------------
# Income Quarterly
# ---------------------------------------------------------------------------

class TestIncomeQuarterly:
    def _sample_rows(self):
        return [
            {
                "date": "2024-09-28", "symbol": "AAPL",
                "reportedCurrency": "USD", "cik": "0000320193",
                "filingDate": "2024-11-01", "acceptedDate": "2024-11-01",
                "fiscalYear": "2024", "period": "Q4",
                "revenue": 94930000000, "costOfRevenue": 52254000000,
                "grossProfit": 42676000000, "netIncome": 14736000000,
                "operatingIncome": 30561000000, "ebitda": 33585000000,
                "eps": 0.97, "epsDiluted": 0.97,
            },
            {
                "date": "2024-06-29", "symbol": "AAPL",
                "reportedCurrency": "USD", "cik": "0000320193",
                "filingDate": "2024-08-02", "acceptedDate": "2024-08-02",
                "fiscalYear": "2024", "period": "Q3",
                "revenue": 85778000000, "costOfRevenue": 46099000000,
                "grossProfit": 39679000000, "netIncome": 21448000000,
                "operatingIncome": 26688000000, "ebitda": 29754000000,
                "eps": 1.40, "epsDiluted": 1.40,
            },
        ]

    def test_upsert_and_get(self, store):
        count = store.upsert_income("AAPL", self._sample_rows())
        assert count == 2

        rows = store.get_income("AAPL")
        assert len(rows) == 2
        # camelCase should be converted
        assert "revenue" in rows[0]
        assert "operating_income" in rows[0]

    def test_unknown_fields_ignored(self, store):
        """Fields not in schema should be silently dropped."""
        rows = [{
            "date": "2024-09-28", "symbol": "AAPL",
            "unknownField": "should_be_dropped",
            "revenue": 100, "period": "Q4", "fiscalYear": "2024",
        }]
        count = store.upsert_income("AAPL", rows)
        assert count == 1

        result = store.get_income("AAPL")
        assert len(result) == 1
        # unknown_field should not exist
        assert "unknown_field" not in result[0]

    def test_missing_fields_null(self, store):
        """Missing fields should be NULL."""
        rows = [{"date": "2024-09-28", "revenue": 100}]
        count = store.upsert_income("AAPL", rows)
        assert count == 1

        result = store.get_income("AAPL")
        assert result[0]["net_income"] is None

    def test_idempotent(self, store):
        store.upsert_income("AAPL", self._sample_rows())
        store.upsert_income("AAPL", self._sample_rows())
        rows = store.get_income("AAPL")
        assert len(rows) == 2


# ---------------------------------------------------------------------------
# Balance Sheet, Cash Flow, Ratios — basic CRUD
# ---------------------------------------------------------------------------

class TestBalanceSheet:
    def test_upsert_and_get(self, store):
        rows = [{
            "date": "2024-09-28", "symbol": "AAPL",
            "fiscalYear": "2024", "period": "Q4",
            "totalAssets": 364980000000,
            "totalCurrentAssets": 152987000000,
            "totalCurrentLiabilities": 176392000000,
            "totalStockholdersEquity": 56950000000,
            "totalDebt": 97300000000,
            "inventory": 6331000000,
            "netReceivables": 66243000000,
            "cashAndCashEquivalents": 29943000000,
        }]
        count = store.upsert_balance_sheet("AAPL", rows)
        assert count == 1

        result = store.get_balance_sheet("AAPL")
        assert len(result) == 1
        assert result[0]["total_assets"] == 364980000000


class TestCashFlow:
    def test_upsert_and_get(self, store):
        rows = [{
            "date": "2024-09-28", "symbol": "AAPL",
            "fiscalYear": "2024", "period": "Q4",
            "operatingCashFlow": 26800000000,
            "capitalExpenditure": -2900000000,
            "freeCashFlow": 23900000000,
            "netIncome": 14736000000,
        }]
        count = store.upsert_cash_flow("AAPL", rows)
        assert count == 1

        result = store.get_cash_flow("AAPL")
        assert result[0]["free_cash_flow"] == 23900000000


class TestRatios:
    def test_upsert_and_get(self, store):
        rows = [{
            "date": "2024-09-28", "symbol": "AAPL",
            "fiscalYear": "2024", "period": "FY",
            "grossProfitMargin": 0.462, "netProfitMargin": 0.264,
            "returnOnEquity": None,  # ratios table doesn't have ROE directly
            "currentRatio": 0.867,
        }]
        count = store.upsert_ratios("AAPL", rows)
        assert count == 1

        result = store.get_ratios("AAPL")
        assert result[0]["gross_profit_margin"] == 0.462


# ---------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------

class TestMetrics:
    def test_upsert_and_get(self, store):
        rows = [{
            "symbol": "AAPL", "date": "2024-09-28",
            "period": "Q4", "fiscal_year": "2024",
            "gross_margin": 0.45, "net_margin": 0.155,
            "roe": 1.61, "roa": 0.30,
        }]
        count = store.upsert_metrics("AAPL", rows)
        assert count == 1

        result = store.get_metrics("AAPL")
        assert len(result) == 1
        assert result[0]["net_margin"] == 0.155


# ---------------------------------------------------------------------------
# Screener
# ---------------------------------------------------------------------------

class TestScreen:
    @pytest.fixture(autouse=True)
    def _seed_data(self, store):
        """Seed metrics for screening tests."""
        store.upsert_metrics("AAPL", [
            {"symbol": "AAPL", "date": "2024-09-28", "period": "Q4", "fiscal_year": "2024",
             "net_margin": 0.26, "roe": 1.61, "revenue_growth_yoy": 0.05},
            {"symbol": "AAPL", "date": "2024-06-29", "period": "Q3", "fiscal_year": "2024",
             "net_margin": 0.25, "roe": 1.50, "revenue_growth_yoy": 0.04},
            {"symbol": "AAPL", "date": "2024-03-30", "period": "Q2", "fiscal_year": "2024",
             "net_margin": 0.27, "roe": 1.55, "revenue_growth_yoy": 0.06},
            {"symbol": "AAPL", "date": "2023-12-30", "period": "Q1", "fiscal_year": "2024",
             "net_margin": 0.28, "roe": 1.60, "revenue_growth_yoy": 0.08},
        ])
        store.upsert_metrics("NVDA", [
            {"symbol": "NVDA", "date": "2024-10-27", "period": "Q3", "fiscal_year": "2025",
             "net_margin": 0.55, "roe": 1.15, "revenue_growth_yoy": 0.94},
            {"symbol": "NVDA", "date": "2024-07-28", "period": "Q2", "fiscal_year": "2025",
             "net_margin": 0.56, "roe": 1.20, "revenue_growth_yoy": 1.22},
        ])
        store.upsert_metrics("LOW_MARGIN", [
            {"symbol": "LOW_MARGIN", "date": "2024-09-28", "period": "Q4", "fiscal_year": "2024",
             "net_margin": 0.05, "roe": 0.10, "revenue_growth_yoy": -0.10},
        ])

    def test_basic_filter(self, store):
        results = store.screen({"net_margin >": 0.20})
        symbols = {r["symbol"] for r in results}
        assert "AAPL" in symbols
        assert "NVDA" in symbols
        assert "LOW_MARGIN" not in symbols

    def test_multiple_filters(self, store):
        results = store.screen({"net_margin >": 0.20, "roe >": 1.50})
        symbols = {r["symbol"] for r in results}
        assert "AAPL" in symbols
        assert "LOW_MARGIN" not in symbols

    def test_latest_only(self, store):
        """Default latest_only=True should return one row per symbol."""
        results = store.screen({"net_margin >": 0.0})
        symbols = [r["symbol"] for r in results]
        assert len(symbols) == len(set(symbols))

    def test_all_rows(self, store):
        """latest_only=False returns all matching rows."""
        results = store.screen({"net_margin >": 0.0}, latest_only=False)
        assert len(results) > 3  # Multiple rows per symbol

    def test_order_by(self, store):
        results = store.screen({"net_margin >": 0.0}, order_by="net_margin")
        assert results[0]["net_margin"] >= results[-1]["net_margin"]

    def test_limit(self, store):
        results = store.screen({"net_margin >": 0.0}, limit=1)
        assert len(results) == 1

    def test_empty_filters(self, store):
        """Empty filters should return all latest rows."""
        results = store.screen({})
        assert len(results) == 3

    def test_invalid_table(self, store):
        with pytest.raises(ValueError, match="Invalid table name"):
            store.screen({}, table="; DROP TABLE metrics_quarterly;")

    def test_invalid_column(self, store):
        with pytest.raises(ValueError, match="Invalid column name"):
            store.screen({"nonexistent >": 0})

    def test_invalid_filter_format(self, store):
        with pytest.raises(ValueError, match="Invalid filter key format"):
            store.screen({"net_margin": 0.25})  # Missing operator

    def test_multi_quarter_screen(self, store):
        """AAPL has 4 quarters all with net_margin > 0.20."""
        result = store.get_multi_quarter_screen("net_margin", ">", 0.20, min_quarters=4)
        assert "AAPL" in result
        assert "NVDA" not in result  # Only 2 quarters
        assert "LOW_MARGIN" not in result

    def test_multi_quarter_partial(self, store):
        """NVDA has 2 quarters with high margin — should match min_quarters=2."""
        result = store.get_multi_quarter_screen("net_margin", ">", 0.50, min_quarters=2)
        assert "NVDA" in result
        assert "AAPL" not in result

    def test_multi_quarter_null_in_recent_disqualifies(self, store):
        """A NULL in the most recent quarter should disqualify the symbol,
        not silently shift the window to older data."""
        store.upsert_metrics("NULL_RECENT", [
            # Most recent quarter has NULL net_margin
            {"symbol": "NULL_RECENT", "date": "2024-09-28", "period": "Q4",
             "fiscal_year": "2024", "net_margin": None, "roe": 1.0},
            # Prior 3 quarters all pass
            {"symbol": "NULL_RECENT", "date": "2024-06-29", "period": "Q3",
             "fiscal_year": "2024", "net_margin": 0.30, "roe": 1.0},
            {"symbol": "NULL_RECENT", "date": "2024-03-30", "period": "Q2",
             "fiscal_year": "2024", "net_margin": 0.30, "roe": 1.0},
            {"symbol": "NULL_RECENT", "date": "2023-12-30", "period": "Q1",
             "fiscal_year": "2024", "net_margin": 0.30, "roe": 1.0},
        ])
        # With 4 most recent quarters, only 3 pass → should NOT match min_quarters=4
        result = store.get_multi_quarter_screen("net_margin", ">", 0.20, min_quarters=4)
        assert "NULL_RECENT" not in result

    def test_multi_quarter_invalid_operator(self, store):
        with pytest.raises(ValueError, match="Invalid operator"):
            store.get_multi_quarter_screen("net_margin", "LIKE", 0.25)


# ---------------------------------------------------------------------------
# Stats
# ---------------------------------------------------------------------------

class TestStats:
    def test_get_stats(self, store):
        store.upsert_income("AAPL", [{"date": "2024-09-28", "revenue": 100}])
        stats = store.get_stats()
        assert stats["income_quarterly"] == 1
        assert stats["daily_price"] == 0


# ---------------------------------------------------------------------------
# Singleton
# ---------------------------------------------------------------------------

class TestSingleton:
    def test_get_store_returns_same_instance(self, tmp_path):
        # Reset global
        import src.data.market_store as mod
        mod._store = None
        db = tmp_path / "singleton.db"
        s1 = get_store(db)
        s2 = get_store(db)
        assert s1 is s2
        s1.close()
        mod._store = None


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------

class TestEdgeCases:
    def test_empty_rows(self, store):
        count = store.upsert_income("AAPL", [])
        assert count == 0

    def test_row_without_date(self, store):
        """Rows without date should be skipped."""
        count = store.upsert_income("AAPL", [{"revenue": 100}])
        assert count == 0

    def test_nan_in_dataframe(self, store):
        """NaN values should be stored as NULL."""
        df = pd.DataFrame({
            "date": pd.to_datetime(["2024-01-02"]),
            "open": [float("nan")],
            "high": [105.0],
            "low": [99.0],
            "close": [104.0],
            "volume": [1000000],
        })
        store.upsert_daily_prices_df("AAPL", df)
        rows = store.get_daily_prices("AAPL")
        assert rows[0]["open"] is None
