"""
Extended Universe Manager — $10B+ stock list for RS Universe Scan & backtest.

Maintains a cached list of ~949 large-cap US stocks (FMP screener, $10B+ market cap;
post-A1 fix for screener limit truncation, see issue 029). This is a superset of the
pool (~130 stocks) and includes sectors excluded from the pool (Energy, Utilities,
etc.) to enable full-universe RS ranking and backtesting.

The weekly refresh commits DB membership FIRST (`refresh_with_snapshot`):
`extended_membership` is the base-universe SSOT and this JSON file is a cache
rebuilt afterwards, so a stale or missing cache never changes what
`universe_resolver.current_base_universe()` returns (R4-P1-1).

Usage:
    from src.data.extended_universe_manager import (
        refresh_extended_universe,
        get_extended_symbols,
    )
    symbols = refresh_extended_universe()   # Refresh from FMP screener
    all_syms = get_extended_symbols()       # All ~949 symbols (post-A1)

`get_extended_only_symbols()` is deprecated (matrix #8) — the yfinance price
line's targets come from `extended_price_fetcher.get_yfinance_price_targets()`.
"""
import json
import logging
import os
from datetime import date
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

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

# Sanity floor: FMP screener API failure can return [] silently. Raise rather
# than overwrite the cache when returned count < floor (preserves old cache for
# next cron retry). Default 800 = ~84% of A1 刷新后预期 ~949; tune via
# `min_count_floor` kwarg in tests/dev paths.
MIN_COUNT_FLOOR = 800

_CACHE_FILENAME = "extended_universe.json"


def _read_cache() -> Dict:
    """Read extended universe cache file."""
    if not EXTENDED_UNIVERSE_FILE.exists():
        return {}
    with open(EXTENDED_UNIVERSE_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def _cache_path(cache_dir: Optional[Path] = None) -> Path:
    if cache_dir is None:
        return EXTENDED_UNIVERSE_FILE
    return Path(cache_dir) / _CACHE_FILENAME


def _publish_cache(data: Dict, cache_dir: Optional[Path] = None) -> bool:
    """Rebuild the derived cache: tmp file + os.replace, never a partial write.

    Called only AFTER the membership snapshot has committed. A publish
    failure is logged and swallowed on purpose (R4-P1-1): the DB is the
    SSOT, `current_base_universe()` never reads this file, and the next
    refresh rebuilds it. Raising here would falsely report the run as
    failed after the real commit point already succeeded.
    """
    path = _cache_path(cache_dir)
    tmp = path.with_name(path.name + ".tmp")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, path)
        return True
    except Exception as exc:
        logger.warning(
            "extended universe cache publish FAILED (%s: %s) — membership is "
            "already committed to DB, so %s stays stale until the next refresh",
            type(exc).__name__, exc, path,
        )
        try:
            tmp.unlink()
        except Exception:
            pass
        return False


def _resolve_store():
    """Default SSOT store handle (seam: tests stub this to stay off market.db)."""
    from src.data.market_store import get_store
    return get_store()


def refresh_with_snapshot(
    symbols: Iterable[str],
    *,
    store,
    client=None,
    cache_dir: Optional[Path] = None,
    min_count_floor: Optional[int] = None,
    min_mcap_b: Optional[float] = None,
    as_of: Optional[str] = None,
) -> Dict[str, Any]:
    """Commit a fresh screener list as Extended membership (T17, R4-P1-1).

    Order is the semantics:

      1. floor guard — a short screener return raises before ANY write
      2. entrants (list − SM) get their identity resolved (`bootstrap_entrants`)
      3. `record_membership_snapshot(list ∩ eligible)` — THE commit point
      4. only then rebuild `extended_universe.json` (derived cache)

    A failure in 2-3 leaves membership AND the cache on their old state; a
    failure in 4 leaves a stale cache and warns, because the DB has already
    moved and it is the SSOT. Symbols whose identity could not be resolved
    stay in the coverage(identity) repair queue instead of entering
    membership unverified.

    Args:
        symbols: raw screener output (the cache list, not the eligible subset).
        store: MarketStore — SM read, membership write.
        client: FMP-shaped client for entrant profiles; constructed lazily and
            only if there are entrants at all.
        cache_dir: directory to publish the cache into (default: config path).
        min_count_floor: floor guard threshold (default `MIN_COUNT_FLOOR`).
        min_mcap_b: recorded in the cache metadata only.
        as_of: membership effective date (default: today).

    Returns:
        {"symbols", "entrants", "bootstrap", "membership", "as_of", "cache_published"}

    Raises:
        RuntimeError: screener count below floor, or SM empty (run T6 bootstrap
            first — a weekly-only cold start would bake in survivorship bias).
    """
    if min_count_floor is None:
        min_count_floor = MIN_COUNT_FLOOR
    if min_mcap_b is None:
        min_mcap_b = EXTENDED_UNIVERSE_MIN_MCAP_B
    as_of = as_of or date.today().isoformat()

    symbols = sorted({s.strip().upper() for s in symbols if s and s.strip()})

    if len(symbols) < min_count_floor:
        raise RuntimeError(
            f"Refresh aborted: FMP returned {len(symbols)} symbols, "
            f"below floor {min_count_floor}. Old cache preserved."
        )

    known = store.get_security_eligibility()  # fail-loud on empty SM (T3)
    entrants = [s for s in symbols if s not in known]

    bootstrap_summary: Dict[str, Any] = {}
    if entrants:
        from src.data.entrant_bootstrap import bootstrap_entrants
        if client is None:
            from src.data.fmp_client import FMPClient
            client = FMPClient()
        logger.info("Extended refresh: %d entrants need identity resolution", len(entrants))
        bootstrap_summary = bootstrap_entrants(entrants, client=client, store=store)
        eligibility = store.get_security_eligibility()
    else:
        eligibility = known

    members = [s for s in symbols if eligibility.get(s, False)]
    if not members:
        # Mirrors bootstrap's FAIL_EMPTY_ELIGIBLE guard: a screener list that
        # passed the floor but resolves to zero eligible securities means SM
        # is broken, not that the universe emptied. Committing it would exit
        # every member and leave current_base_universe() fail-louding.
        raise RuntimeError(
            f"Refresh aborted: 0 of {len(symbols)} screener symbols are eligible "
            f"in security_master. Refusing to empty extended_membership."
        )
    snapshot = store.record_membership_snapshot(members, as_of=as_of)

    cache = {
        "updated": date.today().isoformat(),
        "min_mcap_b": min_mcap_b,
        "count": len(symbols),
        "symbols": symbols,
    }
    published = _publish_cache(cache, cache_dir)

    logger.info(
        "Extended membership committed (as_of=%s): %d members "
        "(entered=%d exited=%d), cache_published=%s",
        as_of, len(members), len(snapshot["entered"]), len(snapshot["exited"]), published,
    )
    return {
        "symbols": symbols,
        "entrants": entrants,
        "bootstrap": bootstrap_summary,
        "membership": snapshot,
        "as_of": as_of,
        "cache_published": published,
    }


