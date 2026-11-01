"""M12 health report: forward returns of frozen quarter-end boards, read-only on both databases.

Plan: docs/plans/2026-11-01-prosperity-m12-health-report.md. North star §4.3. Price returns use
`daily_price` closes with recorded splits resolved in three states (adjusted / already adjusted /
unresolved); dividends are not included. Nothing here feeds `code_version`.
"""
from __future__ import annotations

import bisect
import math
from datetime import date, datetime, timezone
from typing import Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

import numpy as np

from terminal.financial_history import detect_split_like_jumps

TOP_N = 40
HORIZONS = (1, 2, 3)                 # quarters
QUARTER_DAYS = 63                    # trading days per quarter
TIERS = 5
MIN_PERIODS = 5                      # fewer periods → no interval
MIN_IC_STOCKS = 10                   # fewer valid returns in a period → no IC
MOMENTUM_LOOKBACK = 252
MOMENTUM_SKIP = 21
PRICE_SLACK_DAYS = 5                 # an end / look-back close may be up to 5 trading days early
SPLIT_SEARCH_DAYS = 5                # event window = FMP split date ± 5 trading days
SPLIT_ADJUST_MIN = 1.2               # records with max(f, 1/f) < 1.2 are never adjusted
SPLIT_MATCH_TOL = 0.15               # |ln(r·f)| < min(0.15, |ln f| / 2) is the same split
LARGE_MOVE = 2.0                     # single-day moves beyond ×2 / ÷2 go to the appendix
CALENDAR_SYMBOL = 'SPY'
POOL_GRADES = ('STRICT', 'FULL')
CHAPTERS = ('分层前瞻收益', 'IC', '对照组', '与价格动量的重叠度', '行业集中度', '换手率', '失败案例', '短史因子')

_QUERY_BATCH = 500


def _utc_day(instant: str) -> str:
    at = datetime.fromisoformat(instant.replace('Z', '+00:00'))
    if at.tzinfo is None:
        raise ValueError('naive_timestamp')
    return at.astimezone(timezone.utc).date().isoformat()


class _Series:
    __slots__ = ('dates', 'closes', 'notable')

    def __init__(self, dates: List[str], closes: np.ndarray):
        self.dates = dates
        self.closes = closes
        self.notable: List[Tuple[str, float, str]] = []     # (date, ratio, kind) on adjusted closes


def _classify_split(dates: Sequence[str], closes: np.ndarray, lo: str, hi: str, f: float):
    """Three states for one recorded split; returns (state, index of the matched jump or None)."""
    first = max(bisect.bisect_left(dates, lo), 1)
    last = bisect.bisect_right(dates, hi)
    if last <= first:
        return 'unresolved', None
    d = abs(math.log(f))
    ratios = closes[first:last] / closes[first - 1:last - 1]
    big = [first + j for j, r in enumerate(ratios) if abs(math.log(r)) >= d / 2]
    if not big:
        return 'already_adjusted', None
    if (max(f, 1 / f) >= SPLIT_ADJUST_MIN and len(big) == 1
            and abs(math.log(closes[big[0]] / closes[big[0] - 1] * f)) < min(SPLIT_MATCH_TOL, d / 2)):
        return 'adjusted', big[0]
    return 'unresolved', None


