"""
IV Tracker — Implied Volatility rank/percentile computation.

Core functions:
- update_daily_iv(): Batch pull ATM IV for pool stocks, store in DB
- get_iv_rank(): (current - 52w_low) / (52w_high - 52w_low) × 100
- get_iv_percentile(): % of days IV was below current over lookback
- get_iv_history_summary(): One-stop IV snapshot for a symbol
- compute_hv(): Historical (realized) volatility from price CSV data
"""
import logging
import math
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from config.settings import (
    OPTIONS_IV_LOOKBACK_DAYS,
    PRICE_DIR,
)

logger = logging.getLogger(__name__)


def compute_hv(symbol: str, window: int = 30) -> Optional[float]:
    """Compute historical (realized) volatility from price CSV.

    Uses close-to-close log returns, annualized (×sqrt(252)).

    Args:
        symbol: Stock ticker
        window: Number of trading days for HV calculation

    Returns:
        Annualized HV as decimal (e.g., 0.25 = 25%), or None if insufficient data
    """
    csv_path = PRICE_DIR / "{}.csv".format(symbol.upper())
    if not csv_path.exists():
        logger.warning("No price data for %s at %s", symbol, csv_path)
        return None

    import csv
    closes = []
    with open(csv_path, "r") as f:
        reader = csv.DictReader(f)
        for row in reader:
            try:
                closes.append(float(row["close"]))
            except (KeyError, ValueError):
                continue

    if len(closes) < window + 1:
        logger.warning(
            "Insufficient price data for %s HV: need %d, got %d",
            symbol, window + 1, len(closes),
        )
        return None

    # closes are newest-first in our CSVs, reverse for chronological
    closes = closes[:window + 1]
    closes.reverse()

    # Log returns
    log_returns = []
    for i in range(1, len(closes)):
        if closes[i - 1] > 0 and closes[i] > 0:
            log_returns.append(math.log(closes[i] / closes[i - 1]))

    if len(log_returns) < 2:
        return None

    # Standard deviation of log returns
    mean = sum(log_returns) / len(log_returns)
    variance = sum((r - mean) ** 2 for r in log_returns) / (len(log_returns) - 1)
    std_dev = math.sqrt(variance)

    # Annualize
    hv = std_dev * math.sqrt(252)
    return round(hv, 4)


def get_iv_rank(
    symbol: str,
    store,
    lookback: int = OPTIONS_IV_LOOKBACK_DAYS,
) -> Optional[float]:
    """Calculate IV Rank: (current - 52w_low) / (52w_high - 52w_low) × 100.

    Args:
        symbol: Stock ticker
        store: CompanyStore instance
        lookback: Number of trading days to look back

    Returns:
        IV Rank as percentage (0-100), or None if insufficient data
    """
    history = store.get_iv_history(symbol, limit=lookback)
    if not history:
        return None

    current_iv = history[0].get("iv_30d")
    if current_iv is None:
        return None

    iv_values = [h["iv_30d"] for h in history if h.get("iv_30d") is not None]
    if len(iv_values) < 2:
        return None

    iv_high = max(iv_values)
    iv_low = min(iv_values)

    if iv_high == iv_low:
        return 50.0  # No range — return neutral

    rank = (current_iv - iv_low) / (iv_high - iv_low) * 100
    return round(min(max(rank, 0), 100), 1)


def get_iv_percentile(
    symbol: str,
    store,
    lookback: int = OPTIONS_IV_LOOKBACK_DAYS,
) -> Optional[float]:
    """Calculate IV Percentile: % of past days where IV was below current.

    Args:
        symbol: Stock ticker
        store: CompanyStore instance
        lookback: Number of trading days to look back

    Returns:
        IV Percentile as percentage (0-100), or None if insufficient data
    """
    history = store.get_iv_history(symbol, limit=lookback)
    if not history:
        return None

    current_iv = history[0].get("iv_30d")
    if current_iv is None:
        return None

    # Exclude current day (history[0]) from comparison set — standard IVP definition
    historical_ivs = [h["iv_30d"] for h in history[1:] if h.get("iv_30d") is not None]
    if not historical_ivs:
        return None

    below_count = sum(1 for v in historical_ivs if v < current_iv)
    percentile = below_count / len(historical_ivs) * 100
    return round(percentile, 1)


