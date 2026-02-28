"""
Pre-computed metrics engine for market.db.

Computes ~22 derived metrics from income, balance sheet, and cash flow data,
storing results in the metrics_quarterly table. Designed for screening queries
like "net_margin > 25% for 4 consecutive quarters".

Usage:
    from src.data.metrics_calculator import compute_metrics, compute_all_metrics
    compute_metrics("AAPL")           # Single stock
    compute_all_metrics()             # All stocks in DB
"""
import logging
from typing import Any, Dict, List, Optional

from src.data.market_store import get_store, MarketStore

logger = logging.getLogger(__name__)


def _safe_div(numerator: Any, denominator: Any) -> Optional[float]:
    """Safe division: returns None if denominator is zero/None or numerator is None."""
    if numerator is None or denominator is None:
        return None
    try:
        n = float(numerator)
        d = float(denominator)
    except (TypeError, ValueError):
        return None
    if d == 0:
        return None
    return n / d


def _avg(a: Any, b: Any) -> Optional[float]:
    """Average of two values; returns None if either is None."""
    if a is None or b is None:
        return None
    try:
        return (float(a) + float(b)) / 2.0
    except (TypeError, ValueError):
        return None


def _sum_last_n(rows: List[Dict], field: str, n: int) -> Optional[float]:
    """Sum field across first n rows (most recent). Returns None if insufficient data."""
    if len(rows) < n:
        return None
    total = 0.0
    for i in range(n):
        v = rows[i].get(field)
        if v is None:
            return None
        total += float(v)
    return total


def _find_yoy_match(rows: List[Dict], target_period: str, target_fy: Any) -> Optional[Dict]:
    """Find the row matching (period, fiscal_year - 1) for YoY comparison.

    Handles non-standard fiscal years (e.g. AAPL Sep, NVDA Jan) by matching
    on period + fiscal_year rather than date offsets.
    """
    if target_period is None or target_fy is None:
        return None
    try:
        prior_fy = str(int(float(target_fy)) - 1)
    except (TypeError, ValueError):
        return None
    for row in rows:
        if row.get("period") == target_period and str(row.get("fiscal_year")) == prior_fy:
            return row
    return None


def _yoy_growth(current_val: Any, prior_val: Any) -> Optional[float]:
    """Compute YoY growth rate. Returns None if prior is zero/None."""
    if current_val is None or prior_val is None:
        return None
    try:
        c = float(current_val)
        p = float(prior_val)
    except (TypeError, ValueError):
        return None
    if p == 0:
        return None
    return (c - p) / abs(p)


