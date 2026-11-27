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
import re
import statistics
from typing import Callable, Dict, List, Mapping, Optional, Sequence

import numpy as np

from scripts.crypto_trend_metrics import trend_metrics
from src.data.prosperity_pool import (COVERAGE_MIN, LIQUIDITY_TOP, _day, _finite, _is_int,
                                      count_top_days, load_daily_dollar_volumes)   # shared definitions

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
TOP_VOLUME_RANK = LIQUIDITY_TOP          # U70: in each day's dollar-volume top 100 ...
LONG_DRAWDOWN_LIMIT = 0.30
# The V4 ratio 45/12.5/12.5/5/25 with the turnover slot taken by UDV (Boss 2026-11-26; 270-week replay
# in north-star §4.4).
UDV_WEIGHTS = {'return': 0.45, 'er': 0.125, 'r_squared': 0.125, 'drawdown': 0.05, 'udv': 0.25}
_ROW_KEYS = ('return', 'er', 'r_squared', 'slope', 'drawdown')
_SYMBOL = re.compile(r'[A-Z0-9.-]+')     # no "_": the texts go out with Telegram legacy Markdown (issue 016)


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
    return count_top_days(day_volumes, days, TOP_VOLUME_RANK)


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


def reject_non_finite(token: str):
    """json.loads parse_constant: NaN / Infinity never enter a board."""
    raise ValueError(f'non-finite number in momentum board: {token}')


def _unit(value) -> bool:
    return _finite(value) and 0 <= value <= 1


def check_header(payload) -> List[str]:
    """The universe symbols of a payload whose header this CLI could have written, or ValueError.
    The sender and the morning reader both call this, then check_board (review P2)."""
    if (not isinstance(payload, dict) or not _is_int(payload.get('schema_version'))
            or payload['schema_version'] != SCHEMA_VERSION or payload.get('scoring') != SCORING):
        raise ValueError('momentum header invalid')
    boards, universe = payload.get('boards'), payload.get('universe')
    if not isinstance(boards, dict) or set(boards) != set(WINDOWS) or not isinstance(universe, dict):
        raise ValueError('momentum boards or universe invalid')
    _day(payload.get('as_of'))
    symbols = universe.get('symbols')
    if (not isinstance(symbols, list) or not symbols or symbols != sorted(set(symbols))
            or not all(isinstance(s, str) and _SYMBOL.fullmatch(s) for s in symbols)
            or not _is_int(universe.get('count')) or universe['count'] != len(symbols)):
        raise ValueError('momentum universe invalid')
    return symbols


def check_board(payload) -> dict:
    """The one board of a payload passing check_header, if every row is what score_window writes."""
    (label, days), = WINDOWS.items()
    board, price_day = payload['boards'][label], _day(payload.get('price_date'))
    if not 0 <= (_day(payload['as_of']) - price_day).days <= MAX_LAG_DAYS or not isinstance(board, dict):
        raise ValueError('momentum price_date not bound to as_of')
    window = board.get('window')
    if (not isinstance(window, list) or len(window) != 2 or not _day(window[0]) < _day(window[1]) == price_day
            or not _is_int(board.get('days')) or board['days'] != days):
        raise ValueError('momentum window invalid')
    scored, unavailable, idle = board.get('scored'), board.get('unavailable'), board.get('unavailable_top100_days')
    if (not isinstance(scored, list) or not isinstance(unavailable, dict) or not isinstance(idle, dict)
            or set(idle) != set(unavailable) or not all(_is_int(n) and 0 <= n <= days for n in idle.values())
            or not all(_SYMBOL.fullmatch(s) and isinstance(why, str) and why for s, why in unavailable.items())):
        raise ValueError('momentum unavailable rows invalid')
    for r in scored:
        if (not isinstance(r, dict) or type(r.get('eligible')) is not bool or type(r.get('in_u70')) is not bool
                or not all(_finite(r.get(k)) for k in ('score', 'return', 'slope', 'dollar_volume'))
                or not all(_unit(r.get(k)) for k in ('er', 'r_squared', 'drawdown')) or r['dollar_volume'] < 0
                or 'udv' not in r or (r['udv'] is not None and not (_finite(r['udv']) and r['udv'] >= 0))
                or not _is_int(r.get('top100_days')) or not 0 <= r['top100_days'] <= days
                or r['in_u70'] != (r['top100_days'] >= min_top_volume_days(days))
                or r['eligible'] != (r['in_u70'] and r['return'] > 0 and r['slope'] > 0
                                     and r['drawdown'] <= LONG_DRAWDOWN_LIMIT)):
            raise ValueError('momentum scored row invalid')
        if (r['score'] <= 0 or not _is_int(r.get('rank')) or r['rank'] < 1) if r['eligible'] else (
                r.get('rank') is not None or r['score'] != 0.0):
            raise ValueError('momentum rank or score invalid')
    names = [r.get('symbol') for r in scored]
    if (len(set(names)) != len(names) or not set(names).isdisjoint(unavailable)
            or set(names) | set(unavailable) != set(payload['universe']['symbols'])):
        raise ValueError('momentum rows do not partition the universe')
    if len(names) < COVERAGE_MIN * len(payload['universe']['symbols']):
        raise ValueError('momentum coverage below floor')
    keys = [(-r['score'], r['symbol']) for r in scored]
    ranks = [r['rank'] for r in scored if r['eligible']]       # eligible rows in score order: 1..|E|
    if keys != sorted(keys) or ranks != list(range(1, len(ranks) + 1)):
        raise ValueError('momentum rows not in score order')
    return board


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
