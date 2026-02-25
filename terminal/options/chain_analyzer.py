"""
Chain Analyzer — Options chain analysis, liquidity assessment, term structure.

Core functions:
- fetch_and_store_chain(): Pull full chain, store in DB, clean old snapshots
- analyze_liquidity(): Assess liquidity quality (EXCELLENT/GOOD/FAIR/POOR/NO_GO)
- get_term_structure(): ATM IV across expirations
- filter_liquid_strikes(): Filter strikes meeting liquidity thresholds
- find_atm_options(): Find ATM call/put for a given expiration
- get_earnings_proximity(): Days to earnings + zone classification
"""
import logging
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

from config.settings import (
    OPTIONS_CHAIN_DTE_MIN,
    OPTIONS_CHAIN_DTE_MAX,
    OPTIONS_SNAPSHOT_RETAIN_DAYS,
    OPTIONS_LIQUIDITY_MIN_OI,
    OPTIONS_LIQUIDITY_MIN_VOLUME,
    OPTIONS_LIQUIDITY_MAX_SPREAD_PCT,
)

logger = logging.getLogger(__name__)


def fetch_and_store_chain(
    symbol: str,
    store,
    client=None,
    dte_min: int = OPTIONS_CHAIN_DTE_MIN,
    dte_max: int = OPTIONS_CHAIN_DTE_MAX,
) -> Optional[Dict[str, Any]]:
    """Pull full options chain and store in DB.

    Args:
        symbol: Stock ticker
        store: CompanyStore instance
        client: MarketDataClient (defaults to singleton)
        dte_min: Minimum DTE filter
        dte_max: Maximum DTE filter

    Returns:
        Summary dict with contract_count, expirations, snapshot_date, or None on failure
    """
    if client is None:
        from src.data.marketdata_client import marketdata_client
        client = marketdata_client

    symbol = symbol.upper()
    today = datetime.now().strftime("%Y-%m-%d")

    data = client.get_options_chain(symbol, dte_min=dte_min, dte_max=dte_max)
    if not data or data.get("s") != "ok":
        logger.warning("No chain data for %s", symbol)
        return None

    # Parse array-style response into contract dicts
    contracts = _parse_chain_response(data, symbol)
    if not contracts:
        logger.warning("No valid contracts parsed for %s", symbol)
        return None

    # Get underlying price
    underlying_price = None
    underlying_prices = data.get("underlyingPrice", [])
    if underlying_prices:
        underlying_price = underlying_prices[0]

    # Add underlying price to all contracts
    for c in contracts:
        c["underlying_price"] = underlying_price

    # Store in DB
    count = store.save_options_snapshot(symbol, today, contracts)

    # Collect unique expirations
    expirations = sorted(set(c["expiration"] for c in contracts))

    return {
        "symbol": symbol,
        "contract_count": count,
        "expirations": expirations,
        "snapshot_date": today,
        "underlying_price": underlying_price,
    }


def _parse_chain_response(data: Dict, symbol: str) -> List[Dict[str, Any]]:
    """Parse MarketData.app array-style chain response into contract dicts.

    MarketData.app returns parallel arrays:
    {
        "s": "ok",
        "optionSymbol": ["AAPL260321C00200000", ...],
        "strike": [200, ...],
        "side": ["call", ...],
        "bid": [8.50, ...],
        ...
    }
    """
    option_symbols = data.get("optionSymbol", [])
    if not option_symbols:
        return []

    n = len(option_symbols)
    contracts = []

    for i in range(n):
        contract = {
            "expiration": _safe_get(data, "expiration", i, ""),
            "strike": _safe_get(data, "strike", i, 0),
            "side": _safe_get(data, "side", i, ""),
            "bid": _safe_get(data, "bid", i),
            "ask": _safe_get(data, "ask", i),
            "mid": _safe_get(data, "mid", i),
            "last": _safe_get(data, "last", i),
            "volume": _safe_get(data, "volume", i),
            "open_interest": _safe_get(data, "openInterest", i),
            "iv": _safe_get(data, "iv", i),
            "delta": _safe_get(data, "delta", i),
            "gamma": _safe_get(data, "gamma", i),
            "theta": _safe_get(data, "theta", i),
            "vega": _safe_get(data, "vega", i),
            "dte": _safe_get(data, "dte", i),
            "in_the_money": _safe_get(data, "inTheMoney", i, False),
        }
        contracts.append(contract)

    return contracts


def _safe_get(data: Dict, key: str, index: int, default=None):
    """Safely get value from parallel array."""
    arr = data.get(key, [])
    if arr and index < len(arr):
        return arr[index]
    return default


