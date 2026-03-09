# Forward Estimates: FMP → yfinance Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Replace dead FMP analyst-estimates with yfinance forward estimate data (6 datasets), stored in market.db, rendered as pre-digested signals in deep analysis context.

**Architecture:** Thin yfinance client → market.db (2 new tables) → pipeline reads from DB with live fallback → format_context() renders 3 sections (estimates table + momentum signals + price targets).

**Tech Stack:** yfinance, SQLite (market.db), existing MarketStore patterns

**Design doc:** `docs/plans/2026-03-09-forward-estimates-yfinance-design.md`

---

### Task 1: market.db Schema — Add forward_estimates and forward_metadata Tables

**Files:**
- Modify: `src/data/market_store.py:237-261` (append to `_SCHEMA`)
- Modify: `src/data/market_store.py:276-280` (`_VALID_TABLES` whitelist)
- Test: `tests/test_market_store.py`

**Step 1: Write failing tests**

In `tests/test_market_store.py`, add a new test class at the end:

```python
class TestForwardEstimates:
    """Test forward_estimates and forward_metadata tables."""

    def test_upsert_and_get_forward_estimates(self, tmp_path):
        store = MarketStore(db_path=tmp_path / "test.db")
        rows = [
            {"symbol": "AAPL", "date": "2026-03-09", "period": "0q",
             "eps_avg": 1.95, "eps_low": 1.85, "eps_high": 2.16,
             "eps_num_analysts": 29, "rev_avg": 109_079_879_710.0,
             "rev_growth": 0.1439, "eps_growth": 0.1846},
            {"symbol": "AAPL", "date": "2026-03-09", "period": "+1q",
             "eps_avg": 1.72, "eps_low": 1.59, "eps_high": 1.86,
             "eps_num_analysts": 27, "rev_avg": 101_642_789_290.0,
             "rev_growth": 0.0809, "eps_growth": 0.0986},
        ]
        count = store.upsert_forward_estimates("AAPL", rows)
        assert count == 2

        result = store.get_forward_estimates("AAPL")
        assert len(result) == 2
        assert result[0]["period"] in ("0q", "+1q")
        assert result[0]["eps_avg"] in (1.95, 1.72)

    def test_upsert_forward_estimates_replaces_on_conflict(self, tmp_path):
        store = MarketStore(db_path=tmp_path / "test.db")
        rows = [{"symbol": "AAPL", "date": "2026-03-09", "period": "0q",
                 "eps_avg": 1.95, "eps_low": 1.85, "eps_high": 2.16}]
        store.upsert_forward_estimates("AAPL", rows)

        # Update same PK with new data
        rows[0]["eps_avg"] = 2.05
        store.upsert_forward_estimates("AAPL", rows)

        result = store.get_forward_estimates("AAPL")
        assert len(result) == 1
        assert result[0]["eps_avg"] == 2.05

    def test_get_latest_forward_estimates(self, tmp_path):
        store = MarketStore(db_path=tmp_path / "test.db")
        # Two different fetch dates
        old = [{"symbol": "AAPL", "date": "2026-03-02", "period": "0q", "eps_avg": 1.90}]
        new = [{"symbol": "AAPL", "date": "2026-03-09", "period": "0q", "eps_avg": 1.95}]
        store.upsert_forward_estimates("AAPL", old)
        store.upsert_forward_estimates("AAPL", new)

        result = store.get_latest_forward_estimates("AAPL")
        assert len(result) == 1
        assert result[0]["date"] == "2026-03-09"
        assert result[0]["eps_avg"] == 1.95

    def test_upsert_and_get_forward_metadata(self, tmp_path):
        store = MarketStore(db_path=tmp_path / "test.db")
        rows = [{"symbol": "AAPL", "date": "2026-03-09",
                 "price_target_current": 257.46, "price_target_high": 350.0,
                 "price_target_low": 205.0, "price_target_mean": 292.15,
                 "price_target_median": 300.0}]
        count = store.upsert_forward_metadata("AAPL", rows)
        assert count == 1

        result = store.get_latest_forward_metadata("AAPL")
        assert result is not None
        assert result["price_target_mean"] == 292.15

    def test_empty_data_returns_empty(self, tmp_path):
        store = MarketStore(db_path=tmp_path / "test.db")
        assert store.get_latest_forward_estimates("AAPL") == []
        assert store.get_latest_forward_metadata("AAPL") is None
```

**Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/test_market_store.py::TestForwardEstimates -v`
Expected: FAIL — `MarketStore` has no `upsert_forward_estimates` method

**Step 3: Implement schema + methods**

In `src/data/market_store.py`:

1. Append to `_SCHEMA` (after line 260, before the closing `]`):

```python
    # -- Forward estimates (yfinance consensus) --
    """CREATE TABLE IF NOT EXISTS forward_estimates (
    symbol TEXT NOT NULL,
    date TEXT NOT NULL,
    period TEXT NOT NULL,
    eps_avg REAL, eps_low REAL, eps_high REAL,
    eps_year_ago REAL, eps_growth REAL, eps_num_analysts INTEGER,
    rev_avg REAL, rev_low REAL, rev_high REAL,
    rev_year_ago REAL, rev_growth REAL, rev_num_analysts INTEGER,
    growth_stock REAL, growth_index REAL,
    eps_trend_current REAL, eps_trend_7d REAL, eps_trend_30d REAL,
    eps_trend_60d REAL, eps_trend_90d REAL,
    eps_rev_up_7d INTEGER, eps_rev_up_30d INTEGER,
    eps_rev_down_7d INTEGER, eps_rev_down_30d INTEGER,
    PRIMARY KEY (symbol, date, period)
);""",
    "CREATE INDEX IF NOT EXISTS idx_fe_symbol ON forward_estimates(symbol);",
    "CREATE INDEX IF NOT EXISTS idx_fe_date ON forward_estimates(date);",

    # -- Forward metadata (price targets) --
    """CREATE TABLE IF NOT EXISTS forward_metadata (
    symbol TEXT NOT NULL,
    date TEXT NOT NULL,
    price_target_current REAL,
    price_target_high REAL,
    price_target_low REAL,
    price_target_mean REAL,
    price_target_median REAL,
    PRIMARY KEY (symbol, date)
);""",
    "CREATE INDEX IF NOT EXISTS idx_fm_symbol ON forward_metadata(symbol);",
```

2. Update `_VALID_TABLES` (line 276-280):

```python
_VALID_TABLES = frozenset({
    "daily_price", "income_quarterly", "balance_sheet_quarterly",
    "cash_flow_quarterly", "ratios_annual", "metrics_quarterly",
    "iv_daily", "options_snapshots",
    "forward_estimates", "forward_metadata",
})
```

3. Add methods to `MarketStore` class (after the Metrics section, ~line 545):

```python
    # ---- Forward Estimates (yfinance) ----

    def upsert_forward_estimates(self, symbol: str, rows: List[Dict]) -> int:
        """Upsert forward estimate rows. PK: (symbol, date, period)."""
        _validate_table("forward_estimates")
        if not rows:
            return 0
        conn = self._get_conn()
        valid_cols = _get_table_columns("forward_estimates", conn)
        count = 0
        with conn:
            for row in rows:
                data = {k: v for k, v in row.items() if k in valid_cols}
                data["symbol"] = symbol.upper()
                if "date" not in data or not data["date"]:
                    continue
                if "period" not in data or not data["period"]:
                    continue
                cols = [c for c in data if c in valid_cols]
                placeholders = ", ".join(["?"] * len(cols))
                col_names = ", ".join(cols)
                values = [data[c] for c in cols]
                conn.execute(
                    f"INSERT OR REPLACE INTO forward_estimates ({col_names}) VALUES ({placeholders})",
                    values,
                )
                count += 1
        return count

    def get_forward_estimates(self, symbol: str, limit: int = 0) -> List[Dict[str, Any]]:
        """Get all forward estimate rows for a symbol, sorted by date DESC."""
        return self._get_rows("forward_estimates", symbol, limit=limit)

    def get_latest_forward_estimates(self, symbol: str) -> List[Dict[str, Any]]:
        """Get forward estimates from the most recent fetch_date only."""
        conn = self._get_conn()
        # Find the latest date for this symbol
        row = conn.execute(
            "SELECT MAX(date) as max_date FROM forward_estimates WHERE symbol = ?",
            [symbol.upper()],
        ).fetchone()
        if not row or not row["max_date"]:
            return []
        latest_date = row["max_date"]
        rows = conn.execute(
            "SELECT * FROM forward_estimates WHERE symbol = ? AND date = ? ORDER BY period",
            [symbol.upper(), latest_date],
        ).fetchall()
        return [dict(r) for r in rows]

    def upsert_forward_metadata(self, symbol: str, rows: List[Dict]) -> int:
        """Upsert forward metadata rows (price targets). PK: (symbol, date)."""
        return self._bulk_upsert("forward_metadata", symbol, rows, convert=False)

    def get_latest_forward_metadata(self, symbol: str) -> Optional[Dict[str, Any]]:
        """Get the most recent forward metadata row for a symbol."""
        rows = self._get_rows("forward_metadata", symbol, limit=1)
        return rows[0] if rows else None
```

**Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/test_market_store.py::TestForwardEstimates -v`
Expected: All 6 tests PASS

**Step 5: Commit**

```bash
git add src/data/market_store.py tests/test_market_store.py
git commit -m "feat(market_store): add forward_estimates and forward_metadata tables"
```

---

### Task 2: yfinance Client

**Files:**
- Create: `src/data/yfinance_client.py`
- Test: `tests/test_yfinance_client.py`

**Step 1: Write failing tests**

