"""
Fetch historical Binance spot klines into local CSV caches.

Supports multiple intervals so tracked code can hydrate the ignored
`data/crypto/` cache directories used by backtests.
"""

from __future__ import annotations

import argparse
import csv
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Iterable, List

import requests

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.path_utils import resolve_shared_repo_root


BASE_URL = "https://data-api.binance.vision/api/v3/klines"
LIMIT = 1000
RATE_LIMIT_SEC = 0.3

_DEFAULT_START_DATES = {
    "BTCUSDT": "2017-08-17",
    "ETHUSDT": "2017-08-17",
}

_INTERVAL_DIRS = {
    "1d": "binance_daily_cache",
    "4h": "binance_4h_cache",
}

_ROOT = resolve_shared_repo_root(
    PROJECT_ROOT,
    required_paths=(
        "data/crypto/binance_daily_cache",
        "data/crypto/binance_4h_cache",
    ),
)


def _normalize_interval(interval: str) -> str:
    value = interval.strip().lower()
    if value in {"1d", "d", "day", "daily"}:
        return "1d"
    if value in {"4h", "4hr", "4hour", "240m"}:
        return "4h"
    raise ValueError(f"Unsupported interval: {interval}")


def _default_output_dir(interval: str) -> Path:
    normalized = _normalize_interval(interval)
    return _ROOT / "data" / "crypto" / _INTERVAL_DIRS[normalized]


def _date_to_ms(date_str: str) -> int:
    dt = datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    return int(dt.timestamp() * 1000)


def _format_open_time(ms: int, interval: str) -> str:
    dt = datetime.fromtimestamp(ms / 1000, tz=timezone.utc)
    if interval == "1d":
        return dt.strftime("%Y-%m-%d")
    return dt.strftime("%Y-%m-%d %H:%M:%S")


def fetch_all_klines(symbol: str, interval: str, start_date: str) -> List[List]:
    """Fetch all klines from start_date until the Binance API runs out of data."""
    start_ms = _date_to_ms(start_date)
    all_rows: List[List] = []

    while True:
        params = {
            "symbol": symbol,
            "interval": interval,
            "limit": LIMIT,
            "startTime": start_ms,
        }
        resp = requests.get(BASE_URL, params=params, timeout=30)
        resp.raise_for_status()
        rows = resp.json()

        if not rows:
            break

        all_rows.extend(rows)

        if len(rows) < LIMIT:
            break

        start_ms = rows[-1][6] + 1  # close_time + 1 ms
        time.sleep(RATE_LIMIT_SEC)

    return all_rows


def save_csv(symbol: str, interval: str, rows: Iterable[List], output_dir: Path) -> Path:
    """Persist klines as a compact CSV the adapters can read directly."""
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / f"{symbol}.csv"

    with path.open("w", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(
            [
                "open_time",
                "date",
                "open",
                "high",
                "low",
                "close",
                "volume",
                "quote_volume",
            ]
        )
        for row in rows:
            writer.writerow(
                [
                    row[0],
                    _format_open_time(row[0], interval),
                    row[1],
                    row[2],
                    row[3],
                    row[4],
                    row[5],
                    row[7],
                ]
            )

    return path


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Fetch Binance spot klines into local crypto caches."
    )
    parser.add_argument(
        "--interval",
        default="1d",
        help="Kline interval: 1d or 4h (default: 1d)",
    )
    parser.add_argument(
        "--symbols",
        default="BTCUSDT,ETHUSDT",
        help="Comma-separated symbols (default: BTCUSDT,ETHUSDT)",
    )
    parser.add_argument(
        "--start-date",
        help="Override start date for all symbols (YYYY-MM-DD)",
    )
    parser.add_argument(
        "--output-dir",
        help="Optional output directory; defaults to data/crypto/binance_*_cache",
    )
    return parser.parse_args()


def main() -> None:
    args = _parse_args()
    interval = _normalize_interval(args.interval)
    output_dir = Path(args.output_dir) if args.output_dir else _default_output_dir(interval)

    symbols = [part.strip().upper() for part in args.symbols.split(",") if part.strip()]
    start_dates: Dict[str, str] = {
        symbol: (args.start_date or _DEFAULT_START_DATES.get(symbol, "2017-08-17"))
        for symbol in symbols
    }

    for symbol in symbols:
        start_date = start_dates[symbol]
        print(f"Fetching {symbol} interval={interval} from {start_date}")
        rows = fetch_all_klines(symbol, interval, start_date)
        if not rows:
            print(f"  no data for {symbol}")
            continue

        path = save_csv(symbol, interval, rows, output_dir)
        print(
            "  saved %s rows to %s (%s -> %s)"
            % (
                len(rows),
                path,
                _format_open_time(rows[0][0], interval),
                _format_open_time(rows[-1][0], interval),
            )
        )


if __name__ == "__main__":
    main()