def analyze_liquidity(
    symbol: str,
    store,
    snapshot_date: Optional[str] = None,
) -> Dict[str, Any]:
    """Assess options liquidity quality for a symbol.

    Checks bid-ask spread, OI, volume across ATM strikes.

    Returns:
        Dict with verdict (EXCELLENT/GOOD/FAIR/POOR/NO_GO),
        avg_spread_pct, avg_oi, avg_volume, details
    """
    contracts = store.get_options_snapshot(symbol, snapshot_date)
    if not contracts:
        return {
            "verdict": "NO_GO",
            "reason": "No options data available",
            "avg_spread_pct": None,
            "avg_oi": None,
            "avg_volume": None,
        }

    # Focus on contracts with reasonable data
    valid = []
    for c in contracts:
        bid = c.get("bid")
        ask = c.get("ask")
        mid = c.get("mid")
        oi = c.get("open_interest")
        vol = c.get("volume")

        if bid is not None and ask is not None and mid and mid > 0:
            spread_pct = (ask - bid) / mid
            valid.append({
                "spread_pct": spread_pct,
                "oi": oi or 0,
                "volume": vol or 0,
            })

    if not valid:
        return {
            "verdict": "NO_GO",
            "reason": "No valid bid/ask data",
            "avg_spread_pct": None,
            "avg_oi": None,
            "avg_volume": None,
        }

    avg_spread = sum(v["spread_pct"] for v in valid) / len(valid)
    avg_oi = sum(v["oi"] for v in valid) / len(valid)
    avg_volume = sum(v["volume"] for v in valid) / len(valid)

    # Determine verdict
    # Thresholds derived from config constants (EXCELLENT = tightest, NO_GO = worst)
    if (avg_spread <= OPTIONS_LIQUIDITY_MAX_SPREAD_PCT * 0.3
            and avg_oi >= OPTIONS_LIQUIDITY_MIN_OI * 5
            and avg_volume >= OPTIONS_LIQUIDITY_MIN_VOLUME * 5):
        verdict = "EXCELLENT"
    elif (avg_spread <= OPTIONS_LIQUIDITY_MAX_SPREAD_PCT * 0.5
            and avg_oi >= OPTIONS_LIQUIDITY_MIN_OI * 2.5
            and avg_volume >= OPTIONS_LIQUIDITY_MIN_VOLUME * 2):
        verdict = "GOOD"
    elif (avg_spread <= OPTIONS_LIQUIDITY_MAX_SPREAD_PCT
            and avg_oi >= OPTIONS_LIQUIDITY_MIN_OI
            and avg_volume >= OPTIONS_LIQUIDITY_MIN_VOLUME):
        verdict = "FAIR"
    elif avg_spread <= OPTIONS_LIQUIDITY_MAX_SPREAD_PCT * 1.5 and avg_oi >= OPTIONS_LIQUIDITY_MIN_OI * 0.5:
        verdict = "POOR"
    else:
        verdict = "NO_GO"

    return {
        "verdict": verdict,
        "avg_spread_pct": round(avg_spread, 4),
        "avg_oi": round(avg_oi),
        "avg_volume": round(avg_volume),
        "total_contracts": len(contracts),
        "valid_contracts": len(valid),
    }


