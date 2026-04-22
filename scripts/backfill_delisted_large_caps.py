#!/usr/bin/env python3
"""
Backfill delisted large-cap symbols for survivorship-sensitive studies.

This script is intentionally narrow:
- candidate discovery is external/audited
- historical market cap and price are fetched per symbol
- executable overlay only includes symbols that crossed the target cap
  and have price history in the study window
"""

from __future__ import annotations

import argparse
import json
import logging
import sqlite3
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_PROJECT_ROOT))

from src.data.delisted_universe_manager import (
    load_delisted_candidate_registry,
    get_delisted_large_cap_symbols,
    write_delisted_large_caps,
)
from src.data.fmp_client import FMPClient
from src.data.market_store import get_store


logger = logging.getLogger(__name__)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Backfill audited delisted large-cap symbols into market.db",
    )
    parser.add_argument("--symbols", nargs="+", help="Explicit symbol list override")
    parser.add_argument(
        "--from-date",
        default="2021-02-03",
        help="Historical price start date. Use a pre-study buffer so PMARP lookback is available.",
    )
    parser.add_argument(
        "--to-date",
        default=datetime.now().strftime("%Y-%m-%d"),
    )
    parser.add_argument("--mcap-threshold", type=float, default=10e9)
    parser.add_argument(
        "--replace-existing",
        action="store_true",
        help="Delete existing rows for each symbol before rewriting corrected ranges.",
    )
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("-v", "--verbose", action="store_true")
    return parser.parse_args()


def _normalize_symbols(symbols: List[str]) -> List[str]:
    return sorted({symbol.strip().upper() for symbol in symbols if symbol and symbol.strip()})


def _max_market_cap(rows: List[Dict[str, Any]]) -> Optional[float]:
    values = [
        float(row["market_cap"])
        for row in rows
        if row.get("market_cap") is not None
    ]
    if not values:
        return None
    return max(values)


def _candidate_map(loader: Optional[Callable[[], Dict[str, Any]]]) -> Dict[str, Dict[str, Any]]:
    if loader is None:
        registry = load_delisted_candidate_registry()
    else:
        registry = loader()
    return {
        row["symbol"]: row
        for row in registry.get("candidates", [])
        if row.get("symbol")
    }


def _effective_to_date(candidate: Dict[str, Any], requested_to_date: str) -> str:
    delisted_date = candidate.get("delisted_date")
    if isinstance(delisted_date, str) and delisted_date:
        return min(delisted_date, requested_to_date)
    return requested_to_date


def _last_date(rows: List[Dict[str, Any]]) -> Optional[str]:
    dates = [str(row["date"]) for row in rows if row.get("date")]
    return max(dates) if dates else None


def purge_symbol_rows(store, symbol: str) -> None:
    conn = store._get_conn()
    with conn:
        conn.execute("DELETE FROM historical_market_cap WHERE symbol = ?", (symbol,))
        conn.execute("DELETE FROM daily_price WHERE symbol = ?", (symbol,))


def backfill_symbols(
    symbols: List[str],
    from_date: str,
    to_date: str,
    mcap_threshold: float,
    dry_run: bool = False,
    replace_existing: bool = False,
    client: Optional[FMPClient] = None,
    store=None,
    overlay_loader: Optional[Callable[[], List[str]]] = None,
    overlay_writer: Optional[Callable[..., Dict[str, Any]]] = None,
    candidate_registry_loader: Optional[Callable[[], Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    client = client or FMPClient()
    store = store or get_store()
    overlay_loader = overlay_loader or get_delisted_large_cap_symbols
    overlay_writer = overlay_writer or write_delisted_large_caps
    candidates = _candidate_map(candidate_registry_loader)

    normalized = _normalize_symbols(symbols)
    existing_overlay = set(overlay_loader())
    eligible_overlay = set(existing_overlay)

    success: List[str] = []
    below_threshold: List[str] = []
    missing_mcap: List[str] = []
    missing_price: List[str] = []
    details: List[Dict[str, Any]] = []

    for symbol in normalized:
        logger.info("Backfilling %s", symbol)
        candidate = candidates.get(symbol, {})
        effective_to = _effective_to_date(candidate, to_date)

        mcap_rows = client.get_historical_market_cap(symbol, from_date, effective_to)
        if not mcap_rows:
            missing_mcap.append(symbol)
            logger.warning("%s: no historical market cap rows", symbol)
            continue

        price_rows = client.get_historical_price_range(symbol, from_date, effective_to)

        if not dry_run:
            if replace_existing:
                purge_symbol_rows(store, symbol)
            store.upsert_historical_market_cap(symbol, mcap_rows)
            if price_rows:
                store.upsert_daily_prices(symbol, price_rows)

        max_cap = _max_market_cap(mcap_rows)
        row = {
            "symbol": symbol,
            "mcap_rows": len(mcap_rows),
            "price_rows": len(price_rows),
            "max_market_cap": max_cap,
            "effective_to_date": effective_to,
            "last_price_date": _last_date(price_rows),
            "eligible": False,
        }

        if not price_rows:
            missing_price.append(symbol)
            details.append(row)
            logger.warning("%s: no price rows", symbol)
            continue

        if max_cap is None or max_cap < mcap_threshold:
            below_threshold.append(symbol)
            details.append(row)
            logger.info(
                "%s: max market cap %.0f below threshold %.0f",
                symbol,
                max_cap or 0.0,
                mcap_threshold,
            )
            continue

        row["eligible"] = True
        details.append(row)
        success.append(symbol)
        eligible_overlay.add(symbol)
        logger.info("%s: eligible overlay symbol", symbol)

    overlay_payload: Optional[Dict[str, Any]] = None
    if not dry_run:
        overlay_payload = overlay_writer(
            sorted(eligible_overlay),
            metadata={
                "source": "scripts/backfill_delisted_large_caps.py",
                "from_date": from_date,
                "to_date": to_date,
                "mcap_threshold": mcap_threshold,
            },
        )

    return {
        "symbols": normalized,
        "success": success,
        "below_threshold": below_threshold,
        "missing_mcap": missing_mcap,
        "missing_price": missing_price,
        "overlay_symbols": sorted(eligible_overlay),
        "overlay_payload": overlay_payload,
        "details": details,
        "dry_run": dry_run,
    }


def main() -> None:
    args = parse_args()
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    symbols = args.symbols or get_delisted_candidate_symbols()
    if not symbols:
        raise SystemExit("No delisted candidate symbols configured. Pass --symbols or seed the audited registry.")

    summary = backfill_symbols(
        symbols=symbols,
        from_date=args.from_date,
        to_date=args.to_date,
        mcap_threshold=args.mcap_threshold,
        dry_run=args.dry_run,
        replace_existing=args.replace_existing,
    )

    print(json.dumps(summary, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
