# Forward Estimates: FMP → yfinance Migration

**Date**: 2026-03-09
**Status**: Approved
**Problem**: FMP `analyst-estimates` endpoint returns 402 on Starter plan — forward estimates section in deep analysis has been silently empty.

---

## Decision Log

| Question | Decision | Rationale |
|----------|----------|-----------|
| Data source | yfinance (6 datasets) | Free, richer than FMP, no API key needed |
| Architecture | Thin `yfinance_client.py` in `src/data/` | No tool registry overhead — yfinance is free, no rate limiting |
| Storage | market.db (2 new tables) | Alongside fundamentals, cloud-exclusive write preserved |
| Cron frequency | Weekly (Sat 10:00 with fundamentals) | Consensus data changes slowly |
| Deep analysis read | market.db first, live yfinance fallback if >7 days stale | Freshness when needed, no ownership violation (fallback is read-only in-memory) |
| Context rendering | Pre-digested signals + raw tables | LLM gets both computed signals and raw data to reason over |
| Lens injection | Same data_context.md for all 5 lenses | No architectural change needed |

---

## Architecture

```
Cloud Weekly Cron (Sat 10:00)
  └─ yfinance_fetcher (in update_data.py --forward-estimates)
       ├─ fetch 6 datasets per ticker
       ├─ normalize into rows
       └─ upsert → market.db: forward_estimates + forward_metadata

Deep Analysis (Local)
  └─ collect_data()
       ├─ READ from market.db (via market_store)
       ├─ IF stale (>7 days) → live yfinance fallback (read-only, no DB write)
       ├─ compute derived signals (revision momentum, growth vs index, price target upside)
       └─ render into data_context.md:
            ├─ "### Forward Estimates (Consensus)" — EPS + Revenue table
            ├─ "### Estimate Momentum" — pre-digested signals
            └─ "### Analyst Price Targets" — consensus range + implied upside
```

**Removed**: FMP `get_analyst_estimates()` call + `GetAnalystEstimatesTool` (dead code on Starter).
**Unchanged**: analyst recommendations (grades), earnings calendar, insider trades, news — all still FMP.

---

## yfinance Datasets (6 total)

| Dataset | Fields | Use |
|---------|--------|-----|
| `earnings_estimate` | avg/low/high EPS, yearAgoEps, numberOfAnalysts, growth | Core EPS table |
| `revenue_estimate` | avg/low/high revenue, numberOfAnalysts, yearAgoRevenue, growth | Core Revenue table |
| `growth_estimates` | stockTrend, indexTrend per period | "Growth vs Index" signal |
| `analyst_price_targets` | current/high/low/mean/median | Price target section |
| `eps_trend` | current, 7d/30d/60d/90d ago values | "EPS Drift" signal |
| `eps_revisions` | upLast7d, upLast30d, downLast7d, downLast30d | "Revision Momentum" signal |

---

## Schema

### `forward_estimates` table

| Column | Type | Source |
|--------|------|--------|
| symbol | TEXT | — |
| fetch_date | TEXT | YYYY-MM-DD |
| period | TEXT | "0q", "+1q", "0y", "+1y" |
| eps_avg | REAL | earnings_estimate.avg |
| eps_low | REAL | earnings_estimate.low |
| eps_high | REAL | earnings_estimate.high |
| eps_year_ago | REAL | earnings_estimate.yearAgoEps |
| eps_growth | REAL | earnings_estimate.growth |
| eps_num_analysts | INTEGER | earnings_estimate.numberOfAnalysts |
| rev_avg | REAL | revenue_estimate.avg |
| rev_low | REAL | revenue_estimate.low |
| rev_high | REAL | revenue_estimate.high |
| rev_year_ago | REAL | revenue_estimate.yearAgoRevenue |
| rev_growth | REAL | revenue_estimate.growth |
| rev_num_analysts | INTEGER | revenue_estimate.numberOfAnalysts |
| growth_stock | REAL | growth_estimates.stockTrend |
| growth_index | REAL | growth_estimates.indexTrend |
| eps_trend_current | REAL | eps_trend.current |
| eps_trend_7d | REAL | eps_trend.7daysAgo |
| eps_trend_30d | REAL | eps_trend.30daysAgo |
| eps_trend_60d | REAL | eps_trend.60daysAgo |
| eps_trend_90d | REAL | eps_trend.90daysAgo |
| eps_rev_up_7d | INTEGER | eps_revisions.upLast7days |
| eps_rev_up_30d | INTEGER | eps_revisions.upLast30days |
| eps_rev_down_7d | INTEGER | eps_revisions.downLast7Days |
| eps_rev_down_30d | INTEGER | eps_revisions.downLast30days |
| **PK**: (symbol, fetch_date, period) | | |

