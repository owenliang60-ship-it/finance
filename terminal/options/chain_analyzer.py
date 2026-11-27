"""
Chain Analyzer — Options chain analysis, liquidity assessment, term structure.

Core functions:
- fetch_and_store_chain(): Pull full chain, store in DB, clean old snapshots
- analyze_liquidity(): Assess liquidity quality (EXCELLENT/GOOD/FAIR/POOR/NO_GO)
- get_term_structure(): ATM IV across expirations
- filter_liquid_strikes(): Filter strikes meeting liquidity thresholds
- find_atm_options(): Find ATM call/put for a given expiration
- get_earnings_proximity(): Days to earnings + zone classification
- check_snapshot_freshness(): FRESH / STALE / MISSING gate for a chain snapshot
- compute_25d_skew(): 25-delta put/call IV skew + Cornish-Fisher alpha
"""
from __future__ import annotations

import json
import logging
from datetime import date, datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo

from config.settings import (
    PROJECT_ROOT,
    OPTIONS_CHAIN_DTE_MIN,
    OPTIONS_CHAIN_DTE_MAX,
    OPTIONS_SNAPSHOT_RETAIN_DAYS,
    OPTIONS_LIQUIDITY_MIN_OI,
    OPTIONS_LIQUIDITY_MIN_VOLUME,
    OPTIONS_LIQUIDITY_MAX_SPREAD_PCT,
)

from terminal.options.scenario_analyzer import skew_to_alpha

# 无财报的 ETF：FMP 无未来日期时财报门控为 CLEAR / NOT_APPLICABLE；
# 其余标的无未来日期一律 UNKNOWN（fail-closed）。放此处避免修改 config/settings.py 触发景气 code_version 漂移。
ETF_NO_EARNINGS_SYMBOLS = frozenset({"SPY", "QQQ", "SOXX", "SMH", "IWM", "DIA", "XLK"})
EARNINGS_CONFIRMED_PATH = PROJECT_ROOT / "config" / "earnings_confirmed.json"

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
        store: MarketStore or CompanyStore instance
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
    now = datetime.now()
    today_str = now.strftime("%Y-%m-%d")

    # Convert DTE to date range for API
    from datetime import timedelta
    date_from = (now + timedelta(days=dte_min)).strftime("%Y-%m-%d")
    date_to = (now + timedelta(days=dte_max)).strftime("%Y-%m-%d")
    data = client.get_options_chain(symbol, date_from=date_from, date_to=date_to)
    if not data or data.get("s") != "ok":
        logger.warning("No chain data for %s", symbol)
        return None

    # Parse array-style response into contract dicts
    contracts = parse_chain_response(data, symbol)
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
    count = store.save_options_snapshot(symbol, today_str, contracts)

    # Collect unique expirations
    expirations = sorted(set(c["expiration"] for c in contracts))

    return {
        "symbol": symbol,
        "contract_count": count,
        "expirations": expirations,
        "snapshot_date": today_str,
        "underlying_price": underlying_price,
    }


_EPOCH_MIN = 1_000_000_000  # Unix seconds ~2001-09; smaller numbers are not epochs


def _normalize_expiration(val: Any) -> Any:
    """MarketData.app sends expiration as Unix epoch seconds (16:00 ET).
    Epoch int/float/numeric string -> "YYYY-MM-DD" (America/New_York);
    "YYYY-MM-DD..." string -> first 10 chars; anything else unchanged."""
    if isinstance(val, bool):
        return val
    if isinstance(val, str):
        v = val.strip()
        if v.isdigit():
            val = int(v)
        else:
            return v[:10] if len(v) >= 10 and v[4] == "-" and v[7] == "-" else val
    if isinstance(val, (int, float)) and val >= _EPOCH_MIN:
        return datetime.fromtimestamp(val, ZoneInfo("America/New_York")).strftime("%Y-%m-%d")
    return val


def parse_chain_response(data: Dict, symbol: str) -> List[Dict[str, Any]]:
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
            "expiration": _normalize_expiration(_safe_get(data, "expiration", i, "")),
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