Create `tests/test_yfinance_client.py`:

```python
"""Tests for yfinance forward estimates client."""
import pytest
from unittest import mock
from datetime import date

import pandas as pd

from src.data.yfinance_client import YFinanceClient


# Sample DataFrames matching yfinance output
SAMPLE_EARNINGS_EST = pd.DataFrame({
    "avg": [1.95, 1.72, 8.50, 9.31],
    "low": [1.85, 1.59, 8.15, 8.36],
    "high": [2.16, 1.86, 8.97, 10.19],
    "yearAgoEps": [1.65, 1.57, 7.46, 8.50],
    "numberOfAnalysts": [29, 27, 38, 40],
    "growth": [0.1846, 0.0986, 0.1390, 0.0959],
}, index=pd.Index(["0q", "+1q", "0y", "+1y"], name="period"))

SAMPLE_REVENUE_EST = pd.DataFrame({
    "avg": [109_079_879_710, 101_642_789_290, 465_024_459_540, 495_001_546_900],
    "low": [105_000_000_000, 95_980_000_000, 448_737_000_000, 454_827_000_000],
    "high": [115_000_000_000, 108_000_000_000, 480_000_000_000, 540_000_000_000],
    "yearAgoRevenue": [95_359_000_000, 94_036_000_000, 416_161_000_000, 465_024_459_540],
    "numberOfAnalysts": [32, 29, 38, 40],
    "growth": [0.1439, 0.0809, 0.1174, 0.0645],
}, index=pd.Index(["0q", "+1q", "0y", "+1y"], name="period"))

SAMPLE_GROWTH_EST = pd.DataFrame({
    "stockTrend": [0.185, 0.099, 0.139, 0.093, None],
    "indexTrend": [0.133, 0.112, 0.151, 0.162, 0.122],
}, index=pd.Index(["0q", "+1q", "0y", "+1y", "LTG"], name="period"))

SAMPLE_PRICE_TARGETS = {
    "current": 257.46,
    "high": 350.0,
    "low": 205.0,
    "mean": 292.15,
    "median": 300.0,
}

SAMPLE_EPS_TREND = pd.DataFrame({
    "current": [1.95454, 1.72488, 8.49731, 9.31197],
    "7daysAgo": [1.95289, 1.73246, 8.50696, 9.32731],
    "30daysAgo": [1.94679, 1.73226, 8.47850, 9.29309],
    "60daysAgo": [1.84245, 1.70764, 8.26396, 9.13429],
    "90daysAgo": [1.84290, 1.70809, 8.25726, 9.10952],
}, index=pd.Index(["0q", "+1q", "0y", "+1y"], name="period"))

SAMPLE_EPS_REVISIONS = pd.DataFrame({
    "upLast7days": [0, 0, 1, 2],
    "upLast30days": [25, 14, 35, 27],
    "downLast30days": [1, 11, 0, 6],
    "downLast7Days": [0, 0, 0, 0],
}, index=pd.Index(["0q", "+1q", "0y", "+1y"], name="period"))


class TestYFinanceClient:

    def _mock_ticker(self):
        """Create a mock yfinance Ticker with all 6 properties."""
        ticker = mock.MagicMock()
        ticker.earnings_estimate = SAMPLE_EARNINGS_EST
        ticker.revenue_estimate = SAMPLE_REVENUE_EST
        ticker.growth_estimates = SAMPLE_GROWTH_EST
        ticker.analyst_price_targets = SAMPLE_PRICE_TARGETS
        ticker.eps_trend = SAMPLE_EPS_TREND
        ticker.eps_revisions = SAMPLE_EPS_REVISIONS
        return ticker

    @mock.patch("src.data.yfinance_client.yf.Ticker")
    def test_get_forward_estimates_returns_rows(self, mock_ticker_cls):
        mock_ticker_cls.return_value = self._mock_ticker()
        client = YFinanceClient()
        estimates, metadata = client.get_forward_estimates("AAPL")

        assert len(estimates) == 4
        row_0q = next(r for r in estimates if r["period"] == "0q")
        assert row_0q["eps_avg"] == 1.95
        assert row_0q["eps_num_analysts"] == 29
        assert row_0q["rev_avg"] == 109_079_879_710
        assert row_0q["eps_rev_up_30d"] == 25
        assert row_0q["eps_trend_current"] == pytest.approx(1.95454)
        assert row_0q["growth_stock"] == pytest.approx(0.185)

    @mock.patch("src.data.yfinance_client.yf.Ticker")
    def test_get_forward_estimates_metadata(self, mock_ticker_cls):
        mock_ticker_cls.return_value = self._mock_ticker()
        client = YFinanceClient()
        _, metadata = client.get_forward_estimates("AAPL")

        assert metadata["price_target_current"] == 257.46
        assert metadata["price_target_high"] == 350.0
        assert metadata["price_target_median"] == 300.0

    @mock.patch("src.data.yfinance_client.yf.Ticker")
    def test_handles_none_earnings_estimate(self, mock_ticker_cls):
        ticker = self._mock_ticker()
        ticker.earnings_estimate = None
        mock_ticker_cls.return_value = ticker
        client = YFinanceClient()
        estimates, metadata = client.get_forward_estimates("AAPL")

        # Should still return rows (with None eps fields)
        assert len(estimates) == 4  # from revenue_estimate index
        row = next(r for r in estimates if r["period"] == "0q")
        assert row["eps_avg"] is None
        assert row["rev_avg"] == 109_079_879_710

    @mock.patch("src.data.yfinance_client.yf.Ticker")
    def test_handles_all_none(self, mock_ticker_cls):
        ticker = mock.MagicMock()
        ticker.earnings_estimate = None
        ticker.revenue_estimate = None
        ticker.growth_estimates = None
        ticker.analyst_price_targets = None
        ticker.eps_trend = None
        ticker.eps_revisions = None
        mock_ticker_cls.return_value = ticker
        client = YFinanceClient()
        estimates, metadata = client.get_forward_estimates("AAPL")

        assert estimates == []
        assert metadata == {}

    @mock.patch("src.data.yfinance_client.yf.Ticker")
    def test_handles_empty_dataframes(self, mock_ticker_cls):
        ticker = mock.MagicMock()
        ticker.earnings_estimate = pd.DataFrame()
        ticker.revenue_estimate = pd.DataFrame()
        ticker.growth_estimates = pd.DataFrame()
        ticker.analyst_price_targets = {}
        ticker.eps_trend = pd.DataFrame()
        ticker.eps_revisions = pd.DataFrame()
        mock_ticker_cls.return_value = ticker
        client = YFinanceClient()
        estimates, metadata = client.get_forward_estimates("AAPL")

        assert estimates == []
        assert metadata == {}

    @mock.patch("src.data.yfinance_client.yf.Ticker")
    def test_nan_values_become_none(self, mock_ticker_cls):
        """NaN values in DataFrames should be converted to None for SQLite."""
        ticker = self._mock_ticker()
        # growth_estimates has NaN for LTG stockTrend
        mock_ticker_cls.return_value = ticker
        client = YFinanceClient()
        estimates, _ = client.get_forward_estimates("AAPL")

        # LTG period is excluded (only 0q/+1q/0y/+1y), but check NaN handling
        for row in estimates:
            for v in row.values():
                if isinstance(v, float):
                    assert v == v  # NaN != NaN
```

**Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/test_yfinance_client.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'src.data.yfinance_client'`

**Step 3: Implement yfinance client**

Create `src/data/yfinance_client.py`:

```python
"""Thin yfinance wrapper for forward consensus estimates.

Fetches 6 datasets per ticker:
- earnings_estimate: EPS consensus (avg/low/high, analyst count, growth)
- revenue_estimate: Revenue consensus
- growth_estimates: stock vs index growth trends
- analyst_price_targets: street consensus price targets
- eps_trend: EPS estimate drift over 7d/30d/60d/90d
- eps_revisions: up/down revision counts

Returns normalized dicts ready for market.db upsert.
"""
import logging
import math
from datetime import date
from typing import Any, Dict, List, Optional, Tuple

import yfinance as yf

logger = logging.getLogger(__name__)

# Periods we care about (exclude LTG from growth_estimates)
_PERIODS = ("0q", "+1q", "0y", "+1y")


def _safe_val(v: Any) -> Any:
    """Convert NaN/inf to None for SQLite compatibility."""
    if v is None:
        return None
    if isinstance(v, float) and (math.isnan(v) or math.isinf(v)):
        return None
    return v


def _df_to_dict(df, column_map: Dict[str, str]) -> Dict[str, Dict[str, Any]]:
    """Convert a yfinance DataFrame to {period: {col: val}} dict.

    Args:
        df: DataFrame with period index (0q, +1q, 0y, +1y)
        column_map: {df_column: output_column} mapping
    """
    if df is None or (hasattr(df, "empty") and df.empty):
        return {}
    result = {}
    for period in _PERIODS:
        if period not in df.index:
            continue
        row = df.loc[period]
        result[period] = {
            out_col: _safe_val(row.get(df_col))
            for df_col, out_col in column_map.items()
            if df_col in row.index
        }
    return result


