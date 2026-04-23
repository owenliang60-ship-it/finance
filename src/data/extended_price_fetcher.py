"""
Extended Price Fetcher — yfinance batch download for $10B+ stocks.

Downloads OHLCV daily prices for stocks in the extended universe that are
NOT in the pool (pool stocks use FMP). Stores in market.db daily_price table.

Usage:
    from src.data.extended_price_fetcher import update_extended_prices
    result = update_extended_prices()                # Incremental (daily)
    result = update_extended_prices(full_backfill=True)  # 5-year backfill
"""
import logging
import time
from datetime import datetime
from typing import Any, Dict, List, Optional

import pandas as pd

logger = logging.getLogger(__name__)

try:
    from config.settings import (
        EXTENDED_PRICE_BACKFILL_START,
        EXTENDED_PRICE_CHUNK_SIZE,
        EXTENDED_PRICE_INCREMENTAL_PERIOD,
    )
except ImportError:
    EXTENDED_PRICE_CHUNK_SIZE = 200
    EXTENDED_PRICE_BACKFILL_START = "2021-02-01"
    EXTENDED_PRICE_INCREMENTAL_PERIOD = "5d"


def _extract_field_frame(
    data: pd.DataFrame, field: str, symbols: List[str],
) -> pd.DataFrame:
    """Extract a single field from yf.download MultiIndex DataFrame.

    Reuses the proven pattern from broad_market_scan.py.
    """
    if data is None or data.empty:
        return pd.DataFrame()

    if isinstance(data.columns, pd.MultiIndex):
        level0 = set(data.columns.get_level_values(0))
        level1 = set(data.columns.get_level_values(1))

        if field in level0:
            frame = data[field]
        elif field in level1:
            frame = data.xs(field, axis=1, level=1)
        else:
            return pd.DataFrame()

        if isinstance(frame, pd.Series):
            name = symbols[0] if len(symbols) == 1 else str(frame.name)
            return frame.to_frame(name=name)
        return frame.copy()

    if field not in data.columns:
        return pd.DataFrame()

    series = data[field]
    if isinstance(series, pd.Series):
        name = symbols[0] if len(symbols) == 1 else str(series.name or field)
        return series.to_frame(name=name)
    return series.copy()


def _normalize_ohlcv(
    data: pd.DataFrame, symbols: List[str],
) -> Dict[str, pd.DataFrame]:
    """Normalize yf.download result into {symbol: DataFrame(date, open, high, low, close, volume)}.

    Extends broad_market_scan's normalize_downloaded_frames to extract full OHLCV.
    """
    fields = ["Open", "High", "Low", "Close", "Volume"]
    frames = {}
    for field in fields:
        frames[field] = _extract_field_frame(data, field, symbols)

    close_frame = frames["Close"]
    if close_frame.empty:
        return {}

    available = [
        sym for sym in symbols
        if sym in close_frame.columns
        and sym in frames["Volume"].columns
    ]

    normalized = {}
    for sym in available:
        try:
            parts = []
            for field, col_name in [
                ("Open", "open"),
                ("High", "high"),
                ("Low", "low"),
                ("Close", "close"),
                ("Volume", "volume"),
            ]:
                if sym in frames[field].columns:
                    series = pd.to_numeric(frames[field][sym], errors="coerce")
                    parts.append(series.rename(col_name))

            if len(parts) < 5:
                continue

            df = pd.concat(parts, axis=1).dropna(subset=["close"])
            if df.empty:
                continue

            df = df.sort_index()
            # Convert index to date strings
            df.index = pd.to_datetime(df.index)
            df["date"] = df.index.strftime("%Y-%m-%d")
            df = df.reset_index(drop=True)
            normalized[sym] = df
        except Exception as e:
            logger.debug("Failed to normalize %s: %s", sym, e)

    return normalized