def get_iv_history_summary(
    symbol: str,
    store,
) -> Optional[Dict[str, Any]]:
    """One-stop IV summary for a symbol.

    Returns:
        Dict with current_iv, iv_rank, iv_percentile, hv_30d, rv_iv_ratio,
        iv_52w_high, iv_52w_low, data_days
    """
    latest = store.get_latest_iv(symbol)
    if not latest or latest.get("iv_30d") is None:
        return None

    current_iv = latest["iv_30d"]
    iv_rank = get_iv_rank(symbol, store)
    iv_pctl = get_iv_percentile(symbol, store)
    hv = latest.get("hv_30d") or compute_hv(symbol)

    # 52-week high/low
    history = store.get_iv_history(symbol, limit=252)
    iv_values = [h["iv_30d"] for h in history if h.get("iv_30d") is not None]

    rv_iv_ratio = None
    if hv and current_iv and current_iv > 0:
        rv_iv_ratio = round(hv / current_iv, 2)

    return {
        "symbol": symbol.upper(),
        "current_iv": current_iv,
        "iv_rank": iv_rank,
        "iv_percentile": iv_pctl,
        "hv_30d": hv,
        "rv_iv_ratio": rv_iv_ratio,
        "iv_52w_high": max(iv_values) if iv_values else None,
        "iv_52w_low": min(iv_values) if iv_values else None,
        "data_days": len(iv_values),
        "date": latest["date"],
    }


def update_daily_iv(
    symbols: List[str],
    store,
    client=None,
) -> Dict[str, Any]:
    """Batch update daily IV for a list of symbols.

    Pulls ATM IV from MarketData.app (strikeLimit=2, range=atm) and stores in DB.
    Also computes HV from existing price data.

    Args:
        symbols: List of stock tickers
        store: CompanyStore instance
        client: MarketDataClient instance (defaults to singleton)

    Returns:
        Summary dict with success_count, fail_count, errors
    """
    if client is None:
        from src.data.marketdata_client import marketdata_client
        client = marketdata_client

    today = datetime.now().strftime("%Y-%m-%d")
    success = 0
    failures = 0
    errors = []

    for symbol in symbols:
        try:
            data = client.get_atm_iv_data(symbol)
            if not data or data.get("s") != "ok":
                failures += 1
                errors.append("{}: no data".format(symbol))
                continue

            # Extract ATM IV from chain data
            iv_values = data.get("iv", [])
            if not iv_values:
                failures += 1
                errors.append("{}: no IV in chain".format(symbol))
                continue

            # Average of available ATM IVs (call + put)
            valid_ivs = [v for v in iv_values if v is not None and v > 0]
            if not valid_ivs:
                failures += 1
                errors.append("{}: all IV values null".format(symbol))
                continue

            iv_30d = sum(valid_ivs) / len(valid_ivs)

            # Compute HV from price data
            hv_30d = compute_hv(symbol)

            # Extract put/call ratio and volume/OI if available
            total_volume = None
            total_oi = None
            volumes = data.get("volume", [])
            ois = data.get("openInterest", [])
            if volumes:
                total_volume = sum(v for v in volumes if v is not None)
            if ois:
                total_oi = sum(v for v in ois if v is not None)

            store.save_iv_daily(
                symbol=symbol,
                date=today,
                iv_30d=round(iv_30d, 4),
                hv_30d=hv_30d,
                total_volume=total_volume,
                total_oi=total_oi,
            )
            success += 1

        except Exception as e:
            failures += 1
            errors.append("{}: {}".format(symbol, str(e)))
            logger.error("Failed to update IV for %s: %s", symbol, e)

    logger.info(
        "IV update complete: %d success, %d failures out of %d",
        success, failures, len(symbols),
    )
    return {
        "success_count": success,
        "fail_count": failures,
        "total": len(symbols),
        "errors": errors[:10],  # Cap error list
    }