class YFinanceClient:
    """Fetch forward consensus estimates from Yahoo Finance."""

    def get_forward_estimates(self, symbol: str) -> Tuple[List[Dict], Dict]:
        """Fetch all 6 forward estimate datasets for a symbol.

        Returns:
            (estimates, metadata) tuple where:
            - estimates: list of dicts (one per period), ready for market.db upsert
            - metadata: dict with price target fields, ready for market.db upsert
        """
        today_str = date.today().isoformat()

        try:
            t = yf.Ticker(symbol)
        except Exception as e:
            logger.error(f"yfinance Ticker creation failed for {symbol}: {e}")
            return [], {}

        # Fetch all 6 datasets (each is a property, may return None)
        earnings = _df_to_dict(t.earnings_estimate, {
            "avg": "eps_avg", "low": "eps_low", "high": "eps_high",
            "yearAgoEps": "eps_year_ago", "growth": "eps_growth",
            "numberOfAnalysts": "eps_num_analysts",
        })
        revenue = _df_to_dict(t.revenue_estimate, {
            "avg": "rev_avg", "low": "rev_low", "high": "rev_high",
            "yearAgoRevenue": "rev_year_ago", "growth": "rev_growth",
            "numberOfAnalysts": "rev_num_analysts",
        })
        growth = _df_to_dict(t.growth_estimates, {
            "stockTrend": "growth_stock", "indexTrend": "growth_index",
        })
        trend = _df_to_dict(t.eps_trend, {
            "current": "eps_trend_current", "7daysAgo": "eps_trend_7d",
            "30daysAgo": "eps_trend_30d", "60daysAgo": "eps_trend_60d",
            "90daysAgo": "eps_trend_90d",
        })
        revisions = _df_to_dict(t.eps_revisions, {
            "upLast7days": "eps_rev_up_7d", "upLast30days": "eps_rev_up_30d",
            "downLast30days": "eps_rev_down_30d", "downLast7Days": "eps_rev_down_7d",
        })

        # Merge all datasets by period
        all_periods = set()
        for d in [earnings, revenue, growth, trend, revisions]:
            all_periods.update(d.keys())

        if not all_periods:
            return [], {}

        estimates = []
        for period in _PERIODS:
            if period not in all_periods:
                continue
            row = {"date": today_str, "period": period}
            for d in [earnings, revenue, growth, trend, revisions]:
                if period in d:
                    row.update(d[period])
            estimates.append(row)

        # Price targets → metadata
        metadata = {}
        pt = t.analyst_price_targets
        if pt and isinstance(pt, dict) and any(v is not None for v in pt.values()):
            metadata = {
                "date": today_str,
                "price_target_current": _safe_val(pt.get("current")),
                "price_target_high": _safe_val(pt.get("high")),
                "price_target_low": _safe_val(pt.get("low")),
                "price_target_mean": _safe_val(pt.get("mean")),
                "price_target_median": _safe_val(pt.get("median")),
            }

        return estimates, metadata


# Module-level singleton
yfinance_client = YFinanceClient()
```

**Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/test_yfinance_client.py -v`
Expected: All 7 tests PASS

**Step 5: Commit**

```bash
git add src/data/yfinance_client.py tests/test_yfinance_client.py
git commit -m "feat(yfinance): add forward estimates client with 6-dataset normalization"
```

---

### Task 3: pipeline.py — Replace FMP with market.db + yfinance Fallback

**Files:**
- Modify: `terminal/pipeline.py:42-74` (DataPackage fields)
- Modify: `terminal/pipeline.py:198-254` (format_context forward estimates rendering)
- Modify: `terminal/pipeline.py:501-524` (collect_data FMP analyst_estimates block)
- Test: `tests/test_pipeline_fmp_enrichment.py` (rewrite forward estimate tests)

**Step 1: Update DataPackage fields**

In `terminal/pipeline.py`, replace line 70:

```python
    # OLD:
    analyst_estimates: Optional[list] = None

    # NEW:
    forward_estimates: Optional[list] = None    # yfinance consensus rows
    forward_metadata: Optional[dict] = None     # price targets
```

**Step 2: Replace collect_data() FMP analyst_estimates block**

In `terminal/pipeline.py`, replace lines 511-524 (the `# Analyst estimates` block) with:

```python
    # Forward estimates (from market.db, fallback to live yfinance)
    try:
        from src.data.market_store import get_store
        store = get_store()
        fe_rows = store.get_latest_forward_estimates(symbol)
        fe_meta = store.get_latest_forward_metadata(symbol)

        # Staleness check: if data > 7 days old, fetch live
        if fe_rows:
            from datetime import timedelta
            fetch_date = fe_rows[0].get("date", "")
            try:
                age = (datetime.now() - datetime.strptime(fetch_date, "%Y-%m-%d")).days
            except ValueError:
                age = 999
            if age > 7:
                logger.info(f"{symbol}: forward estimates stale ({age}d), fetching live")
                fe_rows, fe_meta = None, None

        if not fe_rows:
            # Live yfinance fallback (read-only, no DB write)
            from src.data.yfinance_client import yfinance_client
            fe_rows, fe_meta_raw = yfinance_client.get_forward_estimates(symbol)
            fe_meta = fe_meta_raw if fe_meta_raw else None

        if fe_rows:
            pkg.forward_estimates = fe_rows
            if scratchpad:
                scratchpad.log_tool_call(
                    "get_forward_estimates", {"symbol": symbol},
                    {"count": len(fe_rows), "source": "market.db" if fetch_date else "yfinance_live"}
                )
        if fe_meta:
            pkg.forward_metadata = fe_meta
    except Exception as e:
        logger.warning(f"Forward estimates fetch failed for {symbol}: {e}")
        if scratchpad:
            scratchpad.log_reasoning("error", f"Forward estimates fetch failed: {e}")
```

