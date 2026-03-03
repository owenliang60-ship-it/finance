"""
Correlation engine — compute pairwise return correlations for portfolio risk analysis.

Used by ExposureAnalyzer.correlation_adjusted_exposure() which expects
Dict[str, Dict[str, float]] as input.
"""
import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

import pandas as pd

from config.settings import DATA_DIR

logger = logging.getLogger(__name__)

CORRELATION_CACHE_DIR = DATA_DIR / "correlation"
CORRELATION_CACHE_FILE = CORRELATION_CACHE_DIR / "matrix.json"


def load_price_returns(symbol: str, window: int = 120) -> Optional[pd.Series]:
    """
    Load daily returns for a symbol from price data (market.db → CSV fallback).

    Args:
        symbol: Stock ticker
        window: Number of trading days to include

    Returns:
        pd.Series of daily returns indexed by date, or None if data unavailable
    """
    from src.data.price_fetcher import get_price_df

    try:
        df = get_price_df(symbol, days=window + 1, max_age_days=0)
        if df is None or df.empty:
            logger.warning(f"No price data for {symbol}")
            return None

        # get_price_df returns descending; sort ascending for pct_change
        df = df.sort_values("date", ascending=True).reset_index(drop=True)

        returns = df.set_index("date")["close"].pct_change().dropna()
        returns.name = symbol
        return returns
    except Exception as e:
        logger.error(f"Failed to load returns for {symbol}: {e}")
        return None


def compute_correlation_matrix(
    symbols: List[str],
    window: int = 120,
    method: str = "pearson",
) -> Dict[str, Dict[str, float]]:
    """
    Compute pairwise correlation matrix for given symbols.

    Args:
        symbols: List of tickers
        window: Trading days lookback
        method: "pearson" or "spearman"

    Returns:
        Nested dict: {symbol_a: {symbol_b: correlation, ...}, ...}
    """
    # Load all returns
    returns_dict = {}
    for sym in symbols:
        ret = load_price_returns(sym, window)
        if ret is not None and len(ret) >= 20:  # Need at least 20 data points
            returns_dict[sym] = ret

    if len(returns_dict) < 2:
        logger.warning(f"Not enough symbols with data ({len(returns_dict)}) to compute correlations")
        return {}

    # Build DataFrame and compute correlation
    returns_df = pd.DataFrame(returns_dict)
    corr_matrix = returns_df.corr(method=method)

    # Convert to nested dict
    result = {}
    for sym_a in corr_matrix.index:
        result[sym_a] = {}
        for sym_b in corr_matrix.columns:
            result[sym_a][sym_b] = round(float(corr_matrix.loc[sym_a, sym_b]), 4)

    return result


def save_correlation_cache(matrix: Dict[str, Dict[str, float]]) -> None:
    """Save correlation matrix to cache file."""
    CORRELATION_CACHE_DIR.mkdir(parents=True, exist_ok=True)

    cache_data = {
        "computed_at": datetime.now().isoformat(),
        "symbol_count": len(matrix),
        "matrix": matrix,
    }

    with open(CORRELATION_CACHE_FILE, "w") as f:
        json.dump(cache_data, f, indent=2)

    logger.info(f"Saved correlation matrix ({len(matrix)} symbols) to {CORRELATION_CACHE_FILE}")


def load_correlation_cache() -> Optional[Dict[str, Dict[str, float]]]:
    """Load correlation matrix from cache. Returns None if no cache."""
    if not CORRELATION_CACHE_FILE.exists():
        return None

    try:
        with open(CORRELATION_CACHE_FILE) as f:
            cache_data = json.load(f)

        logger.info(
            f"Loaded correlation cache: {cache_data.get('symbol_count', '?')} symbols, "
            f"computed at {cache_data.get('computed_at', '?')}"
        )
        return cache_data.get("matrix")
    except Exception as e:
        logger.error(f"Failed to load correlation cache: {e}")
        return None


def get_correlation_matrix(
    symbols: List[str],
    window: int = 120,
    method: str = "pearson",
    use_cache: bool = True,
) -> Dict[str, Dict[str, float]]:
    """
    High-level entry: get correlation matrix, using cache if available.

    Args:
        symbols: List of tickers
        window: Trading days lookback
        method: Correlation method
        use_cache: Whether to try cache first

    Returns:
        Nested dict correlation matrix
    """
    if use_cache:
        cached = load_correlation_cache()
        if cached:
            # Check if cache covers all requested symbols
            cached_symbols = set(cached.keys())
            requested = set(symbols)
            if requested.issubset(cached_symbols):
                logger.info("Using cached correlation matrix")
                return cached
            else:
                missing = requested - cached_symbols
                logger.info(f"Cache missing {len(missing)} symbols, recomputing")

    matrix = compute_correlation_matrix(symbols, window, method)
    if matrix:
        save_correlation_cache(matrix)
    return matrix
