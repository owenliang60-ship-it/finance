"""
Benchmark comparison engine — portfolio returns vs SPY, QQQ, equal-weight.

Reads price data via get_price_df() (market.db → CSV fallback).
SPY/QQQ benchmark data must be added to the price fetcher separately.
Gracefully degrades if benchmark data is not yet available.
"""
import logging
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple

import pandas as pd
import numpy as np

from portfolio.holdings.schema import Position

logger = logging.getLogger(__name__)

# Supported benchmarks
BENCHMARKS = {
    "SPY": "S&P 500 ETF",
    "QQQ": "Nasdaq 100 ETF",
    "EW": "Equal-weight portfolio",
}


class BenchmarkEngine:
    """Compare portfolio performance against benchmarks."""

    def __init__(self, positions: List[Position]):
        self.positions = positions
        self._price_cache: Dict[str, pd.DataFrame] = {}

    def calculate_portfolio_returns(
        self, start_date: str, end_date: Optional[str] = None
    ) -> Optional[pd.Series]:
        """
        Calculate weighted daily portfolio returns.

        Uses position weights and individual stock returns.
        Returns a pd.Series indexed by date with daily returns.
        """
        if not self.positions:
            return None

        # Collect price data for all positions
        returns_data = {}
        for p in self.positions:
            prices = self._load_prices(p.symbol)
            if prices is not None and not prices.empty:
                prices = prices.set_index("date")["close"].sort_index()
                daily_ret = prices.pct_change().dropna()
                returns_data[p.symbol] = daily_ret

        if not returns_data:
            logger.warning("No price data available for any position")
            return None

        # Build returns DataFrame
        returns_df = pd.DataFrame(returns_data)

        # Filter date range
        start = pd.to_datetime(start_date)
        if end_date:
            end = pd.to_datetime(end_date)
            returns_df = returns_df.loc[start:end]
        else:
            returns_df = returns_df.loc[start:]

        if returns_df.empty:
            return None

        # Weight by current_weight (simplified: static weights)
        weights = {}
        total_weight = sum(p.current_weight for p in self.positions if p.symbol in returns_df.columns)
        for p in self.positions:
            if p.symbol in returns_df.columns and total_weight > 0:
                weights[p.symbol] = p.current_weight / total_weight

        # Weighted portfolio return
        portfolio_returns = pd.Series(0.0, index=returns_df.index)
        for symbol, weight in weights.items():
            if symbol in returns_df.columns:
                col = returns_df[symbol].fillna(0)
                portfolio_returns += col * weight

        return portfolio_returns

    def calculate_benchmark_returns(
        self, benchmark: str, start_date: str, end_date: Optional[str] = None
    ) -> Optional[pd.Series]:
        """
        Calculate daily benchmark returns.

        For "EW" (equal-weight), computes equal-weighted return of all positions.
        For SPY/QQQ, reads from price CSVs.
        """
        if benchmark == "EW":
            return self._equal_weight_returns(start_date, end_date)

        prices = self._load_prices(benchmark)
        if prices is None or prices.empty:
            logger.warning(
                f"Benchmark {benchmark} price data not available. "
                f"Add {benchmark}.csv to data/price/ to enable comparison."
            )
            return None

        prices = prices.set_index("date")["close"].sort_index()
        daily_ret = prices.pct_change().dropna()

        start = pd.to_datetime(start_date)
        if end_date:
            end = pd.to_datetime(end_date)
            daily_ret = daily_ret.loc[start:end]
        else:
            daily_ret = daily_ret.loc[start:]

        return daily_ret

    def relative_performance(
        self,
        portfolio_returns: pd.Series,
        benchmark_returns: pd.Series,
    ) -> dict:
        """
        Calculate relative performance metrics.

        Returns:
            {
                "cumulative_portfolio": float,
                "cumulative_benchmark": float,
                "active_return": float,
                "tracking_error": float,
                "information_ratio": float,
                "max_drawdown_portfolio": float,
                "max_drawdown_benchmark": float,
                "win_rate": float,
                "trading_days": int,
            }
        """
        # Align dates
        aligned = pd.DataFrame({
            "portfolio": portfolio_returns,
            "benchmark": benchmark_returns,
        }).dropna()

        if aligned.empty:
            return {"error": "No overlapping dates between portfolio and benchmark"}

        port = aligned["portfolio"]
        bench = aligned["benchmark"]
        active = port - bench

        # Cumulative returns
        cum_port = (1 + port).cumprod().iloc[-1] - 1
        cum_bench = (1 + bench).cumprod().iloc[-1] - 1

        # Tracking error (annualized std of active returns)
        te = active.std() * np.sqrt(252)

        # Information ratio
        ir = (active.mean() * 252) / te if te > 0 else 0.0

        return {
            "cumulative_portfolio": round(float(cum_port), 6),
            "cumulative_benchmark": round(float(cum_bench), 6),
            "active_return": round(float(cum_port - cum_bench), 6),
            "tracking_error": round(float(te), 6),
            "information_ratio": round(float(ir), 4),
            "max_drawdown_portfolio": round(float(self._max_drawdown(port)), 6),
            "max_drawdown_benchmark": round(float(self._max_drawdown(bench)), 6),
            "win_rate": round(float((active > 0).mean()), 4),
            "trading_days": len(aligned),
        }

    def compare_all_benchmarks(
        self, start_date: str, end_date: Optional[str] = None
    ) -> Dict[str, dict]:
        """
        Compare portfolio against all available benchmarks.

        Returns dict keyed by benchmark name.
        """
        port_returns = self.calculate_portfolio_returns(start_date, end_date)
        if port_returns is None:
            return {"error": "No portfolio return data available"}

        results = {}
        for bm_symbol, bm_name in BENCHMARKS.items():
            bm_returns = self.calculate_benchmark_returns(bm_symbol, start_date, end_date)
            if bm_returns is not None:
                results[bm_symbol] = {
                    "name": bm_name,
                    **self.relative_performance(port_returns, bm_returns),
                }
            else:
                results[bm_symbol] = {
                    "name": bm_name,
                    "error": f"Benchmark data not available for {bm_symbol}",
                }

        return results

    # -----------------------------------------------------------------------
    # Internal helpers
    # -----------------------------------------------------------------------

    def _load_prices(self, symbol: str) -> Optional[pd.DataFrame]:
        """Load price data via get_price_df (market.db → CSV fallback)."""
        if symbol in self._price_cache:
            return self._price_cache[symbol]

        try:
            from src.data.price_fetcher import get_price_df
            df = get_price_df(symbol, max_age_days=0)
            if df is None or df.empty:
                return None
            # get_price_df returns descending; benchmark needs ascending
            df = df.sort_values("date", ascending=True).reset_index(drop=True)
            self._price_cache[symbol] = df
            return df
        except Exception as e:
            logger.warning(f"Failed to load prices for {symbol}: {e}")
            return None

    def _equal_weight_returns(
        self, start_date: str, end_date: Optional[str] = None
    ) -> Optional[pd.Series]:
        """Calculate equal-weighted return of all positions."""
        returns_data = {}
        for p in self.positions:
            prices = self._load_prices(p.symbol)
            if prices is not None and not prices.empty:
                prices = prices.set_index("date")["close"].sort_index()
                returns_data[p.symbol] = prices.pct_change().dropna()

        if not returns_data:
            return None

        returns_df = pd.DataFrame(returns_data)

        start = pd.to_datetime(start_date)
        if end_date:
            end = pd.to_datetime(end_date)
            returns_df = returns_df.loc[start:end]
        else:
            returns_df = returns_df.loc[start:]

        # Equal weight = simple mean across columns
        return returns_df.mean(axis=1)

    @staticmethod
    def _max_drawdown(returns: pd.Series) -> float:
        """Calculate maximum drawdown from a return series."""
        cumulative = (1 + returns).cumprod()
        running_max = cumulative.cummax()
        drawdown = (cumulative - running_max) / running_max
        return float(drawdown.min())