**Step 3: Replace format_context() forward estimate rendering**

In `terminal/pipeline.py`, replace lines 198-254 (the `# Analyst consensus` block) with:

```python
        # Forward estimates (yfinance consensus)
        if self.forward_estimates:
            lines = ["### Forward Estimates (Consensus)"]
            lines.append("")
            lines.append("| Period | EPS (Low/Avg/High) | Analysts | Revenue | Analysts | EPS Growth | Rev Growth |")
            lines.append("|--------|-------------------|----------|---------|----------|------------|------------|")

            for row in self.forward_estimates:
                period = row.get("period", "?")
                eps_l = row.get("eps_low")
                eps_a = row.get("eps_avg")
                eps_h = row.get("eps_high")
                eps_str = f"{eps_l:.2f}/{eps_a:.2f}/{eps_h:.2f}" if all(v is not None for v in [eps_l, eps_a, eps_h]) else "N/A"
                eps_n = row.get("eps_num_analysts", "N/A")
                rev = row.get("rev_avg")
                rev_str = f"${rev / 1e9:.1f}B" if rev else "N/A"
                rev_n = row.get("rev_num_analysts", "N/A")
                eps_g = row.get("eps_growth")
                eps_g_str = f"{eps_g:+.1%}" if eps_g is not None else "N/A"
                rev_g = row.get("rev_growth")
                rev_g_str = f"{rev_g:+.1%}" if rev_g is not None else "N/A"
                lines.append(f"| {period} | {eps_str} | {eps_n} | {rev_str} | {rev_n} | {eps_g_str} | {rev_g_str} |")

            sections.append("\n".join(lines))

        # Estimate momentum (pre-digested signals from eps_trend + eps_revisions)
        if self.forward_estimates:
            signals = []

            # EPS revision momentum (from 0q and 0y)
            for target_period in ("0q", "0y"):
                row = next((r for r in self.forward_estimates if r.get("period") == target_period), None)
                if row:
                    up = row.get("eps_rev_up_30d")
                    down = row.get("eps_rev_down_30d")
                    if up is not None and down is not None:
                        signals.append(f"**EPS Revision (30d)**: {up} up / {down} down ({target_period})")

            # EPS drift (90d → current, from 0q)
            row_0q = next((r for r in self.forward_estimates if r.get("period") == "0q"), None)
            if row_0q:
                current = row_0q.get("eps_trend_current")
                ago_90 = row_0q.get("eps_trend_90d")
                if current is not None and ago_90 is not None and ago_90 > 0:
                    drift_pct = (current - ago_90) / ago_90 * 100
                    direction = "trending higher" if drift_pct > 0 else "trending lower"
                    signals.append(
                        f"**EPS Drift (90d\u2192now)**: ${ago_90:.2f} \u2192 ${current:.2f} "
                        f"({drift_pct:+.1f}%) \u2014 estimates {direction}"
                    )

            # Growth vs index (from 0q)
            if row_0q:
                stock_g = row_0q.get("growth_stock")
                index_g = row_0q.get("growth_index")
                if stock_g is not None and index_g is not None:
                    comparison = "outgrowing market" if stock_g > index_g else "underperforming market"
                    signals.append(
                        f"**Growth vs Index**: Stock {stock_g:+.1%} vs S&P {index_g:+.1%} (0q) "
                        f"\u2014 {comparison}"
                    )

            if signals:
                sections.append("### Estimate Momentum\n\n- " + "\n- ".join(signals))

        # Analyst price targets
        if self.forward_metadata:
            m = self.forward_metadata
            current = m.get("price_target_current")
            mean = m.get("price_target_mean")
            median = m.get("price_target_median")
            high = m.get("price_target_high")
            low = m.get("price_target_low")
            lines = ["### Analyst Price Targets"]
            if current and mean and median:
                lines.append(f"- Current: ${current:.2f} | Consensus: ${mean:.2f} (mean) / ${median:.2f} (median)")
            if high and low:
                lines.append(f"- Range: ${low:.2f} \u2014 ${high:.2f}")
            if current and mean and current > 0:
                upside = (mean - current) / current * 100
                lines.append(f"- **Implied Upside: {upside:+.1f}%**")
            if len(lines) > 1:
                sections.append("\n".join(lines))
```

**Step 4: Update tests**

In `tests/test_pipeline_fmp_enrichment.py`:

