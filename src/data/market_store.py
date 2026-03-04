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
import logging
import re
import sqlite3
from datetime import datetime, timedelta
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
        self._conn: Optional[sqlite3.Connection] = None
        self._init_db()

    def _get_conn(self) -> sqlite3.Connection:
        if self._conn is None:
            self._conn = sqlite3.connect(str(self.db_path))
            self._conn.row_factory = sqlite3.Row
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA foreign_keys=ON")
        return self._conn

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
        if self._conn:
            self._conn.close()
            self._conn = None

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
        """Upsert daily prices from a DataFrame (as stored in CSV cache)."""
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
        """Return daily prices as a DataFrame matching CSV cache format.

        Returns DataFrame with columns:
            ["date", "open", "high", "low", "close", "volume", "change", "changePercent"]
        Sorted by date descending (newest first). date dtype is datetime64[ns].
        Returns None if no data found.
        """
        rows = self.get_daily_prices(symbol, limit=limit)
        if not rows:
            return None

        df = pd.DataFrame(rows)

        # Drop symbol column (CSV format doesn't include it)
        if "symbol" in df.columns:
            df = df.drop(columns=["symbol"])

        # Rename change_pct → changePercent to match CSV convention
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

    # ---- Symbol Discovery ----

    def get_symbols(self, table: str = "daily_price") -> List[str]:
        """Return sorted list of distinct symbols in a table."""
        _validate_table(table)
        conn = self._get_conn()
        rows = conn.execute(f"SELECT DISTINCT symbol FROM {table}").fetchall()
        return sorted(r[0] for r in rows)

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
