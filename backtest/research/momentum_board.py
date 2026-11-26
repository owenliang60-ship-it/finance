"""M16 momentum board: U70 + UDV on US daily closes, long side only (Boss 2026-11-26).

Plans: docs/plans/2026-11-13-prosperity-m16-momentum-boards.md; current rule in
docs/design/prosperity-engine-north-star.md §4.4. The window is 70 trading days (14 weeks) of
split-resolved closes (PriceBook three-state rule). U70 = members in that day's raw close × volume
top 100 on a strict majority of the 70 days. E = U70 rows with return > 0, slope > 0 and
drawdown ≤ 30%. Five directed percentiles are taken inside E only; the fifth is UDV = median
up-day / median down-day dollar volume. The listed top is board_top_cut, computed from the rows.
Read-only on market.db; not part of the engine code_version.
"""
from __future__ import annotations

import bisect
from datetime import date
import math
import statistics
from typing import Callable, Dict, List, Mapping, Optional, Sequence

import numpy as np

from scripts.crypto_trend_metrics import trend_metrics
from src.data.prosperity_pool import COVERAGE_MIN      # the prosperity pool's floor, one definition

SCHEMA_VERSION = 1
SCORING = 'udv1'                         # no "_": the delivery text goes out with Telegram legacy Markdown
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
TOP_VOLUME_RANK = 100                    # U70: in each day's dollar-volume top 100 ...
LONG_DRAWDOWN_LIMIT = 0.30
# The V4 ratio 45/12.5/12.5/5/25 with the turnover slot taken by UDV (Boss 2026-11-26; 270-week replay
# in north-star §4.4).
UDV_WEIGHTS = {'return': 0.45, 'er': 0.125, 'r_squared': 0.125, 'drawdown': 0.05, 'udv': 0.25}
_QUERY_BATCH = 500
_ROW_KEYS = ('return', 'er', 'r_squared', 'slope', 'drawdown')


def min_top_volume_days(days: int) -> int:
    """... on a strict majority of the window's days (36 of 70)."""
    return days // 2 + 1


def board_u70_count(board: Mapping) -> int:
    """|U70| over the whole universe, from the per-member days: unscorable members count too."""
    floor = min_top_volume_days(board['days'])
    return (sum(1 for r in board['scored'] if r['in_u70'])
            + sum(1 for n in board['unavailable_top100_days'].values() if n >= floor))