def get_term_structure(
    symbol: str,
    store,
    snapshot_date: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Get ATM IV across expirations (term structure).

    Returns:
        List of {expiration, dte, atm_iv, atm_strike} sorted by DTE
    """
    contracts = store.get_options_snapshot(symbol, snapshot_date)
    if not contracts:
        return []

    # Group by expiration
    by_exp = {}
    for c in contracts:
        exp = c.get("expiration", "")
        if exp not in by_exp:
            by_exp[exp] = []
        by_exp[exp].append(c)

    result = []
    for exp, exp_contracts in sorted(by_exp.items()):
        atm = _find_atm_in_contracts(exp_contracts)
        if atm:
            call_iv = atm.get("call_iv")
            put_iv = atm.get("put_iv")
            ivs = [v for v in [call_iv, put_iv] if v is not None and v > 0]
            if ivs:
                result.append({
                    "expiration": exp,
                    "dte": atm.get("dte"),
                    "atm_iv": round(sum(ivs) / len(ivs), 4),
                    "atm_strike": atm.get("strike"),
                })

    return sorted(result, key=lambda x: x.get("dte") or 0)


def _find_atm_in_contracts(contracts: List[Dict]) -> Optional[Dict]:
    """Find ATM strike from a list of contracts at same expiration.

    ATM = strike closest to underlying price.
    """
    if not contracts:
        return None

    # Get underlying price from any contract
    underlying = None
    for c in contracts:
        if c.get("underlying_price"):
            underlying = c["underlying_price"]
            break

    if underlying is None:
        return None

    # Find closest strike
    strikes = set(c.get("strike", 0) for c in contracts)
    if not strikes:
        return None

    atm_strike = min(strikes, key=lambda s: abs(s - underlying))

    # Get call and put at ATM strike
    call_iv = None
    put_iv = None
    dte = None
    for c in contracts:
        if c.get("strike") == atm_strike:
            if c.get("side") == "call":
                call_iv = c.get("iv")
            elif c.get("side") == "put":
                put_iv = c.get("iv")
            if c.get("dte") is not None:
                dte = c["dte"]

    return {
        "strike": atm_strike,
        "call_iv": call_iv,
        "put_iv": put_iv,
        "dte": dte,
    }


def filter_liquid_strikes(
    symbol: str,
    store,
    expiration: str,
    min_oi: int = OPTIONS_LIQUIDITY_MIN_OI,
    max_spread_pct: float = OPTIONS_LIQUIDITY_MAX_SPREAD_PCT,
) -> List[Dict[str, Any]]:
    """Filter strikes meeting liquidity thresholds for a given expiration.

    Args:
        symbol: Stock ticker
        store: CompanyStore instance
        expiration: Expiration date to filter
        min_oi: Minimum open interest
        max_spread_pct: Maximum bid-ask spread as % of mid

    Returns:
        List of liquid contracts
    """
    contracts = store.get_options_snapshot(symbol, expiration=expiration)
    liquid = []
    for c in contracts:
        oi = c.get("open_interest") or 0
        bid = c.get("bid")
        ask = c.get("ask")
        mid = c.get("mid")

        if oi < min_oi:
            continue
        # Spread filter: contracts with missing/zero mid are illiquid by definition
        if not (bid is not None and ask is not None and mid and mid > 0):
            continue
        spread_pct = (ask - bid) / mid
        if spread_pct > max_spread_pct:
            continue

        liquid.append(c)

    return liquid


def find_atm_options(
    symbol: str,
    store,
    expiration: str,
) -> Optional[Dict[str, Any]]:
    """Find ATM call and put for a given expiration.

    Returns:
        Dict with 'call' and 'put' contract dicts, plus 'atm_strike' and 'underlying_price'
    """
    contracts = store.get_options_snapshot(symbol, expiration=expiration)
    if not contracts:
        return None

    underlying = None
    for c in contracts:
        if c.get("underlying_price"):
            underlying = c["underlying_price"]
            break

    if underlying is None:
        return None

    strikes = set(c.get("strike", 0) for c in contracts)
    atm_strike = min(strikes, key=lambda s: abs(s - underlying))

    result = {
        "atm_strike": atm_strike,
        "underlying_price": underlying,
        "call": None,
        "put": None,
    }

    for c in contracts:
        if c.get("strike") == atm_strike:
            if c.get("side") == "call":
                result["call"] = c
            elif c.get("side") == "put":
                result["put"] = c

    return result


def get_earnings_proximity(
    symbol: str,
    fmp_client=None,
) -> Dict[str, Any]:
    """Get earnings proximity and zone classification.

    Zones:
    - CLEAR: > 30 days
    - CAUTION: 10-30 days
    - T5_WARNING: 5-10 days
    - BLACKOUT: < 5 days

    Args:
        symbol: Stock ticker
        fmp_client: FMP client for earnings calendar (defaults to singleton)

    Returns:
        Dict with days_to_earnings, earnings_date, zone
    """
    if fmp_client is None:
        try:
            from src.data.fmp_client import fmp_client as _fmp
            fmp_client = _fmp
        except ImportError:
            return {
                "days_to_earnings": None,
                "earnings_date": None,
                "zone": "UNKNOWN",
            }

    today = datetime.now()
    to_date = (today + timedelta(days=90)).strftime("%Y-%m-%d")
    from_date = today.strftime("%Y-%m-%d")

    calendar = fmp_client.get_earnings_calendar(
        from_date=from_date, to_date=to_date
    )

    if not calendar:
        return {
            "days_to_earnings": None,
            "earnings_date": None,
            "zone": "CLEAR",  # No earnings data = assume safe
        }

    # Find this symbol's next earnings
    symbol = symbol.upper()
    for entry in calendar:
        if entry.get("symbol", "").upper() == symbol:
            earnings_date_str = entry.get("date", "")
            if not earnings_date_str:
                continue
            try:
                earnings_date = datetime.strptime(earnings_date_str, "%Y-%m-%d")
                days = (earnings_date - today).days

                if days < 0:
                    continue  # Past earnings

                if days > 30:
                    zone = "CLEAR"
                elif days > 10:
                    zone = "CAUTION"
                elif days > 5:
                    zone = "T5_WARNING"
                else:
                    zone = "BLACKOUT"

                return {
                    "days_to_earnings": days,
                    "earnings_date": earnings_date_str,
                    "zone": zone,
                }
            except ValueError:
                continue

    return {
        "days_to_earnings": None,
        "earnings_date": None,
        "zone": "CLEAR",  # No earnings found in 90 days = safe
    }