### `forward_metadata` table

| Column | Type | Source |
|--------|------|--------|
| symbol | TEXT | — |
| fetch_date | TEXT | YYYY-MM-DD |
| price_target_current | REAL | analyst_price_targets.current |
| price_target_high | REAL | analyst_price_targets.high |
| price_target_low | REAL | analyst_price_targets.low |
| price_target_mean | REAL | analyst_price_targets.mean |
| price_target_median | REAL | analyst_price_targets.median |
| **PK**: (symbol, fetch_date) | | |

Historical rows kept — each weekly fetch adds new rows. Enables tracking estimate revision trends over time.

---

## Context Rendering (data_context.md)

Replaces old `format_context()` lines 198-254. Three sections:

```markdown
### Forward Estimates (Consensus)

| Period | EPS (Low/Avg/High) | Analysts | Revenue | Analysts | EPS Growth | Rev Growth |
|--------|-------------------|----------|---------|----------|------------|------------|
| 0q     | 1.85/1.95/2.16    | 29       | $109.1B | 32       | +18.5%     | +14.4%     |
| +1q    | 1.59/1.72/1.86    | 27       | $101.6B | 29       | +9.9%      | +8.1%      |
| 0y     | 8.15/8.50/8.97    | 38       | $465.0B | 38       | +13.9%     | +11.7%     |
| +1y    | 8.36/9.31/10.19   | 40       | $495.0B | 40       | +9.6%      | +6.5%      |

### Estimate Momentum

- **EPS Revision (30d)**: 25 up / 1 down (0q), 35 up / 0 down (0y) → strong upward revision
- **EPS Drift (90d→now)**: $1.84 → $1.95 (+6.1%) — estimates trending higher
- **Growth vs Index**: Stock +18.5% vs S&P +13.3% (0q) — outgrowing market

### Analyst Price Targets

- Current: $257.46 | Consensus: $292.15 (mean) / $300.00 (median)
- Range: $205.00 — $350.00
- **Implied Upside: +13.5%**
```

---

## File Changes

### Modified

| File | Change |
|------|--------|
| `src/data/market_store.py` | Add 2 table schemas, field lists, upsert/get methods |
| `terminal/pipeline.py` | Replace FMP analyst_estimates with market.db read + yfinance fallback in `collect_data()`. Replace `format_context()` lines 198-254 with new 3-section rendering |
| `scripts/update_data.py` | Add `--forward-estimates` flag |
| `terminal/tools/fmp_tools.py` | Remove `GetAnalystEstimatesTool` (dead code) |
| `src/data/fmp_client.py` | Remove `get_analyst_estimates()` (dead code) |

### New

| File | Purpose |
|------|---------|
| `src/data/yfinance_client.py` | Thin yfinance wrapper — `get_forward_estimates(symbol)` |

### Unchanged

| File | Why |
|------|-----|
| `deep_pipeline.py` | Reads data_context.md — richer content, no code change |
| `sync_to_cloud.sh` | Already syncs entire market.db |
| Lens prompt builders | Read data_context.md as-is |
| Cloud crontab | Add flag to existing Saturday invocation |

### Tests

| File | Change |
|------|--------|
| `tests/test_pipeline_fmp_enrichment.py` | Rewrite: mock yfinance, test new rendering |
| `tests/test_yfinance_client.py` | **New** — normalization, error handling, empty data |
| `tests/test_market_store.py` | Add forward_estimates/metadata upsert/get tests |
