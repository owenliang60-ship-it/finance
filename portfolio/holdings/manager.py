"""Portfolio holdings manager — SQLite-backed via CompanyStore.

Data is persisted in company.db (holdings / transactions / portfolio_cash tables).
Reads company profiles and OPRMS ratings from CompanyStore for enrichment.
"""
from __future__ import annotations

import json
import logging
from typing import Dict, List, Optional

from portfolio.holdings.schema import (
    Position,
    WatchlistEntry,
    InvestmentBucket,
    OPRMS_DNA_LIMITS,
    OPRMS_TIMING_DEFAULTS,
    OPRMS_TIMING_COEFFICIENTS,
)

logger = logging.getLogger(__name__)

# File paths (kept for watchlist — still JSON-based)
from pathlib import Path
_HOLDINGS_DIR = Path(__file__).parent
_WATCHLIST_FILE = _HOLDINGS_DIR / "watchlist.json"


# ---------------------------------------------------------------------------
# PortfolioManager — SQLite-backed core
# ---------------------------------------------------------------------------

class PortfolioManager:
    """Manages holdings, cash, and NAV — backed by company.db."""

    def __init__(self, store=None):
        if store is None:
            from terminal.company_store import get_store
            store = get_store()
        self._store = store

    # ---- Holdings CRUD ----

    def load_holdings(self) -> List[Position]:
        """Load all OPEN holdings, enriched with company + OPRMS data."""
        rows = self._store.get_all_open_holdings()
        return [self._enrich(row) for row in rows]

    def get_position(self, symbol: str) -> Optional[Position]:
        row = self._store.get_open_holding(symbol)
        if not row:
            return None
        return self._enrich(row)

    def add_position(self, symbol: str, shares: float, avg_cost: float,
                     date: str) -> Position:
        pid = self._store.insert_holding(symbol, shares=shares, avg_cost=avg_cost,
                                          open_date=date)
        self._store.insert_transaction(pid, symbol, "BUY", shares=shares,
                                        price=avg_cost, date=date)
        return self.get_position(symbol)

    def close_position(self, symbol: str, sell_price: float, date: str) -> float:
        row = self._store.get_open_holding(symbol)
        if not row:
            raise ValueError(f"No open position for {symbol}")
        pid = row["position_id"]
        realized = (sell_price - row["avg_cost"]) * row["shares"]
        self._store.insert_transaction(pid, symbol, "SELL", shares=row["shares"],
                                        price=sell_price, date=date)
        self._store.close_holding(pid, close_date=date, realized_pnl=realized)
        return realized

    # ---- NAV & Weights ----

    def get_total_nav(self, prices: Dict[str, float]) -> float:
        holdings = self._store.get_all_open_holdings()
        invested = sum(h["shares"] * prices.get(h["symbol"], 0) for h in holdings)
        cash = self._store.get_cash_balance()
        return invested + cash

    def refresh_prices(self, prices: Dict[str, float]) -> List[Position]:
        """Load holdings, apply prices, compute weights based on total_NAV."""
        positions = self.load_holdings()
        nav = self.get_total_nav(prices)
        for p in positions:
            p.current_price = prices.get(p.symbol, 0)
            p.current_weight = (p.market_value / nav) if nav > 0 else 0
        return positions

    def get_portfolio_summary(self, prices: Dict[str, float]) -> Dict:
        positions = self.refresh_prices(prices)
        cash = self._store.get_cash_balance()
        nav = self.get_total_nav(prices)
        invested = nav - cash
        total_cost = sum(p.shares * p.cost_basis for p in positions)
        total_pnl = invested - total_cost
        return {
            "total_nav": nav,
            "total_value": invested,       # legacy field: invested value (no cash)
            "total_cost": total_cost,       # legacy field: total cost basis
            "invested_value": invested,
            "cash": cash,
            "invested_pct": invested / nav if nav > 0 else 0,
            "cash_pct": cash / nav if nav > 0 else 0,
            "total_positions": len(positions),
            "total_pnl": total_pnl,
            "total_pnl_pct": total_pnl / total_cost if total_cost > 0 else 0,
            "by_bucket": _count_by_bucket(positions),
            "by_dna": _count_by_field(positions, "dna_rating"),
            "positions": [p.to_dict() for p in positions],
        }

    # ---- Enrichment ----

    def _enrich(self, row: Dict) -> Position:
        symbol = row["symbol"]
        company = self._store.get_company(symbol) or {}
        oprms = self._store.get_current_oprms(symbol) or {}
        kc = self._store.get_kill_conditions(symbol, active_only=True)

        return Position(
            symbol=symbol,
            company_name=company.get("company_name", ""),
            sector=company.get("sector", ""),
            industry=company.get("industry", ""),
            dna_rating=oprms.get("dna", ""),
            timing_rating=oprms.get("timing", ""),
            cost_basis=row["avg_cost"],
            shares=row["shares"],
            entry_date=row["open_date"],
            kill_conditions=[c["description"] for c in kc],
            position_id=row["position_id"],
            status=row["status"],
            close_date=row.get("close_date"),
            realized_pnl=row.get("realized_pnl"),
        )


