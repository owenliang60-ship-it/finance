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
import json
import logging
import re
import sqlite3
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

import pandas as pd

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
    "fmp_estimates", "fmp_earnings", "fmp_etf_holdings_snapshot",
    "fmp_basket_valuation", "fmp_forward_runs",
})


def _validate_table(table_name: str) -> None:
    """Raise ValueError if table name is not in whitelist."""
    if table_name not in _VALID_TABLES:
        raise ValueError(f"Invalid table name: {table_name!r}")


def _validate_column(col: str, valid_cols: List[str]) -> None:
    """Raise ValueError if column is not valid."""
    if col not in valid_cols:
        raise ValueError(f"Invalid column name: {col!r}")


# ---------------------------------------------------------------------------
# MarketStore class
# ---------------------------------------------------------------------------

class MarketStore:
    """SQLite-backed market time-series database."""

    def __init__(self, db_path: Optional[Path] = None):
        self.db_path = db_path or _DEFAULT_DB_PATH
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._local = threading.local()
        self._init_db()

    def _get_conn(self) -> sqlite3.Connection:
        conn = getattr(self._local, 'conn', None)
        if conn is None:
            conn = sqlite3.connect(str(self.db_path))
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA foreign_keys=ON")
            self._local.conn = conn
        return conn

    def _init_db(self) -> None:
        conn = self._get_conn()
        conn.executescript(_SCHEMA)
        self._migrate_add_columns(conn)
        conn.commit()

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

        conn = self._get_conn()
        valid_cols = _get_table_columns(table, conn)
        count = 0

        with conn:
            for row in rows:
                if convert:
                    data = self._convert_row(row, table)
                else:
                    data = {k: v for k, v in row.items() if k in valid_cols}
                data["symbol"] = symbol.upper()

                if "date" not in data or not data["date"]:
                    continue

                cols = [c for c in data if c in valid_cols]
                placeholders = ", ".join(["?"] * len(cols))
                col_names = ", ".join(cols)
                values = [data[c] for c in cols]

                conn.execute(
                    f"INSERT OR REPLACE INTO {table} ({col_names}) VALUES ({placeholders})",
                    values,
                )
                count += 1

        return count

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
