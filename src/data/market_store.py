"""
Unified Market Database — SQLite backend for time-series data.

Stores daily prices, quarterly financials (income, balance sheet, cash flow),
annual ratios, pre-computed metrics, IV daily summaries, and options snapshots.
Complements company.db (company-dimension) with time-series data enabling
cross-stock screening (e.g. "net_margin > 25%").

Usage:
    from src.data.market_store import get_store
    store = get_store()
    store.upsert_income("AAPL", rows)
    store.screen({"net_margin >": 0.25})
"""
import hashlib
import json
import logging
import re
import sqlite3
import threading
from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Union

import pandas as pd

from src.data.fx_validation import validate_usd_per_unit

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
_PROJECT_ROOT = Path(__file__).parent.parent.parent

try:
    from config.settings import MARKET_DB_PATH as _CONFIGURED_PATH
    _DEFAULT_DB_PATH = _CONFIGURED_PATH
except ImportError:
    _DEFAULT_DB_PATH = _PROJECT_ROOT / "data" / "market.db"

try:
    from config.settings import PROVIDER_EMPTY_TTL_DAYS as _CONFIGURED_PROVIDER_EMPTY_TTL_DAYS
    _DEFAULT_PROVIDER_EMPTY_TTL_DAYS = _CONFIGURED_PROVIDER_EMPTY_TTL_DAYS
except ImportError:
    _DEFAULT_PROVIDER_EMPTY_TTL_DAYS = 30


# ---------------------------------------------------------------------------
# camelCase → snake_case helper
# ---------------------------------------------------------------------------
_CAMEL_RE1 = re.compile(r"(.)([A-Z][a-z]+)")
_CAMEL_RE2 = re.compile(r"([a-z0-9])([A-Z])")


def _camel_to_snake(name: str) -> str:
    """Convert camelCase or PascalCase to snake_case."""
    s = _CAMEL_RE1.sub(r"\1_\2", name)
    return _CAMEL_RE2.sub(r"\1_\2", s).lower()


# ---------------------------------------------------------------------------
# Pure-date detection (R3-m2: vintage observed_at/as_of normalization)
# ---------------------------------------------------------------------------
_PURE_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _is_pure_date(s: str) -> bool:
    """True if `s` is a bare YYYY-MM-DD date with no time component."""
    return bool(_PURE_DATE_RE.match(s))


# ---------------------------------------------------------------------------
# FMP field definitions (camelCase as received from API)
# ---------------------------------------------------------------------------
# These lists define the canonical columns for each table. During upsert,
# only keys present in these lists are written; unknown keys are silently
# ignored. The lists are ordered: (symbol, date) are always first (PK).

_INCOME_FIELDS = [
    "date", "symbol", "reportedCurrency", "cik", "filingDate", "acceptedDate",
    "fiscalYear", "period", "revenue", "costOfRevenue", "grossProfit",
    "researchAndDevelopmentExpenses", "generalAndAdministrativeExpenses",
    "sellingAndMarketingExpenses", "sellingGeneralAndAdministrativeExpenses",
    "otherExpenses", "operatingExpenses", "costAndExpenses",
    "netInterestIncome", "interestIncome", "interestExpense",
    "depreciationAndAmortization", "ebitda", "ebit",
    "nonOperatingIncomeExcludingInterest", "operatingIncome",
    "totalOtherIncomeExpensesNet", "incomeBeforeTax", "incomeTaxExpense",
    "netIncomeFromContinuingOperations", "netIncomeFromDiscontinuedOperations",
    "otherAdjustmentsToNetIncome", "netIncome", "netIncomeDeductions",
    "bottomLineNetIncome", "eps", "epsDiluted",
    "weightedAverageShsOut", "weightedAverageShsOutDil",
]

_BALANCE_SHEET_FIELDS = [
    "date", "symbol", "reportedCurrency", "cik", "filingDate", "acceptedDate",
    "fiscalYear", "period",
    "cashAndCashEquivalents", "shortTermInvestments", "cashAndShortTermInvestments",
    "netReceivables", "accountsReceivables", "otherReceivables",
    "inventory", "prepaids", "otherCurrentAssets", "totalCurrentAssets",
    "propertyPlantEquipmentNet", "goodwill", "intangibleAssets",
    "goodwillAndIntangibleAssets", "longTermInvestments", "taxAssets",
    "otherNonCurrentAssets", "totalNonCurrentAssets", "otherAssets", "totalAssets",
    "totalPayables", "accountPayables", "otherPayables", "accruedExpenses",
    "shortTermDebt", "capitalLeaseObligationsCurrent", "taxPayables",
    "deferredRevenue", "otherCurrentLiabilities", "totalCurrentLiabilities",
    "longTermDebt", "capitalLeaseObligationsNonCurrent",
    "deferredRevenueNonCurrent", "deferredTaxLiabilitiesNonCurrent",
    "otherNonCurrentLiabilities", "totalNonCurrentLiabilities",
    "otherLiabilities", "capitalLeaseObligations", "totalLiabilities",
    "treasuryStock", "preferredStock", "commonStock", "retainedEarnings",
    "additionalPaidInCapital", "accumulatedOtherComprehensiveIncomeLoss",
    "otherTotalStockholdersEquity", "totalStockholdersEquity", "totalEquity",
    "minorityInterest", "totalLiabilitiesAndTotalEquity",
    "totalInvestments", "totalDebt", "netDebt",
]

_CASH_FLOW_FIELDS = [
    "date", "symbol", "reportedCurrency", "cik", "filingDate", "acceptedDate",
    "fiscalYear", "period",
    "netIncome", "depreciationAndAmortization", "deferredIncomeTax",
    "stockBasedCompensation", "changeInWorkingCapital",
    "accountsReceivables", "inventory", "accountsPayables",
    "otherWorkingCapital", "otherNonCashItems",
    "netCashProvidedByOperatingActivities",
    "investmentsInPropertyPlantAndEquipment", "acquisitionsNet",
    "purchasesOfInvestments", "salesMaturitiesOfInvestments",
    "otherInvestingActivities", "netCashProvidedByInvestingActivities",
    "netDebtIssuance", "longTermNetDebtIssuance", "shortTermNetDebtIssuance",
    "netStockIssuance", "netCommonStockIssuance", "commonStockIssuance",
    "commonStockRepurchased", "netPreferredStockIssuance",
    "netDividendsPaid", "commonDividendsPaid", "preferredDividendsPaid",
    "otherFinancingActivities", "netCashProvidedByFinancingActivities",
    "effectOfForexChangesOnCash", "netChangeInCash",
    "cashAtEndOfPeriod", "cashAtBeginningOfPeriod",
    "operatingCashFlow", "capitalExpenditure", "freeCashFlow",
    "incomeTaxesPaid", "interestPaid",
]

_RATIOS_FIELDS = [
    "symbol", "date", "fiscalYear", "period", "reportedCurrency",
    "grossProfitMargin", "ebitMargin", "ebitdaMargin",
    "operatingProfitMargin", "pretaxProfitMargin",
    "continuousOperationsProfitMargin", "netProfitMargin",
    "bottomLineProfitMargin",
    "receivablesTurnover", "payablesTurnover", "inventoryTurnover",
    "fixedAssetTurnover", "assetTurnover",
    "currentRatio", "quickRatio", "solvencyRatio", "cashRatio",
    "priceToEarningsRatio", "priceToEarningsGrowthRatio",
    "forwardPriceToEarningsGrowthRatio",
    "priceToBookRatio", "priceToSalesRatio",
    "priceToFreeCashFlowRatio", "priceToOperatingCashFlowRatio",
    "debtToAssetsRatio", "debtToEquityRatio", "debtToCapitalRatio",
    "longTermDebtToCapitalRatio", "financialLeverageRatio",
    "workingCapitalTurnoverRatio",
    "operatingCashFlowRatio", "operatingCashFlowSalesRatio",
    "freeCashFlowOperatingCashFlowRatio",
    "debtServiceCoverageRatio", "interestCoverageRatio",
    "shortTermOperatingCashFlowCoverageRatio",
    "operatingCashFlowCoverageRatio",
    "capitalExpenditureCoverageRatio",
    "dividendPaidAndCapexCoverageRatio",
    "dividendPayoutRatio", "dividendYield", "dividendYieldPercentage",
    "revenuePerShare", "netIncomePerShare", "interestDebtPerShare",
    "cashPerShare", "bookValuePerShare", "tangibleBookValuePerShare",
    "shareholdersEquityPerShare", "operatingCashFlowPerShare",
    "capexPerShare", "freeCashFlowPerShare",
    "netIncomePerEBT", "ebtPerEbit",
    "priceToFairValue", "debtToMarketCap",
    "effectiveTaxRate", "enterpriseValueMultiple", "dividendPerShare",
]

_METRICS_FIELDS = [
    "symbol", "date", "period", "fiscal_year",
    # Margins
    "gross_margin", "operating_margin", "net_margin", "ebitda_margin",
    # Returns
    "roe", "roa", "roic",
    # Leverage
    "debt_to_equity", "debt_to_assets", "current_ratio", "quick_ratio",
    # Efficiency
    "asset_turnover", "inventory_turnover", "receivables_turnover",
    # Growth YoY
    "revenue_growth_yoy", "net_income_growth_yoy", "eps_growth_yoy",
    "operating_income_growth_yoy",
    # Growth QoQ
    "revenue_growth_qoq", "net_income_growth_qoq", "eps_growth_qoq",
    "operating_income_growth_qoq",
    # Margin delta QoQ (decimal; e.g. 0.02 = +2 pp)
    "gross_margin_delta_qoq", "operating_margin_delta_qoq",
    "net_margin_delta_qoq", "ebitda_margin_delta_qoq",
    # Return delta QoQ (decimal; e.g. 0.02 = +2 pp)
    "roe_delta_qoq", "roic_delta_qoq",
    # CAGR trailing 4Q (per-quarter compound growth rate)
    "revenue_cagr_4q", "gross_profit_cagr_4q", "operating_income_cagr_4q",
    "ebitda_cagr_4q", "net_income_cagr_4q", "eps_cagr_4q",
    # Margin change trailing 4Q (decimal; total pp change Q0 vs Q-3)
    "gross_margin_change_4q", "operating_margin_change_4q",
    "net_margin_change_4q", "ebitda_margin_change_4q",
    # Cash flow
    "fcf_margin", "fcf_to_net_income", "operating_cf_to_revenue",
]

# ---------------------------------------------------------------------------
# Schema generation
# ---------------------------------------------------------------------------

def _sql_type(field_name: str) -> str:
    """Determine SQL type for a field name."""
    if field_name in ("symbol", "date", "period", "fiscal_year",
                      "reported_currency", "cik", "filing_date",
                      "accepted_date"):
        return "TEXT"
    return "REAL"


def _build_create_table(table_name: str, fields: List[str], already_snake: bool = False) -> str:
    """Build CREATE TABLE IF NOT EXISTS statement from field list."""
    snake_fields = fields if already_snake else [_camel_to_snake(f) for f in fields]
    lines = []
    for sf in snake_fields:
        if sf == "symbol":
            lines.append("    symbol TEXT NOT NULL")
        elif sf == "date":
            lines.append("    date TEXT NOT NULL")
        else:
            lines.append(f"    {sf} {_sql_type(sf)}")
    lines.append("    PRIMARY KEY (symbol, date)")
    cols = ",\n".join(lines)
    return f"CREATE TABLE IF NOT EXISTS {table_name} (\n{cols}\n);"