_parse_chain_response = parse_chain_response  # backward-compatible alias


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
    expirations: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """Assess options liquidity quality for a symbol.

    Checks bid-ask spread, OI, volume across ATM strikes. `expirations`
    (optional) limits the check to those expiration dates.

    Returns:
        Dict with verdict (EXCELLENT/GOOD/FAIR/POOR/NO_GO),
        avg_spread_pct, avg_oi, avg_volume, details
    """
    contracts = store.get_options_snapshot(symbol, snapshot_date)
    if contracts and expirations is not None:
        wanted = {str(e) for e in expirations}
        contracts = [c for c in contracts if str(c.get("expiration")) in wanted]
    if not contracts:
        return {
            "verdict": "NO_GO",
            "reason": "No options data available",
            "avg_spread_pct": None,
            "avg_oi": None,
            "avg_volume": None,
        }

    # Find underlying price to filter ATM-adjacent strikes only
    underlying = None
    for c in contracts:
        if c.get("underlying_price"):
            underlying = c["underlying_price"]
            break

    # Filter to ATM-adjacent strikes (±10% of underlying) to avoid OTM dilution
    if underlying and underlying > 0:
        atm_low = underlying * 0.90
        atm_high = underlying * 1.10
        atm_contracts = [c for c in contracts if atm_low <= (c.get("strike") or 0) <= atm_high]
    else:
        atm_contracts = contracts  # Fallback: use all if no underlying

    # Focus on ATM contracts with valid bid/ask data
    valid = []
    for c in atm_contracts:
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
        store: MarketStore or CompanyStore instance
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


def _earnings_zone(days: int) -> str:
    if days > 30:
        return "CLEAR"
    if days > 10:
        return "CAUTION"
    if days > 5:
        return "T5_WARNING"
    return "BLACKOUT"


def _load_confirmed_earnings(path: Path) -> Dict[str, Any]:
    """Official-IR earnings dates. Missing / malformed file -> {} (FMP path
    still applies and stays fail-closed)."""
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except Exception as exc:
        logger.warning("earnings_confirmed unreadable (%s); ignored", type(exc).__name__)
        return {}
    if not isinstance(data, dict):
        logger.warning("earnings_confirmed is not an object; ignored")
        return {}
    return {str(k).upper(): v for k, v in data.items() if isinstance(v, dict)}


def get_earnings_proximity(
    symbol: str,
    fmp_client=None,
    confirmed_path: Optional[Path] = None,
    today_et: Optional[date] = None,
) -> Dict[str, Any]:
    """Get earnings proximity and zone classification.

    Zones (US/Eastern calendar days from today to the next earnings date):
    - CLEAR: > 30 days, or an ETF in ETF_NO_EARNINGS_SYMBOLS with no date
    - CAUTION: 11-30 days
    - T5_WARNING: 6-10 days
    - BLACKOUT: 0-5 days (earnings today counts)
    - UNKNOWN: lookup failed, or a non-ETF with no upcoming date listed.
      UNKNOWN is never safe: verify the date by hand.

    Date priority: config/earnings_confirmed.json (date_status CONFIRMED,
    only when the date is today or later) > FMP per-symbol `earnings`
    endpoint (ESTIMATED). The whole-market calendar endpoint truncates at
    4000 rows and silently drops symbols (issue 044).
    """
    symbol = symbol.upper()
    if symbol in ETF_NO_EARNINGS_SYMBOLS:
        # ETFs never report earnings: no FMP call, no dependence on FMP uptime.
        return {"days_to_earnings": None, "earnings_date": None, "zone": "CLEAR",
                "date_status": "NOT_APPLICABLE", "date_source": "etf_allowlist"}
    today = today_et or datetime.now(ZoneInfo("America/New_York")).date()
    unknown = {"days_to_earnings": None, "earnings_date": None, "zone": "UNKNOWN",
               "date_status": "UNKNOWN", "date_source": None}

    entry = _load_confirmed_earnings(confirmed_path or EARNINGS_CONFIRMED_PATH).get(symbol)
    if entry:
        try:
            confirmed = datetime.strptime(str(entry.get("earnings_date", ""))[:10],
                                          "%Y-%m-%d").date()
        except ValueError:
            confirmed = None
            logger.warning("%s: earnings_confirmed date unparseable; ignored", symbol)
        if confirmed is not None and confirmed >= today:
            days = (confirmed - today).days
            return {
                "days_to_earnings": days,
                "earnings_date": confirmed.strftime("%Y-%m-%d"),
                "zone": _earnings_zone(days),
                "date_status": "CONFIRMED",
                "date_source": entry.get("source") or "earnings_confirmed.json",
            }

    if fmp_client is None:
        try:
            from src.data.fmp_client import fmp_client as _fmp
            fmp_client = _fmp
        except ImportError:
            return unknown

    try:
        rows = fmp_client.get_earnings(symbol)
    except Exception as exc:
        logger.warning("%s: earnings lookup failed, zone UNKNOWN: %s", symbol, exc)
        return unknown
    if not isinstance(rows, list):
        logger.warning("%s: earnings lookup returned no list, zone UNKNOWN", symbol)
        return unknown

    # Find the CLOSEST earnings date that is today or later
    closest_days = None
    closest_date_str = None
    for row in rows:
        earnings_date_str = row.get("date", "") if isinstance(row, dict) else ""
        if not earnings_date_str:
            continue
        try:
            earnings_date = datetime.strptime(earnings_date_str[:10], "%Y-%m-%d").date()
        except ValueError:
            continue
        days = (earnings_date - today).days
        if days < 0:
            continue  # Past earnings
        if closest_days is None or days < closest_days:
            closest_days = days
            closest_date_str = earnings_date_str[:10]

    if closest_days is not None:
        return {
            "days_to_earnings": closest_days,
            "earnings_date": closest_date_str,
            "zone": _earnings_zone(closest_days),
            "date_status": "ESTIMATED",
            "date_source": "fmp",
        }

    if symbol in ETF_NO_EARNINGS_SYMBOLS:
        return {"days_to_earnings": None, "earnings_date": None, "zone": "CLEAR",
                "date_status": "NOT_APPLICABLE", "date_source": None}

    # Non-ETF with no upcoming date (incl. FMP []): not proof of safety.
    logger.warning("%s: no upcoming earnings date listed, zone UNKNOWN", symbol)
    return unknown