- Rename file to `tests/test_pipeline_enrichment.py` (no longer FMP-specific)
- Replace `SAMPLE_ESTIMATES` with yfinance-shaped data
- Update `TestFormatContextFMPEnrichment` → `TestFormatContextEnrichment`
- Update `test_forward_estimates_table` to check new 3-section format
- Update `test_all_fmp_fields_populated` to mock market.db + yfinance instead of registry
- Remove `test_recent_estimates_section` (no longer applicable)
- Keep all other tests (insider, news, ratings, earnings) unchanged

Key test rewrites:

```python
SAMPLE_FORWARD_ESTIMATES = [
    {"date": "2026-03-09", "period": "0q",
     "eps_avg": 1.95, "eps_low": 1.85, "eps_high": 2.16,
     "eps_num_analysts": 29, "eps_growth": 0.1846,
     "rev_avg": 109_079_879_710, "rev_num_analysts": 32, "rev_growth": 0.1439,
     "growth_stock": 0.185, "growth_index": 0.133,
     "eps_trend_current": 1.95454, "eps_trend_90d": 1.84290,
     "eps_rev_up_30d": 25, "eps_rev_down_30d": 1},
    {"date": "2026-03-09", "period": "+1q",
     "eps_avg": 1.72, "eps_low": 1.59, "eps_high": 1.86,
     "eps_num_analysts": 27, "eps_growth": 0.0986,
     "rev_avg": 101_642_789_290, "rev_num_analysts": 29, "rev_growth": 0.0809},
]

SAMPLE_FORWARD_METADATA = {
    "date": "2026-03-09",
    "price_target_current": 257.46,
    "price_target_high": 350.0,
    "price_target_low": 205.0,
    "price_target_mean": 292.15,
    "price_target_median": 300.0,
}
```

```python
def test_forward_estimates_consensus_table(self):
    pkg = DataPackage(symbol="NVDA", forward_estimates=SAMPLE_FORWARD_ESTIMATES)
    ctx = pkg.format_context()
    assert "### Forward Estimates (Consensus)" in ctx
    assert "| 0q |" in ctx
    assert "1.85/1.95/2.16" in ctx
    assert "$109.1B" in ctx
    assert "+18.5%" in ctx

def test_estimate_momentum_section(self):
    pkg = DataPackage(symbol="NVDA", forward_estimates=SAMPLE_FORWARD_ESTIMATES)
    ctx = pkg.format_context()
    assert "### Estimate Momentum" in ctx
    assert "25 up / 1 down" in ctx
    assert "EPS Drift" in ctx
    assert "outgrowing market" in ctx

def test_price_targets_section(self):
    pkg = DataPackage(
        symbol="NVDA",
        forward_estimates=SAMPLE_FORWARD_ESTIMATES,
        forward_metadata=SAMPLE_FORWARD_METADATA,
    )
    ctx = pkg.format_context()
    assert "### Analyst Price Targets" in ctx
    assert "$257.46" in ctx
    assert "$292.15" in ctx
    assert "Implied Upside: +13.5%" in ctx

def test_no_forward_estimates_no_sections(self):
    pkg = DataPackage(symbol="NVDA")
    ctx = pkg.format_context()
    assert "### Forward Estimates" not in ctx
    assert "### Estimate Momentum" not in ctx
    assert "### Analyst Price Targets" not in ctx
```

**Step 5: Run tests**

Run: `python3 -m pytest tests/test_pipeline_enrichment.py -v`
Expected: All tests PASS

**Step 6: Commit**

```bash
git add terminal/pipeline.py tests/test_pipeline_enrichment.py
git rm tests/test_pipeline_fmp_enrichment.py
git commit -m "feat(pipeline): replace FMP forward estimates with yfinance + market.db"
```

---

### Task 4: Remove Dead FMP Code

**Files:**
- Modify: `src/data/fmp_client.py:219-233` (remove `get_analyst_estimates`)
- Modify: `terminal/tools/fmp_tools.py:417-445` (remove `GetAnalystEstimatesTool`)

**Step 1: Remove `get_analyst_estimates` from fmp_client.py**

Delete lines 219-233 (`get_analyst_estimates` method).

**Step 2: Remove `GetAnalystEstimatesTool` from fmp_tools.py**

Delete lines 417-445 (`GetAnalystEstimatesTool` class).

**Step 3: Run full test suite to verify no breakage**

Run: `python3 -m pytest tests/ -x -q`
Expected: All tests PASS (no other code references these)

**Step 4: Commit**

```bash
git add src/data/fmp_client.py terminal/tools/fmp_tools.py
git commit -m "chore: remove dead FMP analyst-estimates code (never worked on Starter)"
```

---

### Task 5: Cloud Cron — Add --forward-estimates to update_data.py

**Files:**
- Modify: `scripts/update_data.py:27-103` (add new flag + fetch loop)

**Step 1: Add `--forward-estimates` argument**

After line 34 (`--check` arg):

```python
    parser.add_argument("--forward-estimates", action="store_true",
                        help="更新前瞻预期数据 (yfinance)")
```