_SCHEMA = "\n\n".join([
    _build_create_table("daily_price", [
        "symbol", "date", "open", "high", "low", "close",
        "volume", "change", "change_pct",
    ], already_snake=True),
    "CREATE INDEX IF NOT EXISTS idx_dp_symbol ON daily_price(symbol);",
    "CREATE INDEX IF NOT EXISTS idx_dp_date ON daily_price(date);",

    _build_create_table("income_quarterly", _INCOME_FIELDS),
    "CREATE INDEX IF NOT EXISTS idx_iq_symbol ON income_quarterly(symbol);",

    _build_create_table("balance_sheet_quarterly", _BALANCE_SHEET_FIELDS),
    "CREATE INDEX IF NOT EXISTS idx_bsq_symbol ON balance_sheet_quarterly(symbol);",

    _build_create_table("cash_flow_quarterly", _CASH_FLOW_FIELDS),
    "CREATE INDEX IF NOT EXISTS idx_cfq_symbol ON cash_flow_quarterly(symbol);",

    _build_create_table("ratios_annual", _RATIOS_FIELDS),
    "CREATE INDEX IF NOT EXISTS idx_ra_symbol ON ratios_annual(symbol);",

    _build_create_table("metrics_quarterly", _METRICS_FIELDS, already_snake=True),
    "CREATE INDEX IF NOT EXISTS idx_mq_symbol ON metrics_quarterly(symbol);",

    # -- IV daily: ATM IV + HV summaries per symbol per day --
    """CREATE TABLE IF NOT EXISTS iv_daily (
    symbol TEXT NOT NULL,
    date TEXT NOT NULL,
    iv_30d REAL, iv_60d REAL, hv_30d REAL,
    put_call_ratio REAL, total_volume INTEGER, total_oi INTEGER,
    created_at TEXT NOT NULL,
    PRIMARY KEY (symbol, date)
);""",
    "CREATE INDEX IF NOT EXISTS idx_iv_symbol ON iv_daily(symbol);",

    # -- Options snapshots: full chain snapshots --
    """CREATE TABLE IF NOT EXISTS options_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol TEXT NOT NULL, snapshot_date TEXT NOT NULL,
    expiration TEXT NOT NULL, strike REAL NOT NULL, side TEXT NOT NULL,
    bid REAL, ask REAL, mid REAL, last REAL,
    volume INTEGER, open_interest INTEGER, iv REAL,
    delta REAL, gamma REAL, theta REAL, vega REAL,
    dte INTEGER, in_the_money INTEGER, underlying_price REAL,
    created_at TEXT NOT NULL,
    UNIQUE(symbol, snapshot_date, expiration, strike, side)
);""",
    "CREATE INDEX IF NOT EXISTS idx_snap_symbol ON options_snapshots(symbol, snapshot_date);",
    "CREATE INDEX IF NOT EXISTS idx_snap_exp ON options_snapshots(symbol, expiration);",

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

    # -- Social sentiment (Adanos: Reddit + X) --
    """CREATE TABLE IF NOT EXISTS social_sentiment (
    symbol TEXT NOT NULL,
    date TEXT NOT NULL,
    source TEXT NOT NULL,

    buzz_score REAL,
    total_mentions INTEGER,
    sentiment_score REAL,
    positive_count INTEGER,
    negative_count INTEGER,
    neutral_count INTEGER,
    bullish_pct INTEGER,
    bearish_pct INTEGER,
    trend TEXT,
    total_upvotes INTEGER,

    unique_posts INTEGER,
    subreddit_count INTEGER,
    is_validated INTEGER,

    top_mentions TEXT,
    top_subreddits TEXT,

    period_days INTEGER,
    created_at TEXT NOT NULL,

    PRIMARY KEY (symbol, date, source)
);""",
    "CREATE INDEX IF NOT EXISTS idx_social_date ON social_sentiment(date);",
    "CREATE INDEX IF NOT EXISTS idx_social_symbol ON social_sentiment(symbol);",

    # -- Market sentiment snapshots (Adanos market-level aggregate) --
    """CREATE TABLE IF NOT EXISTS market_sentiment (
    date TEXT NOT NULL,
    source TEXT NOT NULL,

    buzz_score REAL,
    trend TEXT,
    mentions INTEGER,
    unique_posts INTEGER,
    unique_authors INTEGER,
    subreddit_count INTEGER,
    total_upvotes INTEGER,
    active_tickers INTEGER,
    sentiment_score REAL,
    positive_count INTEGER,
    negative_count INTEGER,
    neutral_count INTEGER,
    bullish_pct INTEGER,
    bearish_pct INTEGER,
    trend_history TEXT,
    drivers TEXT,
    raw_json TEXT,

    period_days INTEGER,
    created_at TEXT NOT NULL,

    PRIMARY KEY (date, source)
);""",
    "CREATE INDEX IF NOT EXISTS idx_ms_source_date ON market_sentiment(source, date);",

    # -- Social trending snapshots (Adanos market-level) --
    """CREATE TABLE IF NOT EXISTS social_trending (
    date TEXT NOT NULL,
    source TEXT NOT NULL,
    rank INTEGER NOT NULL,

    ticker TEXT NOT NULL,
    company_name TEXT,
    buzz_score REAL,
    trend TEXT,
    mentions INTEGER,
    sentiment_score REAL,
    bullish_pct INTEGER,
    bearish_pct INTEGER,
    total_upvotes INTEGER,
    trend_history TEXT,

    unique_posts INTEGER,
    subreddit_count INTEGER,
    is_validated INTEGER,

    period_days INTEGER,
    created_at TEXT NOT NULL,

    PRIMARY KEY (date, source, rank)
);""",
    "CREATE INDEX IF NOT EXISTS idx_st_ticker ON social_trending(ticker);",
    "CREATE INDEX IF NOT EXISTS idx_st_date ON social_trending(date);",

    # -- Social trending sectors snapshots (Adanos market-level) --
    """CREATE TABLE IF NOT EXISTS social_trending_sectors (
    date TEXT NOT NULL,
    source TEXT NOT NULL,

    sector TEXT NOT NULL,
    buzz_score REAL,
    trend TEXT,
    mentions INTEGER,
    unique_tickers INTEGER,
    sentiment_score REAL,
    bullish_pct INTEGER,
    bearish_pct INTEGER,
    total_upvotes INTEGER,
    top_tickers TEXT,

    subreddit_count INTEGER,
    unique_authors INTEGER,

    period_days INTEGER,
    created_at TEXT NOT NULL,

    PRIMARY KEY (date, source, sector)
);""",
    "CREATE INDEX IF NOT EXISTS idx_sts_date ON social_trending_sectors(date);",

    # Broad market RVOL scan hits (for factor backtesting)
    """CREATE TABLE IF NOT EXISTS broad_scan_hits (
    symbol TEXT NOT NULL,
    date TEXT NOT NULL,
    rvol REAL NOT NULL,
    return_pct REAL NOT NULL,
    market_cap REAL,
    in_pool INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (symbol, date)
);""",
    "CREATE INDEX IF NOT EXISTS idx_bsh_date ON broad_scan_hits(date);",

    # -- Historical market cap (for universe reconstitution) --
    """CREATE TABLE IF NOT EXISTS historical_market_cap (
    symbol TEXT NOT NULL,
    date TEXT NOT NULL,
    market_cap REAL NOT NULL,
    PRIMARY KEY (symbol, date)
);""",
    "CREATE INDEX IF NOT EXISTS idx_hmc_date ON historical_market_cap(date);",

    # -- Concept registry: evergreen industry tree --
    """CREATE TABLE IF NOT EXISTS concepts (
    concept_id TEXT PRIMARY KEY,
    label TEXT NOT NULL,
    level INTEGER NOT NULL CHECK(level IN (1, 2, 3)),
    parent_id TEXT REFERENCES concepts(concept_id),
    concept_type TEXT NOT NULL DEFAULT 'evergreen',
    status TEXT NOT NULL DEFAULT 'active',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);""",
    "CREATE INDEX IF NOT EXISTS idx_concepts_parent ON concepts(parent_id);",
    "CREATE INDEX IF NOT EXISTS idx_concepts_level ON concepts(level);",

    # -- Concept themes: dynamic hot themes (HBM, liquid cooling) with lifecycle --
    """CREATE TABLE IF NOT EXISTS concept_themes (
    theme_id TEXT PRIMARY KEY,
    label TEXT NOT NULL,
    parent_concept_id TEXT REFERENCES concepts(concept_id),
    lifecycle_state TEXT NOT NULL DEFAULT 'watch',
    active_from TEXT,
    active_to TEXT,
    source TEXT NOT NULL DEFAULT 'manual',
    evidence TEXT DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);""",
    "CREATE INDEX IF NOT EXISTS idx_themes_parent ON concept_themes(parent_concept_id);",
    "CREATE INDEX IF NOT EXISTS idx_themes_state ON concept_themes(lifecycle_state);",

    # -- Company concept tags: Phase 1 materialized display path --
    """CREATE TABLE IF NOT EXISTS company_concept_tags (
    symbol TEXT PRIMARY KEY,
    primary_concept_id TEXT NOT NULL REFERENCES concepts(concept_id),
    secondary_concept_id TEXT REFERENCES concepts(concept_id),
    tertiary_concept_id TEXT REFERENCES concepts(concept_id),
    theme_ids TEXT DEFAULT '[]',
    display_tags TEXT DEFAULT '',
    business_role TEXT DEFAULT '',
    confidence REAL NOT NULL DEFAULT 0,
    source TEXT NOT NULL DEFAULT 'unknown',
    evidence TEXT DEFAULT '',
    needs_review INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL
);""",
    "CREATE INDEX IF NOT EXISTS idx_cct_primary ON company_concept_tags(primary_concept_id);",
    "CREATE INDEX IF NOT EXISTS idx_cct_secondary ON company_concept_tags(secondary_concept_id);",
    "CREATE INDEX IF NOT EXISTS idx_cct_tertiary ON company_concept_tags(tertiary_concept_id);",

    # -- Phase 2 reservation: symbol_concept_edges (N:M graph) --
    # Pre-created in Phase 1 to keep the FK target (concepts.concept_id) and
    # the eventual hotspot-clustering query path stable. NOT written to in
    # Phase 1; the build script only populates company_concept_tags. The
    # composite primary key (symbol, concept_id, edge_type) lets the same
    # symbol carry multiple parallel exposures (e.g. TSLA = electric_vehicles
    # + robotics + ai_compute_cloud) once Phase 2 starts emitting edges.
    """CREATE TABLE IF NOT EXISTS symbol_concept_edges (
    symbol TEXT NOT NULL,
    concept_id TEXT NOT NULL REFERENCES concepts(concept_id),
    weight REAL NOT NULL DEFAULT 1.0,
    edge_type TEXT NOT NULL DEFAULT 'business_exposure',
    confidence REAL NOT NULL DEFAULT 0,
    source TEXT NOT NULL DEFAULT 'unknown',
    evidence TEXT DEFAULT '',
    updated_at TEXT NOT NULL,
    PRIMARY KEY (symbol, concept_id, edge_type)
);""",
    "CREATE INDEX IF NOT EXISTS idx_sce_symbol ON symbol_concept_edges(symbol);",
    "CREATE INDEX IF NOT EXISTS idx_sce_concept ON symbol_concept_edges(concept_id);",
    "CREATE INDEX IF NOT EXISTS idx_sce_edge_type ON symbol_concept_edges(edge_type);",

    # -- Historical basket GAAP TTM valuation source and output tables --
    """CREATE TABLE IF NOT EXISTS fmp_fund_disclosure_holdings (
    basket_symbol TEXT NOT NULL,
    holding_date TEXT NOT NULL,
    source_kind TEXT NOT NULL CHECK(source_kind IN ('disclosure','live')),
    raw_row_index INTEGER NOT NULL,
    rebalance_close_date TEXT NOT NULL,
    composition_effective_date TEXT NOT NULL,
    composition_available_date TEXT NOT NULL,
    raw_symbol TEXT,
    symbol TEXT,
    alias_symbol TEXT,
    alias_mode TEXT,
    alias_reason TEXT,
    name TEXT,
    weight_pct REAL,
    market_value REAL,
    cik TEXT,
    cusip TEXT,
    isin TEXT,
    included INTEGER NOT NULL CHECK(included IN (0,1)),
    filter_reason TEXT,
    covered_by TEXT,
    row_accepted_at TEXT,
    snapshot_warnings_json TEXT NOT NULL,
    fetched_at TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (basket_symbol, holding_date, source_kind, raw_row_index)
);""",
    "CREATE INDEX IF NOT EXISTS idx_ffdh_basket_effective "
    "ON fmp_fund_disclosure_holdings(basket_symbol, composition_effective_date);",
    "CREATE UNIQUE INDEX IF NOT EXISTS idx_ffdh_live_rebalance "
    "ON fmp_fund_disclosure_holdings"
    "(basket_symbol, rebalance_close_date, source_kind, raw_row_index) "
    "WHERE source_kind = 'live';",

    """CREATE TABLE IF NOT EXISTS fx_daily (
    currency TEXT NOT NULL,
    date TEXT NOT NULL,
    usd_per_unit REAL NOT NULL CHECK(usd_per_unit > 0),
    source_symbol TEXT NOT NULL,
    source TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (currency, date)
);""",
    "CREATE INDEX IF NOT EXISTS idx_fx_daily_date ON fx_daily(date);",

    """CREATE TABLE IF NOT EXISTS fmp_stock_splits (
    symbol TEXT NOT NULL,
    date TEXT NOT NULL,
    numerator REAL NOT NULL CHECK(numerator > 0),
    denominator REAL NOT NULL CHECK(denominator > 0),
    split_type TEXT NOT NULL,
    source TEXT NOT NULL,
    fetched_at TEXT NOT NULL,
    PRIMARY KEY (symbol, date)
);""",
    "CREATE INDEX IF NOT EXISTS idx_fss_symbol_date "
    "ON fmp_stock_splits(symbol, date);",

    """CREATE TABLE IF NOT EXISTS basket_ttm_valuation (
    basket_symbol TEXT NOT NULL,
    valuation_date TEXT NOT NULL,
    holding_date TEXT NOT NULL,
    composition_effective_date TEXT NOT NULL,
    composition_available_date TEXT NOT NULL,
    is_ex_post_composition INTEGER NOT NULL CHECK(is_ex_post_composition IN (0,1)),
    weight_basis TEXT NOT NULL,
    data_quality_tier TEXT NOT NULL,
    is_observed_weight_date INTEGER NOT NULL CHECK(is_observed_weight_date IN (0,1)),
    eligible_weight REAL NOT NULL,
    covered_weight REAL NOT NULL,
    rebalance_weighted_ttm_pe_gaap_proxy REAL,
    weighted_earnings_yield REAL,
    uncapped_mcap_basket_pe_gaap REAL,
    covered_market_cap REAL,
    ttm_net_income_usd REAL,
    member_count INTEGER NOT NULL,
    covered_count INTEGER NOT NULL,
    weight_coverage REAL NOT NULL CHECK(weight_coverage >= 0 AND weight_coverage <= 1),
    mcap_weight_coverage REAL NOT NULL CHECK(mcap_weight_coverage >= 0 AND mcap_weight_coverage <= 1),
    income_weight_coverage REAL NOT NULL CHECK(income_weight_coverage >= 0 AND income_weight_coverage <= 1),
    fx_weight_coverage REAL NOT NULL CHECK(fx_weight_coverage >= 0 AND fx_weight_coverage <= 1),
    members_json TEXT NOT NULL,
    warnings_json TEXT NOT NULL,
    mcap_sanity_json TEXT NOT NULL,
    methodology_version TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (basket_symbol, valuation_date)
);""",
    "CREATE INDEX IF NOT EXISTS idx_btv_basket_date "
    "ON basket_ttm_valuation(basket_symbol, valuation_date);",

    # 周频估值 SSOT（Plan 2026-07-19 §3.3）：hindsight NTM actual/estimate 引擎
    # 的落库目标表。quality_tier 承载 R5 tail 演化契约（见
    # upsert_basket_weekly_pe_batch 的 docstring）。
    """CREATE TABLE IF NOT EXISTS basket_weekly_pe_history (
    basket TEXT NOT NULL,
    valuation_date TEXT NOT NULL,
    -- 写入本行的 run（basket_pe_backfill_runs.run_id）。verifier 以此判定
    -- "行归属" 与 "认证 run" 是否一致：manifest 只防改写不防追加，伪造一对
    -- run_started+run_completed 曾能重新认证被篡改的行；行自带归属后，
    -- 认证只对该 run 名下的行生效。R5 tail 升级会把归属改到重算的那次 run。
    run_id TEXT NOT NULL,
    ttm_pe_gaap REAL,
    hindsight_ntm_pe_gaap REAL,
    ttm_total_mcap REAL,
    ttm_net_income REAL,
    hindsight_total_mcap REAL,
    hindsight_ntm_net_income REAL,
    n_members INTEGER NOT NULL CHECK(n_members >= 0),
    n_covered_ttm INTEGER NOT NULL CHECK(n_covered_ttm >= 0),
    n_covered_hindsight INTEGER NOT NULL CHECK(n_covered_hindsight >= 0),
    mcap_coverage_ttm REAL NOT NULL
        CHECK(mcap_coverage_ttm >= 0 AND mcap_coverage_ttm <= 1),
    mcap_coverage_hindsight REAL NOT NULL
        CHECK(mcap_coverage_hindsight >= 0 AND mcap_coverage_hindsight <= 1),
    hindsight_actual_quarters INTEGER NOT NULL
        CHECK(hindsight_actual_quarters >= 0 AND hindsight_actual_quarters <= 4),
    hindsight_estimate_quarters INTEGER NOT NULL
        CHECK(hindsight_estimate_quarters >= 0 AND hindsight_estimate_quarters <= 4),
    composition_effective_date TEXT NOT NULL,
    composition_available_date TEXT NOT NULL,
    quality_tier TEXT NOT NULL
        CHECK(quality_tier IN ('actual_only','latest_consensus_tail','unpublishable')),
    members_json TEXT NOT NULL,
    warnings_json TEXT NOT NULL,
    methodology_version TEXT NOT NULL,
    created_at TEXT NOT NULL,
    last_updated TEXT NOT NULL,
    PRIMARY KEY (basket, valuation_date),
    CHECK (
        quality_tier = 'unpublishable'
        OR hindsight_actual_quarters + hindsight_estimate_quarters = 4
    )
);""",
    "CREATE INDEX IF NOT EXISTS idx_bwph_basket_date "
    "ON basket_weekly_pe_history(basket, valuation_date);",

    # 三指数周频 backfill 的 append-only run manifest（Plan 2026-07-19 Task 4 /
    # issue048 recurring 关闭条件）。每次 run 只追加事件，从不改写：
    # run_started 冻结 writer 当时的 target universe 与 expected date range
    # （防止后续用较晚 --min-date 只验证 suffix），forced_refresh 保留被覆盖
    # 区间的 pre/post row hash（refresh 后源表已无法自证），run_completed /
    # run_failed 记终局。verifier 以本表为分母 SSOT，不用当前 universe 重建。
    """CREATE TABLE IF NOT EXISTS basket_pe_backfill_runs (
    run_id TEXT NOT NULL,
    basket TEXT NOT NULL,
    event_seq INTEGER NOT NULL CHECK(event_seq >= 0),
    event_kind TEXT NOT NULL CHECK(event_kind IN
        ('run_started','forced_refresh','run_completed','run_failed')),
    frequency TEXT NOT NULL,
    expected_from_date TEXT NOT NULL,
    expected_to_date TEXT NOT NULL,
    methodology_version TEXT NOT NULL,
    target_count INTEGER NOT NULL CHECK(target_count >= 0),
    target_universe_json TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (run_id, basket, event_seq),
    CHECK (expected_from_date <= expected_to_date)
);""",
    "CREATE INDEX IF NOT EXISTS idx_bpbr_basket_run "
    "ON basket_pe_backfill_runs(basket, run_id, event_seq);",

    # -- FMP forward EPS 数据线（Spec 2026-07-09 §5.2，4 业务表 + 1 run-manifest）--
    # 周频 PIT 快照（append-only）
    """CREATE TABLE IF NOT EXISTS fmp_estimates (
    symbol TEXT NOT NULL,
    snapshot_date TEXT NOT NULL,
    fiscal_date TEXT NOT NULL,
    period_type TEXT NOT NULL CHECK(period_type IN ('Q','FY')),
    snapshot_kind TEXT NOT NULL DEFAULT 'weekly'
        CHECK(snapshot_kind IN ('weekly','backfill')),
    eps_avg REAL, eps_high REAL, eps_low REAL,
    rev_avg REAL, rev_high REAL, rev_low REAL,
    net_income_avg REAL, ebitda_avg REAL,
    num_analysts_eps INTEGER, num_analysts_rev INTEGER,
    PRIMARY KEY (symbol, snapshot_date, fiscal_date, period_type)
);""",
    "CREATE INDEX IF NOT EXISTS idx_fest_symbol_snap ON fmp_estimates(symbol, snapshot_date);",
    "CREATE INDEX IF NOT EXISTS idx_fest_snap ON fmp_estimates(snapshot_date);",

    # 街道口径财报事实（非快照制；历史计算须 as-of 过滤）
    """CREATE TABLE IF NOT EXISTS fmp_earnings (
    symbol TEXT NOT NULL,
    announce_date TEXT NOT NULL,
    fiscal_date TEXT,
    match_method TEXT,
    eps_actual REAL, eps_estimated REAL,
    revenue_actual REAL, revenue_estimated REAL,
    last_updated TEXT,
    PRIMARY KEY (symbol, announce_date)
);""",
    "CREATE INDEX IF NOT EXISTS idx_fearn_symbol_fiscal ON fmp_earnings(symbol, fiscal_date);",

    # ETF 成分快照（全部原始行落库，included/filter_reason 审计证据链）
    """CREATE TABLE IF NOT EXISTS fmp_etf_holdings_snapshot (
    basket TEXT NOT NULL,
    snapshot_date TEXT NOT NULL,
    raw_row_index INTEGER NOT NULL,
    raw_asset TEXT,
    symbol TEXT,
    name TEXT, weight_pct REAL, market_value REAL,
    updated_at TEXT,
    included INTEGER NOT NULL,
    filter_reason TEXT,
    covered_by TEXT,
    PRIMARY KEY (basket, snapshot_date, raw_row_index)
);""",

    # 指数篮子估值（Phase 2 才写入，本期只建表锁契约）
    """CREATE TABLE IF NOT EXISTS fmp_basket_valuation (
    basket TEXT NOT NULL,
    snapshot_date TEXT NOT NULL,
    fwd_pe_blend REAL,
    fwd_pe_ntm REAL,
    total_mcap REAL, ntm_net_income REAL, blend_net_income REAL,
    n_members INTEGER, n_covered_ntm INTEGER, n_covered_blend INTEGER,
    mcap_coverage_ntm REAL, mcap_coverage_blend REAL,
    weight_coverage REAL,
    members_json TEXT,
    PRIMARY KEY (basket, snapshot_date)
);""",

    # Run manifest（审计元数据：冻结 writer 当时的 exact denominator）
    """CREATE TABLE IF NOT EXISTS fmp_forward_runs (
    snapshot_date TEXT NOT NULL,
    run_kind TEXT NOT NULL CHECK(run_kind IN ('weekly','backfill')),
    status TEXT NOT NULL CHECK(status IN ('planned','running','complete','failed')),
    target_universe_json TEXT NOT NULL,
    target_count INTEGER NOT NULL,
    quarter_success INTEGER NOT NULL DEFAULT 0,
    quarter_failure_count INTEGER NOT NULL DEFAULT 0,
    started_at TEXT NOT NULL,
    completed_at TEXT,
    summary_json TEXT,
    PRIMARY KEY (snapshot_date, run_kind)
);""",
    "CREATE INDEX IF NOT EXISTS idx_ffr_status ON fmp_forward_runs(status, snapshot_date);",

    # -- Extended Primary Universe storage foundation (north-star.md 数据层) --

    # -- Security master: identity + eligibility (ETF/fund/ADR exclusion) --
    """CREATE TABLE IF NOT EXISTS security_master (
    symbol TEXT PRIMARY KEY, cik TEXT, company_name TEXT, exchange TEXT,
    is_etf INTEGER NOT NULL DEFAULT 0, is_fund INTEGER NOT NULL DEFAULT 0,
    is_adr INTEGER NOT NULL DEFAULT 0, share_class_of TEXT,
    eligible INTEGER NOT NULL, reason TEXT NOT NULL, updated_at TEXT NOT NULL
);""",

    # -- Extended universe membership windows (as-of reconstitution) --
    """CREATE TABLE IF NOT EXISTS extended_membership (
    symbol TEXT NOT NULL, effective_from TEXT NOT NULL, effective_to TEXT,
    reason TEXT NOT NULL DEFAULT 'screener',
    PRIMARY KEY (symbol, effective_from)
);""",
    "CREATE INDEX IF NOT EXISTS idx_membership_window ON extended_membership(effective_from, effective_to);",

    # -- Per-symbol per-dataset fetch/coverage status + retry bookkeeping.
    # Retry semantics (R2-P1-3): fetch_failed -> next_retry_at = now + min(2^consecutive_failures, 16) days;
    # provider_empty -> negative-cache with TTL, next_retry_at = now + 30 days (settings.PROVIDER_EMPTY_TTL_DAYS);
    # ok -> consecutive_failures reset to 0, last_success_at set, next_retry_at = NULL.
    # dataset also takes the value "identity" (identity backfill queue, see T12/T17).
    """CREATE TABLE IF NOT EXISTS coverage_status (
    symbol TEXT NOT NULL, dataset TEXT NOT NULL, status TEXT NOT NULL,
    detail TEXT, updated_at TEXT NOT NULL,
    last_attempt_at TEXT, last_success_at TEXT,
    consecutive_failures INTEGER NOT NULL DEFAULT 0,
    next_retry_at TEXT,
    PRIMARY KEY (symbol, dataset)
);""",

    # -- Company profile payload cache (raw JSON blob keyed by symbol) --
    """CREATE TABLE IF NOT EXISTS company_profile (
    symbol TEXT PRIMARY KEY, payload TEXT NOT NULL, updated_at TEXT NOT NULL
);""",

    # -- Fundamental vintage: point-in-time snapshots per statement/fiscal period --
    """CREATE TABLE IF NOT EXISTS fundamental_vintage (
    symbol TEXT NOT NULL, statement TEXT NOT NULL, fiscal_date TEXT NOT NULL,
    observed_at TEXT NOT NULL, filing_date TEXT, accepted_date TEXT,
    content_hash TEXT NOT NULL, vintage_quality TEXT NOT NULL, payload TEXT NOT NULL,
    PRIMARY KEY (symbol, statement, fiscal_date, observed_at)
);""",
    "CREATE INDEX IF NOT EXISTS idx_fv_symbol_stmt ON fundamental_vintage(symbol, statement, fiscal_date);",

    # Current-row repairs are observed now, not fabricated historical vintages.
    """CREATE TABLE IF NOT EXISTS fundamental_current_archive (
    archive_id INTEGER PRIMARY KEY,
    operation_id TEXT NOT NULL, archived_at TEXT NOT NULL,
    source_table TEXT NOT NULL, symbol TEXT NOT NULL, original_date TEXT NOT NULL,
    reason TEXT NOT NULL, context TEXT NOT NULL, payload TEXT NOT NULL
);""",
    "CREATE INDEX IF NOT EXISTS idx_fca_operation ON fundamental_current_archive(operation_id);",

    # -- Fundamental backfill run manifest --
    """CREATE TABLE IF NOT EXISTS fundamental_backfill_runs (
    run_id TEXT PRIMARY KEY, universe_hash TEXT NOT NULL, params_json TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'running', started_at TEXT NOT NULL, finished_at TEXT
);""",

    # -- Fundamental backfill per-symbol/dataset job queue --
    """CREATE TABLE IF NOT EXISTS fundamental_backfill_jobs (
    run_id TEXT NOT NULL, symbol TEXT NOT NULL, dataset TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    attempts INTEGER NOT NULL DEFAULT 0, last_error TEXT,
    claimed_at TEXT, completed_at TEXT,
    PRIMARY KEY (run_id, symbol, dataset)
);""",
])

# Pre-compute snake-case column sets per table for fast lookup
_TABLE_COLUMNS: Dict[str, List[str]] = {}


def _get_table_columns(table_name: str, conn: sqlite3.Connection) -> List[str]:
    """Get column names for a table (cached)."""
    if table_name not in _TABLE_COLUMNS:
        rows = conn.execute(f"PRAGMA table_info({table_name})").fetchall()
        _TABLE_COLUMNS[table_name] = [row[1] for row in rows]
    return _TABLE_COLUMNS[table_name]