class PriceBook:
    """Trading calendar, split-resolved closes and window returns."""

    def __init__(self, *, closes: Mapping[str, Sequence[Tuple[str, float]]],
                 splits: Mapping[str, Sequence[Tuple[str, float]]], calendar: Sequence[str]):
        self.calendar = tuple(calendar)
        self._series: Dict[str, _Series] = {}
        self._unresolved: Dict[str, List[Tuple[str, str]]] = {}
        adjustments, events = [], []
        n = len(self.calendar)
        for symbol in sorted(closes):
            points = sorted((d, float(c)) for d, c in closes[symbol] if c is not None and c > 0)
            if not points:
                continue
            dates = [d for d, _ in points]
            raw = np.array([c for _, c in points], dtype=float)
            adjusted = raw.copy()
            matched = []
            for fmp_date, f in sorted(splits.get(symbol, ())):
                f = float(f)
                if f == 1 or not n or fmp_date < self.calendar[0] or fmp_date > self.calendar[-1]:
                    continue
                k = bisect.bisect_left(self.calendar, fmp_date)
                lo = self.calendar[max(0, k - SPLIT_SEARCH_DAYS)]
                hi = self.calendar[min(n - 1, k + SPLIT_SEARCH_DAYS)]
                state, jump = _classify_split(dates, raw, lo, hi, f)
                events.append((symbol, fmp_date, f, state))
                if state == 'adjusted':
                    matched.append((jump, f))
                elif state == 'unresolved':
                    self._unresolved.setdefault(symbol, []).append((lo, hi))
            for jump, f in sorted(matched, reverse=True):
                adjusted[:jump] /= f
                adjustments.append((symbol, dates[jump], f))
            series = _Series(dates, adjusted)
            ratios = adjusted[1:] / adjusted[:-1]
            for j in np.flatnonzero((ratios < 0.55) | (ratios > 1.8)):
                d0, d1 = date.fromisoformat(dates[j]), date.fromisoformat(dates[j + 1])
                r = float(ratios[j])
                if detect_split_like_jumps([(d0, adjusted[j]), (d1, adjusted[j + 1])]):
                    series.notable.append((dates[j + 1], r, 'split_suspect'))
                elif r > LARGE_MOVE or r < 1 / LARGE_MOVE:
                    series.notable.append((dates[j + 1], r, 'large_move'))
            self._series[symbol] = series
        self.adjustments = tuple(sorted(adjustments))
        self.split_events = tuple(sorted(events))

    @classmethod
    def load(cls, market, symbols: Iterable[str]) -> 'PriceBook':
        names = sorted(set(symbols))
        conn = market._get_conn()
        calendar = [r[0] for r in conn.execute(
            'SELECT date FROM daily_price WHERE symbol=? AND close > 0 ORDER BY date', (CALENDAR_SYMBOL,))]
        closes: Dict[str, List[Tuple[str, float]]] = {}
        splits: Dict[str, List[Tuple[str, float]]] = {}
        for i in range(0, len(names), _QUERY_BATCH):
            batch = names[i:i + _QUERY_BATCH]
            marks = ','.join('?' for _ in batch)
            for s, d, c in conn.execute(
                    f'SELECT symbol, date, close FROM daily_price WHERE symbol IN ({marks}) AND close > 0',
                    batch):
                closes.setdefault(s, []).append((d, c))
            for s, d, num, den in conn.execute(
                    f'SELECT symbol, date, numerator, denominator FROM fmp_stock_splits '
                    f'WHERE symbol IN ({marks})', batch):
                splits.setdefault(s, []).append((d, num / den))
        return cls(closes=closes, splits=splits, calendar=calendar)

    def start_index(self, frozen_at: str) -> Optional[int]:
        i = bisect.bisect_right(self.calendar, _utc_day(frozen_at))
        return i if i < len(self.calendar) else None

    def close_at(self, symbol: str, i: int) -> Optional[float]:
        s = self._series.get(symbol)
        if s is None:
            return None
        lo = self.calendar[max(0, i - PRICE_SLACK_DAYS)]
        j = bisect.bisect_right(s.dates, self.calendar[i]) - 1
        return float(s.closes[j]) if j >= 0 and s.dates[j] >= lo else None

    def _close_on(self, symbol: str, day: str) -> Optional[float]:
        s = self._series[symbol]
        j = bisect.bisect_left(s.dates, day)
        return float(s.closes[j]) if j < len(s.dates) and s.dates[j] == day else None

    def jumps(self, symbol: str, start_i: int, end_i: int) -> List[dict]:
        s = self._series.get(symbol)
        if s is None:
            return []
        a, b = self.calendar[start_i], self.calendar[end_i]
        return [{'date': d, 'ratio': r, 'kind': k} for d, r, k in s.notable if a < d <= b]

    def last_date(self, symbol: str) -> Optional[str]:
        s = self._series.get(symbol)
        return s.dates[-1] if s else None

    def unresolved_overlaps(self, symbol: str, start_i: int, end_i: int) -> bool:
        a, b = self.calendar[start_i], self.calendar[end_i]
        return any(lo <= b and hi > a for lo, hi in self._unresolved.get(symbol, ()))

    def window_return(self, symbol: str, start_i: int, end_i: int) -> Tuple[Optional[float], str]:
        s = self._series.get(symbol)
        if s is None:
            return None, 'no_price'
        start = self._close_on(symbol, self.calendar[start_i])
        if start is None:
            return None, 'no_start_price'
        if s.dates[-1] < self.calendar[end_i]:
            return None, 'ended_early'
        end = self.close_at(symbol, end_i)
        if end is None:
            return None, 'price_gap'
        if self.unresolved_overlaps(symbol, start_i, end_i):
            return None, 'split_unresolved'
        if any(j['kind'] == 'split_suspect' for j in self.jumps(symbol, start_i, end_i)):
            return None, 'split_suspect'
        return end / start - 1, 'ok'
