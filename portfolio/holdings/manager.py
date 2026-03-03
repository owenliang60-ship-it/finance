"""
Holdings manager — CRUD operations for portfolio positions.

Data is persisted as JSON in portfolio/holdings/.
Reads price data from Data Desk (data/price/*.csv) for price refresh.
Reads profiles from Data Desk (data/fundamental/profiles.json) for metadata.
"""
import json
import logging
from datetime import datetime
from pathlib import Path
from typing import List, Optional

from portfolio.holdings.schema import (
    Position,
    WatchlistEntry,
    InvestmentBucket,
    OPRMS_DNA_LIMITS,
    OPRMS_TIMING_DEFAULTS,
    OPRMS_TIMING_COEFFICIENTS,
)
from portfolio.holdings.history import log_position_change

logger = logging.getLogger(__name__)

# File paths
_HOLDINGS_DIR = Path(__file__).parent
_HOLDINGS_FILE = _HOLDINGS_DIR / "holdings.json"
_WATCHLIST_FILE = _HOLDINGS_DIR / "watchlist.json"

# Project root (for reading Data Desk files)
_PROJECT_ROOT = Path(__file__).parent.parent.parent


# ---------------------------------------------------------------------------
# Holdings CRUD
# ---------------------------------------------------------------------------

def load_holdings() -> List[Position]:
    """Load all positions from holdings.json."""
    if not _HOLDINGS_FILE.exists():
        return []
    try:
        with open(_HOLDINGS_FILE, "r") as f:
            data = json.load(f)
        return [Position.from_dict(d) for d in data]
    except (json.JSONDecodeError, Exception) as e:
        logger.error(f"Failed to load holdings: {e}")
        return []


def save_holdings(positions: List[Position]) -> None:
    """Persist positions to holdings.json."""
    _HOLDINGS_DIR.mkdir(parents=True, exist_ok=True)
    with open(_HOLDINGS_FILE, "w") as f:
        json.dump([p.to_dict() for p in positions], f, indent=2, ensure_ascii=False)
    logger.info(f"Saved {len(positions)} positions to {_HOLDINGS_FILE}")


def get_position(symbol: str) -> Optional[Position]:
    """Look up a single position by symbol."""
    symbol = symbol.upper()
    for p in load_holdings():
        if p.symbol == symbol:
            return p
    return None


def add_position(position: Position) -> None:
    """Add a new position. Raises ValueError if symbol already exists."""
    positions = load_holdings()
    existing = {p.symbol for p in positions}
    if position.symbol in existing:
        raise ValueError(f"Position {position.symbol} already exists. Use update_position() instead.")

    # Auto-fill metadata from profiles if available
    position = _enrich_from_profile(position)

    # Calculate target weight
    position.target_weight = calculate_target_weight(
        position.dna_rating, position.timing_rating
    )

    positions.append(position)
    save_holdings(positions)
    log_position_change(position.symbol, "OPEN", {
        "shares": position.shares,
        "cost_basis": position.cost_basis,
        "dna_rating": position.dna_rating,
        "timing_rating": position.timing_rating,
        "investment_bucket": position.investment_bucket,
    })


def update_position(symbol: str, **kwargs) -> Optional[Position]:
    """
    Update fields on an existing position.
    Returns the updated Position, or None if not found.
    """
    symbol = symbol.upper()
    positions = load_holdings()

    for i, p in enumerate(positions):
        if p.symbol == symbol:
            old_values = {}
            for key, value in kwargs.items():
                if hasattr(p, key):
                    old_values[key] = getattr(p, key)
                    setattr(p, key, value)

            # Recalculate target weight if ratings changed
            if "dna_rating" in kwargs or "timing_rating" in kwargs:
                p.target_weight = calculate_target_weight(p.dna_rating, p.timing_rating)

            positions[i] = p
            save_holdings(positions)

            # Determine action type for history
            if "shares" in kwargs:
                old_shares = old_values.get("shares", 0)
                new_shares = kwargs["shares"]
                action = "ADD" if new_shares > old_shares else "TRIM"
            elif "dna_rating" in kwargs or "timing_rating" in kwargs:
                action = "RATING_CHANGE"
            else:
                action = "REVIEW"

            log_position_change(symbol, action, {
                "old": old_values,
                "new": kwargs,
            })
            return p

    logger.warning(f"Position {symbol} not found")
    return None