def refresh_extended_universe(
    min_mcap_b: Optional[float] = None,
    min_count_floor: Optional[int] = None,
    *,
    store=None,
    client=None,
    cache_dir: Optional[Path] = None,
    as_of: Optional[str] = None,
) -> List[str]:
    """Refresh extended universe from FMP screener (weekly cron entry).

    Fetches the screener list, then hands it to `refresh_with_snapshot`,
    which commits DB membership before rebuilding the JSON cache.

    Args:
        min_mcap_b: Minimum market cap in billions (default from config).
        min_count_floor: Minimum returned count to accept; below this raises
            RuntimeError without touching the cache file. Default `MIN_COUNT_FLOOR`.
        store / client / cache_dir / as_of: injection seams (default: live store,
            live FMP client, config cache path, today).

    Returns:
        Sorted list of screener symbols (the raw list, not the eligible subset).

    Raises:
        RuntimeError: FMP returned < min_count_floor symbols (API failure mode);
            neither membership nor the cache file is touched.
    """
    from src.data.fmp_client import FMPClient

    if min_mcap_b is None:
        min_mcap_b = EXTENDED_UNIVERSE_MIN_MCAP_B

    min_mcap = int(min_mcap_b * 1_000_000_000)
    logger.info("Refreshing extended universe (market cap >= $%dB)...", int(min_mcap_b))

    if client is None:
        client = FMPClient()
    stocks = client.get_large_cap_stocks(min_mcap)
    symbols = [s["symbol"] for s in stocks if s.get("symbol")]

    if store is None:
        store = _resolve_store()

    result = refresh_with_snapshot(
        symbols, store=store, client=client, cache_dir=cache_dir,
        min_count_floor=min_count_floor, min_mcap_b=min_mcap_b, as_of=as_of,
    )
    logger.info("Extended universe refreshed: %d symbols", len(result["symbols"]))
    return result["symbols"]


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
    """DEPRECATED — forwards to `get_yfinance_price_targets()` (matrix #8).

    "Extended cache minus core pool" stopped being a meaningful set once the
    base universe moved into the DB: the yfinance price line's targets are now
    `current base universe − FMP overlay tier` (matrix #6/#7). This shim keeps
    the remaining callers working until Stop G phase 2 deletes it.

    Returns:
        Sorted list of yfinance price targets.
    """
    import warnings

    warnings.warn(
        "get_extended_only_symbols() is deprecated — use "
        "src.data.extended_price_fetcher.get_yfinance_price_targets(); "
        "removed at Stop G phase 2",
        DeprecationWarning,
        stacklevel=2,
    )
    from src.data.extended_price_fetcher import get_yfinance_price_targets

    return get_yfinance_price_targets()


def get_cache_age_days() -> Optional[int]:
    """Return age of the extended universe cache in days, or None if no cache."""
    cache = _read_cache()
    updated = cache.get("updated")
    if not updated:
        return None
    from datetime import datetime
    updated_date = datetime.strptime(updated, "%Y-%m-%d").date()
    return (date.today() - updated_date).days


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(
        description="Manage extended universe ($10B+ FMP screener) cache"
    )
    parser.add_argument(
        "--refresh",
        action="store_true",
        help="Refresh extended_universe.json from FMP screener (raises if "
             f"returned count < {MIN_COUNT_FLOOR})",
    )
    args = parser.parse_args()

    if args.refresh:
        logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
        symbols = refresh_extended_universe()
        print(f"Extended universe refreshed: {len(symbols)} symbols")
    else:
        parser.print_help()
