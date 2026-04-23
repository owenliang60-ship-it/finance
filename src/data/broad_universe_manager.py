"""Broad universe bootstrap manager.

Seed = yf.screen($500M+) U existing daily_price symbols U broad scan hits.
Final universe = seed symbols whose max historical market cap ever crossed $1B.
"""

from __future__ import annotations

import argparse
import json
import logging
import sqlite3
from datetime import date
from pathlib import Path
from typing import Dict, Iterable, List, Set

from config.settings import (
    BROAD_PRICE_BACKFILL_START,
    BROAD_UNIVERSE_FILE,
    BROAD_UNIVERSE_MAX_COUNT,
    BROAD_UNIVERSE_MIN_COUNT,
    BROAD_UNIVERSE_MIN_MCAP_USD,
    BROAD_UNIVERSE_PAGE_SIZE,
    BROAD_UNIVERSE_SEED_FILE,
    BROAD_UNIVERSE_SEED_MAX_COUNT,
    BROAD_UNIVERSE_SEED_MIN_COUNT,
    BROAD_UNIVERSE_SEED_MIN_MCAP_USD,
    MARKET_DB_PATH,
    SCANS_DIR,
)

logger = logging.getLogger(__name__)

_SQLITE_CHUNK = 500
_PRICE_SUFFICIENT_ROWS = 1000


def _read_json(path: Path) -> Dict:
    if not path.exists():
        return {}
    return json.loads(path.read_text())


def _write_json(path: Path, payload: Dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False))


def _fetch_yfscreen_dedup(min_mcap_usd: int) -> Set[str]:
    import yfinance as yf

    query = yf.EquityQuery(
        "and",
        [
            yf.EquityQuery("gte", ["intradaymarketcap", min_mcap_usd]),
            yf.EquityQuery("is-in", ["exchange", "NMS", "NYQ"]),
        ],
    )
    seen: Dict[str, Dict] = {}
    offset = 0
    for _ in range(60):
        result = yf.screen(
            query,
            offset=offset,
            size=BROAD_UNIVERSE_PAGE_SIZE,
            sortField="intradaymarketcap",
            sortAsc=False,
        )
        quotes = result.get("quotes", [])
        if not quotes:
            break
        for quote in quotes:
            symbol = quote.get("symbol")
            quote_type = quote.get("quoteType")
            if symbol and symbol not in seen and (not quote_type or quote_type == "EQUITY"):
                seen[symbol] = quote
        offset += len(quotes)
        total = result.get("total")
        logger.info(
            "yfscreen page offset=%d got=%d total=%s unique=%d",
            offset - len(quotes),
            len(quotes),
            total,
            len(seen),
        )
        if total is not None and offset >= total:
            break
    return set(seen)


def _load_existing_price_symbols() -> Set[str]:
    if not MARKET_DB_PATH.exists():
        return set()
    conn = sqlite3.connect(str(MARKET_DB_PATH))
    try:
        rows = conn.execute(
            "SELECT DISTINCT symbol FROM daily_price WHERE date >= '2021-02-01'"
        ).fetchall()
        return {row[0] for row in rows}
    finally:
        conn.close()


def _load_existing_price_sufficient_symbols(
    min_rows: int | None = None,
) -> Set[str]:
    min_rows = min_rows or _PRICE_SUFFICIENT_ROWS
    if not MARKET_DB_PATH.exists():
        return set()
    conn = sqlite3.connect(str(MARKET_DB_PATH))
    try:
        rows = conn.execute(
            """
            SELECT symbol, COUNT(*) AS row_count
            FROM daily_price
            WHERE date >= ?
            GROUP BY symbol
            """,
            (BROAD_PRICE_BACKFILL_START,),
        ).fetchall()
        return {row[0] for row in rows if row[1] >= min_rows}
    finally:
        conn.close()


def _load_broadscan_symbols() -> Set[str]:
    tracker_path = SCANS_DIR / "broad_scan_tracker.json"
    data = _read_json(tracker_path)
    if not isinstance(data, dict):
        return set()
    return {symbol for symbol in data.keys() if symbol != "_meta"}


def _enforce_count(name: str, count: int, min_count: int, max_count: int) -> None:
    if count < min_count or count > max_count:
        raise RuntimeError(
            f"{name} count {count} outside [{min_count}, {max_count}]"
        )