def check_snapshot_freshness(
    snapshot_date: Optional[str],
    as_of: Optional[date] = None,
    max_stale_days: int = 3,
) -> Dict[str, Any]:
    """Classify a chain snapshot date as FRESH, STALE or MISSING.

    Calendar days. A missing or unparseable date is MISSING (fail-closed).
    """
    missing = {"status": "MISSING", "snapshot_date": None, "age_days": None}
    if not snapshot_date:
        return missing
    try:
        snap = datetime.strptime(str(snapshot_date), "%Y-%m-%d").date()
    except ValueError:
        logger.warning("Unparseable snapshot_date %r, freshness MISSING", snapshot_date)
        return missing

    ref = as_of or datetime.now().date()
    age_days = (ref - snap).days
    return {
        "status": "FRESH" if age_days <= max_stale_days else "STALE",
        "snapshot_date": snap.strftime("%Y-%m-%d"),
        "age_days": age_days,
    }


def _valid_skew_leg(c: Dict[str, Any], side: str) -> bool:
    return c.get("side") == side and c.get("delta") is not None and (c.get("iv") or 0) > 0


def compute_25d_skew(
    symbol: str,
    store,
    target_dte_min: int = 20,
    target_dte_max: int = 60,
) -> Dict[str, Any]:
    """25-delta skew from the latest snapshot.

    Expiration: closest to 35 DTE inside [target_dte_min, target_dte_max]
    with at least one valid put and call (delta present, iv > 0). No
    expiry outside the window is used: none qualifying -> unavailable.
    """
    contracts = store.get_options_snapshot(symbol)
    if not contracts:
        return {"available": False, "reason": "no_snapshot"}

    by_exp: Dict[str, List[Dict[str, Any]]] = {}
    for c in contracts:
        by_exp.setdefault(c.get("expiration", ""), []).append(c)

    candidates = []  # (expiration, dte, puts, calls)
    for exp, exp_contracts in by_exp.items():
        puts = [c for c in exp_contracts if _valid_skew_leg(c, "put")]
        calls = [c for c in exp_contracts if _valid_skew_leg(c, "call")]
        dte = next((c["dte"] for c in exp_contracts if c.get("dte") is not None), None)
        if puts and calls and dte is not None:
            candidates.append((exp, dte, puts, calls))
    if not candidates:
        return {"available": False, "reason": "no_valid_put_call"}

    in_window = [x for x in candidates if target_dte_min <= x[1] <= target_dte_max]
    if not in_window:
        return {"available": False, "reason": "no_expiry_in_window"}
    exp, dte, puts, calls = min(in_window, key=lambda x: (abs(x[1] - 35), x[1]))

    put_25 = min(puts, key=lambda c: abs(c["delta"] + 0.25))
    call_25 = min(calls, key=lambda c: abs(c["delta"] - 0.25))
    atm = min(puts + calls, key=lambda c: abs(abs(c["delta"]) - 0.50))

    put_iv, call_iv, atm_iv = put_25["iv"], call_25["iv"], atm["iv"]
    alpha = skew_to_alpha(put_iv, call_iv, atm_iv)
    return {
        "available": True,
        "snapshot_date": contracts[0].get("snapshot_date"),
        "expiration": exp,
        "dte": dte,
        "put_iv_25d": put_iv,
        "call_iv_25d": call_iv,
        "atm_iv": atm_iv,
        "skew_25d_pt": round((put_iv - call_iv) * 100, 2),
        "skew_alpha": round(alpha, 4),
    }