# Whitelist of valid table names for SQL injection protection
_VALID_TABLES = frozenset({
    "daily_price", "income_quarterly", "balance_sheet_quarterly",
    "cash_flow_quarterly", "ratios_annual", "metrics_quarterly",
    "iv_daily", "options_snapshots",
    "forward_estimates", "forward_metadata",
    "social_sentiment", "market_sentiment",
    "social_trending",
    "social_trending_sectors", "broad_scan_hits",
    "historical_market_cap",
    "concepts", "concept_themes", "company_concept_tags",
    "symbol_concept_edges",
    "fmp_fund_disclosure_holdings", "fx_daily", "fmp_stock_splits",
    "basket_ttm_valuation", "basket_weekly_pe_history",
    "basket_pe_backfill_runs",
    "fmp_estimates", "fmp_earnings", "fmp_etf_holdings_snapshot",
    "fmp_basket_valuation", "fmp_forward_runs",
    "security_master", "extended_membership", "coverage_status",
    "company_profile", "fundamental_vintage", "fundamental_current_archive",
    "fundamental_backfill_runs", "fundamental_backfill_jobs",
})


def _validate_table(table_name: str) -> None:
    """Raise ValueError if table name is not in whitelist."""
    if table_name not in _VALID_TABLES:
        raise ValueError(f"Invalid table name: {table_name!r}")


# ---------------------------------------------------------------------------
# Extended Primary Universe collection kernel: dataset -> destination
# ---------------------------------------------------------------------------
# Shared by `write_symbol_dataset_in_conn` (below) and
# `src/data/fundamental_collector.py` (T8 kernel) so backfill / events /
# reconcile / --scope core all resolve a dataset to the same table.
# NOTE the two different key spaces in play:
#   - `coverage_status.dataset` holds the CURRENT TABLE name (income_quarterly,
#     ...), plus the non-table value "identity" written by the identity path.
#   - `fundamental_backfill_jobs.dataset` holds the DATASET KEY (income, ...).
COLLECTION_DATASET_TABLES = {
    "profile": "company_profile",
    "income": "income_quarterly",
    "balance": "balance_sheet_quarterly",
    "cashflow": "cash_flow_quarterly",
    "ratios": "ratios_annual",
}

# Only the three statements get point-in-time vintage history; the dataset key
# doubles as the `fundamental_vintage.statement` value.
VINTAGE_DATASETS = frozenset({"income", "balance", "cashflow"})


def _validate_column(col: str, valid_cols: List[str]) -> None:
    """Raise ValueError if column is not valid."""
    if col not in valid_cols:
        raise ValueError(f"Invalid column name: {col!r}")


# ---------------------------------------------------------------------------
# MarketStore class
# ---------------------------------------------------------------------------