**Step 2: Add forward estimates update block**

After the fundamental block (after line 103), before the correlation block:

```python
    # 更新前瞻预期数据
    if args.all or args.forward_estimates:
        print("=" * 40)
        print("Step 3b: 更新前瞻预期数据 (yfinance)")
        print("=" * 40)
        import time
        from src.data.yfinance_client import yfinance_client
        from src.data.market_store import get_store

        store = get_store()
        target_symbols = symbols or get_symbols()
        success = 0
        failed = []

        for sym in target_symbols:
            try:
                estimates, metadata = yfinance_client.get_forward_estimates(sym)
                if estimates:
                    store.upsert_forward_estimates(sym, estimates)
                if metadata:
                    store.upsert_forward_metadata(sym, [metadata])
                success += 1
                print(f"  ✓ {sym}: {len(estimates)} periods")
            except Exception as e:
                failed.append(sym)
                print(f"  ✗ {sym}: {e}")
            time.sleep(1)  # polite to Yahoo

        print(f"\n✅ 成功: {success}")
        if failed:
            print(f"❌ 失败: {failed}")
        print()
```

**Step 3: Update the `--all` check**

Update line 45 to include `forward_estimates`:

```python
    if not any([args.all, args.pool, args.price, args.fundamental,
                args.forward_estimates, args.correlation]):
```

**Step 4: Test manually**

Run: `python3 scripts/update_data.py --forward-estimates --symbols AAPL`
Expected: Fetches AAPL forward estimates, prints "✓ AAPL: 4 periods"

**Step 5: Commit**

```bash
git add scripts/update_data.py
git commit -m "feat(cron): add --forward-estimates flag to update_data.py"
```

---

### Task 6: Integration Test — End-to-End Verify

**Step 1: Verify yfinance → market.db → pipeline round-trip**

```bash
# Fetch and store
python3 -c "
from src.data.yfinance_client import yfinance_client
from src.data.market_store import get_store

store = get_store()
estimates, metadata = yfinance_client.get_forward_estimates('AAPL')
print(f'Fetched {len(estimates)} estimate rows')
store.upsert_forward_estimates('AAPL', estimates)
if metadata:
    store.upsert_forward_metadata('AAPL', [metadata])
    print(f'Stored metadata: PT mean={metadata.get(\"price_target_mean\")}')

# Read back
rows = store.get_latest_forward_estimates('AAPL')
print(f'Read back {len(rows)} rows from DB')
for r in rows:
    print(f'  {r[\"period\"]}: EPS={r.get(\"eps_avg\")}, Rev={r.get(\"rev_avg\")}')

meta = store.get_latest_forward_metadata('AAPL')
print(f'PT: {meta}')
"
```

**Step 2: Verify pipeline renders correctly**

```bash
python3 -c "
from terminal.pipeline import collect_data
pkg = collect_data('AAPL')
ctx = pkg.format_context()
# Check the 3 new sections exist
for section in ['Forward Estimates (Consensus)', 'Estimate Momentum', 'Analyst Price Targets']:
    if section in ctx:
        print(f'✓ {section} present')
    else:
        print(f'✗ {section} MISSING')
# Print the forward sections
start = ctx.find('### Forward Estimates')
if start >= 0:
    print()
    print(ctx[start:start+1500])
"
```

**Step 3: Run full test suite**

Run: `python3 -m pytest tests/ -x -q`
Expected: All tests PASS

**Step 4: Commit any fixes, then final commit**

```bash
git commit --allow-empty -m "test: verify forward estimates end-to-end integration"
```

---

### Task 7: Cloud Deployment — Add to Weekly Cron

**Step 1: Update cloud crontab**

SSH to cloud, add `--forward-estimates` to the Saturday fundamental cron:

```bash
ssh aliyun "crontab -l"
# Find the line:  0 10 * * 6 cd /root/workspace/Finance && ...update_data.py --fundamental...
# Change to:      0 10 * * 6 cd /root/workspace/Finance && ...update_data.py --fundamental --forward-estimates...
```

Or add as a separate job after fundamentals:

```
15 10 * * 6 cd /root/workspace/Finance && /root/workspace/Finance/.venv/bin/python scripts/update_data.py --forward-estimates >> logs/cron_forward_est.log 2>&1
```

**Step 2: Verify on cloud**

```bash
ssh aliyun "cd /root/workspace/Finance && .venv/bin/python scripts/update_data.py --forward-estimates --symbols AAPL,NVDA"
```

**Step 3: Sync market.db to local**

```bash
./sync_to_cloud.sh --pull
```

**Step 4: Verify local reads from synced DB**

```bash
python3 -c "
from src.data.market_store import get_store
store = get_store()
rows = store.get_latest_forward_estimates('AAPL')
print(f'AAPL: {len(rows)} forward estimate rows from synced DB')
"
```
