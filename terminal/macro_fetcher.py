"""
Macro data fetcher — collects FRED data and builds MacroSnapshot.

fetch_macro_snapshot()  → API calls, returns MacroSnapshot
get_macro_snapshot()    → reads cache, refetches if stale

Cache: data/macro/macro_snapshot.json
Expiry: 4h trading days, 12h non-trading days
"""
import json
import logging
import os
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import requests

from terminal.macro_snapshot import MacroSnapshot

logger = logging.getLogger(__name__)

# Cache config
MACRO_DIR = Path(__file__).parent.parent / "data" / "macro"
CACHE_FILE = MACRO_DIR / "macro_snapshot.json"
CACHE_TTL_TRADING = timedelta(hours=4)
CACHE_TTL_NON_TRADING = timedelta(hours=12)

# FRED API base
FRED_BASE_URL = "https://api.stlouisfed.org/fred/series/observations"


# ---------------------------------------------------------------------------
# Low-level FRED fetch (reuses BaseFREDTool pattern but standalone)
# ---------------------------------------------------------------------------

def _get_api_key() -> Optional[str]:
    """Get FRED API key from environment."""
    return os.getenv("FRED_API_KEY")


def _fetch_series(series_id: str, limit: int = 60) -> List[Dict]:
    """
    Fetch a FRED series. Returns list of {date, value} dicts, newest first.

    Args:
        series_id: FRED series ID
        limit: Number of observations (default 60 for 30d trend calc)
    """
    api_key = _get_api_key()
    if not api_key:
        logger.warning("FRED_API_KEY not set, skipping %s", series_id)
        return []

    params = {
        "series_id": series_id,
        "api_key": api_key,
        "file_type": "json",
        "limit": limit,
        "sort_order": "desc",
    }

    try:
        response = requests.get(FRED_BASE_URL, params=params, timeout=10)
        response.raise_for_status()
        data = response.json()

        return [
            {"date": obs["date"], "value": float(obs["value"])}
            for obs in data.get("observations", [])
            if obs["value"] != "."
        ]
    except Exception as e:
        logger.warning("FRED fetch %s failed: %s", series_id, e)
        return []


def _latest_value(series: List[Dict]) -> Optional[float]:
    """Extract the latest (index 0) value from a FRED series."""
    return series[0]["value"] if series else None


def _trend_bp(series: List[Dict], lookback: int = 30) -> Optional[int]:
    """
    Calculate basis-point change over lookback observations.
    series[0] is newest, series[lookback] is ~30 days ago.
    """
    if len(series) > lookback:
        current = series[0]["value"]
        past = series[lookback]["value"]
        return round((current - past) * 100)
    return None


def _trend_raw(series: List[Dict], lookback: int = 30) -> Optional[float]:
    """Calculate raw change over lookback observations."""
    if len(series) > lookback:
        return round(series[0]["value"] - series[lookback]["value"], 2)
    return None


# ---------------------------------------------------------------------------
# Fetch groups (organized by domain)
# ---------------------------------------------------------------------------

def _fetch_yield_curve() -> Dict[str, List[Dict]]:
    """Fetch yield curve data: 2Y, 5Y, 10Y, 30Y, spreads."""
    return {
        "DGS2": _fetch_series("DGS2"),
        "DGS5": _fetch_series("DGS5"),
        "DGS10": _fetch_series("DGS10"),
        "DGS30": _fetch_series("DGS30"),
        "T10Y2Y": _fetch_series("T10Y2Y"),
        "T10Y3M": _fetch_series("T10Y3M"),
    }


def _fetch_fed_inflation() -> Dict[str, List[Dict]]:
    """Fetch Fed funds rate and CPI index (for YoY computation)."""
    return {
        "FEDFUNDS": _fetch_series("FEDFUNDS", limit=12),
        # CPIAUCSL is the raw CPI index; we compute YoY in _build_cpi_yoy()
        "CPIAUCSL": _fetch_series("CPIAUCSL", limit=15),
    }


def _fetch_economy() -> Dict[str, List[Dict]]:
    """Fetch GDP and unemployment."""
    return {
        "A191RL1Q225SBEA": _fetch_series("A191RL1Q225SBEA", limit=8),
        "UNRATE": _fetch_series("UNRATE", limit=12),
    }


