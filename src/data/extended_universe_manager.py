"""
Extended Universe Manager — $10B+ stock list for RS Universe Scan & backtest.

Maintains a cached list of ~533 large-cap US stocks (FMP screener, $10B+ market cap).
This is a superset of the pool (~147 stocks) and includes sectors excluded from the pool
(Energy, Utilities, etc.) to enable full-universe RS ranking and backtesting.

Usage:
    from src.data.extended_universe_manager import (
        refresh_extended_universe,
        get_extended_symbols,
        get_extended_only_symbols,
    )
    symbols = refresh_extended_universe()   # Refresh from FMP screener
    all_syms = get_extended_symbols()       # All ~533 symbols
    ext_only = get_extended_only_symbols()  # ~386 symbols NOT in pool
"""
import json
import logging
from datetime import date
from pathlib import Path
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

try:
    from config.settings import (
        EXTENDED_UNIVERSE_FILE,
        EXTENDED_UNIVERSE_MIN_MCAP_B,
    )
except ImportError:
    _PROJECT_ROOT = Path(__file__).parent.parent.parent
    EXTENDED_UNIVERSE_FILE = _PROJECT_ROOT / "data" / "pool" / "extended_universe.json"
    EXTENDED_UNIVERSE_MIN_MCAP_B = 10


def _read_cache() -> Dict:
    """Read extended universe cache file."""
    if not EXTENDED_UNIVERSE_FILE.exists():
        return {}
    with open(EXTENDED_UNIVERSE_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def _write_cache(data: Dict) -> None:
    """Write extended universe cache file."""
    EXTENDED_UNIVERSE_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(EXTENDED_UNIVERSE_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def refresh_extended_universe(
    min_mcap_b: Optional[float] = None,
) -> List[str]:
    """Refresh extended universe from FMP screener.

    Args:
        min_mcap_b: Minimum market cap in billions (default from config).

    Returns:
        Sorted list of symbols.
    """
    from src.data.fmp_client import FMPClient

    if min_mcap_b is None:
        min_mcap_b = EXTENDED_UNIVERSE_MIN_MCAP_B

    min_mcap = int(min_mcap_b * 1_000_000_000)
    logger.info("Refreshing extended universe (market cap >= $%dB)...", int(min_mcap_b))

    client = FMPClient()
    stocks = client.get_large_cap_stocks(min_mcap)
    symbols = sorted(set(s["symbol"] for s in stocks if s.get("symbol")))

    cache = {
        "updated": date.today().isoformat(),
        "min_mcap_b": min_mcap_b,
        "count": len(symbols),
        "symbols": symbols,
    }
    _write_cache(cache)
    logger.info("Extended universe refreshed: %d symbols", len(symbols))
    return symbols


def load_extended_universe() -> Dict:
    """Load the full extended universe cache (metadata + symbols)."""
    return _read_cache()


def get_extended_symbols() -> List[str]:
    """Return all symbols in the extended universe.

    Returns:
        Sorted list of symbols, or empty list if cache doesn't exist.
    """
    cache = _read_cache()
    return cache.get("symbols", [])


def get_extended_only_symbols() -> List[str]:
    """Return symbols in extended universe but NOT in the pool.

    These are the symbols that need yfinance price fetching
    (pool symbols use FMP).

    Returns:
        Sorted list of extended-only symbols.
    """
    from src.data.pool_manager import get_symbols as get_pool_symbols

    extended = set(get_extended_symbols())
    pool = set(get_pool_symbols())
    return sorted(extended - pool)


def get_cache_age_days() -> Optional[int]:
    """Return age of the extended universe cache in days, or None if no cache."""
    cache = _read_cache()
    updated = cache.get("updated")
    if not updated:
        return None
    from datetime import datetime
    updated_date = datetime.strptime(updated, "%Y-%m-%d").date()
    return (date.today() - updated_date).days
