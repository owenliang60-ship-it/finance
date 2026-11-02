"""M12 health report: forward returns of frozen quarter-end boards, read-only on both databases.

Plan: docs/plans/2026-11-01-prosperity-m12-health-report.md. North star §4.3. Price returns use
`daily_price` closes with recorded splits resolved in three states (adjusted / already adjusted /
unresolved); dividends are not included. Nothing here feeds `code_version`.
"""
from __future__ import annotations

import bisect
import math
import statistics
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

import numpy as np
from scipy.stats import spearmanr
from scipy.stats import t as t_dist

from backtest.pipeline.primitives.evaluation import newey_west_tstat
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
        if not calendar:
            raise ValueError('empty_calendar: no CALENDAR_SYMBOL prices')   # second review #2
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
                if state == 'adjusted' and jump in {j for j, _ in matched}:
                    state = 'already_adjusted'           # a second record for the same jump (review #2)
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


# ---------------------------------------------------------------------------
# Board view
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Row:
    symbol: str
    status: str                      # ranked / observe / excluded
    grade: Optional[str]
    rank: Optional[int]
    score: Optional[float]
    sector: Optional[str]
    pit_basis: str
    beta: Optional[float]            # archived packet beta of the frozen board
    breakdown: Mapping[str, Mapping[str, Optional[float]]]   # factor → {raw, z, weight, contribution}
    family_scores: Mapping[str, Optional[float]]
    badges: Tuple[str, ...]


@dataclass(frozen=True)
class BoardView:
    scheme_id: str
    scheme_hash: str
    code_version: str
    as_of: str
    quarter_label: Optional[str]
    frozen_at: Optional[str]
    board_type: str
    mode: str
    rows: Tuple[Row, ...]


def board_view(stored) -> BoardView:
    stocks = {p['symbol']: p for p in stored.stocks}
    rows = []
    for r in stored.board.rows:
        p = stocks.get(r.symbol, {})
        factors = list(dict.fromkeys([*r.values, *r.z, *r.missing]))
        breakdown = {f: {'raw': r.values.get(f), 'z': r.z.get(f), 'weight': r.weights.get(f),
                         'contribution': r.contributions.get(f)} for f in factors}
        rows.append(Row(symbol=r.symbol, status=r.status, grade=r.grade, rank=r.rank, score=r.score,
                        sector=p.get('sector'), pit_basis=p.get('pit_basis', 'approximate'), beta=p.get('beta'),
                        breakdown=breakdown, family_scores=dict(r.family_scores), badges=tuple(r.badges)))
    b, m = stored.board, stored.meta
    return BoardView(scheme_id=b.scheme_id, scheme_hash=b.scheme_hash, code_version=b.code_version,
                     as_of=b.as_of, quarter_label=m.quarter_label, frozen_at=m.frozen_at,
                     board_type=m.board_type, mode=m.mode, rows=tuple(rows))


def universe(view: BoardView) -> List[Row]:
    return [r for r in view.rows if r.status != 'excluded']


def ranked_rows(view: BoardView) -> List[Row]:
    return sorted((r for r in view.rows if r.status == 'ranked'), key=lambda r: r.rank)


# ---------------------------------------------------------------------------
# Per-board evaluation and statistics
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class PeriodResult:
    as_of: str
    horizon: int
    status: str                      # complete / in_progress / not_started
    start: Optional[str]
    end: Optional[str]
    start_i: Optional[int]
    end_i: Optional[int]
    returns: Mapping[str, Tuple[Optional[float], str]]
    tiers: Mapping[str, int]
    baseline: Optional[float]
    tier_excess: Mapping[int, Optional[float]]
    spread: Optional[float]
    ic: Optional[float]
    top: Tuple[str, ...]
    top_excess: Optional[float]
    coverage: Mapping[str, Tuple[int, int]]