def compute_metrics(symbol: str, store: Optional[MarketStore] = None) -> int:
    """Compute all metrics for a single symbol and store in metrics_quarterly.

    Returns number of metric rows written.
    """
    if store is None:
        store = get_store()

    # Fetch all available data (up to 20 quarters for TTM + YoY lookback)
    income = store.get_income(symbol, limit=20)
    bs = store.get_balance_sheet(symbol, limit=20)
    cf = store.get_cash_flow(symbol, limit=20)

    if not income:
        return 0

    # Build lookup dicts keyed by date for balance sheet and cash flow
    bs_by_date: Dict[str, Dict] = {r["date"]: r for r in bs}
    cf_by_date: Dict[str, Dict] = {r["date"]: r for r in cf}

    results = []

    for idx, inc in enumerate(income):
        date = inc.get("date")
        period = inc.get("period")
        fy = inc.get("fiscal_year")
        if not date:
            continue

        bs_row = bs_by_date.get(date, {})
        cf_row = cf_by_date.get(date, {})

        m: Dict[str, Any] = {
            "symbol": symbol.upper(),
            "date": date,
            "period": period,
            "fiscal_year": str(fy) if fy is not None else None,
        }

        revenue = inc.get("revenue")

        # --- Margins ---
        m["gross_margin"] = _safe_div(inc.get("gross_profit"), revenue)
        m["operating_margin"] = _safe_div(inc.get("operating_income"), revenue)
        m["net_margin"] = _safe_div(inc.get("net_income"), revenue)
        m["ebitda_margin"] = _safe_div(inc.get("ebitda"), revenue)

        # --- Returns (TTM preferred, fallback to annualized single quarter) ---
        ttm_ni = _sum_last_n(income[idx:], "net_income", 4)

        # For avg denominators, need current and period-start balance sheet.
        # TTM sums income[idx..idx+3]. The BS at the START of that window
        # is the BS at the end of the quarter BEFORE the window = income[idx+4].
        current_equity = bs_row.get("total_stockholders_equity")
        current_assets = bs_row.get("total_assets")

        prior_bs_date = income[idx + 4].get("date") if idx + 4 < len(income) else None
        prior_bs = bs_by_date.get(prior_bs_date, {}) if prior_bs_date else {}

        if ttm_ni is not None:
            avg_eq = _avg(current_equity, prior_bs.get("total_stockholders_equity"))
            avg_assets = _avg(current_assets, prior_bs.get("total_assets"))
            m["roe"] = _safe_div(ttm_ni, avg_eq)
            m["roa"] = _safe_div(ttm_ni, avg_assets)
        else:
            # Fallback: annualize single quarter
            single_ni = inc.get("net_income")
            if single_ni is not None:
                annualized = float(single_ni) * 4
                m["roe"] = _safe_div(annualized, current_equity)
                m["roa"] = _safe_div(annualized, current_assets)
            else:
                m["roe"] = None
                m["roa"] = None

        # ROIC = NOPAT / Invested Capital
        # NOPAT = Operating Income * (1 - effective tax rate)
        # Invested Capital = Total Equity + Total Debt - Cash
        op_inc = inc.get("operating_income")
        tax_expense = inc.get("income_tax_expense")
        pre_tax = inc.get("income_before_tax")
        eff_tax = _safe_div(tax_expense, pre_tax) if pre_tax and pre_tax != 0 else None
        if op_inc is not None and eff_tax is not None:
            nopat = float(op_inc) * (1 - float(eff_tax))
            total_debt = bs_row.get("total_debt") or 0
            equity = current_equity or 0
            cash = bs_row.get("cash_and_cash_equivalents") or 0
            invested = float(equity) + float(total_debt) - float(cash)
            m["roic"] = _safe_div(nopat, invested) if invested != 0 else None
        else:
            m["roic"] = None

        # --- Leverage ---
        m["debt_to_equity"] = _safe_div(bs_row.get("total_debt"), current_equity)
        m["debt_to_assets"] = _safe_div(bs_row.get("total_debt"), current_assets)
        m["current_ratio"] = _safe_div(
            bs_row.get("total_current_assets"),
            bs_row.get("total_current_liabilities"),
        )
        inv = bs_row.get("inventory") or 0
        ca = bs_row.get("total_current_assets")
        cl = bs_row.get("total_current_liabilities")
        if ca is not None and cl is not None:
            m["quick_ratio"] = _safe_div(float(ca) - float(inv), cl)
        else:
            m["quick_ratio"] = None

        # --- Efficiency (TTM where possible) ---
        ttm_rev = _sum_last_n(income[idx:], "revenue", 4)
        ttm_cogs = _sum_last_n(income[idx:], "cost_of_revenue", 4)

        avg_assets_eff = _avg(current_assets, prior_bs.get("total_assets"))
        m["asset_turnover"] = _safe_div(ttm_rev, avg_assets_eff) if ttm_rev else _safe_div(
            float(revenue or 0) * 4, current_assets
        )

        avg_inv = _avg(bs_row.get("inventory"), prior_bs.get("inventory"))
        m["inventory_turnover"] = _safe_div(ttm_cogs, avg_inv) if ttm_cogs else None

        avg_recv = _avg(bs_row.get("net_receivables"), prior_bs.get("net_receivables"))
        m["receivables_turnover"] = _safe_div(ttm_rev, avg_recv) if ttm_rev else None

        # --- Growth YoY ---
        yoy_match = _find_yoy_match(income, period, fy)
        if yoy_match:
            m["revenue_growth_yoy"] = _yoy_growth(revenue, yoy_match.get("revenue"))
            m["net_income_growth_yoy"] = _yoy_growth(inc.get("net_income"), yoy_match.get("net_income"))
            m["eps_growth_yoy"] = _yoy_growth(inc.get("eps_diluted"), yoy_match.get("eps_diluted"))
            m["operating_income_growth_yoy"] = _yoy_growth(
                inc.get("operating_income"), yoy_match.get("operating_income")
            )
        else:
            m["revenue_growth_yoy"] = None
            m["net_income_growth_yoy"] = None
            m["eps_growth_yoy"] = None
            m["operating_income_growth_yoy"] = None

        # --- Cash Flow ---
        fcf = cf_row.get("free_cash_flow")
        op_cf = cf_row.get("operating_cash_flow")
        ni = inc.get("net_income")

        m["fcf_margin"] = _safe_div(fcf, revenue)
        m["fcf_to_net_income"] = _safe_div(fcf, ni)
        m["operating_cf_to_revenue"] = _safe_div(op_cf, revenue)

        results.append(m)

    if results:
        count = store.upsert_metrics(symbol, results)
        logger.info("Computed %d metric rows for %s", count, symbol)
        return count
    return 0


def compute_all_metrics(
    symbols: Optional[List[str]] = None,
    store: Optional[MarketStore] = None,
) -> Dict[str, int]:
    """Compute metrics for all symbols (or a given list).

    Returns dict of {symbol: rows_written}.
    """
    if store is None:
        store = get_store()

    if symbols is None:
        # Get all unique symbols from income_quarterly
        conn = store._get_conn()
        rows = conn.execute(
            "SELECT DISTINCT symbol FROM income_quarterly ORDER BY symbol"
        ).fetchall()
        symbols = [r["symbol"] for r in rows]

    results = {}
    for i, symbol in enumerate(symbols, 1):
        count = compute_metrics(symbol, store)
        if count > 0:
            results[symbol] = count
        if i % 20 == 0 or i == len(symbols):
            logger.info("[metrics] %d/%d symbols processed", i, len(symbols))

    logger.info("Metrics computation complete: %d symbols, %d total rows",
                len(results), sum(results.values()))
    return results