class MarketStore:
    """SQLite-backed market time-series database."""

    def __init__(self, db_path: Optional[Path] = None, read_only: bool = False):
        self.db_path = Path(db_path or _DEFAULT_DB_PATH)
        self.read_only = read_only
        if read_only:
            if not self.db_path.is_file():
                raise FileNotFoundError(f"market database not found: {self.db_path}")
        else:
            self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._local = threading.local()
        if not read_only:
            self._init_db()

    def _get_conn(self) -> sqlite3.Connection:
        conn = getattr(self._local, 'conn', None)
        if conn is None:
            if self.read_only:
                uri = self.db_path.resolve().as_uri() + "?mode=ro"
                conn = sqlite3.connect(uri, uri=True)
                conn.execute("PRAGMA query_only=ON")
            else:
                conn = sqlite3.connect(str(self.db_path))
            conn.row_factory = sqlite3.Row
            if not self.read_only:
                conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA foreign_keys=ON")
            self._local.conn = conn
        return conn

    def _init_db(self) -> None:
        conn = self._get_conn()
        self._migrate_basket_weekly_pe_run_id(conn)
        conn.executescript(_SCHEMA)
        self._migrate_add_columns(conn)
        conn.commit()

    @staticmethod
    def _migrate_basket_weekly_pe_run_id(conn: sqlite3.Connection) -> None:
        """Bring a pre-run_id basket_weekly_pe_history up to the current shape.

        ``CREATE TABLE IF NOT EXISTS`` does nothing to a table that already
        exists, and ``_insert_validated`` writes only the columns a table
        actually has. A database carrying the older table would therefore
        accept published rows and silently drop their ``run_id``, leaving every
        row unattributable and row-level certification defeated on its first
        production use, with no error anywhere. Review found a legacy empty
        table without this column in an existing database copy.

        An empty legacy table is rebuilt in place -- there is nothing to
        preserve. A *populated* one refuses: those rows predate ownership, no
        correct owner can be invented for them, and choosing between
        re-backfilling and discarding them is a decision rather than a
        default.
        """
        existing = {row[1] for row in conn.execute(
            "PRAGMA table_info(basket_weekly_pe_history)").fetchall()}
        if not existing or "run_id" in existing:
            return
        rows = conn.execute(
            "SELECT COUNT(*) FROM basket_weekly_pe_history").fetchone()[0]
        if rows:
            raise RuntimeError(
                f"basket_weekly_pe_history holds {rows} row(s) written before "
                "run_id ownership existed; they name no run and none can be "
                "inferred. Migrate deliberately -- re-backfill under a new "
                "run, or remove them -- then reopen the store.")
        conn.execute("DROP TABLE basket_weekly_pe_history")
        logger.info(
            "Migration: rebuilt empty basket_weekly_pe_history to carry run_id")
        _TABLE_COLUMNS.pop("basket_weekly_pe_history", None)

    def _migrate_add_columns(self, conn: sqlite3.Connection) -> None:
        """Add any new columns defined in field lists but missing from existing tables."""
        migrations = [
            ("metrics_quarterly", _METRICS_FIELDS, True),
        ]
        for table, fields, already_snake in migrations:
            existing = {row[1] for row in conn.execute(
                f"PRAGMA table_info({table})"
            ).fetchall()}
            snake_fields = fields if already_snake else [_camel_to_snake(f) for f in fields]
            for col in snake_fields:
                if col not in existing:
                    sql_t = _sql_type(col)
                    conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {sql_t}")
                    logger.info("Migration: added column %s.%s", table, col)
        # Invalidate column cache so upsert sees updated schema
        _TABLE_COLUMNS.pop("metrics_quarterly", None)

    def close(self) -> None:
        conn = getattr(self._local, 'conn', None)
        if conn:
            conn.close()
            self._local.conn = None

    # ---- Internal helpers ----

    def _convert_row(self, row: Dict[str, Any], table: str) -> Dict[str, Any]:
        """Convert a camelCase FMP row to snake_case, filtering to valid columns."""
        conn = self._get_conn()
        valid_cols = _get_table_columns(table, conn)
        result = {}
        for key, value in row.items():
            snake = _camel_to_snake(key)
            # Handle changePercent → change_pct for daily_price
            if snake == "change_percent":
                snake = "change_pct"
            if snake in valid_cols:
                result[snake] = value
        return result

    def _upsert_rows_in_conn(self, conn: sqlite3.Connection, table: str,
                              rows: List[Dict]) -> int:
        """Insert or replace already-prepared rows into `table` using `conn`.

        Does not open its own transaction (no `with conn:`) — the caller owns
        the transaction boundary. Only call this from within `transaction()`
        or from a method that already holds `with conn:` itself (e.g.
        `_bulk_upsert`); calling it outside of any open transaction leaves
        each INSERT auto-committed individually.

        Args:
            conn: Connection with an already-open transaction.
            table: Target table name (must be in whitelist).
            rows: List of dicts already filtered/converted to final column
                names (e.g. output of `_convert_row` plus injected keys).

        Returns:
            Number of rows upserted.
        """
        _validate_table(table)
        valid_cols = _get_table_columns(table, conn)
        count = 0
        for row in rows:
            cols = [c for c in row if c in valid_cols]
            if not cols:
                continue
            placeholders = ", ".join(["?"] * len(cols))
            col_names = ", ".join(cols)
            values = [row[c] for c in cols]

            conn.execute(
                f"INSERT OR REPLACE INTO {table} ({col_names}) VALUES ({placeholders})",
                values,
            )
            count += 1

        return count

    def _prepare_upsert_rows(self, table: str, symbol: str, rows: List[Dict],
                             convert: bool = True) -> List[Dict]:
        """Turn provider rows into rows `_upsert_rows_in_conn` can write.

        Deliberately the ONLY implementation of this prep, shared by the
        legacy writer (`_bulk_upsert`) and the T8 collection kernel
        (`write_symbol_dataset_in_conn`): the kernel must produce rows
        byte-identical to the legacy path (T11 parity contract), so both get
        the same camelCase→snake_case conversion, the same uppercase `symbol`
        injection, and the same skip of rows carrying no fiscal date.

        Args:
            table: Target table name (must be in whitelist).
            symbol: Stock symbol to inject into each row.
            rows: List of dicts (camelCase or snake_case).
            convert: If True, convert camelCase → snake_case.

        Returns:
            Prepared rows, in input order, minus any row with no `date`.
        """
        conn = self._get_conn()
        valid_cols = _get_table_columns(table, conn)
        prepared = []

        for row in rows:
            if convert:
                data = self._convert_row(row, table)
            else:
                data = {k: v for k, v in row.items() if k in valid_cols}
            data["symbol"] = symbol.upper()

            if "date" not in data or not data["date"]:
                continue

            prepared.append(data)

        return prepared

    def _bulk_upsert(self, table: str, symbol: str, rows: List[Dict],
                     convert: bool = True) -> int:
        """Insert or replace rows in a single transaction.

        Args:
            table: Target table name (must be in whitelist).
            symbol: Stock symbol to inject into each row.
            rows: List of dicts (camelCase or snake_case).
            convert: If True, convert camelCase → snake_case.

        Returns:
            Number of rows upserted.
        """
        _validate_table(table)
        if not rows:
            return 0

        from src.data.fiscal_repair import STATEMENT_TABLES
        if table in STATEMENT_TABLES:
            # The alias check reads current rows before its first write. Acquire
            # write ownership now so two legacy callers cannot both pass it.
            with self.transaction() as conn:
                prepared = self._prepare_upsert_rows(table, symbol, rows, convert)
                return self._write_current_rows_in_conn(conn, table, prepared)

        conn = self._get_conn()
        prepared = self._prepare_upsert_rows(table, symbol, rows, convert)

        with conn:
            count = self._write_current_rows_in_conn(conn, table, prepared)

        return count

    def _write_current_rows_in_conn(self, conn: sqlite3.Connection, table: str,
                                    rows: List[Dict]) -> int:
        """Shared current writer; quarterly aliases retain archival provenance."""
        from src.data.fiscal_repair import STATEMENT_TABLES, write_statement_in_conn
        if table in STATEMENT_TABLES:
            return write_statement_in_conn(self, conn, table, rows)
        return self._upsert_rows_in_conn(conn, table, rows)

    @contextmanager
    def transaction(self):
        """Explicit multi-statement transaction boundary.

        Opens the thread-local connection with `BEGIN IMMEDIATE`, yields it,
        commits on clean exit, and rolls back on any exception (re-raised).

        Only call `_in_conn`-suffixed helpers (e.g. `_upsert_rows_in_conn`)
        inside this block. Do not call public methods that open their own
        `with conn:` block (e.g. `_bulk_upsert`) — nesting would commit the
        outer transaction early, defeating the rollback guarantee.
        """
        if self.read_only:
            raise RuntimeError("transactions are unavailable on a read-only MarketStore")
        conn = self._get_conn()
        conn.execute("BEGIN IMMEDIATE")
        try:
            yield conn
        except Exception:
            conn.rollback()
            raise
        else:
            conn.commit()

    def _get_rows(self, table: str, symbol: str,
                  start_date: Optional[str] = None,
                  end_date: Optional[str] = None,
                  limit: int = 0) -> List[Dict[str, Any]]:
        """Retrieve rows for a symbol with optional date range and limit."""
        _validate_table(table)
        conn = self._get_conn()

        query = f"SELECT * FROM {table} WHERE symbol = ?"
        params: list = [symbol.upper()]

        if start_date:
            query += " AND date >= ?"
            params.append(start_date)
        if end_date:
            query += " AND date <= ?"
            params.append(end_date)

        query += " ORDER BY date DESC"
        if limit > 0:
            query += " LIMIT ?"
            params.append(limit)

        rows = conn.execute(query, params).fetchall()
        return [dict(r) for r in rows]

    # ---- Daily Price ----

    def upsert_daily_prices(self, symbol: str, rows: List[Dict]) -> int:
        """Upsert daily price rows (camelCase input)."""
        return self._bulk_upsert("daily_price", symbol, rows, convert=True)

    def upsert_daily_prices_df(self, symbol: str, df: pd.DataFrame) -> int:
        """Upsert daily prices from a DataFrame."""
        if df is None or df.empty:
            return 0
        records = df.to_dict("records")
        # DataFrame columns are already snake-ish (date, open, high, etc.)
        # but changePercent might be present
        cleaned = []
        for r in records:
            row = {}
            for k, v in r.items():
                sk = _camel_to_snake(k) if k != k.lower() else k
                if sk == "change_percent":
                    sk = "change_pct"
                # Convert pandas Timestamp to string
                if hasattr(v, "strftime"):
                    v = v.strftime("%Y-%m-%d")
                # Handle NaN
                if isinstance(v, float) and v != v:
                    v = None
                row[sk] = v
            cleaned.append(row)
        return self._bulk_upsert("daily_price", symbol, cleaned, convert=False)

    def get_daily_prices(self, symbol: str, start_date: Optional[str] = None,
                         end_date: Optional[str] = None,
                         limit: int = 0) -> List[Dict[str, Any]]:
        return self._get_rows("daily_price", symbol, start_date, end_date, limit)

    def get_daily_prices_df(self, symbol: str,
                            limit: int = 0) -> Optional[pd.DataFrame]:
        """Return daily prices as a DataFrame.

        Returns DataFrame with columns:
            ["date", "open", "high", "low", "close", "volume", "change", "changePercent"]
        Sorted by date descending (newest first). date dtype is datetime64[ns].
        Returns None if no data found.
        """
        rows = self.get_daily_prices(symbol, limit=limit)
        if not rows:
            return None

        df = pd.DataFrame(rows)

        # Drop symbol column (not part of the standard price columns)
        if "symbol" in df.columns:
            df = df.drop(columns=["symbol"])

        # Rename change_pct → changePercent to match PRICE_COLUMNS convention
        if "change_pct" in df.columns:
            df = df.rename(columns={"change_pct": "changePercent"})

        # Convert date to datetime64
        df["date"] = pd.to_datetime(df["date"])

        # Align column order to match PRICE_COLUMNS
        _PRICE_COLUMNS = ["date", "open", "high", "low", "close",
                          "volume", "change", "changePercent"]
        available = [c for c in _PRICE_COLUMNS if c in df.columns]
        df = df[available]

        # Sort descending (newest first) and reset index
        df = df.sort_values("date", ascending=False).reset_index(drop=True)

        return df

    # ---- Income ----

    def upsert_income(self, symbol: str, rows: List[Dict]) -> int:
        return self._bulk_upsert("income_quarterly", symbol, rows)

    def get_income(self, symbol: str, limit: int = 8) -> List[Dict[str, Any]]:
        return self._get_rows("income_quarterly", symbol, limit=limit)

    # ---- Balance Sheet ----

    def upsert_balance_sheet(self, symbol: str, rows: List[Dict]) -> int:
        return self._bulk_upsert("balance_sheet_quarterly", symbol, rows)

    def get_balance_sheet(self, symbol: str, limit: int = 8) -> List[Dict[str, Any]]:
        return self._get_rows("balance_sheet_quarterly", symbol, limit=limit)

    # ---- Cash Flow ----

    def upsert_cash_flow(self, symbol: str, rows: List[Dict]) -> int:
        return self._bulk_upsert("cash_flow_quarterly", symbol, rows)

    def get_cash_flow(self, symbol: str, limit: int = 8) -> List[Dict[str, Any]]:
        return self._get_rows("cash_flow_quarterly", symbol, limit=limit)

    # ---- Ratios ----

    def upsert_ratios(self, symbol: str, rows: List[Dict]) -> int:
        return self._bulk_upsert("ratios_annual", symbol, rows)

    def get_ratios(self, symbol: str, limit: int = 4) -> List[Dict[str, Any]]:
        return self._get_rows("ratios_annual", symbol, limit=limit)

    # ---- Metrics ----

    def upsert_metrics(self, symbol: str, rows: List[Dict]) -> int:
        return self._bulk_upsert("metrics_quarterly", symbol, rows, convert=False)

    def get_metrics(self, symbol: str, limit: int = 8) -> List[Dict[str, Any]]:
        return self._get_rows("metrics_quarterly", symbol, limit=limit)

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

    # ---- Historical basket GAAP TTM valuation ----

    @staticmethod
    def _require_iso_date(value: str, field_name: str) -> str:
        if not isinstance(value, str):
            raise ValueError(f"{field_name} must be YYYY-MM-DD")
        try:
            return date.fromisoformat(value).isoformat()
        except ValueError as exc:
            raise ValueError(f"{field_name} must be YYYY-MM-DD") from exc

    @staticmethod
    def _json_text(value: Any, field_name: str) -> str:
        try:
            payload = json.loads(value) if isinstance(value, str) else value
            return json.dumps(payload, ensure_ascii=False, sort_keys=True,
                              separators=(",", ":"))
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{field_name} must be valid JSON") from exc

    def replace_fund_disclosure_snapshot(
        self,
        basket_symbol: str,
        holding_date: str,
        source_kind: str,
        rows: List[Dict[str, Any]],
        *,
        rebalance_close_date: str,
        composition_effective_date: str,
        composition_available_date: str,
        fetched_at: str,
        refresh_live: bool = False,
    ) -> int:
        """Atomically replace one disclosure snapshot.

        A live snapshot is frozen per scheduled rebalance. A later run is a
        no-op unless ``refresh_live`` is explicit, preventing drifting live
        weights from rewriting an already published backcast.
        """
        _validate_table("fmp_fund_disclosure_holdings")
        basket = str(basket_symbol).upper().strip()
        if not basket:
            raise ValueError("basket_symbol required")
        holding_date = self._require_iso_date(holding_date, "holding_date")
        rebalance_close_date = self._require_iso_date(
            rebalance_close_date, "rebalance_close_date")
        composition_effective_date = self._require_iso_date(
            composition_effective_date, "composition_effective_date")
        composition_available_date = self._require_iso_date(
            composition_available_date, "composition_available_date")
        if source_kind not in {"disclosure", "live"}:
            raise ValueError("source_kind must be disclosure or live")
        if not fetched_at:
            raise ValueError("fetched_at required")
        if not rows:
            raise ValueError("complete disclosure snapshot cannot be empty")

        indexes = set()
        for row in rows:
            index = row.get("raw_row_index")
            if not isinstance(index, int) or index < 0 or index in indexes:
                raise ValueError("raw_row_index must be unique non-negative integers")
            indexes.add(index)
            if row.get("included") not in {0, 1}:
                raise ValueError("included must be 0 or 1")

        conn = self._get_conn()
        if source_kind == "live" and not refresh_live:
            frozen = conn.execute(
                "SELECT COUNT(*) FROM fmp_fund_disclosure_holdings "
                "WHERE basket_symbol = ? AND source_kind = 'live' "
                "AND rebalance_close_date = ?",
                [basket, rebalance_close_date],
            ).fetchone()[0]
            if frozen:
                return 0

        created_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        with conn:
            if source_kind == "live" and refresh_live:
                conn.execute(
                    "DELETE FROM fmp_fund_disclosure_holdings "
                    "WHERE basket_symbol = ? AND source_kind = 'live' "
                    "AND rebalance_close_date = ?",
                    [basket, rebalance_close_date],
                )
            else:
                conn.execute(
                    "DELETE FROM fmp_fund_disclosure_holdings "
                    "WHERE basket_symbol = ? AND holding_date = ? "
                    "AND source_kind = ?",
                    [basket, holding_date, source_kind],
                )
            for row in rows:
                self._insert_validated(
                    conn,
                    "fmp_fund_disclosure_holdings",
                    {
                        **row,
                        "basket_symbol": basket,
                        "holding_date": holding_date,
                        "source_kind": source_kind,
                        "rebalance_close_date": rebalance_close_date,
                        "composition_effective_date": composition_effective_date,
                        "composition_available_date": composition_available_date,
                        "snapshot_warnings_json": self._json_text(
                            row.get("snapshot_warnings_json", []),
                            "snapshot_warnings_json"),
                        "fetched_at": fetched_at,
                        "created_at": created_at,
                    },
                )
        return len(rows)

    def get_fund_disclosure_snapshots(
        self,
        basket_symbol: str,
        holding_date: Optional[str] = None,
        source_kind: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        conn = self._get_conn()
        query = (
            "SELECT * FROM fmp_fund_disclosure_holdings "
            "WHERE basket_symbol = ?")
        params: List[Any] = [basket_symbol.upper()]
        if holding_date is not None:
            query += " AND holding_date = ?"
            params.append(holding_date)
        if source_kind is not None:
            if source_kind not in {"disclosure", "live"}:
                raise ValueError("source_kind must be disclosure or live")
            query += " AND source_kind = ?"
            params.append(source_kind)
        query += " ORDER BY holding_date, source_kind, raw_row_index"
        return [dict(row) for row in conn.execute(query, params).fetchall()]

    def upsert_fx_daily(self, rows: List[Dict[str, Any]]) -> int:
        _validate_table("fx_daily")
        prepared = []
        created_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        for row in rows:
            currency = str(row.get("currency", "")).upper().strip()
            source_symbol = str(row.get("source_symbol", "")).upper().strip()
            if len(currency) != 3 or not source_symbol:
                raise ValueError("currency/source_symbol required")
            row_date = self._require_iso_date(row.get("date"), "FX date")
            try:
                rate = validate_usd_per_unit(
                    currency, row["usd_per_unit"], source_symbol)
            except KeyError as exc:
                raise ValueError("USD-per-unit rate required") from exc
            prepared.append({
                "currency": currency,
                "date": row_date,
                "usd_per_unit": rate,
                "source_symbol": source_symbol,
                "source": row.get("source", "fmp"),
                "created_at": created_at,
            })
        conn = self._get_conn()
        with conn:
            for row in prepared:
                self._insert_validated(conn, "fx_daily", row)
        return len(prepared)

    def get_fx_at_or_before(
        self, currency: str, valuation_date: str,
    ) -> Optional[Dict[str, Any]]:
        conn = self._get_conn()
        row = conn.execute(
            "SELECT * FROM fx_daily WHERE currency = ? AND date <= ? "
            "ORDER BY date DESC LIMIT 1",
            [currency.upper(), valuation_date],
        ).fetchone()
        return dict(row) if row else None

    def replace_stock_splits(
        self, symbol: str, rows: List[Dict[str, Any]],
    ) -> int:
        """Replace complete split history for a symbol, including empty history."""
        _validate_table("fmp_stock_splits")
        normalized_symbol = str(symbol).upper().strip()
        if not normalized_symbol:
            raise ValueError("symbol required")
        fetched_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        prepared = []
        seen_dates = set()
        for row in rows:
            row_symbol = str(row.get("symbol", normalized_symbol)).upper()
            if row_symbol != normalized_symbol:
                raise ValueError("split row symbol mismatch")
            split_date = self._require_iso_date(row.get("date"), "split date")
            if split_date in seen_dates:
                raise ValueError("duplicate split date")
            seen_dates.add(split_date)
            try:
                numerator = float(row["numerator"])
                denominator = float(row["denominator"])
            except (KeyError, TypeError, ValueError) as exc:
                raise ValueError("split ratio must be positive") from exc
            split_type = row.get("split_type", row.get("splitType"))
            if numerator <= 0 or denominator <= 0 or not split_type:
                raise ValueError("split ratio/type must be valid")
            prepared.append({
                "symbol": normalized_symbol,
                "date": split_date,
                "numerator": numerator,
                "denominator": denominator,
                "split_type": split_type,
                "source": row.get("source", "fmp"),
                "fetched_at": fetched_at,
            })
        conn = self._get_conn()
        with conn:
            conn.execute(
                "DELETE FROM fmp_stock_splits WHERE symbol = ?",
                [normalized_symbol],
            )
            for row in prepared:
                self._insert_validated(conn, "fmp_stock_splits", row)
        return len(prepared)

    def get_stock_splits(self, symbol: str) -> List[Dict[str, Any]]:
        conn = self._get_conn()
        rows = conn.execute(
            "SELECT * FROM fmp_stock_splits WHERE symbol = ? ORDER BY date",
            [symbol.upper()],
        ).fetchall()
        return [dict(row) for row in rows]

    def replace_basket_ttm_valuation_range(
        self,
        basket_symbol: str,
        from_date: str,
        to_date: str,
        rows: List[Dict[str, Any]],
    ) -> int:
        """Atomically replace one output range so stale prior rows cannot survive."""
        _validate_table("basket_ttm_valuation")
        basket = str(basket_symbol).upper().strip()
        start = self._require_iso_date(from_date, "from_date")
        end = self._require_iso_date(to_date, "to_date")
        if not basket or start > end:
            raise ValueError("valid basket_symbol and date range required")
        if not rows:
            raise ValueError("non-empty basket valuation range required")
        created_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        prepared = []
        seen_dates = set()
        for row in rows:
            valuation_date = self._require_iso_date(
                row.get("valuation_date"), "valuation_date")
            if not start <= valuation_date <= end or valuation_date in seen_dates:
                raise ValueError("valuation dates must be unique and inside range")
            seen_dates.add(valuation_date)
            for field_name in (
                "weight_coverage", "mcap_weight_coverage",
                "income_weight_coverage", "fx_weight_coverage",
            ):
                value = row.get(field_name)
                if value is None or not 0 <= float(value) <= 1:
                    raise ValueError(f"{field_name} must be between 0 and 1")
            prepared.append({
                **row,
                "basket_symbol": basket,
                "valuation_date": valuation_date,
                "members_json": self._json_text(
                    row.get("members_json", []), "members_json"),
                "warnings_json": self._json_text(
                    row.get("warnings_json", []), "warnings_json"),
                "mcap_sanity_json": self._json_text(
                    row.get("mcap_sanity_json", []), "mcap_sanity_json"),
                "created_at": created_at,
            })
        conn = self._get_conn()
        with conn:
            conn.execute(
                "DELETE FROM basket_ttm_valuation "
                "WHERE basket_symbol = ? AND valuation_date BETWEEN ? AND ?",
                [basket, start, end],
            )
            for row in prepared:
                self._insert_validated(conn, "basket_ttm_valuation", row)
        return len(prepared)

    def get_basket_ttm_valuations(
        self,
        basket_symbol: str,
        from_date: Optional[str] = None,
        to_date: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        conn = self._get_conn()
        query = "SELECT * FROM basket_ttm_valuation WHERE basket_symbol = ?"
        params: List[Any] = [basket_symbol.upper()]
        if from_date is not None:
            query += " AND valuation_date >= ?"
            params.append(from_date)
        if to_date is not None:
            query += " AND valuation_date <= ?"
            params.append(to_date)
        query += " ORDER BY valuation_date"
        return [dict(row) for row in conn.execute(query, params).fetchall()]

    # ---- Weekly basket PE valuation SSOT (Plan 2026-07-19 §3.3 — Task 2) ----

    _BWPH_QUALITY_TIERS = frozenset({
        "actual_only", "latest_consensus_tail", "unpublishable",
    })
    _BWPH_COMPLETE_TIER = "actual_only"

    def _validate_bwph_row(self, row: Dict[str, Any]) -> Dict[str, Any]:
        """Row-intrinsic validation only; does not touch the DB.

        Mirrors the DDL CHECK constraints in Python so a bad row fails fast
        with a clear ValueError before any row in the batch is written.
        """
        basket = str(row.get("basket") or "").upper().strip()
        if not basket:
            raise ValueError("basket required")
        valuation_date = self._require_iso_date(
            row.get("valuation_date"), "valuation_date")
        composition_effective_date = self._require_iso_date(
            row.get("composition_effective_date"), "composition_effective_date")
        composition_available_date = self._require_iso_date(
            row.get("composition_available_date"), "composition_available_date")
        methodology_version = row.get("methodology_version")
        if not methodology_version:
            raise ValueError("methodology_version required")
        quality_tier = row.get("quality_tier")
        if quality_tier not in self._BWPH_QUALITY_TIERS:
            raise ValueError(f"invalid quality_tier: {quality_tier!r}")
        run_id = str(row.get("run_id") or "").strip()
        if not run_id:
            raise ValueError("run_id required: a published row must name the "
                             "run accountable for it")
        for field_name in ("n_members", "n_covered_ttm", "n_covered_hindsight"):
            value = row.get(field_name)
            if not isinstance(value, int) or isinstance(value, bool) or value < 0:
                raise ValueError(f"{field_name} must be a non-negative integer")
        for field_name in ("mcap_coverage_ttm", "mcap_coverage_hindsight"):
            value = row.get(field_name)
            if value is None or not 0 <= float(value) <= 1:
                raise ValueError(f"{field_name} must be between 0 and 1")
        actual_q = row.get("hindsight_actual_quarters")
        estimate_q = row.get("hindsight_estimate_quarters")
        for field_name, value in (
            ("hindsight_actual_quarters", actual_q),
            ("hindsight_estimate_quarters", estimate_q),
        ):
            if not isinstance(value, int) or isinstance(value, bool) \
                    or not 0 <= value <= 4:
                raise ValueError(
                    f"{field_name} must be an integer between 0 and 4")
        if quality_tier != "unpublishable" and actual_q + estimate_q != 4:
            raise ValueError(
                "hindsight_actual_quarters + hindsight_estimate_quarters "
                "must equal 4 for publishable quality_tier rows")
        return {
            **row,
            "basket": basket,
            "run_id": run_id,
            "valuation_date": valuation_date,
            "composition_effective_date": composition_effective_date,
            "composition_available_date": composition_available_date,
            "members_json": self._json_text(
                row.get("members_json", []), "members_json"),
            "warnings_json": self._json_text(
                row.get("warnings_json", []), "warnings_json"),
        }

    def upsert_basket_weekly_pe_batch(self, rows: List[Dict[str, Any]]) -> int:
        """Atomic whole-batch upsert for basket_weekly_pe_history.

        R5 tail evolution contract: for the same (basket, valuation_date,
        methodology_version), quality_tier may only move
        latest_consensus_tail -> actual_only (or repeat unchanged) as the
        weekly hindsight tail fills in with real earnings. Downgrading away
        from actual_only is rejected and logged as a warning — it should
        never happen under a stable methodology.

        A different methodology_version is rejected for ANY existing row,
        regardless of quality_tier — not just actual_only/complete ones.
        Allowing a cross-version overwrite of a still-tail or unpublishable
        row would let the historical series silently mix methodologies
        across a version boundary, exactly what a fixed methodology_version
        exists to prevent. A genuine methodology change must go through an
        explicit version migration (delete the old version's rows, then
        backfill under the new version) rather than an implicit overwrite
        through this upsert path.

        Whole batch is one transaction: any row rejected — whether by
        row-intrinsic validation or by the DB-state-dependent checks above —
        rolls back every write already made earlier in the same batch call.
        """
        _validate_table("basket_weekly_pe_history")
        if not rows:
            raise ValueError("non-empty batch required")
        prepared = [self._validate_bwph_row(row) for row in rows]
        seen = set()
        for row in prepared:
            key = (row["basket"], row["valuation_date"])
            if key in seen:
                raise ValueError(f"duplicate row for {key} within batch")
            seen.add(key)

        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        conn = self._get_conn()
        with conn:
            self._upsert_bwph_prepared(conn, prepared, now)
        return len(prepared)

    def _check_bwph_replacement(self, existing, row: Dict[str, Any]) -> None:
        if existing["methodology_version"] != row["methodology_version"]:
            raise ValueError(
                "refusing cross-methodology_version overwrite of existing row "
                f"{row['basket']}/{row['valuation_date']}. "
                "Use an explicit version migration instead.")
        if (existing["quality_tier"] == self._BWPH_COMPLETE_TIER
                and row["quality_tier"] != self._BWPH_COMPLETE_TIER):
            logger.warning(
                "Rejected quality_tier downgrade for %s/%s: %s -> %s",
                row["basket"], row["valuation_date"],
                existing["quality_tier"], row["quality_tier"])
            raise ValueError(
                "refusing quality_tier downgrade for "
                f"{row['basket']}/{row['valuation_date']}: "
                f"{existing['quality_tier']!r} -> {row['quality_tier']!r}")

    def _upsert_bwph_prepared(self, conn: sqlite3.Connection,
                             prepared: List[Dict[str, Any]], now: str) -> None:
        """Shared row write logic; the public caller owns the transaction."""
        for row in prepared:
            existing = conn.execute(
                "SELECT quality_tier, methodology_version, created_at "
                "FROM basket_weekly_pe_history "
                "WHERE basket = ? AND valuation_date = ?",
                [row["basket"], row["valuation_date"]]).fetchone()
            if existing is not None:
                self._check_bwph_replacement(existing, row)
            self._insert_validated(conn, "basket_weekly_pe_history", {
                **row, "created_at": existing["created_at"] if existing else now,
                "last_updated": now,
            })

    def commit_basket_weekly_pe_window(
        self, rows: List[Dict[str, Any]], completed_event: Dict[str, Any],
        certify: Callable[[sqlite3.Connection], bool],
    ) -> int:
        """Publish one full retained window and its completion atomically.

        certify reads the candidate database with query_only enabled and must
        return True. Any rejected row, prune, manifest insert or certification
        rolls back to the previously committed product. The caller records
        run_failed separately, after this method has rolled back.
        """
        if not rows or not callable(certify):
            raise ValueError("non-empty window and certification required")
        event = self._validate_bpbr_row(completed_event)
        if event["event_kind"] != "run_completed":
            raise ValueError("window requires a run_completed event")
        prepared = [self._validate_bwph_row(row) for row in rows]
        basket, run_id = event["basket"], event["run_id"]
        start, end = event["expected_from_date"], event["expected_to_date"]
        if any(row["basket"] != basket or row["run_id"] != run_id
               or row["methodology_version"] != event["methodology_version"]
               or not start <= row["valuation_date"] <= end for row in prepared):
            raise ValueError("window rows disagree with completion identity/range")
        conn = self._get_conn()
        if conn.in_transaction:
            raise ValueError("window commit requires its own transaction")
        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        def week(day):
            return date.fromisoformat(day).isocalendar()[:2]
        try:
            conn.execute("BEGIN IMMEDIATE")
            started = conn.execute(
                "SELECT * FROM basket_pe_backfill_runs WHERE basket = ? "
                "AND run_id = ? AND event_kind = 'run_started'",
                [basket, run_id]).fetchall()
            if len(started) != 1:
                raise ValueError("window requires exactly one persisted run_started")
            for key in ("expected_from_date", "expected_to_date", "methodology_version"):
                if started[0][key] != event[key]:
                    raise ValueError(f"completion disagrees with started: {key}")
            expected = json.loads(started[0]["payload_json"]).get("expected_weeks")
            if not isinstance(expected, list) or not expected:
                raise ValueError("run_started has no frozen expected weeks")
            expected_by_week = {week(day): day for day in expected}
            rows_by_week = {week(row["valuation_date"]): row for row in prepared}
            if (len(rows_by_week) != len(prepared)
                    or len(expected_by_week) != len(expected)
                    or set(rows_by_week) != set(expected_by_week)
                    or any(row["valuation_date"] > expected_by_week[key]
                           for key, row in rows_by_week.items())):
                raise ValueError("candidate week set differs from frozen expected weeks")
            # Refuse accidental rewind of the retained product.
            latest = conn.execute(
                "SELECT MAX(expected_to_date) FROM basket_pe_backfill_runs "
                "WHERE basket = ? AND event_kind = 'run_completed'", [basket]
            ).fetchone()[0]
            if latest and end < latest:
                raise ValueError("retained window cannot move backwards")
            previous = conn.execute(
                "SELECT * FROM basket_weekly_pe_history WHERE basket = ?",
                [basket]).fetchall()
            for old in previous:
                if old["methodology_version"] != event["methodology_version"]:
                    raise ValueError("cross-methodology_version window replacement")
                if old["run_id"] == run_id:
                    raise ValueError("window run_id already owns published rows")
                replacement = rows_by_week.get(week(old["valuation_date"]))
                if replacement is not None:
                    self._check_bwph_replacement(old, replacement)
            self._upsert_bwph_prepared(conn, prepared, now)
            # Includes superseded sample dates within a week as well as rows
            # outside the retained range; missing weeks were rejected above.
            conn.execute("DELETE FROM basket_weekly_pe_history "
                         "WHERE basket = ? AND run_id != ?", [basket, run_id])
            self._append_bpbr_prepared(conn, [event], now)
            conn.execute("PRAGMA query_only=ON")
            try:
                if certify(conn) is not True:
                    raise ValueError("weekly window certification failed")
            finally:
                conn.execute("PRAGMA query_only=OFF")
            conn.commit()
        except BaseException:
            conn.rollback()
            raise
        return len(prepared)

    def get_basket_weekly_pe_history(
        self,
        basket: str,
        from_date: Optional[str] = None,
        to_date: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Read-only range query. Never writes (including last_updated)."""
        conn = self._get_conn()
        query = "SELECT * FROM basket_weekly_pe_history WHERE basket = ?"
        params: List[Any] = [basket.upper()]
        if from_date is not None:
            query += " AND valuation_date >= ?"
            params.append(from_date)
        if to_date is not None:
            query += " AND valuation_date <= ?"
            params.append(to_date)
        query += " ORDER BY valuation_date"
        return [dict(row) for row in conn.execute(query, params).fetchall()]

    # ---- 三指数 backfill run manifest（Plan 2026-07-19 Task 4 / issue048）----

    _BPBR_EVENT_KINDS = frozenset({
        "run_started", "forced_refresh", "run_completed", "run_failed",
    })

    def _validate_bpbr_row(self, row: Dict[str, Any]) -> Dict[str, Any]:
        run_id = str(row.get("run_id") or "").strip()
        basket = str(row.get("basket") or "").upper().strip()
        if not run_id or not basket:
            raise ValueError("run_id and basket required")
        event_seq = row.get("event_seq")
        if not isinstance(event_seq, int) or isinstance(event_seq, bool) \
                or event_seq < 0:
            raise ValueError("event_seq must be a non-negative integer")
        event_kind = row.get("event_kind")
        if event_kind not in self._BPBR_EVENT_KINDS:
            raise ValueError(f"invalid event_kind: {event_kind!r}")
        frequency = str(row.get("frequency") or "").strip()
        if not frequency:
            raise ValueError("frequency required")
        expected_from = self._require_iso_date(
            row.get("expected_from_date"), "expected_from_date")
        expected_to = self._require_iso_date(
            row.get("expected_to_date"), "expected_to_date")
        if expected_from > expected_to:
            raise ValueError(
                "expected_from_date must be on or before expected_to_date")
        methodology_version = row.get("methodology_version")
        if not methodology_version:
            raise ValueError("methodology_version required")
        target_count = row.get("target_count")
        if not isinstance(target_count, int) or isinstance(target_count, bool) \
                or target_count < 0:
            raise ValueError("target_count must be a non-negative integer")
        return {
            "run_id": run_id,
            "basket": basket,
            "event_seq": event_seq,
            "event_kind": event_kind,
            "frequency": frequency,
            "expected_from_date": expected_from,
            "expected_to_date": expected_to,
            "methodology_version": str(methodology_version),
            "target_count": target_count,
            "target_universe_json": self._json_text(
                row.get("target_universe_json", []), "target_universe_json"),
            "payload_json": self._json_text(
                row.get("payload_json", {}), "payload_json"),
        }

    def append_basket_pe_run_events(self, rows: List[Dict[str, Any]]) -> int:
        """Append run-manifest events. Never rewrites an existing event.

        The manifest is the verifier's denominator SSOT and its only evidence
        for repairs that the source tables can no longer show, so a plain
        INSERT is deliberate: re-appending an existing (run_id, basket,
        event_seq) raises instead of quietly replacing history. The whole
        batch is one transaction.
        """
        _validate_table("basket_pe_backfill_runs")
        if not rows:
            raise ValueError("non-empty batch required")
        prepared = [self._validate_bpbr_row(row) for row in rows]
        seen = set()
        for row in prepared:
            key = (row["run_id"], row["basket"], row["event_seq"])
            if key in seen:
                raise ValueError(f"duplicate manifest event within batch: {key}")
            seen.add(key)
        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        conn = self._get_conn()
        try:
            with conn:
                self._append_bpbr_prepared(conn, prepared, now)
        except sqlite3.IntegrityError as exc:
            raise ValueError(
                f"refusing to rewrite an existing manifest event: {exc}") from exc
        return len(prepared)

    @staticmethod
    def _append_bpbr_prepared(conn: sqlite3.Connection,
                             prepared: List[Dict[str, Any]], now: str) -> None:
        for row in prepared:
            conn.execute(
                "INSERT INTO basket_pe_backfill_runs "
                "(run_id, basket, event_seq, event_kind, frequency, "
                "expected_from_date, expected_to_date, methodology_version, "
                "target_count, target_universe_json, payload_json, created_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                [row["run_id"], row["basket"], row["event_seq"], row["event_kind"],
                 row["frequency"], row["expected_from_date"], row["expected_to_date"],
                 row["methodology_version"], row["target_count"],
                 row["target_universe_json"], row["payload_json"], now])

    def get_basket_pe_run_events(
        self,
        basket: Optional[str] = None,
        run_id: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Read-only manifest query. Never writes."""
        query = "SELECT * FROM basket_pe_backfill_runs WHERE 1=1"
        params: List[Any] = []
        if basket is not None:
            query += " AND basket = ?"
            params.append(basket.upper())
        if run_id is not None:
            query += " AND run_id = ?"
            params.append(run_id)
        query += " ORDER BY run_id, basket, event_seq"
        return [dict(row)
                for row in self._get_conn().execute(query, params).fetchall()]

    # ---- FMP forward EPS 数据线（Spec 2026-07-09 §5.2/§5.3）----
    # 输入行均为 ingestion 层产出的 snake_case 规范化行，不做 camelCase 转换。

    _FMP_KINDS = frozenset({"weekly", "backfill"})
    _FMP_PERIODS = frozenset({"Q", "FY"})
    _FMP_RUN_STATUS = frozenset({"planned", "running", "complete", "failed"})

    def _insert_validated(self, conn, table: str, data: Dict[str, Any]) -> None:
        valid_cols = _get_table_columns(table, conn)
        cols = [c for c in data if c in valid_cols]
        conn.execute(
            f"INSERT OR REPLACE INTO {table} ({', '.join(cols)}) "
            f"VALUES ({', '.join(['?'] * len(cols))})",
            [data[c] for c in cols],
        )

    def upsert_fmp_estimates(self, symbol: str, rows: List[Dict]) -> int:
        """周频/backfill estimates 快照。PK 同键替换、异 snapshot 追加。"""
        _validate_table("fmp_estimates")
        sym = symbol.upper()
        # 事务前全量校验：坏行直接拒绝整批
        for row in rows:
            if not row.get("snapshot_date") or not row.get("fiscal_date"):
                raise ValueError(f"fmp_estimates row missing required dates: {sym}")
            if row.get("period_type") not in self._FMP_PERIODS:
                raise ValueError(f"invalid period_type: {row.get('period_type')!r}")
            if row.get("snapshot_kind") not in self._FMP_KINDS:
                raise ValueError(f"invalid snapshot_kind: {row.get('snapshot_kind')!r}")
        conn = self._get_conn()
        with conn:
            for row in rows:
                self._insert_validated(conn, "fmp_estimates",
                                       {**row, "symbol": sym})
        return len(rows)

    def get_fmp_estimates(self, symbol: str, snapshot_date: Optional[str] = None,
                          period_type: Optional[str] = None,
                          snapshot_kind: Optional[str] = "weekly") -> List[Dict]:
        """默认 weekly 口径；snapshot_date 缺省时只取最新 weekly 快照。

        snapshot_kind=None 仅供显式审计调用方：跨 kind 全量返回。
        backfill 永远不能成为隐式"最新 PIT 快照"。
        """
        if period_type is not None and period_type not in self._FMP_PERIODS:
            raise ValueError(f"invalid period_type: {period_type!r}")
        if snapshot_kind is not None and snapshot_kind not in self._FMP_KINDS:
            raise ValueError(f"invalid snapshot_kind: {snapshot_kind!r}")
        conn = self._get_conn()
        sym = symbol.upper()
        query = "SELECT * FROM fmp_estimates WHERE symbol = ?"
        params: list = [sym]
        if snapshot_kind is not None:
            query += " AND snapshot_kind = ?"
            params.append(snapshot_kind)
            if snapshot_date is None:
                row = conn.execute(
                    "SELECT MAX(snapshot_date) AS d FROM fmp_estimates "
                    "WHERE symbol = ? AND snapshot_kind = ?",
                    [sym, snapshot_kind],
                ).fetchone()
                if not row or not row["d"]:
                    return []
                snapshot_date = row["d"]
        if snapshot_date is not None:
            query += " AND snapshot_date = ?"
            params.append(snapshot_date)
        if period_type is not None:
            query += " AND period_type = ?"
            params.append(period_type)
        query += " ORDER BY snapshot_date, period_type, fiscal_date"
        return [dict(r) for r in conn.execute(query, params).fetchall()]

    def replace_fmp_earnings(self, symbol: str, rows: List[Dict]) -> int:
        """先清删该股 eps_actual IS NULL 的预排幽灵行，再 upsert，同一事务。

        同 PK 且新行映射更弱（fiscal_date=NULL）时保留既有非空 fiscal 映射，
        只更新 actual 值（Task 5 冻结行为：weekly 重跑不得把已匹配行降级为 none）。
        """
        _validate_table("fmp_earnings")
        sym = symbol.upper()
        for row in rows:
            if not row.get("announce_date"):
                raise ValueError(f"fmp_earnings row missing announce_date: {sym}")
        conn = self._get_conn()
        with conn:
            conn.execute(
                "DELETE FROM fmp_earnings WHERE symbol = ? AND eps_actual IS NULL",
                [sym],
            )
            for row in rows:
                data = {**row, "symbol": sym}
                if data.get("fiscal_date") is None:
                    existing = conn.execute(
                        "SELECT fiscal_date, match_method FROM fmp_earnings "
                        "WHERE symbol = ? AND announce_date = ?",
                        [sym, data["announce_date"]],
                    ).fetchone()
                    if existing and existing["fiscal_date"] is not None:
                        data["fiscal_date"] = existing["fiscal_date"]
                        data["match_method"] = existing["match_method"]
                self._insert_validated(conn, "fmp_earnings", data)
        return len(rows)

    def get_fmp_earnings(self, symbol: str,
                         as_of: Optional[str] = None) -> List[Dict]:
        """财报事实。as_of: 只取 announce_date <= as_of（历史计算防泄露）。"""
        conn = self._get_conn()
        query = "SELECT * FROM fmp_earnings WHERE symbol = ?"
        params: list = [symbol.upper()]
        if as_of:
            query += " AND announce_date <= ?"
            params.append(as_of)
        query += " ORDER BY announce_date"
        return [dict(r) for r in conn.execute(query, params).fetchall()]

    def replace_fmp_etf_holdings(self, basket: str, snapshot_date: str,
                                 rows: List[Dict]) -> int:
        """整批替换 (basket, snapshot_date) 快照，坏行整批拒绝。"""
        _validate_table("fmp_etf_holdings_snapshot")
        bk = basket.upper()
        if not snapshot_date:
            raise ValueError("snapshot_date required")
        for row in rows:
            if row.get("raw_row_index") is None or row.get("included") is None:
                raise ValueError(
                    f"holdings row missing raw_row_index/included: {bk}")
        conn = self._get_conn()
        with conn:
            conn.execute(
                "DELETE FROM fmp_etf_holdings_snapshot "
                "WHERE basket = ? AND snapshot_date = ?",
                [bk, snapshot_date],
            )
            for row in rows:
                self._insert_validated(
                    conn, "fmp_etf_holdings_snapshot",
                    {**row, "basket": bk, "snapshot_date": snapshot_date})
        return len(rows)

    def get_fmp_etf_holdings(self, basket: str, snapshot_date: str,
                             included_only: bool = False) -> List[Dict]:
        conn = self._get_conn()
        query = ("SELECT * FROM fmp_etf_holdings_snapshot "
                 "WHERE basket = ? AND snapshot_date = ?")
        params: list = [basket.upper(), snapshot_date]
        if included_only:
            query += " AND included = 1"
        query += " ORDER BY raw_row_index"
        return [dict(r) for r in conn.execute(query, params).fetchall()]

    def upsert_fmp_basket_valuation(self, row: Dict) -> int:
        """Phase 2 写入；本期只锁契约。PK: (basket, snapshot_date)。"""
        _validate_table("fmp_basket_valuation")
        if not row.get("basket") or not row.get("snapshot_date"):
            raise ValueError("basket and snapshot_date required")
        conn = self._get_conn()
        with conn:
            self._insert_validated(conn, "fmp_basket_valuation",
                                   {**row, "basket": row["basket"].upper()})
        return 1

    def get_fmp_basket_valuation(self, basket: str,
                                 snapshot_date: Optional[str] = None) -> List[Dict]:
        conn = self._get_conn()
        query = "SELECT * FROM fmp_basket_valuation WHERE basket = ?"
        params: list = [basket.upper()]
        if snapshot_date:
            query += " AND snapshot_date = ?"
            params.append(snapshot_date)
        query += " ORDER BY snapshot_date DESC"
        return [dict(r) for r in conn.execute(query, params).fetchall()]

    def commit_fmp_basket_valuations(self, rows, certify) -> int:
        """Atomically certify six derived PIT rows; an existing vintage is frozen."""
        baskets = {"SPY", "QQQ", "SOX", "MAGS", "IGV", "XLF"}
        if len(rows) != 6 or {r.get("basket") for r in rows} != baskets:
            raise ValueError("six unique PIT baskets required")
        snapshots = {
            self._require_iso_date(r.get("snapshot_date"), "snapshot_date")
            for r in rows
        }
        if len(snapshots) != 1 or not callable(certify):
            raise ValueError("one PIT snapshot and certification required")
        snapshot = next(iter(snapshots))
        prepared = [
            {
                **r,
                "members_json": self._json_text(r.get("members_json"), "members_json"),
            }
            for r in rows
        ]
        conn = self._get_conn()
        if conn.in_transaction:
            raise ValueError("PIT commit requires its own transaction")
        try:
            conn.execute("BEGIN IMMEDIATE")
            for row in prepared:
                existing = conn.execute(
                    "SELECT * FROM fmp_basket_valuation "
                    "WHERE basket=? AND snapshot_date=?",
                    [row["basket"], snapshot],
                ).fetchone()
                if existing:
                    for key, value in row.items():
                        old = existing[key]
                        if key == "members_json":
                            old, value = json.loads(old), json.loads(value)
                        if old != value:
                            raise ValueError(
                                "refusing to rewrite frozen PIT valuation "
                                f"{row['basket']}/{snapshot}"
                            )
                else:
                    self._insert_validated(conn, "fmp_basket_valuation", row)
            conn.execute("PRAGMA query_only=ON")
            try:
                if certify(conn) is not True:
                    raise ValueError("PIT valuation certification failed")
            finally:
                conn.execute("PRAGMA query_only=OFF")
            conn.commit()
        except BaseException:
            conn.rollback()
            raise
        return len(rows)

    def upsert_fmp_forward_run(self, row: Dict) -> int:
        """Run manifest。同 PK 只允许更新执行统计；universe 不可变。

        插入时 target_universe（list）排序去重后 JSON 序列化冻结；
        更新时若传入 universe 与既存不一致 → ValueError（禁止改写历史分母）。
        """
        _validate_table("fmp_forward_runs")
        snapshot_date = row.get("snapshot_date")
        run_kind = row.get("run_kind")
        status = row.get("status")
        if not snapshot_date:
            raise ValueError("snapshot_date required")
        if run_kind not in self._FMP_KINDS:
            raise ValueError(f"invalid run_kind: {run_kind!r}")
        if status not in self._FMP_RUN_STATUS:
            raise ValueError(f"invalid status: {status!r}")

        universe = row.get("target_universe")
        universe_json = None
        if universe is not None:
            normalized = sorted({str(s).upper() for s in universe})
            universe_json = json.dumps(normalized)

        conn = self._get_conn()
        with conn:
            existing = conn.execute(
                "SELECT target_universe_json, started_at FROM fmp_forward_runs "
                "WHERE snapshot_date = ? AND run_kind = ?",
                [snapshot_date, run_kind],
            ).fetchone()
            if existing:
                if (universe_json is not None
                        and universe_json != existing["target_universe_json"]):
                    raise ValueError(
                        "target universe mismatch for existing run manifest "
                        f"({snapshot_date}, {run_kind}); history is immutable")
                conn.execute(
                    "UPDATE fmp_forward_runs SET status = ?, "
                    "quarter_success = ?, quarter_failure_count = ?, "
                    "completed_at = ?, summary_json = ? "
                    "WHERE snapshot_date = ? AND run_kind = ?",
                    [status,
                     int(row.get("quarter_success", 0)),
                     int(row.get("quarter_failure_count", 0)),
                     row.get("completed_at"),
                     row.get("summary_json"),
                     snapshot_date, run_kind],
                )
            else:
                if universe_json is None:
                    raise ValueError("target_universe required on first insert")
                if not row.get("started_at"):
                    raise ValueError("started_at required on first insert")
                conn.execute(
                    "INSERT INTO fmp_forward_runs (snapshot_date, run_kind, "
                    "status, target_universe_json, target_count, "
                    "quarter_success, quarter_failure_count, started_at, "
                    "completed_at, summary_json) VALUES (?,?,?,?,?,?,?,?,?,?)",
                    [snapshot_date, run_kind, status, universe_json,
                     len(json.loads(universe_json)),
                     int(row.get("quarter_success", 0)),
                     int(row.get("quarter_failure_count", 0)),
                     row["started_at"],
                     row.get("completed_at"),
                     row.get("summary_json")],
                )
        return 1

    def has_fmp_weekly_estimates(self, snapshot_date: str) -> bool:
        """只读检查：该 snapshot_date 是否已有 weekly 行（backfill 同日守卫）。"""
        conn = self._get_conn()
        row = conn.execute(
            "SELECT 1 FROM fmp_estimates "
            "WHERE snapshot_date = ? AND snapshot_kind = 'weekly' LIMIT 1",
            [snapshot_date],
        ).fetchone()
        return row is not None

    def get_fmp_forward_run(self, snapshot_date: str,
                            run_kind: str = "weekly") -> Optional[Dict]:
        conn = self._get_conn()
        row = conn.execute(
            "SELECT * FROM fmp_forward_runs "
            "WHERE snapshot_date = ? AND run_kind = ?",
            [snapshot_date, run_kind],
        ).fetchone()
        if not row:
            return None
        result = dict(row)
        result["target_universe"] = json.loads(result["target_universe_json"])
        return result

    # ---- Social Sentiment ----

    def upsert_social_sentiment(self, symbol: str, rows: List[Dict]) -> int:
        """Upsert social sentiment rows. PK: (symbol, date, source).

        Args:
            symbol: Stock ticker.
            rows: List of dicts from adanos_client.get_sentiment_rows().

        Returns:
            Number of rows upserted.
        """
        if not rows:
            return 0
        conn = self._get_conn()
        valid_cols = _get_table_columns("social_sentiment", conn)
        count = 0
        with conn:
            for row in rows:
                data = {k: v for k, v in row.items() if k in valid_cols}
                data["symbol"] = symbol.upper()
                if not data.get("date") or not data.get("source"):
                    continue
                cols = [c for c in data if c in valid_cols]
                placeholders = ", ".join(["?"] * len(cols))
                col_names = ", ".join(cols)
                values = [data[c] for c in cols]
                conn.execute(
                    "INSERT OR REPLACE INTO social_sentiment ({}) VALUES ({})".format(
                        col_names, placeholders),
                    values,
                )
                count += 1
        return count

    def get_social_sentiment(
        self,
        symbol: str,
        source: Optional[str] = None,
        limit: int = 30,
    ) -> List[Dict[str, Any]]:
        """Get sentiment history for a symbol, newest first.

        Args:
            symbol: Stock ticker.
            source: Filter by 'reddit' or 'x'. None = both.
            limit: Max rows to return.
        """
        conn = self._get_conn()
        query = "SELECT * FROM social_sentiment WHERE symbol = ?"
        params: list = [symbol.upper()]
        if source:
            query += " AND source = ?"
            params.append(source)
        query += " ORDER BY date DESC LIMIT ?"
        params.append(limit)
        rows = conn.execute(query, params).fetchall()
        return [dict(r) for r in rows]

    def get_latest_social_sentiment(
        self,
        symbol: str,
        source: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """Get most recent sentiment snapshot for a symbol."""
        rows = self.get_social_sentiment(symbol, source=source, limit=1)
        return rows[0] if rows else None

    def get_social_sentiment_bulk(
        self,
        symbols: List[str],
        source: Optional[str] = None,
        days: int = 1,
    ) -> Dict[str, List[Dict[str, Any]]]:
        """Get latest N days of sentiment for multiple symbols.

        Returns:
            {symbol: [rows]} dict for building cross-sectional views.
        """
        conn = self._get_conn()
        result: Dict[str, List[Dict[str, Any]]] = {}

        for sym in symbols:
            query = "SELECT * FROM social_sentiment WHERE symbol = ?"
            params: list = [sym.upper()]
            if source:
                query += " AND source = ?"
                params.append(source)
            query += " ORDER BY date DESC LIMIT ?"
            params.append(days * 2)  # 2 sources per day
            rows = conn.execute(query, params).fetchall()
            if rows:
                result[sym] = [dict(r) for r in rows]

        return result

    def upsert_market_sentiment(self, rows: List[Dict[str, Any]]) -> int:
        """Replace market sentiment rows by (date, source)."""
        if not rows:
            return 0
        conn = self._get_conn()
        valid_cols = _get_table_columns("market_sentiment", conn)
        count = 0
        with conn:
            snapshots = {
                (row.get("date"), row.get("source"))
                for row in rows
                if row.get("date") and row.get("source")
            }
            for date, source in snapshots:
                conn.execute(
                    "DELETE FROM market_sentiment WHERE date = ? AND source = ?",
                    [date, source],
                )
            for row in rows:
                data = {k: v for k, v in row.items() if k in valid_cols}
                if not data.get("date") or not data.get("source"):
                    continue
                cols = [c for c in data if c in valid_cols]
                placeholders = ", ".join(["?"] * len(cols))
                col_names = ", ".join(cols)
                values = [data[c] for c in cols]
                conn.execute(
                    "INSERT INTO market_sentiment ({}) VALUES ({})".format(
                        col_names, placeholders),
                    values,
                )
                count += 1
        return count

    def get_market_sentiment(
        self,
        source: Optional[str] = None,
        limit: int = 30,
    ) -> List[Dict[str, Any]]:
        """Get market sentiment history, newest first."""
        conn = self._get_conn()
        query = "SELECT * FROM market_sentiment"
        params: list = []
        if source:
            query += " WHERE source = ?"
            params.append(source)
        query += " ORDER BY date DESC, source ASC LIMIT ?"
        params.append(limit)
        rows = conn.execute(query, params).fetchall()
        return [dict(r) for r in rows]

    def get_latest_market_sentiment(
        self,
        source: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """Get most recent market sentiment snapshot."""
        rows = self.get_market_sentiment(source=source, limit=1)
        return rows[0] if rows else None

    def upsert_social_trending(
        self,
        date: str,
        source: str,
        rows: List[Dict[str, Any]],
    ) -> int:
        """Replace all trending rows for a given UTC date + source."""
        conn = self._get_conn()
        valid_cols = _get_table_columns("social_trending", conn)
        count = 0

        with conn:
            conn.execute(
                "DELETE FROM social_trending WHERE date = ? AND source = ?",
                [date, source],
            )
            for row in rows:
                data = {k: v for k, v in row.items() if k in valid_cols}
                data["date"] = date
                data["source"] = source
                if not data.get("rank") or not data.get("ticker"):
                    continue
                cols = [c for c in data if c in valid_cols]
                placeholders = ", ".join(["?"] * len(cols))
                col_names = ", ".join(cols)
                values = [data[c] for c in cols]
                conn.execute(
                    "INSERT INTO social_trending ({}) VALUES ({})".format(
                        col_names, placeholders),
                    values,
                )
                count += 1

        return count

    def get_social_trending(
        self,
        date: str,
        source: str,
    ) -> List[Dict[str, Any]]:
        """Get trending rows for a UTC date + source ordered by rank."""
        conn = self._get_conn()
        rows = conn.execute(
            "SELECT * FROM social_trending WHERE date = ? AND source = ? ORDER BY rank ASC",
            [date, source],
        ).fetchall()
        return [dict(r) for r in rows]

    def upsert_social_trending_sectors(
        self,
        date: str,
        source: str,
        rows: List[Dict[str, Any]],
    ) -> int:
        """Replace all sector snapshot rows for a given UTC date + source."""
        conn = self._get_conn()
        valid_cols = _get_table_columns("social_trending_sectors", conn)
        count = 0

        with conn:
            conn.execute(
                "DELETE FROM social_trending_sectors WHERE date = ? AND source = ?",
                [date, source],
            )
            for row in rows:
                data = {k: v for k, v in row.items() if k in valid_cols}
                data["date"] = date
                data["source"] = source
                if not data.get("sector"):
                    continue
                cols = [c for c in data if c in valid_cols]
                placeholders = ", ".join(["?"] * len(cols))
                col_names = ", ".join(cols)
                values = [data[c] for c in cols]
                conn.execute(
                    "INSERT INTO social_trending_sectors ({}) VALUES ({})".format(
                        col_names, placeholders),
                    values,
                )
                count += 1

        return count

    def get_social_trending_sectors(
        self,
        date: str,
        source: str,
    ) -> List[Dict[str, Any]]:
        """Get sector snapshot rows for a UTC date + source."""
        conn = self._get_conn()
        rows = conn.execute(
            """SELECT * FROM social_trending_sectors
               WHERE date = ? AND source = ?
               ORDER BY buzz_score DESC, sector ASC""",
            [date, source],
        ).fetchall()
        return [dict(r) for r in rows]

    # ---- Screener ----

    def screen(
        self,
        filters: Dict[str, Any],
        table: str = "metrics_quarterly",
        latest_only: bool = True,
        order_by: Optional[str] = None,
        limit: int = 50,
    ) -> List[Dict[str, Any]]:
        """Screen stocks by multiple criteria.

        Args:
            filters: Dict of "column operator": value.
                     Supported operators: >, <, >=, <=, =, !=
                     Examples: {"net_margin >": 0.25, "roe >": 0.15}
            table: Table to screen against.
            latest_only: If True, only consider each symbol's most recent row.
            order_by: Column to sort by (descending). Must be valid column.
            limit: Max results.

        Returns:
            List of matching rows as dicts.
        """
        _validate_table(table)
        conn = self._get_conn()
        valid_cols = _get_table_columns(table, conn)

        # Parse filters
        where_clauses = []
        params: list = []
        op_pattern = re.compile(r"^(\w+)\s*(>=|<=|!=|>|<|=)$")

        for key, value in filters.items():
            m = op_pattern.match(key.strip())
            if not m:
                raise ValueError(f"Invalid filter key format: {key!r}. Use 'column op' e.g. 'net_margin >'")
            col, op = m.group(1), m.group(2)
            _validate_column(col, valid_cols)
            where_clauses.append(f"t.{col} {op} ?")
            params.append(value)

        if latest_only:
            cte = f"""
                WITH latest AS (
                    SELECT symbol, MAX(date) as max_date
                    FROM {table} GROUP BY symbol
                )
                SELECT t.* FROM {table} t
                JOIN latest l ON t.symbol = l.symbol AND t.date = l.max_date
            """
        else:
            cte = f"SELECT t.* FROM {table} t"

        if where_clauses:
            query = cte + "\nWHERE " + " AND ".join(where_clauses)
        else:
            query = cte

        if order_by:
            _validate_column(order_by, valid_cols)
            query += f"\nORDER BY t.{order_by} DESC"

        query += "\nLIMIT ?"
        params.append(limit)

        rows = conn.execute(query, params).fetchall()
        return [dict(r) for r in rows]

    def get_multi_quarter_screen(
        self,
        column: str,
        operator: str,
        value: float,
        min_quarters: int = 4,
        table: str = "metrics_quarterly",
    ) -> List[str]:
        """Find symbols where a condition holds for N consecutive recent quarters.

        Returns list of symbols that satisfy the condition for at least
        `min_quarters` of their most recent quarters.
        """
        _validate_table(table)
        conn = self._get_conn()
        valid_cols = _get_table_columns(table, conn)
        _validate_column(column, valid_cols)

        if operator not in (">", "<", ">=", "<=", "=", "!="):
            raise ValueError(f"Invalid operator: {operator!r}")

        # Get each symbol's most recent N quarters (including NULLs in the
        # window so that a NULL in a recent quarter disqualifies the symbol
        # rather than silently shifting the window to older data).
        query = f"""
            WITH ranked AS (
                SELECT symbol, {column},
                       ROW_NUMBER() OVER (PARTITION BY symbol ORDER BY date DESC) as rn
                FROM {table}
            ),
            recent AS (
                SELECT symbol, {column}
                FROM ranked
                WHERE rn <= ?
            )
            SELECT symbol
            FROM recent
            WHERE {column} {operator} ?
            GROUP BY symbol
            HAVING COUNT(*) >= ?
        """

        rows = conn.execute(query, [min_quarters, value, min_quarters]).fetchall()
        return [row["symbol"] for row in rows]

    # ---- IV Daily ----

    def save_iv_daily(
        self,
        symbol: str,
        date: str,
        iv_30d: Optional[float] = None,
        iv_60d: Optional[float] = None,
        hv_30d: Optional[float] = None,
        put_call_ratio: Optional[float] = None,
        total_volume: Optional[int] = None,
        total_oi: Optional[int] = None,
    ) -> None:
        """Save daily IV summary for a symbol (upsert on symbol+date)."""
        symbol = symbol.upper()
        now = datetime.now().isoformat()
        conn = self._get_conn()
        conn.execute(
            """
            INSERT INTO iv_daily
                (symbol, date, iv_30d, iv_60d, hv_30d,
                 put_call_ratio, total_volume, total_oi, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(symbol, date) DO UPDATE SET
                iv_30d = excluded.iv_30d,
                iv_60d = excluded.iv_60d,
                hv_30d = excluded.hv_30d,
                put_call_ratio = excluded.put_call_ratio,
                total_volume = excluded.total_volume,
                total_oi = excluded.total_oi,
                created_at = excluded.created_at
            """,
            (symbol, date, iv_30d, iv_60d, hv_30d,
             put_call_ratio, total_volume, total_oi, now),
        )
        conn.commit()

    def get_iv_history(
        self, symbol: str, limit: int = 252
    ) -> List[Dict[str, Any]]:
        """Get IV history for a symbol, newest first."""
        conn = self._get_conn()
        rows = conn.execute(
            "SELECT * FROM iv_daily WHERE symbol = ? ORDER BY date DESC LIMIT ?",
            (symbol.upper(), limit),
        ).fetchall()
        return [dict(r) for r in rows]

    def get_latest_iv(self, symbol: str) -> Optional[Dict[str, Any]]:
        """Get the most recent IV daily record for a symbol."""
        conn = self._get_conn()
        row = conn.execute(
            "SELECT * FROM iv_daily WHERE symbol = ? ORDER BY date DESC LIMIT 1",
            (symbol.upper(),),
        ).fetchone()
        return dict(row) if row else None

    # ---- Options Snapshots ----

    def save_options_snapshot(
        self,
        symbol: str,
        snapshot_date: str,
        contracts: List[Dict[str, Any]],
    ) -> int:
        """Save a batch of option contracts from a chain snapshot.

        Args:
            symbol: Underlying symbol
            snapshot_date: Date of the snapshot (YYYY-MM-DD)
            contracts: List of contract dicts with keys matching schema columns

        Returns:
            Number of contracts saved
        """
        symbol = symbol.upper()
        now = datetime.now().isoformat()
        conn = self._get_conn()

        # snapshot_date may be a datetime object from fetch_and_store_chain
        if hasattr(snapshot_date, "strftime"):
            snapshot_date = snapshot_date.strftime("%Y-%m-%d")

        count = 0
        with conn:
            for c in contracts:
                conn.execute(
                    """
                    INSERT INTO options_snapshots
                        (symbol, snapshot_date, expiration, strike, side,
                         bid, ask, mid, last, volume, open_interest, iv,
                         delta, gamma, theta, vega, dte, in_the_money,
                         underlying_price, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(symbol, snapshot_date, expiration, strike, side) DO UPDATE SET
                         bid = excluded.bid, ask = excluded.ask, mid = excluded.mid,
                         last = excluded.last, volume = excluded.volume,
                         open_interest = excluded.open_interest, iv = excluded.iv,
                         delta = excluded.delta, gamma = excluded.gamma,
                         theta = excluded.theta, vega = excluded.vega,
                         dte = excluded.dte, in_the_money = excluded.in_the_money,
                         underlying_price = excluded.underlying_price,
                         created_at = excluded.created_at
                    """,
                    (
                        symbol, snapshot_date,
                        c.get("expiration", ""),
                        c.get("strike", 0),
                        c.get("side", ""),
                        c.get("bid"),
                        c.get("ask"),
                        c.get("mid"),
                        c.get("last"),
                        c.get("volume"),
                        c.get("open_interest"),
                        c.get("iv"),
                        c.get("delta"),
                        c.get("gamma"),
                        c.get("theta"),
                        c.get("vega"),
                        c.get("dte"),
                        1 if c.get("in_the_money") else 0,
                        c.get("underlying_price"),
                        now,
                    ),
                )
                count += 1
        logger.info(
            "Saved %d option contracts for %s (%s)", count, symbol, snapshot_date
        )
        return count

    def get_options_snapshot(
        self,
        symbol: str,
        snapshot_date: Optional[str] = None,
        expiration: Optional[str] = None,
        side: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Get option contracts from a snapshot.

        Args:
            symbol: Underlying symbol
            snapshot_date: Filter by snapshot date; if None, uses latest
            expiration: Filter by expiration date
            side: Filter by 'call' or 'put'

        Returns:
            List of contract dicts
        """
        conn = self._get_conn()
        symbol = symbol.upper()

        if snapshot_date is None:
            row = conn.execute(
                "SELECT MAX(snapshot_date) as d FROM options_snapshots WHERE symbol = ?",
                (symbol,),
            ).fetchone()
            if not row or not row["d"]:
                return []
            snapshot_date = row["d"]

        query = "SELECT * FROM options_snapshots WHERE symbol = ? AND snapshot_date = ?"
        params: list = [symbol, snapshot_date]

        if expiration:
            query += " AND expiration = ?"
            params.append(expiration)
        if side:
            query += " AND side = ?"
            params.append(side)

        query += " ORDER BY expiration, strike, side"
        rows = conn.execute(query, params).fetchall()
        return [dict(r) for r in rows]

    def cleanup_old_snapshots(self, retain_days: int = 7) -> int:
        """Delete option snapshots older than retain_days.

        Returns:
            Number of rows deleted
        """
        conn = self._get_conn()
        cutoff_date = (datetime.now() - timedelta(days=retain_days)).strftime("%Y-%m-%d")

        cursor = conn.execute(
            "DELETE FROM options_snapshots WHERE snapshot_date < ?",
            (cutoff_date,),
        )
        conn.commit()
        deleted = cursor.rowcount
        if deleted > 0:
            logger.info("Cleaned up %d old option snapshot rows (before %s)", deleted, cutoff_date)
        return deleted

    # ---- Broad Market Scan ----

    def save_broad_scan_hits(self, rows: List[Dict]) -> int:
        """Save broad market RVOL scan hits (multi-symbol batch upsert).

        Args:
            rows: [{symbol, date, rvol, return_pct, market_cap, in_pool}, ...]

        Returns:
            Number of rows saved.
        """
        if not rows:
            return 0
        conn = self._get_conn()
        count = 0
        with conn:
            for row in rows:
                if not row.get("symbol") or not row.get("date"):
                    continue
                conn.execute(
                    """INSERT OR REPLACE INTO broad_scan_hits
                       (symbol, date, rvol, return_pct, market_cap, in_pool)
                       VALUES (?, ?, ?, ?, ?, ?)""",
                    (
                        row["symbol"].upper(),
                        row["date"],
                        row["rvol"],
                        row["return_pct"],
                        row.get("market_cap"),
                        1 if row.get("in_pool") else 0,
                    ),
                )
                count += 1
        logger.info("Saved %d broad scan hits", count)
        return count

    # ---- Symbol Discovery ----

    def get_symbols(self, table: str = "daily_price") -> List[str]:
        """Return sorted list of distinct symbols in a table."""
        _validate_table(table)
        conn = self._get_conn()
        rows = conn.execute(f"SELECT DISTINCT symbol FROM {table}").fetchall()
        return sorted(r[0] for r in rows)

    # ---- Historical market cap (universe reconstitution) ----

    def upsert_historical_market_cap(self, symbol: str, rows: List[Dict]) -> int:
        """写入历史市值数据。"""
        if not rows:
            return 0
        sql = """INSERT OR REPLACE INTO historical_market_cap
                 (symbol, date, market_cap) VALUES (?, ?, ?)"""
        data = [(r.get("symbol", symbol), r["date"], r["market_cap"]) for r in rows]
        conn = self._get_conn()
        conn.executemany(sql, data)
        conn.commit()
        return len(data)

    def replace_historical_market_cap_range(
        self,
        symbol: str,
        from_date: str,
        to_date: str,
        rows: List[Dict[str, Any]],
    ) -> int:
        """Atomically replace an authoritative vendor range.

        Unlike ordinary upsert, deleting the complete range first guarantees
        that a stale bad date cannot survive merely because the refresh
        response omitted that date. Validation happens before the transaction;
        an empty or malformed response preserves the prior range.
        """
        normalized_symbol = str(symbol).upper().strip()
        start = self._require_iso_date(from_date, "from_date")
        end = self._require_iso_date(to_date, "to_date")
        if not normalized_symbol or start > end or not rows:
            raise ValueError("non-empty authoritative market-cap range required")
        prepared = []
        seen_dates = set()
        for row in rows:
            row_symbol = str(row.get("symbol", normalized_symbol)).upper()
            row_date = self._require_iso_date(row.get("date"), "market-cap date")
            if (row_symbol != normalized_symbol or not start <= row_date <= end
                    or row_date in seen_dates):
                raise ValueError("market-cap row symbol/date outside range")
            seen_dates.add(row_date)
            try:
                market_cap = float(row["market_cap"])
            except (KeyError, TypeError, ValueError) as exc:
                raise ValueError("market_cap must be positive") from exc
            if market_cap <= 0:
                raise ValueError("market_cap must be positive")
            prepared.append((normalized_symbol, row_date, market_cap))

        conn = self._get_conn()
        with conn:
            conn.execute(
                "DELETE FROM historical_market_cap "
                "WHERE symbol = ? AND date BETWEEN ? AND ?",
                [normalized_symbol, start, end],
            )
            conn.executemany(
                "INSERT INTO historical_market_cap "
                "(symbol, date, market_cap) VALUES (?, ?, ?)",
                prepared,
            )
        return len(prepared)

    def get_historical_market_cap_range(
        self, symbol: str, from_date: str, to_date: str,
    ) -> List[Dict[str, Any]]:
        conn = self._get_conn()
        rows = conn.execute(
            "SELECT symbol, date, market_cap FROM historical_market_cap "
            "WHERE symbol = ? AND date BETWEEN ? AND ? ORDER BY date",
            [symbol.upper(), from_date, to_date],
        ).fetchall()
        return [dict(row) for row in rows]

    def get_market_cap_at(self, symbol: str, date: str) -> Optional[float]:
        """查询 symbol 在 date（或之前最近交易日）的市值。无数据返回 None。"""
        sql = """SELECT market_cap FROM historical_market_cap
                 WHERE symbol = ? AND date <= ?
                 ORDER BY date DESC LIMIT 1"""
        conn = self._get_conn()
        row = conn.execute(sql, (symbol, date)).fetchone()
        return row[0] if row else None

    def get_bulk_market_caps_at(self, date: str) -> Dict[str, float]:
        """查询所有 symbol 在 date（或之前最近日）的市值。"""
        sql = """SELECT symbol, market_cap FROM historical_market_cap
                 WHERE (symbol, date) IN (
                     SELECT symbol, MAX(date) FROM historical_market_cap
                     WHERE date <= ? GROUP BY symbol
                 )"""
        conn = self._get_conn()
        rows = conn.execute(sql, (date,)).fetchall()
        return {r[0]: r[1] for r in rows}

    def list_symbols_in_historical_market_cap(self) -> List[str]:
        """Return symbols that have any historical market cap rows."""
        conn = self._get_conn()
        rows = conn.execute(
            "SELECT DISTINCT symbol FROM historical_market_cap"
        ).fetchall()
        return sorted(row[0] for row in rows)

    def get_symbols_with_market_cap_at(
        self, date: str, threshold_usd: int, freshness_days: int = 90
    ) -> List[str]:
        """Return symbols whose latest as-of market cap is fresh and above threshold."""
        conn = self._get_conn()
        rows = conn.execute(
            """
            WITH latest AS (
                SELECT symbol, market_cap, date,
                       ROW_NUMBER() OVER (
                           PARTITION BY symbol ORDER BY date DESC
                       ) AS rn
                FROM historical_market_cap
                WHERE date <= ?
            )
            SELECT symbol
            FROM latest
            WHERE rn = 1
              AND date >= date(?, ?)
              AND market_cap >= ?
            """,
            (date, date, f"-{freshness_days} days", threshold_usd),
        ).fetchall()
        return sorted(row[0] for row in rows)

    # ---- Concept Registry ----

    @staticmethod
    def _utc_now_iso() -> str:
        return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    def upsert_concepts(self, rows: List[Dict[str, Any]]) -> int:
        """Upsert evergreen concept tree rows. Preserves created_at on update."""
        if not rows:
            return 0
        conn = self._get_conn()
        now = self._utc_now_iso()
        count = 0
        with conn:
            for row in rows:
                concept_id = row["concept_id"]
                existing = conn.execute(
                    "SELECT created_at FROM concepts WHERE concept_id = ?",
                    (concept_id,),
                ).fetchone()
                created_at = existing[0] if existing else now
                conn.execute(
                    """INSERT OR REPLACE INTO concepts
                    (concept_id, label, level, parent_id, concept_type, status,
                     created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        concept_id,
                        row["label"],
                        int(row["level"]),
                        row.get("parent_id"),
                        row.get("concept_type", "evergreen"),
                        row.get("status", "active"),
                        created_at,
                        now,
                    ),
                )
                count += 1
        return count

    def rebuild_concept_tree(self, rows: List[Dict[str, Any]]) -> int:
        """Atomic rebuild of the concepts tree (v2 migration).

        Order (must execute in one transaction):
            1. PRAGMA defer_foreign_keys=ON (defer FK checks until COMMIT so
               DELETE FROM concepts is safe even with self-FK hierarchy)
            2. UPDATE concept_themes SET parent_concept_id=NULL (cut FK refs;
               historical themes survive as orphan snapshots)
            3. DELETE FROM company_concept_tags (FK references concepts)
            4. DELETE FROM symbol_concept_edges (FK references concepts)
            5. DELETE FROM concepts (now safe under deferred FK)
            6. INSERT new concepts ordered by level (L1 before L2 to satisfy
               concepts.parent_id self-FK at commit time)
        """
        if not rows:
            return 0
        # Order by level so L1 INSERT precedes L2 parent_id ref
        ordered = sorted(rows, key=lambda r: int(r["level"]))
        conn = self._get_conn()
        now = self._utc_now_iso()
        with conn:
            conn.execute("PRAGMA defer_foreign_keys=ON")
            conn.execute("UPDATE concept_themes SET parent_concept_id = NULL")
            conn.execute("DELETE FROM company_concept_tags")
            conn.execute("DELETE FROM symbol_concept_edges")
            conn.execute("DELETE FROM concepts")
            count = 0
            for row in ordered:
                conn.execute(
                    """INSERT INTO concepts
                    (concept_id, label, level, parent_id, concept_type, status,
                     created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        row["concept_id"],
                        row["label"],
                        int(row["level"]),
                        row.get("parent_id"),
                        row.get("concept_type", "evergreen"),
                        row.get("status", "active"),
                        now,
                        now,
                    ),
                )
                count += 1
        return count

    def upsert_concept_themes(self, rows: List[Dict[str, Any]]) -> int:
        """Upsert dynamic theme rows. Preserves created_at on update.

        .. deprecated:: v2 (2026-05-13)
            ``concept_themes`` is no longer the write target for themes.
            v2 stores themes as ``concepts`` rows with ``level=3`` and
            references them via ``company_concept_tags.theme_ids``
            (same namespace, FK-validated). This method is retained only
            so the 5-row historical snapshot (hbm/liquid_cooling/...) and
            ``rebuild_concept_tree``'s FK cut-step continue to function.
            New code MUST use ``upsert_concepts(level=3)`` + ``theme_ids``.
        """
        import warnings
        warnings.warn(
            "upsert_concept_themes is deprecated in v2 — themes live in "
            "concepts table as level=3 and are referenced via "
            "company_concept_tags.theme_ids",
            DeprecationWarning,
            stacklevel=2,
        )
        if not rows:
            return 0
        conn = self._get_conn()
        now = self._utc_now_iso()
        count = 0
        with conn:
            for row in rows:
                theme_id = row["theme_id"]
                existing = conn.execute(
                    "SELECT created_at FROM concept_themes WHERE theme_id = ?",
                    (theme_id,),
                ).fetchone()
                created_at = existing[0] if existing else now
                conn.execute(
                    """INSERT OR REPLACE INTO concept_themes
                    (theme_id, label, parent_concept_id, lifecycle_state,
                     active_from, active_to, source, evidence, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        theme_id,
                        row["label"],
                        row.get("parent_concept_id"),
                        row.get("lifecycle_state", "watch"),
                        row.get("active_from"),
                        row.get("active_to"),
                        row.get("source", "manual"),
                        row.get("evidence", ""),
                        created_at,
                        now,
                    ),
                )
                count += 1
        return count

    def upsert_company_concepts(self, rows: List[Dict[str, Any]]) -> int:
        """Upsert per-symbol display tags. theme_ids list is JSON-encoded.

        v2 invariant (Task 8): every element of `theme_ids` MUST reference a
        ``concepts`` row with ``level=3``. concepts.theme_ids is JSON in a TEXT
        column, so SQLite cannot enforce a FK on its elements; this guard runs
        the level check in Python before any write. Pre-checking once for the
        whole batch lets the write loop stay short and the error message
        actionable (lists the offending ids in one place).
        """
        if not rows:
            return 0
        conn = self._get_conn()

        # v2 theme_ids invariant: every referenced concept_id must be level=3.
        # Empty list → skip the query; one query covers the whole batch.
        all_theme_ids: set[str] = set()
        for row in rows:
            for tid in row.get("theme_ids", []) or []:
                if tid:
                    all_theme_ids.add(str(tid))
        if all_theme_ids:
            placeholders = ",".join("?" * len(all_theme_ids))
            level_by_id = {
                r[0]: r[1] for r in conn.execute(
                    f"SELECT concept_id, level FROM concepts "
                    f"WHERE concept_id IN ({placeholders})",
                    list(all_theme_ids),
                )
            }
            bad = sorted(
                tid for tid in all_theme_ids
                if level_by_id.get(tid) != 3
            )
            if bad:
                raise ValueError(
                    f"theme_ids must reference level=3 concepts; offenders: {bad}"
                )

        now = self._utc_now_iso()
        count = 0
        with conn:
            for row in rows:
                theme_ids = row.get("theme_ids", [])
                if not isinstance(theme_ids, str):
                    theme_ids = json.dumps(list(theme_ids), ensure_ascii=False)
                conn.execute(
                    """INSERT OR REPLACE INTO company_concept_tags
                    (symbol, primary_concept_id, secondary_concept_id,
                     tertiary_concept_id, theme_ids, display_tags, business_role,
                     confidence, source, evidence, needs_review, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        str(row["symbol"]).upper(),
                        row["primary_concept_id"],
                        row.get("secondary_concept_id"),
                        row.get("tertiary_concept_id"),
                        theme_ids,
                        row.get("display_tags", ""),
                        row.get("business_role", ""),
                        float(row.get("confidence", 0)),
                        row.get("source", "unknown"),
                        row.get("evidence", ""),
                        int(row.get("needs_review", 0)),
                        now,
                    ),
                )
                count += 1
        return count

    def get_company_concepts(self, symbols: List[str]) -> Dict[str, Dict[str, Any]]:
        """Fetch tag rows by symbol; missing symbols are omitted."""
        if not symbols:
            return {}
        conn = self._get_conn()
        placeholders = ", ".join(["?"] * len(symbols))
        rows = conn.execute(
            f"SELECT * FROM company_concept_tags WHERE symbol IN ({placeholders})",
            [s.upper() for s in symbols],
        ).fetchall()
        out: Dict[str, Dict[str, Any]] = {}
        for row in rows:
            d = dict(row)
            try:
                d["theme_ids"] = json.loads(d.get("theme_ids") or "[]")
            except (TypeError, ValueError):
                d["theme_ids"] = []
            out[d["symbol"]] = d
        return out

    def get_company_concept_coverage(self) -> Dict[str, int]:
        """Aggregate row counts by source + needs_review for build summary."""
        conn = self._get_conn()
        total = conn.execute("SELECT COUNT(*) FROM company_concept_tags").fetchone()[0]
        needs = conn.execute(
            "SELECT COUNT(*) FROM company_concept_tags WHERE needs_review = 1"
        ).fetchone()[0]
        by_source = conn.execute(
            "SELECT source, COUNT(*) FROM company_concept_tags GROUP BY source"
        ).fetchall()
        src = {row[0]: row[1] for row in by_source}
        return {
            "total": int(total),
            "manual": int(src.get("manual", 0)),
            "rule": int(src.get("rule", 0)),
            "fallback": int(src.get("fallback", 0)),
            "legacy": int(src.get("legacy", 0)),
            "needs_review": int(needs),
        }

    # ---- Extended Primary Universe: Security Master (R1) ----

    _SM_REASONS = frozenset({
        "ok", "etf", "fund", "secondary_share_class", "identity_conflict",
        "needs_review_primary", "missing_profile", "non_common_instrument",
    })
    _SM_IDENTITY_BLOCKED_REASONS = frozenset({
        "etf", "fund", "secondary_share_class", "identity_conflict",
        "non_common_instrument",
    })

    def upsert_security_master(self, records: List[Dict]) -> int:
        """SM upsert：symbol 非空 + eligible∈{0,1} + reason 白名单，坏行整批拒绝

        （照抄 upsert_fmp_estimates 模式：事务前全量校验，事务内逐行写入）。
        """
        _validate_table("security_master")
        for r in records:
            if not r.get("symbol"):
                raise ValueError(f"security_master row missing symbol: {r!r}")
            if r.get("eligible") not in (0, 1):
                raise ValueError(f"invalid eligible (must be 0/1): {r.get('eligible')!r}")
            if r.get("reason") not in self._SM_REASONS:
                raise ValueError(f"invalid reason: {r.get('reason')!r}")
        conn = self._get_conn()
        with conn:
            for r in records:
                self._insert_validated(conn, "security_master",
                                       {**r, "symbol": r["symbol"].upper()})
        return len(records)

    def get_security_eligibility(self) -> Dict[str, bool]:
        """symbol -> eligible(bool)。表为空 fail-loud（P1-2：bootstrap 前不可静默返回 {}）。"""
        conn = self._get_conn()
        rows = conn.execute("SELECT symbol, eligible FROM security_master").fetchall()
        if not rows:
            raise RuntimeError("security_master empty — run bootstrap first")
        return {r["symbol"]: bool(r["eligible"]) for r in rows}

    def get_needs_review_symbols(self) -> List[str]:
        """reason == 'needs_review_primary' 的 symbol 列表（share-class 主类未定）。"""
        conn = self._get_conn()
        rows = conn.execute(
            "SELECT symbol FROM security_master WHERE reason = 'needs_review_primary' "
            "ORDER BY symbol"
        ).fetchall()
        return [r["symbol"] for r in rows]

    # ---- Extended Primary Universe: Membership (SCD-2, R4-P1-1) ----

    _MEMBERSHIP_UPDATE_BATCH = 500

    def record_membership_snapshot(self, symbols: List[str], as_of: str) -> Dict[str, List[str]]:
        """SCD-2 membership 快照；幂等。

        exited 在 Python 侧求差（先读 active set，与本次名单求差），再按 <=500
        参数分批 UPDATE —— 禁止生成千参 NOT IN(...)（R2-P2-1）。
        """
        new_set = {s.upper() for s in symbols}
        with self.transaction() as conn:
            active_rows = conn.execute(
                "SELECT symbol FROM extended_membership WHERE effective_to IS NULL"
            ).fetchall()
            active_set = {r["symbol"] for r in active_rows}

            entered = sorted(new_set - active_set)
            exited = sorted(active_set - new_set)

            if entered:
                entered_rows = [
                    {"symbol": s, "effective_from": as_of, "effective_to": None,
                     "reason": "screener"}
                    for s in entered
                ]
                self._upsert_rows_in_conn(conn, "extended_membership", entered_rows)

            chunk_size = self._MEMBERSHIP_UPDATE_BATCH - 1  # 留 1 位给 as_of 参数
            for i in range(0, len(exited), chunk_size):
                chunk = exited[i:i + chunk_size]
                placeholders = ", ".join(["?"] * len(chunk))
                conn.execute(
                    f"UPDATE extended_membership SET effective_to = ? "
                    f"WHERE effective_to IS NULL AND symbol IN ({placeholders})",
                    [as_of, *chunk],
                )

        return {"entered": entered, "exited": exited}

    def get_active_members(self) -> List[str]:
        """当前在册（effective_to IS NULL），去重排序。T7 resolver base='extended' 默认 loader。"""
        conn = self._get_conn()
        rows = conn.execute(
            "SELECT DISTINCT symbol FROM extended_membership WHERE effective_to IS NULL "
            "ORDER BY symbol"
        ).fetchall()
        return [r["symbol"] for r in rows]

    def get_members_as_of(self, as_of: str) -> List[str]:
        """严格接口：as_of 早于首条 membership 记录 -> ValueError（不静默降级）。"""
        conn = self._get_conn()
        earliest_row = conn.execute(
            "SELECT MIN(effective_from) AS d FROM extended_membership"
        ).fetchone()
        earliest = earliest_row["d"] if earliest_row else None
        if earliest is None or as_of < earliest:
            raise ValueError(
                f"as_of={as_of!r} is earlier than the first membership record "
                f"(earliest={earliest!r})"
            )
        rows = conn.execute(
            "SELECT symbol FROM extended_membership "
            "WHERE effective_from <= ? AND (effective_to IS NULL OR effective_to > ?) "
            "ORDER BY symbol",
            (as_of, as_of),
        ).fetchall()
        return [r["symbol"] for r in rows]

    def approximate_members_as_of(self, as_of: str,
                                   min_mcap_usd: float = 1e10) -> Dict[str, Any]:
        """近似接口（P1-6 + R2-P1-1）：historical_market_cap 中 as-of 达标全体。

        不与当前 Extended membership 求交（已跌出池/已退市者必须保留）；仅剔除
        security_master 中身份封禁者；SM 无记录的达标 symbol 保留并单列进
        `unverified`。`approximate` 标志硬编码在返回结构里，调用方无法丢弃。
        """
        conn = self._get_conn()
        qualifying_rows = conn.execute(
            """
            SELECT symbol FROM (
                SELECT symbol, market_cap,
                       ROW_NUMBER() OVER (
                           PARTITION BY symbol ORDER BY date DESC
                       ) AS rn
                FROM historical_market_cap
                WHERE date <= ?
            )
            WHERE rn = 1 AND market_cap >= ?
            """,
            (as_of, min_mcap_usd),
        ).fetchall()
        qualifying = {r["symbol"] for r in qualifying_rows}

        sm_rows = conn.execute("SELECT symbol, reason FROM security_master").fetchall()
        sm_reason = {r["symbol"]: r["reason"] for r in sm_rows}

        symbols_out: List[str] = []
        unverified: List[str] = []
        for sym in qualifying:
            reason = sm_reason.get(sym)
            if reason is None:
                symbols_out.append(sym)
                unverified.append(sym)
            elif reason in self._SM_IDENTITY_BLOCKED_REASONS:
                continue
            else:
                symbols_out.append(sym)

        return {
            "symbols": sorted(symbols_out),
            "unverified": sorted(unverified),
            "approximate": True,
            "as_of": as_of,
            "basis": "historical_market_cap",
        }

    # ---- Extended Primary Universe: Coverage status + retry bookkeeping (R2-P1-3) ----

    _COVERAGE_STATUSES = frozenset({
        "ok", "not_applicable", "provider_empty", "fetch_failed", "stale",
        "identity_blocked",
    })
    _PROVIDER_EMPTY_TTL_DAYS = _DEFAULT_PROVIDER_EMPTY_TTL_DAYS
    _FETCH_FAILED_MAX_BACKOFF_DAYS = 16

    def _upsert_coverage_status_in_conn(self, conn: sqlite3.Connection,
                                        rows: List[Dict]) -> int:
        """六态白名单 + 重试字段维护（见 T1 DDL 注释），conn 级、不开自己的事务
        —— 由调用方持有事务边界，供 T8 内核与 current 表/vintage/manifest 写入
        合并进同一次原子提交。独立调用请用 `upsert_coverage_status`。

        fetch_failed -> next_retry_at = now + min(2^consecutive_failures, 16) 天；
        provider_empty -> 负缓存 TTL，next_retry_at = now + 30 天；
        ok -> consecutive_failures 清零、记 last_success_at、next_retry_at = NULL；
        not_applicable / stale -> 纯状态标注，**必须保留**既有 next_retry_at（不驱动
        重试计时器——一次 stale/not_applicable 写入不能悄悄清掉一个待到期的 backoff）；
        identity_blocked -> **显式清空** next_retry_at（终态：不自动重试，等人工 override，
        per plan T12）。坏 status 整批拒绝（任何写入之前全量校验）。
        """
        for row in rows:
            if row.get("status") not in self._COVERAGE_STATUSES:
                raise ValueError(f"invalid coverage status: {row.get('status')!r}")
        now_dt = datetime.now(timezone.utc)
        now_iso = now_dt.strftime("%Y-%m-%dT%H:%M:%SZ")
        for row in rows:
            sym = row["symbol"].upper()
            dataset = row["dataset"]
            status = row["status"]
            existing = conn.execute(
                "SELECT consecutive_failures, last_success_at, next_retry_at "
                "FROM coverage_status WHERE symbol = ? AND dataset = ?",
                (sym, dataset),
            ).fetchone()
            prior_failures = existing["consecutive_failures"] if existing else 0
            last_success_at = existing["last_success_at"] if existing else None
            consecutive_failures = prior_failures
            # Default: preserve whatever retry timer already existed. Only
            # fetch_failed/provider_empty/ok/identity_blocked below are allowed
            # to change it — not_applicable/stale fall through untouched.
            next_retry_at = existing["next_retry_at"] if existing else None

            if status == "fetch_failed":
                consecutive_failures = prior_failures + 1
                delay_days = min(2 ** consecutive_failures,
                                 self._FETCH_FAILED_MAX_BACKOFF_DAYS)
                next_retry_at = (now_dt + timedelta(days=delay_days)).strftime(
                    "%Y-%m-%dT%H:%M:%SZ")
            elif status == "provider_empty":
                next_retry_at = (now_dt + timedelta(
                    days=self._PROVIDER_EMPTY_TTL_DAYS)).strftime("%Y-%m-%dT%H:%M:%SZ")
            elif status == "ok":
                consecutive_failures = 0
                last_success_at = now_iso
                next_retry_at = None
            elif status == "identity_blocked":
                next_retry_at = None

            self._insert_validated(conn, "coverage_status", {
                "symbol": sym,
                "dataset": dataset,
                "status": status,
                "detail": row.get("detail"),
                "updated_at": row.get("updated_at") or now_iso,
                "last_attempt_at": now_iso,
                "last_success_at": last_success_at,
                "consecutive_failures": consecutive_failures,
                "next_retry_at": next_retry_at,
            })
        return len(rows)

    def upsert_coverage_status(self, rows: List[Dict]) -> int:
        """独立调用入口：自开事务包住 `_upsert_coverage_status_in_conn`（语义见该方法）。"""
        conn = self._get_conn()
        with conn:
            return self._upsert_coverage_status_in_conn(conn, rows)

    def get_coverage(self, dataset: str) -> Dict[str, str]:
        """dataset 下全部 symbol -> status 映射。"""
        conn = self._get_conn()
        rows = conn.execute(
            "SELECT symbol, status FROM coverage_status WHERE dataset = ?",
            (dataset,),
        ).fetchall()
        return {r["symbol"]: r["status"] for r in rows}

    # ---- Extended Primary Universe: Fundamental Vintage (R7, R8) ----
    #
    # Two deliberately incompatible read interfaces (P1-6 rejects a single
    # get_fundamentals_as_of(anchor=...) that silently mixes cognition-timeline
    # replay with restated-current lookups):
    #   - known_as_of: strict PIT replay over `fundamental_vintage` itself.
    #     No hits -> [] (no fallback to current tables).
    #   - approximate_as_reported: pre-golive substitute reading CURRENT
    #     (latest-restated) tables, explicitly tagged approximate=True.

    _VINTAGE_QUALITIES = frozenset({"latest_known", "as_reported", "revised"})

    _VINTAGE_STATEMENT_TABLES = {
        "income": "income_quarterly",
        "balance": "balance_sheet_quarterly",
        "cashflow": "cash_flow_quarterly",
    }

    def record_vintage_in_conn(self, conn: sqlite3.Connection, symbol: str,
                                statement: str, rows: List[Dict],
                                observed_at: str, quality: str) -> int:
        """Change-only append into `fundamental_vintage` (conn-level, no own
        transaction — caller owns the boundary; composed by T8 alongside other
        `_in_conn` writes in a single atomic commit). Use `record_vintage` for
        a standalone call.

        content_hash = sha256(json.dumps(row, sort_keys=True, separators=(",", ":"))).
        If it matches the latest existing version for that fiscal_date, the
        row is skipped (not re-appended); only newly-inserted rows count
        toward the returned total.

        observed_at MUST be a full UTC timestamp (R3-m2: write side is
        strict) — a bare date raises ValueError. This keeps `observed_at`
        lexicographically comparable across same-day revisions (R2-P2-2: two
        revisions on the same calendar day get distinct timestamps, so they
        don't collide on the (symbol, statement, fiscal_date, observed_at) PK).

        Two rows in the SAME call that share a fiscal_date would collide on
        that PK (symbol/statement/observed_at are constant for the whole
        batch). If their content DIFFERS, that's rejected up front with
        ValueError, whole batch atomically, before any write happens
        (fix-round-1 Finding 1: a dirty upstream response must surface as an
        error, not be silently resolved by letting the later row clobber the
        earlier one). If their content is BYTE-IDENTICAL (idempotent
        upstream retry / paginated-response overlap), that's not an error —
        it falls through to the same change-only hash-skip as a repeat
        `record_vintage` call and is written (or skipped, if already latest)
        once (fix-round-2 Finding 1).
        """
        if quality not in self._VINTAGE_QUALITIES:
            raise ValueError(f"invalid vintage quality: {quality!r}")
        if _is_pure_date(observed_at):
            raise ValueError(
                f"observed_at must be a full UTC timestamp, not a pure date: "
                f"{observed_at!r}"
            )
        _validate_table("fundamental_vintage")
        sym = symbol.upper()

        # Pre-write validation pass: compute each row's fiscal_date + content
        # hash once (reused in the write loop below, not re-hashed) and
        # reject the whole batch atomically if two rows share a fiscal_date
        # with DIFFERING content — that would collide on the (symbol,
        # statement, fiscal_date, observed_at) PK with no well-defined
        # winner. Byte-identical duplicates are left alone here; they're
        # deduped in the write loop via the same hash-skip logic used for
        # cross-call repeats. Must run before any INSERT below.
        prepared = []  # (row, fiscal_date, content_hash, payload_json)
        hash_by_fiscal_date: Dict[str, str] = {}
        for row in rows:
            fiscal_date = row.get("date")
            if not fiscal_date:
                raise ValueError(f"fundamental_vintage row missing fiscal date: {row!r}")
            payload_json = json.dumps(row, sort_keys=True, separators=(",", ":"))
            content_hash = hashlib.sha256(payload_json.encode("utf-8")).hexdigest()
            prior_hash = hash_by_fiscal_date.get(fiscal_date)
            if prior_hash is not None and prior_hash != content_hash:
                raise ValueError(
                    f"duplicate fiscal_date with differing content in same "
                    f"vintage batch: symbol={sym} statement={statement!r} "
                    f"fiscal_date={fiscal_date!r} "
                    f"(would collide on (symbol, statement, fiscal_date, observed_at) PK)"
                )
            hash_by_fiscal_date[fiscal_date] = content_hash
            prepared.append((row, fiscal_date, content_hash, payload_json))

        count = 0
        written_fiscal_dates = set()
        for row, fiscal_date, content_hash, payload_json in prepared:
            if fiscal_date in written_fiscal_dates:
                continue  # byte-identical duplicate within this batch, already handled
            written_fiscal_dates.add(fiscal_date)

            latest = conn.execute(
                "SELECT content_hash FROM fundamental_vintage "
                "WHERE symbol = ? AND statement = ? AND fiscal_date = ? "
                "ORDER BY observed_at DESC LIMIT 1",
                (sym, statement, fiscal_date),
            ).fetchone()
            if latest is not None and latest["content_hash"] == content_hash:
                continue  # change-only append: identical to latest version

            # Plain INSERT (not OR REPLACE): any unexpected PK collision past
            # the batch-level check above raises sqlite3.IntegrityError
            # instead of silently clobbering append-only history.
            conn.execute(
                "INSERT INTO fundamental_vintage "
                "(symbol, statement, fiscal_date, observed_at, filing_date, "
                "accepted_date, content_hash, vintage_quality, payload) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (sym, statement, fiscal_date, observed_at,
                 row.get("filingDate") or row.get("filing_date"),
                 row.get("acceptedDate") or row.get("accepted_date"),
                 content_hash, quality, payload_json),
            )
            count += 1
        return count

    def record_vintage(self, symbol: str, statement: str, rows: List[Dict],
                       observed_at: str, quality: str) -> int:
        """Standalone convenience wrapper: opens its own transaction around
        `record_vintage_in_conn`."""
        with self.transaction() as conn:
            return self.record_vintage_in_conn(conn, symbol, statement, rows,
                                               observed_at, quality)

    # ---- Extended Primary Universe: per-dataset composite write (T8) ----

    def write_company_profile_in_conn(self, conn: sqlite3.Connection, symbol: str,
                                      profile: Dict[str, Any],
                                      updated_at: str) -> int:
        """Upsert one symbol's raw profile payload into `company_profile`
        (conn-level, no own transaction — caller owns the boundary).

        `company_profile` is keyed by symbol alone and stores the provider
        payload as one JSON blob, so it cannot go through
        `_prepare_upsert_rows` (no `date` column). Serialization matches
        `scripts/bootstrap_security_master.py:_write_company_profiles`
        (`json.dumps(payload, default=str)`) so the identity path and the
        collection kernel leave byte-identical blobs in the same table.
        """
        if not isinstance(profile, dict):
            raise ValueError(
                f"company profile payload must be a dict, got {type(profile).__name__}"
            )
        return self._upsert_rows_in_conn(conn, "company_profile", [{
            "symbol": symbol.upper(),
            "payload": json.dumps(profile, default=str),
            "updated_at": updated_at,
        }])

    def write_symbol_dataset_in_conn(self, conn: sqlite3.Connection, symbol: str,
                                     dataset: str, rows: List[Dict], *,
                                     observed_at: Optional[str] = None,
                                     quality: str = "latest_known",
                                     updated_at: Optional[str] = None
                                     ) -> Dict[str, int]:
        """Write one dataset's payload — current table plus vintage where the
        dataset has one — on the caller's connection.

        This is the write half of the T8 atomic boundary (P1-4): the kernel
        wraps this call, the coverage upsert and the manifest job write in a
        SINGLE `transaction()`, so a symbol's dataset is either fully durable
        or fully absent. Nothing here opens a transaction of its own.

        - `income`/`balance`/`cashflow`: prepared rows into the current table
          (see `_prepare_upsert_rows`) + the RAW provider rows appended to
          `fundamental_vintage` under the same dataset key as statement name.
          Raw rows on purpose: the vintage payload is the as-reported
          snapshot, keeping vendor fields the current-table column whitelist
          drops, and `record_vintage_in_conn` reads `filingDate`/`acceptedDate`
          off that camelCase shape.
        - `ratios`: current table only (annual ratios are derived, not filed).
        - `profile`: single JSON blob into `company_profile` (SSOT). The
          `profiles.json` mirror is NOT written here (R2-P2-3) — see
          `fundamental_collector.rebuild_profiles_json`.

        Args:
            observed_at: full UTC timestamp; REQUIRED for vintage datasets
                (`record_vintage_in_conn` rejects a bare date).
            quality: vintage quality tag (`latest_known` for routine
                collection; historical/deep pulls stay `latest_known` too).
            updated_at: `company_profile.updated_at`; defaults to `observed_at`.

        Returns:
            {"rows": current-table rows written, "vintage_rows": vintage rows appended}
        """
        table = COLLECTION_DATASET_TABLES.get(dataset)
        if table is None:
            raise ValueError(
                f"unknown collection dataset: {dataset!r}; expected one of "
                f"{sorted(COLLECTION_DATASET_TABLES)}"
            )
        _validate_table(table)

        if dataset == "profile":
            stamp = updated_at or observed_at
            if not stamp:
                raise ValueError("company_profile write needs observed_at or updated_at")
            payload = rows[0] if rows else None
            written = self.write_company_profile_in_conn(conn, symbol, payload, stamp)
            return {"rows": written, "vintage_rows": 0}

        prepared = self._prepare_upsert_rows(table, symbol, rows)
        written = self._write_current_rows_in_conn(conn, table, prepared)

        vintage_rows = 0
        if dataset in VINTAGE_DATASETS:
            if not observed_at:
                raise ValueError(f"vintage dataset {dataset!r} needs observed_at")
            vintage_rows = self.record_vintage_in_conn(conn, symbol, dataset, rows,
                                                       observed_at, quality)
        return {"rows": written, "vintage_rows": vintage_rows}

    def known_as_of(self, symbol: str, statement: str, observed_at: str) -> List[Dict]:
        """Strict cognition-timeline replay: per fiscal_date, the latest
        version with `observed_at <= observed_at` param. No fallback — if
        nothing qualifies (as-of predates the first recorded vintage), returns
        [] rather than silently reaching into current/restated tables.

        A pure-date param uses an EXCLUSIVE next-day bound —
        `observed_at < <date+1>T00:00:00Z` — for "as of end of that day"
        semantics (R3-m2). A full timestamp keeps the original INCLUSIVE
        `observed_at <= observed_at` comparison, used as-is.

        (Fix-round-1 Finding 2: an inclusive compare against the literal
        string `<date>T23:59:59.999999Z` looks equivalent but isn't — SQLite
        compares TEXT lexicographically, and a vintage stored as exactly
        `<date>T23:59:59Z` (no fractional seconds) sorts AFTER that bound
        because "Z" (0x5A) > "." (0x2E) at the first differing byte, so it
        was silently excluded. The exclusive next-day bound sidesteps the
        byte-comparison trap entirely.)

        Each returned row is annotated with `_vintage_quality` and
        `_observed_at`.
        """
        _validate_table("fundamental_vintage")
        if _is_pure_date(observed_at):
            next_day = (datetime.strptime(observed_at, "%Y-%m-%d").date()
                        + timedelta(days=1))
            operator, bound = "<", f"{next_day.isoformat()}T00:00:00Z"
        else:
            operator, bound = "<=", observed_at
        sym = symbol.upper()
        conn = self._get_conn()
        rows = conn.execute(
            f"""
            SELECT fiscal_date, payload, vintage_quality, observed_at FROM (
                SELECT fiscal_date, payload, vintage_quality, observed_at,
                       ROW_NUMBER() OVER (
                           PARTITION BY fiscal_date ORDER BY observed_at DESC
                       ) AS rn
                FROM fundamental_vintage
                WHERE symbol = ? AND statement = ? AND observed_at {operator} ?
            )
            WHERE rn = 1
            ORDER BY fiscal_date
            """,
            (sym, statement, bound),
        ).fetchall()
        out = []
        for r in rows:
            payload = json.loads(r["payload"])
            payload["_vintage_quality"] = r["vintage_quality"]
            payload["_observed_at"] = r["observed_at"]
            out.append(payload)
        return out

    def approximate_as_reported(self, symbol: str, statement: str, as_of: str) -> Dict[str, Any]:
        """Pre-golive approximate read: queries the CURRENT (latest-restated)
        table for `statement`, filtered by the first available public filing
        timestamp (`accepted_date`, falling back to `filing_date`) <= as_of. Unlike
        `known_as_of`, this reflects whatever restatements have since landed
        in the current tables — it is a substitute for periods before vintage
        recording went live, not a PIT replay.

        The `approximate` flag lives in the return structure (not just a
        docstring) so callers cannot silently drop it (P1-6).
        """
        table = self._VINTAGE_STATEMENT_TABLES.get(statement)
        if table is None:
            raise ValueError(f"unknown statement: {statement!r}")
        _validate_table(table)
        conn = self._get_conn()
        rows = conn.execute(
            f"SELECT * FROM {table} WHERE symbol = ? AND "
            f"COALESCE(NULLIF(substr(accepted_date, 1, 10), ''), "
            f"NULLIF(filing_date, '')) <= ? "
            f"ORDER BY date",
            (symbol.upper(), as_of),
        ).fetchall()
        return {
            "rows": [dict(r) for r in rows],
            "approximate": True,
            "basis": "current_tables_restated",
        }

    # ---- Fundamental backfill manifest (run header + dataset jobs, T9/R6) ----
    # (run_id, symbol, dataset)-granular job ledger the T10 runner drives.
    # `create_backfill_run` freezes the grid once per run_id: the header's
    # `universe_hash` is immutable (same pattern as `upsert_fmp_forward_run`'s
    # frozen `target_universe_json` — history must not be rewritable), while
    # a matching re-create is treated as a resume no-op rather than an error
    # so `--resume` can safely call this every time without disturbing job
    # progress already recorded.

    _BACKFILL_JOB_TERMINAL_STATUSES = frozenset({"done", "provider_empty", "skipped"})
    _BACKFILL_JOB_STATUSES = frozenset({
        "pending", "in_progress", "done", "provider_empty", "fetch_failed", "skipped",
    })
    _BACKFILL_RUN_STATUSES = frozenset({"complete", "aborted", "rolled_back"})
    _BACKFILL_MAX_ATTEMPTS = 3

    def create_backfill_run(self, run_id: str, symbols: List[str],
                            datasets: List[str], params: Dict) -> None:
        """Freeze the full (run_id, symbol, dataset) grid as pending.

        `universe_hash` is sha256 over the sorted, upper-cased, deduplicated
        symbol list — the run's frozen denominator. A second call with the
        same run_id and the SAME hash is a no-op (the `--resume` path); a
        different hash raises, mirroring `upsert_fmp_forward_run`'s
        immutable-history semantics (:1282-1348).
        """
        if not symbols:
            raise ValueError("symbols must not be empty")
        if not datasets:
            raise ValueError("datasets must not be empty")

        normalized_symbols = sorted({str(s).upper() for s in symbols})
        universe_hash = hashlib.sha256(
            ",".join(normalized_symbols).encode("utf-8")
        ).hexdigest()

        conn = self._get_conn()
        now = self._utc_now_iso()
        with conn:
            existing = conn.execute(
                "SELECT universe_hash FROM fundamental_backfill_runs WHERE run_id = ?",
                [run_id],
            ).fetchone()
            if existing:
                if existing["universe_hash"] != universe_hash:
                    raise ValueError(
                        f"universe mismatch for existing backfill run {run_id!r}; "
                        "run manifests are immutable once created"
                    )
                return  # idempotent resume: grid already frozen, jobs untouched

            conn.execute(
                "INSERT INTO fundamental_backfill_runs "
                "(run_id, universe_hash, params_json, status, started_at, finished_at) "
                "VALUES (?, ?, ?, 'running', ?, NULL)",
                [run_id, universe_hash, json.dumps(params or {}), now],
            )
            job_rows = [
                (run_id, symbol, dataset)
                for symbol in normalized_symbols
                for dataset in datasets
            ]
            conn.executemany(
                "INSERT INTO fundamental_backfill_jobs (run_id, symbol, dataset, status) "
                "VALUES (?, ?, ?, 'pending')",
                job_rows,
            )

    def claim_pending_jobs(self, run_id: str, symbol: str) -> List[str]:
        """Claim this symbol's non-terminal dataset jobs: fresh `pending`, or
        `fetch_failed` under the retry cap. Marks each claimed row
        `in_progress` with `claimed_at` set so a second claim — or a crashed
        runner restarted before it calls `reset_in_progress_jobs` — cannot
        double-work the same job. Returns the claimed dataset keys.
        """
        sym = symbol.upper()
        conn = self._get_conn()
        now = self._utc_now_iso()
        with conn:
            rows = conn.execute(
                "SELECT dataset FROM fundamental_backfill_jobs "
                "WHERE run_id = ? AND symbol = ? AND "
                "(status = 'pending' OR (status = 'fetch_failed' AND attempts < ?)) "
                "ORDER BY rowid",
                [run_id, sym, self._BACKFILL_MAX_ATTEMPTS],
            ).fetchall()
            datasets = [r["dataset"] for r in rows]
            if datasets:
                conn.executemany(
                    "UPDATE fundamental_backfill_jobs SET status = 'in_progress', "
                    "claimed_at = ? WHERE run_id = ? AND symbol = ? AND dataset = ?",
                    [(now, run_id, sym, d) for d in datasets],
                )
        return datasets

    def reset_in_progress_jobs(self, run_id: str) -> int:
        """Crash-recovery: reset every `in_progress` job of this run back to
        `pending` (attempts untouched) so a restarted runner can reclaim it.
        Never leave a job permanently `in_progress` after a crash. Returns
        the number of rows reset.
        """
        conn = self._get_conn()
        with conn:
            cur = conn.execute(
                "UPDATE fundamental_backfill_jobs SET status = 'pending', "
                "claimed_at = NULL WHERE run_id = ? AND status = 'in_progress'",
                [run_id],
            )
            return cur.rowcount

    def complete_job_in_conn(self, conn: sqlite3.Connection, run_id: str,
                             symbol: str, dataset: str, status: str,
                             error: Optional[str] = None) -> None:
        """Record one dataset job's outcome on the caller's connection — the
        manifest half of T8's per-dataset atomic boundary. T10 binds this as
        the `job_writer(conn, dataset, status, detail=None)` closure the
        collection kernel calls INSIDE its own transaction, so the manifest
        commits or rolls back with the data it describes.

        Increments `attempts` on every call. Sets `completed_at` once the job
        reaches a terminal state: `{done, provider_empty, skipped}` always;
        `fetch_failed` only once `attempts` has exhausted the retry cap
        (below the cap it stays claimable via `claim_pending_jobs`).
        """
        if status not in self._BACKFILL_JOB_STATUSES:
            raise ValueError(f"invalid backfill job status: {status!r}")
        sym = symbol.upper()
        row = conn.execute(
            "SELECT attempts FROM fundamental_backfill_jobs "
            "WHERE run_id = ? AND symbol = ? AND dataset = ?",
            [run_id, sym, dataset],
        ).fetchone()
        if row is None:
            raise ValueError(
                f"no manifest job for run_id={run_id!r} symbol={sym!r} "
                f"dataset={dataset!r}; was create_backfill_run called for this grid?"
            )
        attempts = row["attempts"] + 1
        is_terminal = (status in self._BACKFILL_JOB_TERMINAL_STATUSES
                       or (status == "fetch_failed"
                           and attempts >= self._BACKFILL_MAX_ATTEMPTS))
        completed_at = self._utc_now_iso() if is_terminal else None
        conn.execute(
            "UPDATE fundamental_backfill_jobs SET status = ?, attempts = ?, "
            "last_error = ?, completed_at = ? "
            "WHERE run_id = ? AND symbol = ? AND dataset = ?",
            [status, attempts, error, completed_at, run_id, sym, dataset],
        )

    def run_progress(self, run_id: str) -> Dict[str, Any]:
        """Per-status job counts for a run, plus `total_symbols`,
        `total_jobs` and `is_complete` (every job terminal — `fetch_failed`
        counts once it has exhausted the retry cap).

        CONTROLLER RULING #2: raises ValueError on an unknown run_id rather
        than returning an empty dict, so a caller that expects the manifest
        to exist (e.g. T10's lock-busy path, which must NOT have created one)
        gets a loud signal instead of a silently-zeroed progress report.
        """
        conn = self._get_conn()
        header = conn.execute(
            "SELECT run_id FROM fundamental_backfill_runs WHERE run_id = ?",
            [run_id],
        ).fetchone()
        if header is None:
            raise ValueError(f"unknown backfill run_id: {run_id!r}")

        rows = conn.execute(
            "SELECT symbol, status, attempts FROM fundamental_backfill_jobs "
            "WHERE run_id = ?",
            [run_id],
        ).fetchall()

        counts = {s: 0 for s in self._BACKFILL_JOB_STATUSES}
        symbols = set()
        is_complete = True
        for r in rows:
            counts[r["status"]] = counts.get(r["status"], 0) + 1
            symbols.add(r["symbol"])
            terminal = (r["status"] in self._BACKFILL_JOB_TERMINAL_STATUSES
                        or (r["status"] == "fetch_failed"
                            and r["attempts"] >= self._BACKFILL_MAX_ATTEMPTS))
            if not terminal:
                is_complete = False

        result: Dict[str, Any] = dict(counts)
        result["total_symbols"] = len(symbols)
        result["total_jobs"] = len(rows)
        result["is_complete"] = is_complete
        return result

    def finish_run(self, run_id: str, status: str) -> None:
        """Close out a run header: status ∈ {complete, aborted, rolled_back},
        `finished_at` stamped now. Raises ValueError for an invalid status or
        an unknown run_id (nothing to finish).
        """
        if status not in self._BACKFILL_RUN_STATUSES:
            raise ValueError(f"invalid backfill run status: {status!r}")
        conn = self._get_conn()
        now = self._utc_now_iso()
        with conn:
            cur = conn.execute(
                "UPDATE fundamental_backfill_runs SET status = ?, finished_at = ? "
                "WHERE run_id = ?",
                [status, now, run_id],
            )
            if cur.rowcount == 0:
                raise ValueError(f"unknown backfill run_id: {run_id!r}")

    def get_backfill_run(self, run_id: str) -> Dict[str, Any]:
        """Header row: run_id/universe_hash/status/started_at/finished_at
        (plus `params`, decoded from `params_json`). Raises ValueError on an
        unknown run_id (CONTROLLER RULING #2 — see `run_progress`).
        """
        conn = self._get_conn()
        row = conn.execute(
            "SELECT * FROM fundamental_backfill_runs WHERE run_id = ?",
            [run_id],
        ).fetchone()
        if row is None:
            raise ValueError(f"unknown backfill run_id: {run_id!r}")
        result = dict(row)
        result["params"] = json.loads(result["params_json"])
        return result

    # ---- Stats ----

    def get_stats(self) -> Dict[str, int]:
        """Get row counts for all tables."""
        conn = self._get_conn()
        stats = {}
        for table in sorted(_VALID_TABLES):
            count = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            stats[table] = count
        return stats


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

_store: Optional[MarketStore] = None


def get_store(db_path: Optional[Path] = None) -> MarketStore:
    """Get or create the singleton MarketStore instance."""
    global _store
    resolved = db_path or _DEFAULT_DB_PATH
    if _store is None or _store.db_path != resolved:
        _store = MarketStore(db_path)
    return _store