def remove_position(symbol: str) -> Optional[Position]:
    """Remove a position (archives to history). Returns the removed Position."""
    symbol = symbol.upper()
    positions = load_holdings()

    for i, p in enumerate(positions):
        if p.symbol == symbol:
            removed = positions.pop(i)
            save_holdings(positions)
            log_position_change(symbol, "CLOSE", removed.to_dict())
            return removed

    logger.warning(f"Position {symbol} not found for removal")
    return None


def get_positions_by_bucket(bucket: InvestmentBucket) -> List[Position]:
    """Filter positions by investment bucket."""
    return [p for p in load_holdings() if p.investment_bucket == bucket.value]


# ---------------------------------------------------------------------------
# OPRMS Sizing
# ---------------------------------------------------------------------------

def calculate_target_weight(dna_rating: str, timing_rating: str) -> float:
    """
    OPRMS position sizing formula:
        target_weight = DNA_limit * Timing_coefficient

    Uses midpoint of timing range as default coefficient.
    """
    dna_limit = OPRMS_DNA_LIMITS.get(dna_rating, 0.02)
    timing_coeff = OPRMS_TIMING_DEFAULTS.get(timing_rating, 0.2)
    return round(dna_limit * timing_coeff, 4)


def calculate_target_weight_range(dna_rating: str, timing_rating: str) -> tuple:
    """Return (min_weight, max_weight) based on OPRMS timing range."""
    dna_limit = OPRMS_DNA_LIMITS.get(dna_rating, 0.02)
    lo, hi = OPRMS_TIMING_COEFFICIENTS.get(timing_rating, (0.1, 0.3))
    return (round(dna_limit * lo, 4), round(dna_limit * hi, 4))


# ---------------------------------------------------------------------------
# Watchlist
# ---------------------------------------------------------------------------

def load_watchlist() -> List[WatchlistEntry]:
    """Load watchlist entries."""
    if not _WATCHLIST_FILE.exists():
        return []
    try:
        with open(_WATCHLIST_FILE, "r") as f:
            data = json.load(f)
        return [WatchlistEntry.from_dict(d) for d in data]
    except (json.JSONDecodeError, Exception) as e:
        logger.error(f"Failed to load watchlist: {e}")
        return []


def save_watchlist(entries: List[WatchlistEntry]) -> None:
    """Persist watchlist entries."""
    _HOLDINGS_DIR.mkdir(parents=True, exist_ok=True)
    with open(_WATCHLIST_FILE, "w") as f:
        json.dump([e.to_dict() for e in entries], f, indent=2, ensure_ascii=False)


def add_to_watchlist(entry: WatchlistEntry) -> None:
    """Add a stock to watchlist."""
    entries = load_watchlist()
    existing = {e.symbol for e in entries}
    if entry.symbol in existing:
        raise ValueError(f"{entry.symbol} already on watchlist")
    entry = _enrich_watchlist_from_profile(entry)
    entries.append(entry)
    save_watchlist(entries)


def remove_from_watchlist(symbol: str) -> Optional[WatchlistEntry]:
    """Remove a stock from watchlist."""
    symbol = symbol.upper()
    entries = load_watchlist()
    for i, e in enumerate(entries):
        if e.symbol == symbol:
            removed = entries.pop(i)
            save_watchlist(entries)
            return removed
    return None


# ---------------------------------------------------------------------------
# Price Refresh
# ---------------------------------------------------------------------------