# ---------------------------------------------------------------------------
# Backward-compatible module-level API (shim layer)
# ---------------------------------------------------------------------------
# Existing callers (commands.py, monitor.py, benchmark/review.py, __init__.py)
# continue through these functions, no import changes needed.

_default_mgr: Optional[PortfolioManager] = None


def _get_default_mgr() -> PortfolioManager:
    global _default_mgr
    if _default_mgr is None:
        _default_mgr = PortfolioManager()
    return _default_mgr


def load_holdings() -> List[Position]:
    return _get_default_mgr().load_holdings()


def get_position(symbol: str) -> Optional[Position]:
    return _get_default_mgr().get_position(symbol)


def save_holdings(positions: List[Position]) -> None:
    """No-op: SQLite persistence is automatic. Kept for backward compat."""
    pass


def add_position(position: Position) -> None:
    _get_default_mgr().add_position(
        position.symbol, shares=position.shares,
        avg_cost=position.cost_basis, date=position.entry_date or "",
    )


def update_position(symbol: str, **kwargs) -> Optional[Position]:
    p = _get_default_mgr().get_position(symbol)
    if p and p.position_id:
        _get_default_mgr()._store.update_holding(p.position_id, **kwargs)
    return p


def remove_position(symbol: str) -> Optional[Position]:
    """Close position (shim — does not calculate realized P&L)."""
    from datetime import date
    p = _get_default_mgr().get_position(symbol)
    if p and p.position_id:
        _get_default_mgr()._store.close_holding(
            p.position_id, close_date=date.today().isoformat(), realized_pnl=0.0
        )
    return p


def _fetch_latest_prices(symbols: List[str]) -> Dict[str, float]:
    """Fetch latest prices from market.db. Returns descending df, iloc[0] = newest."""
    from src.data.price_fetcher import get_price_df
    prices = {}
    for sym in symbols:
        try:
            df = get_price_df(sym, days=5, max_age_days=0)
            if df is not None and not df.empty:
                prices[sym] = df["close"].iloc[0]
        except Exception:
            pass
    return prices


def refresh_prices(positions: Optional[List[Position]] = None) -> List[Position]:
    """Backward-compatible: fetch prices internally, return updated positions."""
    if positions is None:
        positions = load_holdings()
    if not positions:
        return positions
    prices = _fetch_latest_prices([p.symbol for p in positions])
    return _get_default_mgr().refresh_prices(prices)


def get_portfolio_value(positions: Optional[List[Position]] = None) -> float:
    """Sum of market values (no cash). Kept for benchmark compat."""
    if positions is None:
        positions = load_holdings()
    return sum(p.market_value for p in positions)


def get_portfolio_summary(positions: Optional[List[Position]] = None) -> dict:
    """Backward-compatible: uses refresh_prices internally."""
    if positions is None:
        positions = load_holdings()
    if not positions:
        return {"total_positions": 0, "message": "No holdings found."}
    prices = _fetch_latest_prices([p.symbol for p in positions])
    return _get_default_mgr().get_portfolio_summary(prices)


def calculate_target_weight(dna_rating: str, timing_rating: str) -> float:
    """Pure calculation — no DB access needed."""
    dna_limit = OPRMS_DNA_LIMITS.get(dna_rating, 0.02)
    timing_coeff = OPRMS_TIMING_DEFAULTS.get(timing_rating, 0.3)
    return round(dna_limit * timing_coeff, 4)


def calculate_target_weight_range(dna_rating: str, timing_rating: str) -> tuple:
    """Return (min_weight, max_weight) based on OPRMS timing range."""
    dna_limit = OPRMS_DNA_LIMITS.get(dna_rating, 0.02)
    lo, hi = OPRMS_TIMING_COEFFICIENTS.get(timing_rating, (0.1, 0.3))
    return (round(dna_limit * lo, 4), round(dna_limit * hi, 4))


def get_positions_by_bucket(bucket: InvestmentBucket) -> List[Position]:
    """Filter positions by investment bucket."""
    return [p for p in load_holdings() if p.investment_bucket == bucket.value]


# ---------------------------------------------------------------------------
# Watchlist (still JSON-based)
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
# Internal helpers
# ---------------------------------------------------------------------------

def _count_by_bucket(positions: List[Position]) -> dict:
    result = {}
    for p in positions:
        bucket = p.investment_bucket
        if bucket not in result:
            result[bucket] = {"count": 0, "value": 0.0}
        result[bucket]["count"] += 1
        result[bucket]["value"] += p.market_value
    return result


def _count_by_field(positions: List[Position], field_name: str) -> dict:
    result = {}
    for p in positions:
        key = getattr(p, field_name, "Unknown")
        if key not in result:
            result[key] = 0
        result[key] += 1
    return result