def build_over_inclusive_seed() -> List[str]:
    yfscreen = _fetch_yfscreen_dedup(BROAD_UNIVERSE_SEED_MIN_MCAP_USD)
    existing = _load_existing_price_symbols()
    broadscan = _load_broadscan_symbols()
    merged = sorted(yfscreen | existing | broadscan)

    _enforce_count(
        "broad universe seed",
        len(merged),
        BROAD_UNIVERSE_SEED_MIN_COUNT,
        BROAD_UNIVERSE_SEED_MAX_COUNT,
    )

    payload = {
        "updated": date.today().isoformat(),
        "seed_min_mcap_usd": BROAD_UNIVERSE_SEED_MIN_MCAP_USD,
        "count": len(merged),
        "symbols": merged,
        "source_breakdown": {
            "yfscreen": len(yfscreen),
            "existing_price": len(existing - yfscreen),
            "broadscan": len(broadscan - yfscreen - existing),
        },
        "overlaps": {
            "yfscreen_and_existing": len(yfscreen & existing),
            "yfscreen_and_scan": len(yfscreen & broadscan),
            "existing_and_scan": len(existing & broadscan),
        },
    }
    _write_json(BROAD_UNIVERSE_SEED_FILE, payload)
    logger.info(
        "Built seed: %d symbols (yfscreen=%d, existing+=%d, scan+=%d)",
        len(merged),
        len(yfscreen),
        payload["source_breakdown"]["existing_price"],
        payload["source_breakdown"]["broadscan"],
    )
    return merged


def _iter_chunks(values: Iterable[str], size: int = _SQLITE_CHUNK) -> Iterable[List[str]]:
    chunk: List[str] = []
    for value in values:
        chunk.append(value)
        if len(chunk) >= size:
            yield chunk
            chunk = []
    if chunk:
        yield chunk


def _query_max_historical_mcap(symbols: List[str]) -> Dict[str, float]:
    if not MARKET_DB_PATH.exists() or not symbols:
        return {}

    conn = sqlite3.connect(str(MARKET_DB_PATH))
    try:
        result: Dict[str, float] = {}
        for chunk in _iter_chunks(symbols):
            placeholders = ",".join("?" for _ in chunk)
            rows = conn.execute(
                f"""
                SELECT symbol, MAX(market_cap)
                FROM historical_market_cap
                WHERE symbol IN ({placeholders})
                GROUP BY symbol
                """,
                chunk,
            ).fetchall()
            result.update({row[0]: row[1] or 0 for row in rows})
        return result
    finally:
        conn.close()


def finalize_broad_universe(min_mcap_usd: int | None = None) -> Dict:
    threshold = min_mcap_usd or BROAD_UNIVERSE_MIN_MCAP_USD
    seed_data = _read_json(BROAD_UNIVERSE_SEED_FILE)
    seed_symbols = seed_data.get("symbols", [])
    if not seed_symbols:
        raise RuntimeError(
            "broad_universe_seed.json missing or empty. Run --refresh-seed first."
        )

    max_mcaps = _query_max_historical_mcap(seed_symbols)
    filtered = sorted(
        symbol for symbol in seed_symbols if max_mcaps.get(symbol, 0) >= threshold
    )
    _enforce_count(
        "broad universe final",
        len(filtered),
        BROAD_UNIVERSE_MIN_COUNT,
        BROAD_UNIVERSE_MAX_COUNT,
    )

    payload = {
        "updated": date.today().isoformat(),
        "filter_threshold_usd": threshold,
        "bootstrap_seed_size": seed_data.get("count", len(seed_symbols)),
        "source_breakdown": seed_data.get("source_breakdown", {}),
        "count": len(filtered),
        "symbols": filtered,
        "metadata": {
            symbol: {"max_hist_mcap_usd": max_mcaps.get(symbol, 0)} for symbol in filtered
        },
    }
    _write_json(BROAD_UNIVERSE_FILE, payload)
    logger.info(
        "Finalized broad universe: %d symbols (from seed %d, threshold $%dB)",
        len(filtered),
        len(seed_symbols),
        threshold // int(1e9),
    )
    return payload


def get_broad_symbols() -> List[str]:
    return _read_json(BROAD_UNIVERSE_FILE).get("symbols", [])


def get_broad_seed_symbols() -> List[str]:
    return _read_json(BROAD_UNIVERSE_SEED_FILE).get("symbols", [])


def load_broad_universe() -> Dict:
    return _read_json(BROAD_UNIVERSE_FILE)


def get_new_symbols_vs_price() -> List[str]:
    return sorted(set(get_broad_symbols()) - _load_existing_price_sufficient_symbols())


def main() -> None:
    parser = argparse.ArgumentParser(description="Manage broad universe bootstrap files")
    parser.add_argument("--refresh-seed", action="store_true")
    parser.add_argument("--finalize", action="store_true")
    parser.add_argument("--show", action="store_true")
    parser.add_argument("--min-mcap-usd", type=int)
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    if args.refresh_seed:
        print(f"Seed built: {len(build_over_inclusive_seed())} symbols")
        return
    if args.finalize:
        result = finalize_broad_universe(min_mcap_usd=args.min_mcap_usd)
        print(f"Final universe: {result['count']} symbols")
        return
    if args.show:
        data = load_broad_universe()
        print(json.dumps(data, indent=2, ensure_ascii=False))
        return
    parser.print_help()


if __name__ == "__main__":
    main()