def refresh_prices(positions: Optional[List[Position]] = None) -> List[Position]:
    """
    Update current_price for all positions via get_price_df (market.db → CSV fallback).
    Also recalculates current_weight based on portfolio total value.
    """
    if positions is None:
        positions = load_holdings()

    if not positions:
        return positions

    from src.data.price_fetcher import get_price_df

    for p in positions:
        try:
            df = get_price_df(p.symbol, days=1, max_age_days=0)
            if df is not None and not df.empty:
                p.current_price = float(df["close"].iloc[0])
        except Exception as e:
            logger.warning(f"Failed to read price for {p.symbol}: {e}")

    # Recalculate weights
    total_value = sum(p.market_value for p in positions)
    if total_value > 0:
        for p in positions:
            p.current_weight = round(p.market_value / total_value, 6)

    return positions


# ---------------------------------------------------------------------------
# Portfolio Summary
# ---------------------------------------------------------------------------

def get_portfolio_value(positions: Optional[List[Position]] = None) -> float:
    """Total market value of all positions."""
    if positions is None:
        positions = load_holdings()
    return sum(p.market_value for p in positions)


def get_portfolio_summary(positions: Optional[List[Position]] = None) -> dict:
    """Quick summary of the portfolio."""
    if positions is None:
        positions = load_holdings()

    positions = refresh_prices(positions)
    total_value = get_portfolio_value(positions)
    total_cost = sum(p.shares * p.cost_basis for p in positions)
    total_pnl = total_value - total_cost

    return {
        "total_positions": len(positions),
        "total_value": total_value,
        "total_cost": total_cost,
        "total_pnl": total_pnl,
        "total_pnl_pct": total_pnl / total_cost if total_cost > 0 else 0.0,
        "by_bucket": _count_by_bucket(positions),
        "by_dna": _count_by_field(positions, "dna_rating"),
        "positions": [p.to_dict() for p in positions],
    }


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _enrich_from_profile(position: Position) -> Position:
    """Fill company_name, sector, industry from Data Desk profiles."""
    profiles_path = _PROJECT_ROOT / "data" / "fundamental" / "profiles.json"
    if not profiles_path.exists():
        return position
    try:
        with open(profiles_path, "r") as f:
            profiles = json.load(f)
        profile = profiles.get(position.symbol)
        if profile:
            if not position.company_name:
                position.company_name = profile.get("companyName", "")
            if not position.sector:
                position.sector = profile.get("sector", "")
            if not position.industry:
                position.industry = profile.get("industry", "")
    except Exception as e:
        logger.warning(f"Failed to enrich profile for {position.symbol}: {e}")
    return position


def _enrich_watchlist_from_profile(entry: WatchlistEntry) -> WatchlistEntry:
    """Fill metadata from Data Desk profiles for watchlist entry."""
    profiles_path = _PROJECT_ROOT / "data" / "fundamental" / "profiles.json"
    if not profiles_path.exists():
        return entry
    try:
        with open(profiles_path, "r") as f:
            profiles = json.load(f)
        profile = profiles.get(entry.symbol)
        if profile:
            if not entry.company_name:
                entry.company_name = profile.get("companyName", "")
            if not entry.sector:
                entry.sector = profile.get("sector", "")
            if not entry.industry:
                entry.industry = profile.get("industry", "")
    except Exception as e:
        logger.warning(f"Failed to enrich profile for {entry.symbol}: {e}")
    return entry


def _count_by_bucket(positions: List[Position]) -> dict:
    """Count positions and value by investment bucket."""
    result = {}
    for p in positions:
        bucket = p.investment_bucket
        if bucket not in result:
            result[bucket] = {"count": 0, "value": 0.0}
        result[bucket]["count"] += 1
        result[bucket]["value"] += p.market_value
    return result


def _count_by_field(positions: List[Position], field: str) -> dict:
    """Count positions by an arbitrary field."""
    result = {}
    for p in positions:
        key = getattr(p, field, "Unknown")
        if key not in result:
            result[key] = 0
        result[key] += 1
    return result