def _fetch_volatility() -> Dict[str, List[Dict]]:
    """Fetch VIX and HY spread."""
    return {
        "VIXCLS": _fetch_series("VIXCLS"),
        "BAMLH0A0HYM2": _fetch_series("BAMLH0A0HYM2"),
    }


def _fetch_dollar() -> Dict[str, List[Dict]]:
    """Fetch DXY index."""
    return {
        "DTWEXBGS": _fetch_series("DTWEXBGS"),
    }


def _fetch_international() -> Dict[str, List[Dict]]:
    """Fetch Japan rate and USD/JPY."""
    return {
        "IRSTCI01JPM156N": _fetch_series("IRSTCI01JPM156N", limit=12),
        "DEXJPUS": _fetch_series("DEXJPUS"),
    }


def _fetch_liquidity() -> Dict[str, List[Dict]]:
    """Fetch Fed balance sheet."""
    return {
        "WALCL": _fetch_series("WALCL", limit=12),
    }


# ---------------------------------------------------------------------------
# Derived value computation
# ---------------------------------------------------------------------------

def _compute_cpi_yoy(series: List[Dict]) -> Optional[float]:
    """Compute CPI YoY% from CPIAUCSL index: (latest / 12mo_ago - 1) * 100."""
    if len(series) >= 13:
        current = series[0]["value"]
        year_ago = series[12]["value"]
        if year_ago > 0:
            return round((current / year_ago - 1) * 100, 1)
    return None


def _classify_vix(vix: Optional[float]) -> str:
    """Classify VIX into regime bucket."""
    if vix is None:
        return "UNKNOWN"
    if vix < 15:
        return "LOW"
    if vix < 25:
        return "NORMAL"
    if vix < 35:
        return "ELEVATED"
    return "PANIC"


def _classify_dxy_trend(series: List[Dict]) -> str:
    """Classify DXY trend: compare latest vs 50-day SMA."""
    if len(series) < 2:
        return "UNKNOWN"
    current = series[0]["value"]
    # Use up to 50 observations for SMA
    sma_window = min(len(series), 50)
    sma = sum(obs["value"] for obs in series[:sma_window]) / sma_window
    pct_diff = (current - sma) / sma * 100
    if pct_diff > 1.0:
        return "STRENGTHENING"
    if pct_diff < -1.0:
        return "WEAKENING"
    return "STABLE"


# ---------------------------------------------------------------------------
# Regime assessment (deterministic decision tree)
# ---------------------------------------------------------------------------

def _assess_regime(snapshot: MacroSnapshot) -> Tuple[str, str, str]:
    """
    Assess market regime from snapshot data.

    Returns:
        (regime, confidence, rationale)
    """
    reasons = []

    # CRISIS check (highest priority)
    if snapshot.vix is not None and snapshot.vix > 45:
        return "CRISIS", _confidence(snapshot), f"VIX at {snapshot.vix:.1f} > 45"

    if (snapshot.vix is not None and snapshot.vix > 35
            and snapshot.spread_10y_2y is not None and snapshot.spread_10y_2y < -0.5):
        return ("CRISIS", _confidence(snapshot),
                f"VIX {snapshot.vix:.1f} > 35 + deep curve inversion ({snapshot.spread_10y_2y:+.2f}%)")

    # RISK_OFF check
    risk_off_signals = 0

    if (snapshot.vix is not None and snapshot.vix > 25
            and snapshot.spread_10y_2y is not None and snapshot.spread_10y_2y < 0):
        risk_off_signals += 1
        reasons.append(f"VIX elevated ({snapshot.vix:.1f}) + curve inverted ({snapshot.spread_10y_2y:+.2f}%)")

    if (snapshot.gdp_growth is not None and snapshot.gdp_growth < 0):
        risk_off_signals += 1
        reasons.append(f"GDP contracting ({snapshot.gdp_growth:.1f}%)")

    if (snapshot.hy_spread is not None and snapshot.hy_spread > 5.0):
        risk_off_signals += 1
        reasons.append(f"HY spread wide ({snapshot.hy_spread:.0f}bp)")

    if risk_off_signals > 0:
        return "RISK_OFF", _confidence(snapshot), "; ".join(reasons)

    # RISK_ON check
    if (snapshot.vix is not None and snapshot.vix < 18
            and snapshot.spread_10y_2y is not None and snapshot.spread_10y_2y > 0.5
            and snapshot.gdp_growth is not None and snapshot.gdp_growth > 2.0):
        return ("RISK_ON", _confidence(snapshot),
                f"Low VIX ({snapshot.vix:.1f}), positive curve ({snapshot.spread_10y_2y:+.2f}%), strong GDP ({snapshot.gdp_growth:.1f}%)")

    # Default NEUTRAL
    rationale_parts = []
    if snapshot.vix is not None:
        rationale_parts.append(f"VIX {snapshot.vix:.1f}")
    if snapshot.spread_10y_2y is not None:
        rationale_parts.append(f"10Y-2Y {snapshot.spread_10y_2y:+.2f}%")
    if snapshot.gdp_growth is not None:
        rationale_parts.append(f"GDP {snapshot.gdp_growth:.1f}%")
    rationale = "Mixed signals: " + ", ".join(rationale_parts) if rationale_parts else "Insufficient data"

    return "NEUTRAL", _confidence(snapshot), rationale


