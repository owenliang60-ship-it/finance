"""M10 景气精选池: F1 live weekly board ∩ top-100 average dollar volume → prosperity_pool.json.

Reads prosperity.db (one board) and market.db (daily_price) read-only. Not in code_version:
pool selection never changes a score, only which already-scored rows are published.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
import os
from pathlib import Path
from typing import Mapping, Sequence, Tuple

from config.settings import DATA_DIR

POOL_NAME = '景气精选池'
POOL_SCHEMA_VERSION = 1
POOL_MAX_AGE_DAYS = 8
POOL_PATH = Path(os.environ.get('FINANCE_PROSPERITY_POOL_PATH', str(DATA_DIR / 'pool' / 'prosperity_pool.json')))
POOL_SCHEME_ID = 'F1'
POOL_GRADES = ('STRICT', 'FULL')
LIQUIDITY_TOP = 100
LIQUIDITY_DAYS = 30
LIQUIDITY_MIN_DAYS = 5
LIQUIDITY_LOOKBACK_CALENDAR_DAYS = 70   # 30 trading days ≈ 42 calendar days, plus holiday slack
LIQUIDITY_MAX_LAG_DAYS = 7
COVERAGE_MIN = 0.90


@dataclass(frozen=True)
class LiquidityResult:
    as_of: str                       # board date these ranks were computed for
    window: Tuple[str, ...]          # ascending, distinct trading days
    averages: Mapping[str, float]    # symbol → average daily dollar volume
    ranks: Mapping[str, int]         # symbol → 1..n; average descending, ties by symbol


def liquidity_ranks(market, symbols: Sequence[str], as_of: str) -> LiquidityResult:
    """Original-site rule: mean(close × volume) over the last 30 trading days up to as_of.

    A trading day is a date on which at least half of `symbols` have a price row, so stray
    holiday rows (e.g. ^VIX alone) never enter the window. Fewer than 5 valid days → unranked.
    """
    members = sorted(set(symbols))
    if not members:
        raise ValueError('liquidity_no_members')
    end = date.fromisoformat(as_of)
    start = end - timedelta(days=LIQUIDITY_LOOKBACK_CALENDAR_DAYS)
    marks = ','.join('?' for _ in members)
    conn = market._get_conn()
    counts = conn.execute(
        f'SELECT date, COUNT(DISTINCT symbol) FROM daily_price WHERE date > ? AND date <= ? '
        f'AND symbol IN ({marks}) GROUP BY date ORDER BY date',
        [start.isoformat(), as_of, *members]).fetchall()
    days = [d for d, n in counts if n * 2 >= len(members)]
    if len(days) < LIQUIDITY_DAYS:
        raise ValueError(f'liquidity_window_short: {len(days)} < {LIQUIDITY_DAYS} trading days')
    window = tuple(days[-LIQUIDITY_DAYS:])
    lag = (end - date.fromisoformat(window[-1])).days
    if lag > LIQUIDITY_MAX_LAG_DAYS:
        raise ValueError(f'liquidity_prices_stale: last trading day {window[-1]} is {lag} days before {as_of}')
    day_marks = ','.join('?' for _ in window)
    rows = conn.execute(
        f'SELECT symbol, AVG(close * volume), COUNT(*) FROM daily_price '
        f'WHERE date IN ({day_marks}) AND symbol IN ({marks}) AND close > 0 AND volume >= 0 '
        f'GROUP BY symbol', [*window, *members]).fetchall()
    averages = {s: float(v) for s, v, n in rows if n >= LIQUIDITY_MIN_DAYS and v is not None}
    order = sorted(averages, key=lambda s: (-averages[s], s))
    return LiquidityResult(as_of, window, averages, {s: i for i, s in enumerate(order, 1)})