def _yf_download_ohlcv(
    symbols: List[str],
    period: Optional[str] = None,
    start: Optional[str] = None,
) -> Dict[str, pd.DataFrame]:
    """Batch download OHLCV data via yfinance.

    Args:
        symbols: List of ticker symbols.
        period: yfinance period string (e.g., "5d", "1y"). Mutually exclusive with start.
        start: Start date string "YYYY-MM-DD". If given, downloads from start to today.

    Returns:
        {symbol: DataFrame(date, open, high, low, close, volume)}
    """
    import yfinance as yf

    if not symbols:
        return {}

    chunk_size = EXTENDED_PRICE_CHUNK_SIZE
    total_chunks = (len(symbols) + chunk_size - 1) // chunk_size
    all_frames = {}

    for i in range(0, len(symbols), chunk_size):
        chunk = symbols[i:i + chunk_size]
        chunk_no = i // chunk_size + 1
        logger.info(
            "Downloading chunk %d/%d (%d symbols)...",
            chunk_no, total_chunks, len(chunk),
        )

        download_kwargs = {
            "interval": "1d",
            "auto_adjust": False,
            "progress": False,
            "group_by": "column",
            "threads": True,
            "timeout": 30,
        }
        if start:
            download_kwargs["start"] = start
        elif period:
            download_kwargs["period"] = period
        else:
            download_kwargs["period"] = EXTENDED_PRICE_INCREMENTAL_PERIOD

        for attempt in range(2):
            try:
                data = yf.download(chunk, **download_kwargs)
                chunk_frames = _normalize_ohlcv(data, chunk)
                all_frames.update(chunk_frames)
                break
            except Exception as e:
                if attempt == 0:
                    logger.warning(
                        "Chunk %d failed (attempt 1), retrying in 5s: %s",
                        chunk_no, e,
                    )
                    time.sleep(5)
                else:
                    logger.error("Chunk %d failed permanently, skipping: %s", chunk_no, e)

        if i + chunk_size < len(symbols):
            time.sleep(1)

    return all_frames


def update_extended_prices(
    full_backfill: bool = False,
    symbols: Optional[List[str]] = None,
    start_date: Optional[str] = None,
) -> Dict[str, Any]:
    """Update extended universe prices in market.db.

    Args:
        full_backfill: Force 5-year backfill for all symbols.
        symbols: Override symbol list (default: get_extended_only_symbols()).
        start_date: Optional incremental start date for symbols with existing history.

    Returns:
        Stats dict with total, success, failed, rows_inserted.
    """
    from src.data.extended_universe_manager import get_extended_only_symbols
    from src.data.market_store import get_store

    if symbols is None:
        symbols = get_extended_only_symbols()

    if not symbols:
        logger.warning("No extended-only symbols to update")
        return {"total": 0, "success": 0, "failed": [], "rows_inserted": 0}

    store = get_store()
    logger.info("Extended price update: %d symbols", len(symbols))

    # Partition symbols into backfill vs incremental
    backfill_symbols = []
    incremental_symbols = []

    if full_backfill:
        backfill_symbols = symbols
    else:
        for sym in symbols:
            rows = store.get_daily_prices(sym, limit=1)
            if rows:
                incremental_symbols.append(sym)
            else:
                backfill_symbols.append(sym)

    logger.info(
        "Backfill: %d symbols, Incremental: %d symbols",
        len(backfill_symbols), len(incremental_symbols),
    )

    all_frames = {}
    stats = {"total": len(symbols), "success": 0, "failed": [], "rows_inserted": 0}

    # Backfill batch (5-year history)
    if backfill_symbols:
        logger.info("Starting backfill from %s...", EXTENDED_PRICE_BACKFILL_START)
        frames = _yf_download_ohlcv(backfill_symbols, start=EXTENDED_PRICE_BACKFILL_START)
        all_frames.update(frames)
        failed = set(backfill_symbols) - set(frames.keys())
        if failed:
            logger.warning("Backfill failed for %d symbols: %s", len(failed), sorted(failed)[:10])

    # Incremental batch (last 5 days)
    if incremental_symbols:
        if start_date:
            logger.info("Starting incremental update from %s...", start_date)
        else:
            logger.info("Starting incremental update (%s)...", EXTENDED_PRICE_INCREMENTAL_PERIOD)
        frames = _yf_download_ohlcv(
            incremental_symbols,
            period=None if start_date else EXTENDED_PRICE_INCREMENTAL_PERIOD,
            start=start_date,
        )
        all_frames.update(frames)
        failed = set(incremental_symbols) - set(frames.keys())
        if failed:
            logger.warning("Incremental failed for %d symbols: %s", len(failed), sorted(failed)[:10])

    # Upsert to market.db
    for sym, df in all_frames.items():
        try:
            n = store.upsert_daily_prices_df(sym, df)
            stats["success"] += 1
            stats["rows_inserted"] += n
        except Exception as e:
            logger.error("Failed to upsert %s: %s", sym, e)
            stats["failed"].append(sym)

    stats["failed"].extend(sorted(set(symbols) - set(all_frames.keys()) - set(stats["failed"])))

    logger.info(
        "Extended price update complete: %d/%d success, %d failed, %d rows",
        stats["success"], stats["total"], len(stats["failed"]), stats["rows_inserted"],
    )
    return stats