def _confidence(snapshot: MacroSnapshot) -> str:
    """Determine confidence from data source count."""
    count = snapshot.data_source_count
    if count >= 8:
        return "high"
    if count >= 4:
        return "medium"
    return "low"


# ---------------------------------------------------------------------------
# Main fetch + build
# ---------------------------------------------------------------------------

def fetch_macro_snapshot() -> MacroSnapshot:
    """
    Fetch all FRED data and build a MacroSnapshot.

    Makes ~16 API calls. FRED allows 120 req/min, completes in <10 seconds.
    """
    logger.info("Fetching macro snapshot from FRED...")

    # Fetch all groups
    raw = {}
    raw.update(_fetch_yield_curve())
    raw.update(_fetch_fed_inflation())
    raw.update(_fetch_economy())
    raw.update(_fetch_volatility())
    raw.update(_fetch_dollar())
    raw.update(_fetch_international())
    raw.update(_fetch_liquidity())

    fetched_count = sum(1 for v in raw.values() if v)
    logger.info("FRED fetch complete: %d/%d series with data", fetched_count, len(raw))

    # Build snapshot
    snapshot = MacroSnapshot(
        fetched_at=datetime.now().isoformat(),

        # Yield curve
        us2y=_latest_value(raw.get("DGS2", [])),
        us5y=_latest_value(raw.get("DGS5", [])),
        us10y=_latest_value(raw.get("DGS10", [])),
        us30y=_latest_value(raw.get("DGS30", [])),
        spread_10y_2y=_latest_value(raw.get("T10Y2Y", [])),
        spread_10y_3m=_latest_value(raw.get("T10Y3M", [])),

        # Fed & inflation
        fed_funds=_latest_value(raw.get("FEDFUNDS", [])),
        cpi_yoy=_compute_cpi_yoy(raw.get("CPIAUCSL", [])),

        # Economy
        gdp_growth=_latest_value(raw.get("A191RL1Q225SBEA", [])),
        unemployment=_latest_value(raw.get("UNRATE", [])),

        # Volatility
        vix=_latest_value(raw.get("VIXCLS", [])),
        hy_spread=_latest_value(raw.get("BAMLH0A0HYM2", [])),

        # Dollar
        dxy=_latest_value(raw.get("DTWEXBGS", [])),

        # International
        japan_rate=_latest_value(raw.get("IRSTCI01JPM156N", [])),
        usdjpy=_latest_value(raw.get("DEXJPUS", [])),

        # Liquidity (convert millions to trillions)
        fed_balance_sheet_t=(
            round(_latest_value(raw.get("WALCL", [])) / 1_000_000, 2)
            if _latest_value(raw.get("WALCL", [])) is not None
            else None
        ),
    )

    # Derived: term premium
    if snapshot.us30y is not None and snapshot.us2y is not None:
        snapshot.term_premium = round(snapshot.us30y - snapshot.us2y, 2)

    # Derived: real rate
    if snapshot.us10y is not None and snapshot.cpi_yoy is not None:
        snapshot.real_rate_10y = round(snapshot.us10y - snapshot.cpi_yoy, 1)

    # Trends (basis points for rates)
    snapshot.us2y_30d_chg_bp = _trend_bp(raw.get("DGS2", []))
    snapshot.us5y_30d_chg_bp = _trend_bp(raw.get("DGS5", []))
    snapshot.us10y_30d_chg_bp = _trend_bp(raw.get("DGS10", []))
    snapshot.us30y_30d_chg_bp = _trend_bp(raw.get("DGS30", []))
    snapshot.vix_30d_chg = _trend_raw(raw.get("VIXCLS", []))
    snapshot.dxy_30d_chg = _trend_raw(raw.get("DTWEXBGS", []))
    snapshot.usdjpy_30d_chg = _trend_raw(raw.get("DEXJPUS", []))
    snapshot.hy_spread_30d_chg = _trend_raw(raw.get("BAMLH0A0HYM2", []))

    # Fed BS 30d % change (weekly data, ~4 weeks ≈ 30 days)
    walcl = raw.get("WALCL", [])
    if len(walcl) > 4:
        current_bs = walcl[0]["value"]
        past_bs = walcl[min(4, len(walcl) - 1)]["value"]
        if past_bs > 0:
            snapshot.fed_bs_30d_chg_pct = round((current_bs - past_bs) / past_bs * 100, 2)

    # Classify VIX regime
    snapshot.vix_regime = _classify_vix(snapshot.vix)

    # Classify DXY trend
    snapshot.dxy_trend = _classify_dxy_trend(raw.get("DTWEXBGS", []))

    # Regime assessment
    snapshot.regime, snapshot.regime_confidence, snapshot.regime_rationale = _assess_regime(snapshot)

    # Save cache
    _save_cache(snapshot)

    logger.info(
        "Macro snapshot built: %d sources, regime=%s (%s)",
        snapshot.data_source_count, snapshot.regime, snapshot.regime_confidence,
    )

    return snapshot