def assign_tiers(symbols_by_rank: Sequence[str]) -> Dict[str, int]:
    n = len(symbols_by_rank)
    return {s: i * TIERS // n for i, s in enumerate(symbols_by_rank)}


def top_symbols(view: BoardView) -> Tuple[str, ...]:
    return tuple(r.symbol for r in ranked_rows(view) if r.grade in POOL_GRADES)[:TOP_N]


def _mean(values: Iterable[Optional[float]]) -> Optional[float]:
    xs = [v for v in values if v is not None]
    return statistics.fmean(xs) if xs else None


def group_excess(returns: Mapping[str, Tuple[Optional[float], str]], symbols: Iterable[str],
                 baseline: Optional[float]) -> Optional[float]:
    mean = _mean(returns.get(s, (None, ''))[0] for s in symbols)
    return None if mean is None or baseline is None else mean - baseline


def window(prices: PriceBook, frozen_at: Optional[str], horizon: int):
    """(status, start_i, end_i) for one board and holding period."""
    start_i = prices.start_index(frozen_at) if frozen_at else None
    if start_i is None:
        return 'not_started', None, None
    end_i = start_i + QUARTER_DAYS * horizon
    if end_i >= len(prices.calendar):
        return 'in_progress', start_i, len(prices.calendar) - 1
    return 'complete', start_i, end_i


def evaluate_board(view: BoardView, prices: PriceBook, *, horizon: int) -> PeriodResult:
    status, start_i, end_i = window(prices, view.frozen_at, horizon)
    if status == 'not_started':
        return PeriodResult(view.as_of, horizon, status, None, None, None, None, {}, {}, None, {}, None, None,
                            (), None, {})
    members = universe(view)
    returns = {r.symbol: prices.window_return(r.symbol, start_i, end_i) for r in members}
    ranked = ranked_rows(view)
    tiers = assign_tiers([r.symbol for r in ranked])
    baseline = _mean(v for v, _ in returns.values())
    tier_excess = {t: group_excess(returns, [s for s, k in tiers.items() if k == t], baseline)
                   for t in range(TIERS)}
    spread = (None if tier_excess[0] is None or tier_excess[TIERS - 1] is None
              else tier_excess[0] - tier_excess[TIERS - 1])
    valid = [(r.rank, returns[r.symbol][0]) for r in ranked if returns[r.symbol][0] is not None]
    ic = None
    if len(valid) >= MIN_IC_STOCKS and len({v for _, v in valid}) > 1:
        ic = float(spearmanr([-k for k, _ in valid], [v for _, v in valid]).statistic)
    top = top_symbols(view)
    coverage = {'ranked': (len(valid), len(ranked)),
                'universe': (sum(v is not None for v, _ in returns.values()), len(members))}
    return PeriodResult(view.as_of, horizon, status, prices.calendar[start_i], prices.calendar[end_i],
                        start_i, end_i, returns, tiers, baseline, tier_excess, spread, ic, top,
                        group_excess(returns, top, baseline), coverage)


def overlap_lag(windows: Sequence[Tuple[int, int]]) -> int:
    """Largest number of later windows that start before an earlier window ends."""
    ws = sorted(windows)
    return max((sum(1 for s, _ in ws[i + 1:] if s < e) for i, (_, e) in enumerate(ws)), default=0)


def interval(values: Sequence[float], *, lag: int) -> dict:
    xs = [float(v) for v in values]
    n = len(xs)
    if n < MIN_PERIODS:
        return {'n': n, 'mean': statistics.fmean(xs) if xs else None, 'lag': lag}
    mean = statistics.fmean(xs)
    t, p = newey_west_tstat(xs, lag)
    if t == 0:
        return {'n': n, 'mean': mean, 't': t, 'p': p, 'lag': lag}
    se = mean / t
    half = float(t_dist.ppf(0.975, n - 1)) * abs(se)
    return {'n': n, 'mean': mean, 'se': se, 'lo': mean - half, 'hi': mean + half, 't': t, 'p': p, 'lag': lag}


def ic_summary(ics: Sequence[float], *, lag: int) -> dict:
    out = interval(ics, lag=lag)
    sd = statistics.stdev(ics) if len(ics) >= 2 else None
    out['sd'] = sd
    out['icir'] = None if len(ics) < MIN_PERIODS or not sd else out['mean'] / sd
    return out


def monotonic(means: Sequence[Optional[float]]) -> bool:
    return all(m is not None for m in means) and all(a > b for a, b in zip(means, means[1:]))


# ---------------------------------------------------------------------------
# Structure: concentration, turnover, failure cases, short-history factors
# ---------------------------------------------------------------------------

UNKNOWN_SECTOR = '未知'


def concentration(view: BoardView, symbols: Sequence[str]) -> dict:
    sectors = {r.symbol: r.sector or UNKNOWN_SECTOR for r in view.rows}
    counts: Dict[str, int] = {}
    for s in symbols:
        counts[sectors.get(s, UNKNOWN_SECTOR)] = counts.get(sectors.get(s, UNKNOWN_SECTOR), 0) + 1
    if not counts:
        return {'top_sector': None, 'max_share': None, 'hhi': None}
    n = len(symbols)
    top = min(counts, key=lambda k: (-counts[k], k))
    return {'top_sector': top, 'max_share': counts[top] / n, 'hhi': sum((c / n) ** 2 for c in counts.values())}


def turnover(previous: Sequence[str], current: Sequence[str]) -> Optional[float]:
    if not previous:
        return None
    return 1 - len(set(previous) & set(current)) / len(set(previous))


def failure_cases(pairs: Sequence[Tuple[BoardView, PeriodResult]], *, limit: int = 10) -> List[dict]:
    cases = []
    for v, res in pairs:
        if res.status != 'complete':
            continue
        for r in v.rows:
            if r.status != 'ranked' or r.grade != 'STRICT':
                continue
            ret = res.returns.get(r.symbol, (None, ''))[0]
            if ret is None:
                continue
            cases.append({'as_of': v.as_of, 'symbol': r.symbol, 'sector': r.sector, 'return': ret,
                          'excess': ret - res.baseline, 'score': r.score, 'rank': r.rank,
                          'badges': list(r.badges), 'family_scores': dict(r.family_scores),
                          'breakdown': {f: dict(x) for f, x in r.breakdown.items()}})
    return sorted(cases, key=lambda c: (c['return'], c['as_of'], c['symbol']))[:limit]


def short_history(views: Sequence[BoardView], scheme) -> List[dict]:
    from terminal.prosperity.schemes import FACTOR_SPECS
    factors = [f for f, _ in scheme.weights if FACTOR_SPECS[f].family == 'expectation']
    if not factors or not views:
        return [{'factor': f, 'periods': 0, 'latest_coverage': None, 'verdict': 'UNKNOWN'} for f in factors]
    latest = max(views, key=lambda v: v.as_of)

    def has_z(r: Row, f: str) -> bool:
        return (r.breakdown.get(f) or {}).get('z') is not None

    out = []
    for f in factors:
        periods = sum(any(has_z(r, f) for r in ranked_rows(v)) for v in views)
        ranked = ranked_rows(latest)
        cov = sum(has_z(r, f) for r in ranked) / len(ranked) if ranked else None
        out.append({'factor': f, 'periods': periods, 'latest_coverage': cov, 'verdict': 'UNKNOWN'})
    return out


# ---------------------------------------------------------------------------
# Controls: 12-1 momentum Top 40 and point-in-time Premium
# ---------------------------------------------------------------------------

def momentum_top(view: BoardView, prices: PriceBook) -> Optional[Tuple[str, ...]]:
    t0 = bisect.bisect_right(prices.calendar, view.as_of) - 1
    if t0 < MOMENTUM_LOOKBACK:
        return None
    lo, hi = t0 - MOMENTUM_LOOKBACK, t0 - MOMENTUM_SKIP
    scored = []
    for r in universe(view):
        a, b = prices.close_at(r.symbol, lo), prices.close_at(r.symbol, hi)
        if a is None or b is None or prices.unresolved_overlaps(r.symbol, lo, t0):
            continue
        if any(j['kind'] == 'split_suspect' for j in prices.jumps(r.symbol, lo, t0)):
            continue
        scored.append((-(b / a - 1), r.symbol))
    return tuple(s for _, s in sorted(scored)[:TOP_N])


def load_fundamentals(market, symbols: Iterable[str]) -> Dict[str, dict]:
    return {s: {'income': market.get_income(s, limit=0), 'metrics': market.get_metrics(s, limit=0),
                'earnings': market.get_fmp_earnings(s)}
            for s in sorted(set(symbols))}


def _available_on(row: Mapping[str, Any], earnings: Sequence[Mapping[str, Any]]) -> Optional[str]:
    """The engine's replay rule: placeholder dates are rejected and dated by the EPS release."""
    from src.data.prosperity_quality import statement_known_on
    return statement_known_on(dict(row), earnings_rows=[dict(e) for e in earnings])


class _PitFundamentalStore:
    """The three reads `build_premium_pool` makes, cut to quarters disclosed by `as_of`."""

    def __init__(self, fundamentals: Mapping[str, dict], as_of: str):
        self._income: Dict[str, List[dict]] = {}
        self._metrics: Dict[str, List[dict]] = {}
        for s, f in fundamentals.items():
            earnings = f.get('earnings', ())
            income = sorted((r for r in f.get('income', ()) if (_available_on(r, earnings) or '9999') <= as_of),
                            key=lambda r: r['date'], reverse=True)
            known = {r['date'] for r in income}
            self._income[s] = income
            self._metrics[s] = sorted((m for m in f.get('metrics', ()) if m.get('date') in known),
                                      key=lambda m: m['date'], reverse=True)

    def get_coverage(self, dataset: str) -> Dict[str, str]:
        return {s: 'ok' for s in self._income}

    def get_income(self, symbol: str, limit: int = 8) -> List[dict]:
        rows = self._income.get(symbol, [])
        return rows[:limit] if limit > 0 else list(rows)

    def get_metrics(self, symbol: str, limit: int = 8) -> List[dict]:
        rows = self._metrics.get(symbol, [])
        return rows[:limit] if limit > 0 else list(rows)


def premium_members(view: BoardView, fundamentals: Mapping[str, dict], *, as_of: str) -> dict:
    from terminal.selection_compass import build_premium_pool
    members = universe(view)
    symbols = [r.symbol for r in members]
    store = _PitFundamentalStore({s: fundamentals[s] for s in symbols if s in fundamentals}, as_of)
    result = build_premium_pool(store=store, symbols=symbols, as_of=as_of,
                                beta_observations={r.symbol: r.beta for r in members},
                                min_fundamental_coverage=0.0, min_beta_coverage=0.0)
    chosen = tuple(m['symbol'] for m in result.get('members', ())) if result.get('available') else ()
    return {'members': chosen, 'coverage': result.get('coverage'), 'basis': 'approximate'}


# ---------------------------------------------------------------------------
# Report assembly and BH-FDR
# ---------------------------------------------------------------------------

NOTES = ('不作上线门槛：体检报告只用来发现榜单的明显毛病，Boss 看完再拍板。',
         '价格收益，不含分红：daily_price 收盘价，已处理有记录的拆股。',
         '期数约 20，置信区间宽；p 值只作参考。')


def _group_mean(returns: Mapping[str, Tuple[Optional[float], str]], symbols: Iterable[str]) -> Optional[float]:
    return _mean(returns.get(s, (None, ''))[0] for s in symbols)


def _series(points: Sequence[Tuple[PeriodResult, Optional[float]]], *, ic: bool = False) -> dict:
    """Interval (or IC summary) over the periods that have a value; lag from those periods' windows."""
    used = [(p, v) for p, v in points if v is not None]
    lag = overlap_lag([(p.start_i, p.end_i) for p, _ in used])
    values = [v for _, v in used]
    out = ic_summary(values, lag=lag) if ic else interval(values, lag=lag)
    out['as_of'] = [p.as_of for p, _ in used]
    return out


def _period_row(res: PeriodResult) -> dict:
    return {'as_of': res.as_of, 'status': res.status, 'start': res.start, 'end': res.end,
            'baseline': res.baseline, 'tier_excess': [res.tier_excess.get(t) for t in range(TIERS)],
            'spread': res.spread, 'ic': res.ic, 'top_excess': res.top_excess,
            'coverage': {k: list(v) for k, v in res.coverage.items()}}


def scheme_report(views: Sequence[BoardView], weekly: Sequence[BoardView], prices: PriceBook,
                  fundamentals: Mapping[str, dict], *, memo: Optional[dict] = None) -> dict:
    from terminal.prosperity.schemes import SCHEMES
    views = sorted(views, key=lambda v: v.as_of)
    memo = {} if memo is None else memo
    results = {h: [evaluate_board(v, prices, horizon=h) for v in views] for h in HORIZONS}
    tops = [top_symbols(v) for v in views]
    moms = [momentum_top(v, prices) for v in views]
    premiums = []
    for v in views:
        members = universe(v)
        key = (v.as_of, tuple((r.symbol, r.beta) for r in members))
        if key not in memo:
            memo[key] = premium_members(v, fundamentals, as_of=v.as_of)
        premiums.append(memo[key])

    tiers_ch, ic_ch, control_ch = {}, {}, {'common_periods': {}, 'common_range': {}, 'horizons': {},
                                           'premium_basis': 'approximate'}
    control_ch['premium_periods'] = [
        {'as_of': v.as_of, 'members': len(p['members']),
         'fundamental_ready': ((p.get('coverage') or {}).get('fundamental_ready') or {}).get('ratio'),
         'beta_ready': ((p.get('coverage') or {}).get('beta_ready') or {}).get('ratio')}
        for v, p in zip(views, premiums)]
    coverage, unknown, large = {}, [], {}
    for h in HORIZONS:
        done = [(i, r) for i, r in enumerate(results[h]) if r.status == 'complete']
        means = [_series([(r, r.tier_excess.get(t)) for _, r in done]) for t in range(TIERS)]
        tiers_ch[str(h)] = {'tiers': means, 'spread': _series([(r, r.spread) for _, r in done]),
                            'top': _series([(r, r.top_excess) for _, r in done]),
                            'monotonic': monotonic([m['mean'] for m in means]),
                            'periods': [_period_row(r) for r in results[h]]}
        ic_ch[str(h)] = {**_series([(r, r.ic) for _, r in done], ic=True),
                         'series': [[r.as_of, r.ic] for r in results[h] if r.status == 'complete']}
        rows = []
        for i, r in done:
            mom = moms[i]
            groups = {'top': tops[i], 'momentum': mom, 'premium': premiums[i]['members']}
            excess = {k: (None if g is None else group_excess(r.returns, g, r.baseline)) for k, g in groups.items()}
            if all(x is not None for x in excess.values()):
                raw = {k: _group_mean(r.returns, g) for k, g in groups.items()}
                rows.append((r, excess, raw))
        control_ch['common_periods'][str(h)] = len(rows)
        control_ch['common_range'][str(h)] = [rows[0][0].as_of, rows[-1][0].as_of] if rows else None
        control_ch['horizons'][str(h)] = {
            **{k: {'mean_return': _mean(x[2][k] for x in rows), 'excess': _series([(x[0], x[1][k]) for x in rows])}
               for k in ('top', 'momentum', 'premium')},
            'universe': {'mean_return': _mean(x[0].baseline for x in rows), 'excess': 0.0 if rows else None},
            'top_minus_momentum': {'excess': _series([(x[0], x[1]['top'] - x[1]['momentum']) for x in rows])},
            'top_minus_premium': {'excess': _series([(x[0], x[1]['top'] - x[1]['premium']) for x in rows])},
        }
        ranked_valid = ranked_total = uni_valid = uni_total = 0
        reasons: Dict[str, int] = {}
        for i, r in done:
            ranked_valid += r.coverage['ranked'][0]
            ranked_total += r.coverage['ranked'][1]
            uni_valid += r.coverage['universe'][0]
            uni_total += r.coverage['universe'][1]
            for row_ in ranked_rows(views[i]):
                ret, why = r.returns[row_.symbol]
                if ret is None:
                    reasons[why] = reasons.get(why, 0) + 1
                    unknown.append({'symbol': row_.symbol, 'as_of': r.as_of, 'horizon': h, 'reason': why,
                                    'last_price_date': prices.last_date(row_.symbol)})
                for j in prices.jumps(row_.symbol, r.start_i, r.end_i):
                    if j['kind'] == 'large_move':
                        large[(row_.symbol, j['date'])] = j['ratio']
        coverage[str(h)] = {'ranked': [ranked_valid, ranked_total], 'universe': [uni_valid, uni_total],
                            'reasons': dict(sorted(reasons.items()))}

    overlap = [{'as_of': v.as_of, 'overlap': len(set(tops[i]) & set(moms[i])), 'top_n': len(tops[i]),
                'momentum_n': len(moms[i]), 'top': list(tops[i]), 'momentum': list(moms[i])}
               for i, v in enumerate(views) if moms[i] is not None]
    conc = [{'as_of': v.as_of, **concentration(v, tops[i])} for i, v in enumerate(views)]
    quarterly = [{'as_of': views[i].as_of, 'turnover': turnover(tops[i - 1], tops[i])} for i in range(1, len(views))]
    wk = sorted(weekly, key=lambda v: v.as_of)
    wtops = [top_symbols(v) for v in wk]
    weekly_turn = ({'n': len(wk)} if len(wk) < 2 else
                   {'n': len(wk), 'series': [{'as_of': wk[i].as_of, 'turnover': turnover(wtops[i - 1], wtops[i])}
                                             for i in range(1, len(wk))]})
    if 'series' in weekly_turn:
        weekly_turn['mean'] = _mean(x['turnover'] for x in weekly_turn['series'])
    no_price: Dict[str, int] = {}
    for v in views:
        for r in universe(v):
            if prices.last_date(r.symbol) is None:
                no_price[r.symbol] = no_price.get(r.symbol, 0) + 1
    members = {r.symbol for v in views for r in universe(v)}
    ranked_all = [r for v in views for r in ranked_rows(v)]
    first = views[0] if views else None
    scheme = SCHEMES.get(first.scheme_id) if first else None
    header = {
        'scheme_id': first.scheme_id if first else None, 'scheme_hash': first.scheme_hash if first else None,
        'code_version': first.code_version if first else None, 'periods': len(views),
        'as_of_range': [views[0].as_of, views[-1].as_of] if views else None,
        'complete': {str(h): sum(r.status == 'complete' for r in results[h]) for h in HORIZONS},
        'in_progress': {str(h): sum(r.status == 'in_progress' for r in results[h]) for h in HORIZONS},
        'approximate_share': (sum(r.pit_basis == 'approximate' for r in ranked_all) / len(ranked_all)
                              if ranked_all else None),
        'return_coverage': coverage, 'price_last_date': prices.calendar[-1] if prices.calendar else None,
        'notes': list(NOTES),
    }
    return {
        'header': header,
        'chapters': {
            CHAPTERS[0]: tiers_ch,
            CHAPTERS[1]: ic_ch,
            CHAPTERS[2]: control_ch,
            CHAPTERS[3]: {'periods': overlap, 'mean_overlap': _mean(x['overlap'] for x in overlap),
                          'mean_share': _mean(x['overlap'] / x['top_n'] for x in overlap if x['top_n'])},
            CHAPTERS[4]: {'periods': conc, 'mean_max_share': _mean(x['max_share'] for x in conc),
                          'mean_hhi': _mean(x['hhi'] for x in conc)},
            CHAPTERS[5]: {'quarterly': quarterly, 'quarterly_mean': _mean(x['turnover'] for x in quarterly),
                          'weekly': weekly_turn},
            CHAPTERS[6]: failure_cases([(v, results[3][i]) for i, v in enumerate(views)]),
            CHAPTERS[7]: short_history(views, scheme) if scheme else [],
        },
        'appendix': {
            'unknown': unknown,
            'no_price': [{'symbol': s, 'periods': n} for s, n in sorted(no_price.items())],
            'large_moves': [{'symbol': s, 'date': d, 'ratio': x} for (s, d), x in sorted(large.items())],
            'splits': [{'symbol': s, 'fmp_date': d, 'factor': f, 'state': st}
                       for s, d, f, st in prices.split_events if s in members],
            'adjustments': [{'symbol': s, 'date': d, 'factor': f} for s, d, f in prices.adjustments if s in members],
        },
    }


def bh_adjust(p_values: Sequence[Optional[float]]) -> List[Optional[float]]:
    """BH over a fixed family; tests without a p value count as p = 1 and stay unknown.

    Reuses the event-study helper (north star §4.3: reuse backtest/event_study/stats.py)."""
    from backtest.event_study.stats import _apply_bh_fdr_to_p_values
    adjusted = _apply_bh_fdr_to_p_values([1.0 if p is None else float(p) for p in p_values])
    return [None if p is None else float(a) for p, a in zip(p_values, adjusted)]


def apply_bh(reports: Mapping[str, dict]) -> dict:
    tests = []
    for rep in reports.values():
        for h in HORIZONS:
            tests.append(rep['chapters'][CHAPTERS[0]][str(h)]['spread'])
            tests.append(rep['chapters'][CHAPTERS[1]][str(h)])
    adjusted = bh_adjust([t.get('p') for t in tests])
    for t, a in zip(tests, adjusted):
        t['p_bh'] = a
    family = {'family': len(tests), 'estimated': sum(t.get('p') is not None for t in tests)}
    for rep in reports.values():
        rep['header']['bh'] = dict(family)
    return family
