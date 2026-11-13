"""M16 momentum board: the momentum-ls-01 V4 score on US daily closes, long side only.

Plan: docs/plans/2026-11-13-prosperity-m16-momentum-boards.md. Rules: docs/runbooks/momentum-ls-01.md §2.
The V4 kernel is imported, never copied: percentiles over the whole valid pool first, then the
long gate (return > 0, slope > 0, drawdown ≤ 30%). The window is 70 trading days (14 weeks) of
split-resolved closes (PriceBook three-state rule); dollar volume is mean(raw close × volume) over
the same days. Read-only on market.db; not part of the engine code_version.
"""
from __future__ import annotations

import bisect
from datetime import date
import math
from typing import Callable, Dict, List, Mapping, Sequence

import numpy as np

from scripts.crypto_trend_metrics import trend_metrics
from scripts.crypto_trend_rankings import V4_WEIGHTS, direction_eligible, score_rows
from src.data.prosperity_pool import COVERAGE_MIN      # the prosperity pool's floor, one definition

SCHEMA_VERSION = 1
SCORING = 'v4'
# One window, 14 weeks (Boss 2026-11-13). The window backtest over 270 replayed weeks is in
# reports/prosperity/m16-window-backtest-20261113/: 70 days and 25 days tied for the best alpha within
# noise; 70 was kept for its good neighbour (60), lower turnover and smaller drawdown. Alpha is about
# equal to the earlier 7∩14 rule, whose 7-week leg alone was mediocre. Those reasons hold for the
# 'every eligible pool member' rule; under the shipped top-30 rule the window was not re-chosen and its
# neighbours are weak (README there).
WINDOWS = {'14w': 70}                    # label → trading days (closes = days + 1)
# as_of is a Saturday: the last close is Friday (lag 1), Thursday after a Friday holiday (2), or
# Wednesday when Thursday and Friday are both closed (3). Deliberately tighter than the pool's
# 7-day LIQUIDITY_MAX_LAG_DAYS: a price outage from Wednesday on must not yield a board (review #2).
MAX_LAG_DAYS = 3
_QUERY_BATCH = 500
_ROW_KEYS = ('return', 'er', 'r_squared', 'slope', 'drawdown')


def score_window(closes: Mapping[str, np.ndarray], dollar_volume: Mapping[str, float]) -> List[dict]:
    """Every valid symbol scored, best first; `rank` counts long-eligible rows only."""
    rows = [dict(symbol=s, status='ok', **trend_metrics(closes[s])) for s in sorted(closes)]
    score_rows(rows, SCORING, dollar_volume)
    out, rank = [], 0
    for row in sorted(rows, key=lambda r: (-r['score'], r['symbol'])):
        eligible = direction_eligible(row, 1, SCORING)
        rank += eligible
        out.append({'symbol': row['symbol'], 'score': row['score'], **{k: row[k] for k in _ROW_KEYS},
                    'dollar_volume': row['quote_volume'], 'eligible': eligible,
                    'rank': rank if eligible else None})
    return out


def load_dollar_volumes(market, symbols: Sequence[str], days: Sequence[str]) -> Dict[str, float]:
    """mean(close × volume) for symbols with a usable row on every one of `days`."""
    conn = market._get_conn()
    names, out = sorted(set(symbols)), {}
    day_marks = ','.join('?' for _ in days)
    for i in range(0, len(names), _QUERY_BATCH):
        batch = names[i:i + _QUERY_BATCH]
        marks = ','.join('?' for _ in batch)
        for symbol, average, n in conn.execute(
                f'SELECT symbol, AVG(close * volume), COUNT(*) FROM daily_price WHERE date IN ({day_marks}) '
                f'AND symbol IN ({marks}) AND close > 0 AND volume >= 0 GROUP BY symbol', [*days, *batch]):
            if n == len(days) and average is not None:
                out[symbol] = float(average)
    return out


def build_payload(*, book, dollar_volumes: Callable[[Sequence[str]], Mapping[str, float]],
                  symbols: Sequence[str], as_of: str, generated_at: str) -> dict:
    """The board for one universe. Fails closed: stale prices, short calendar, thin coverage."""
    members = sorted(set(symbols))
    if not members:
        raise ValueError('momentum_no_members')
    calendar = book.calendar
    end_i = bisect.bisect_right(calendar, as_of) - 1
    if end_i < 0:
        raise ValueError(f'momentum_window_short: no trading day on or before {as_of}')
    lag = (date.fromisoformat(as_of) - date.fromisoformat(calendar[end_i])).days
    if lag > MAX_LAG_DAYS:
        raise ValueError(f'momentum_prices_stale: last trading day {calendar[end_i]} is {lag} days before {as_of}')
    if end_i < max(WINDOWS.values()):
        raise ValueError(f'momentum_window_short: {end_i + 1} trading days < {max(WINDOWS.values()) + 1}')
    boards = {}
    for label, days in WINDOWS.items():
        start_i = end_i - days
        closes, unavailable = {}, {}
        for symbol in members:
            series, why = book.window_closes(symbol, start_i, end_i)
            if series is None:
                unavailable[symbol] = why
            else:
                closes[symbol] = series
        volumes = dollar_volumes(calendar[start_i + 1:end_i + 1])
        for symbol in list(closes):
            value = volumes.get(symbol)
            if value is None or not math.isfinite(value) or value < 0:
                del closes[symbol]
                unavailable[symbol] = 'volume_missing'
        if len(closes) < COVERAGE_MIN * len(members):
            raise ValueError(f'momentum_coverage_below_floor: {label} {len(closes)}/{len(members)}')
        scored = score_window(closes, {s: volumes[s] for s in closes})
        names = [r['symbol'] for r in scored]
        if sorted([*names, *unavailable]) != members:          # every member exactly once
            raise ValueError(f'momentum_universe_mismatch: {label}')
        first, last = calendar[start_i], calendar[end_i]
        boards[label] = {
            'days': days, 'window': [first, last], 'scored': scored,
            'unavailable': dict(sorted(unavailable.items())),
            'adjustments': [{'symbol': s, 'date': d, 'factor': f} for s, d, f in book.adjustments
                            if s in closes and first < d <= last]}
    return {'schema_version': SCHEMA_VERSION, 'as_of': as_of, 'generated_at': generated_at,
            'price_date': calendar[end_i], 'scoring': SCORING, 'weights': dict(V4_WEIGHTS),
            'universe': {'count': len(members), 'symbols': members}, 'boards': boards}