def board_top_cut(board: Mapping) -> int:
    """Rows listed from the long board: min(ceil(|U70| / 5), ceil(|E| / 2)), counted from the rows."""
    e_count = sum(1 for r in board['scored'] if r['eligible'])
    if e_count == 0:
        return 0
    return min(-(-board_u70_count(board) // 5), -(-e_count // 2))


def top_volume_days(day_volumes: Mapping[str, Mapping], days: Sequence) -> Dict[str, int]:
    """Days each symbol spent in that day's dollar-volume top TOP_VOLUME_RANK; ties go to the symbol."""
    counts = {s: 0 for s in day_volumes}
    for d in days:
        ranked = sorted((-v[d], s) for s, v in day_volumes.items() if d in v)
        for _, s in ranked[:TOP_VOLUME_RANK]:
            counts[s] += 1
    return counts


def _udv(closes: np.ndarray, dvs: Sequence[float]) -> Optional[float]:
    """median(up-day dollar volume) / median(down-day dollar volume); None when no down day traded."""
    ups = [v for k, v in enumerate(dvs) if closes[k + 1] > closes[k]]
    dns = [v for k, v in enumerate(dvs) if closes[k + 1] < closes[k]]
    if not ups or not dns or statistics.median(dns) <= 0:
        return None
    udv = float(statistics.median(ups) / statistics.median(dns))
    if not math.isfinite(udv):
        raise ValueError('udv not finite')
    return udv


def _directed(row: dict, key: str) -> float:
    """Larger is better; drawdown is reversed and a missing UDV (no priced down day) is the top."""
    value = row[key]
    if key == 'udv' and value is None:
        return math.inf
    return -value if key == 'drawdown' else value


def score_window(closes: Mapping[str, np.ndarray], daily_volumes: Mapping[str, Sequence[float]], *,
                 top100_days: Optional[Mapping[str, int]] = None) -> List[dict]:
    """Every symbol of `closes`, best first; only E rows are scored and ranked, the rest score 0.

    closes[s] has L + 1 closes and daily_volumes[s] the L dollar volumes of closes 1..L.
    top100_days defaults to the daily top of `daily_volumes` itself; build_payload passes the
    count over every member with volume, so unscorable members still take their place in the top.
    """
    vols = {}
    for s in sorted(closes):
        vols[s] = [float(v) for v in daily_volumes[s]]
        if len(vols[s]) != len(closes[s]) - 1 or not all(math.isfinite(v) and v >= 0 for v in vols[s]):
            raise ValueError(f'invalid daily dollar volume: {s}')
    if top100_days is None:
        span = max((len(v) for v in vols.values()), default=0)
        top100_days = top_volume_days({s: dict(enumerate(v)) for s, v in vols.items()}, range(span))
    rows = []
    for s, dvs in vols.items():
        tm = trend_metrics(closes[s])
        if not all(0 <= tm[k] <= 1 for k in ('er', 'r_squared', 'drawdown')):
            raise ValueError(f'invalid trend metric: {s}')
        in_u70 = top100_days[s] >= min_top_volume_days(len(dvs))
        rows.append({'symbol': s, 'score': 0.0, **{k: tm[k] for k in _ROW_KEYS},
                     'dollar_volume': float(sum(dvs) / len(dvs)), 'udv': _udv(closes[s], dvs),
                     'top100_days': int(top100_days[s]), 'in_u70': in_u70,
                     'eligible': bool(in_u70 and tm['return'] > 0 and tm['slope'] > 0
                                      and tm['drawdown'] <= LONG_DRAWDOWN_LIMIT), 'rank': None})
    eligible = [r for r in rows if r['eligible']]
    for r in eligible:           # integer numerators, one division: exact ties (as score_valid_rows)
        r['score'] = sum(sum(_directed(o, k) <= _directed(r, k) for o in eligible) * (w * 100.0)
                         for k, w in UDV_WEIGHTS.items()) / len(eligible)
    rows.sort(key=lambda r: (-r['score'], r['symbol']))
    for rank, r in enumerate((r for r in rows if r['eligible']), 1):
        r['rank'] = rank
    return rows


def load_daily_dollar_volumes(market, symbols: Sequence[str], days: Sequence[str]) -> Dict[str, Dict[str, float]]:
    """{symbol: {day: close × volume}} for every usable row on `days`; a missing day stays missing."""
    conn = market._get_conn()
    names, out = sorted(set(symbols)), {}
    day_marks = ','.join('?' for _ in days)
    for i in range(0, len(names), _QUERY_BATCH):
        batch = names[i:i + _QUERY_BATCH]
        marks = ','.join('?' for _ in batch)
        for symbol, day, value in conn.execute(
                f'SELECT symbol, date, close * volume FROM daily_price WHERE date IN ({day_marks}) '
                f'AND symbol IN ({marks}) AND close > 0 AND volume >= 0', [*days, *batch]):
            if value is not None and math.isfinite(value) and value >= 0:
                out.setdefault(symbol, {})[day] = float(value)
    return out


def build_payload(*, book, daily_volumes: Callable[[Sequence[str]], Mapping[str, Mapping[str, float]]],
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
        window_days = calendar[start_i + 1:end_i + 1]
        raw, wanted = daily_volumes(window_days), set(window_days)
        day_volumes = {s: {d: float(v) for d, v in raw[s].items() if d in wanted and math.isfinite(v) and v >= 0}
                       for s in members if s in raw}          # members only: an outsider never takes a top slot
        for symbol in list(closes):
            if not all(d in day_volumes.get(symbol, {}) for d in window_days):
                del closes[symbol]
                unavailable[symbol] = 'volume_missing'
        if len(closes) < COVERAGE_MIN * len(members):
            raise ValueError(f'momentum_coverage_below_floor: {label} {len(closes)}/{len(members)}')
        counts = top_volume_days(day_volumes, window_days)
        scored = score_window(closes, {s: [day_volumes[s][d] for d in window_days] for s in closes},
                              top100_days=counts)
        names = [r['symbol'] for r in scored]
        if sorted([*names, *unavailable]) != members:          # every member exactly once
            raise ValueError(f'momentum_universe_mismatch: {label}')
        first, last = calendar[start_i], calendar[end_i]
        boards[label] = {
            'days': days, 'window': [first, last], 'scored': scored,
            'unavailable': dict(sorted(unavailable.items())),
            'unavailable_top100_days': {s: int(counts.get(s, 0)) for s in sorted(unavailable)},
            'adjustments': [{'symbol': s, 'date': d, 'factor': f} for s, d, f in book.adjustments
                            if s in closes and first < d <= last]}
    return {'schema_version': SCHEMA_VERSION, 'as_of': as_of, 'generated_at': generated_at,
            'price_date': calendar[end_i], 'scoring': SCORING, 'weights': dict(UDV_WEIGHTS),
            'universe': {'count': len(members), 'symbols': members}, 'boards': boards}
