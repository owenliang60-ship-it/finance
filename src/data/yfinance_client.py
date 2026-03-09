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
from typing import Any, Dict, List, Tuple

import numpy as np
import yfinance as yf

logger = logging.getLogger(__name__)

# Periods we care about (exclude LTG from growth_estimates)
_PERIODS = ("0q", "+1q", "0y", "+1y")


def _safe_val(v: Any) -> Any:
    """Convert NaN/inf to None, numpy types to Python native for SQLite."""
    if v is None:
        return None
    # Convert numpy types to Python native (prevents SQLite blob storage)
    if isinstance(v, (np.integer,)):
        return int(v)
    if isinstance(v, (np.floating,)):
        v = float(v)
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