# ---------------------------------------------------------------------------
# Cache management
# ---------------------------------------------------------------------------

def _save_cache(snapshot: MacroSnapshot) -> None:
    """Save snapshot to JSON cache file."""
    MACRO_DIR.mkdir(parents=True, exist_ok=True)
    with open(CACHE_FILE, "w") as f:
        f.write(snapshot.to_json())


def _load_cache() -> Optional[MacroSnapshot]:
    """Load snapshot from JSON cache file."""
    if not CACHE_FILE.exists():
        return None
    try:
        with open(CACHE_FILE, "r") as f:
            return MacroSnapshot.from_json(f.read())
    except Exception as e:
        logger.warning("Cache load failed: %s", e)
        return None


def _is_trading_day() -> bool:
    """Check if today is a US trading day (Mon-Fri, no holiday check)."""
    return datetime.now().weekday() < 5


def _cache_is_fresh(snapshot: MacroSnapshot) -> bool:
    """Check if cached snapshot is still fresh."""
    if not snapshot.fetched_at:
        return False
    try:
        fetched = datetime.fromisoformat(snapshot.fetched_at)
        ttl = CACHE_TTL_TRADING if _is_trading_day() else CACHE_TTL_NON_TRADING
        return datetime.now() - fetched < ttl
    except (ValueError, TypeError):
        return False


def get_macro_snapshot() -> Optional[MacroSnapshot]:
    """
    Get macro snapshot, using cache if fresh.

    Returns None only if both cache and API fail.
    """
    cached = _load_cache()
    if cached and _cache_is_fresh(cached):
        logger.debug("Using cached macro snapshot from %s", cached.fetched_at)
        return cached

    try:
        return fetch_macro_snapshot()
    except Exception as e:
        logger.error("Macro snapshot fetch failed: %s", e)
        # Return stale cache as fallback
        if cached:
            logger.warning("Returning stale cached snapshot from %s", cached.fetched_at)
            return cached
        return None
