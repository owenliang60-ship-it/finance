"""M10 景气精选池: F1 live weekly board, first tier (rank ≤ ceil(n/5)) ∩ STRICT/FULL ∩
at least 31 of the last 90 trading days in the daily dollar-volume top 100 → prosperity_pool.json.

Reads prosperity.db (one board) and market.db (daily_price) read-only. Not in code_version:
pool selection never changes a score, only which already-scored rows are published.
"""
from __future__ import annotations

import bisect
from dataclasses import dataclass
from datetime import date, datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import re
import tempfile
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from config.settings import DATA_DIR
from src.data.prosperity_gates import CST, instant
from src.data.prosperity_storage import content_hash
from terminal.prosperity.schemes import SCHEMES, scheme_hash

POOL_NAME = '景气精选池'
POOL_SCHEMA_VERSION = 1
POOL_MAX_AGE_DAYS = 8
POOL_PATH = Path(os.environ.get('FINANCE_PROSPERITY_POOL_PATH', str(DATA_DIR / 'pool' / 'prosperity_pool.json')))
POOL_SCHEME_ID = 'F1'
POOL_GRADES = ('STRICT', 'FULL')
LIQUIDITY_TOP = 100
POOL_TIERS = 5                           # first tier = top 1/5 of ranked rows, same as M12 assign_tiers
LIQUIDITY_DAYS = 90                      # 90 SPY trading days (~1 quarter + slack)
LIQUIDITY_MIN_TOP_DAYS = 31              # strictly > 30 days in daily dollar-volume top 100
LIQUIDITY_MAX_SPAN_DAYS = 160            # 90 trading days ≤ 133 calendar days + 7-day lag + holiday headroom
LIQUIDITY_MAX_LAG_DAYS = 7
COVERAGE_MIN = 0.90
CALENDAR_SYMBOL = 'SPY'
_QUERY_BATCH = 500
_MEMBER_KEYS = frozenset({'symbol', 'name', 'sector', 'industry', 'rank', 'grade', 'score',
                          'family_scores', 'badges', 'top100_days'})
_TIMESTAMP = re.compile(r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d{1,6})?(Z|[+-]\d{2}:\d{2})')


class PoolCriteriaMismatch(RuntimeError):
    """The file was built for another scheme/rule set. Distinct from RecursionError (also a RuntimeError)."""


class PoolStale(TimeoutError):
    """generated_at or as_of older than POOL_MAX_AGE_DAYS."""


@dataclass(frozen=True)
class LiquidityResult:
    as_of: str                       # board date these counts were computed for
    window: Tuple[str, ...]          # LIQUIDITY_DAYS ascending trading days
    top_days: Mapping[str, int]      # symbol → days spent in daily top LIQUIDITY_TOP (0..LIQUIDITY_DAYS)
    daily_min: int                   # fewest members with usable dollar volume on any day of `window`


def load_calendar(market) -> List[str]:
    """Ascending trading days on which CALENDAR_SYMBOL has a positive close."""
    return [r[0] for r in market._get_conn().execute(
        'SELECT date FROM daily_price WHERE symbol=? AND close > 0 ORDER BY date', (CALENDAR_SYMBOL,))]


def load_daily_dollar_volumes(market, symbols: Sequence[str], days: Sequence[str]) -> Dict[str, Dict[str, float]]:
    """{symbol: {day: close × volume}} for every usable row on `days`; a missing day stays missing."""
    if not symbols or not days:
        return {}
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


def count_top_days(day_volumes: Mapping[str, Mapping], days: Sequence, top: int) -> Dict[str, int]:
    """Days each symbol spent in that day's dollar-volume top `top`; ties go to the earlier symbol."""
    counts = {s: 0 for s in day_volumes}
    for d in days:
        ranked = sorted((-v[d], s) for s, v in day_volumes.items() if d in v)
        for _, s in ranked[:top]:
            counts[s] += 1
    return counts


def _day(value: Any) -> date:
    """YYYY-MM-DD only. Python 3.11+ also accepts '20261030'; the cloud's 3.10 does not."""
    if not isinstance(value, str) or date.fromisoformat(value).isoformat() != value:
        raise ValueError(f'invalid date: {value!r}')
    return date.fromisoformat(value)


def liquidity_top_days(market, symbols: Sequence[str], as_of: str) -> LiquidityResult:
    """Days each member spent in the daily close × volume top LIQUIDITY_TOP over the last 90 SPY days."""
    board_day = _day(as_of)
    members = sorted(set(symbols))
    if not members:
        raise ValueError('liquidity_no_members')
    calendar = load_calendar(market)
    end_i = bisect.bisect_right(calendar, as_of) - 1
    if end_i + 1 < LIQUIDITY_DAYS:
        raise ValueError(f'liquidity_window_short: {end_i + 1} < {LIQUIDITY_DAYS} trading days')
    window = tuple(calendar[end_i - LIQUIDITY_DAYS + 1:end_i + 1])
    lag = (board_day - _day(window[-1])).days
    if lag > LIQUIDITY_MAX_LAG_DAYS:
        raise ValueError(f'liquidity_prices_stale: last trading day {window[-1]} is {lag} days before {as_of}')
    volumes = load_daily_dollar_volumes(market, members, window)
    counts = count_top_days(volumes, window, LIQUIDITY_TOP)
    daily_min = min(sum(1 for s in members if d in volumes.get(s, ())) for d in window)
    return LiquidityResult(as_of, window, {s: int(counts.get(s, 0)) for s in members}, daily_min)


def tier_cut(n_ranked: int) -> int:
    """Rank of the last first-tier row: ceil(n/5). Ranks are contiguous 1..n (prosperity_storage)."""
    return -(-n_ranked // POOL_TIERS)


def criteria_payload() -> dict:
    return {'scheme_id': POOL_SCHEME_ID, 'scheme_hash': scheme_hash(SCHEMES[POOL_SCHEME_ID]),
            'grades': list(POOL_GRADES),
            'tier': {'tiers': POOL_TIERS},
            'liquidity': {'metric': 'top_dollar_volume_days', 'calendar': CALENDAR_SYMBOL,
                          'trading_days': LIQUIDITY_DAYS, 'min_top_days': LIQUIDITY_MIN_TOP_DAYS,
                          'top': LIQUIDITY_TOP}}


def _window_error(as_of: str, start: str, end: str) -> Optional[str]:
    """The price window must belong to this board date: end ≤ as_of, at most 7 days stale."""
    day, first, last = (_day(x) for x in (as_of, start, end))
    if not first < last <= day:
        return 'window_order'
    if (day - last).days > LIQUIDITY_MAX_LAG_DAYS:
        return 'window_stale'
    if (day - first).days >= LIQUIDITY_MAX_SPAN_DAYS:
        return 'window_too_long'
    return None


def _stat(covered: int, total: int) -> dict:
    return {'covered': covered, 'total': total, 'ratio': covered / total}


def _utc(at: datetime) -> str:
    return instant(at).isoformat().replace('+00:00', 'Z')


def _universe(members) -> dict:
    symbols = sorted(set(members))
    return {'name': 'extended', 'count': len(symbols),
            'symbols_sha256': hashlib.sha256('\n'.join(symbols).encode('utf-8')).hexdigest()}


def _is_int(value) -> bool:
    return type(value) is int


def _finite(value) -> bool:
    if type(value) not in (int, float):
        return False
    try:
        return math.isfinite(float(value))
    except OverflowError:          # a 400-digit JSON integer
        return False


def build_pool(stored, liquidity: LiquidityResult, *, generated_at: str) -> dict:
    board, meta = stored.board, stored.meta
    if (board.scheme_id != POOL_SCHEME_ID or meta.board_type != 'weekly' or meta.mode != 'live'
            or board.scheme_hash != scheme_hash(SCHEMES[POOL_SCHEME_ID])):
        raise ValueError('pool_source_invalid')
    window = tuple(liquidity.window)
    members = set(meta.members)
    if (liquidity.as_of != board.as_of or len(window) != LIQUIDITY_DAYS or list(window) != sorted(set(window))
            or _window_error(board.as_of, window[0], window[-1]) or not set(liquidity.top_days) <= members
            or not all(_is_int(v) and 0 <= v <= LIQUIDITY_DAYS for v in liquidity.top_days.values())
            or not _is_int(liquidity.daily_min) or not 0 <= liquidity.daily_min <= len(members)):
        raise ValueError('pool_liquidity_mismatch')
    stocks = {p['symbol']: p for p in stored.stocks}
    ranked = [r for r in board.rows if r.status == 'ranked']
    cut = tier_cut(len(ranked))
    graded = sorted((r for r in ranked if r.rank <= cut and r.grade in POOL_GRADES), key=lambda r: r.rank)
    selected = []
    for r in graded:
        top_days = liquidity.top_days.get(r.symbol, 0)
        if top_days < LIQUIDITY_MIN_TOP_DAYS:
            continue
        stock = stocks.get(r.symbol, {})
        selected.append({
            'symbol': r.symbol, 'name': stock.get('name'), 'sector': stock.get('sector'),
            'industry': stock.get('industry'), 'rank': r.rank, 'grade': r.grade, 'score': r.score,
            'family_scores': dict(r.family_scores), 'badges': list(r.badges),
            'top100_days': top_days})
    universe = _universe(members)
    return {
        'schema_version': POOL_SCHEMA_VERSION, 'name': POOL_NAME, 'as_of': board.as_of,
        'generated_at': generated_at, 'criteria': criteria_payload(),
        'source': {'board_type': meta.board_type, 'code_version': board.code_version,
                   'computed_at': meta.computed_at, 'content_hash': content_hash(stored)},
        'universe': universe,
        'coverage': {'ranked': _stat(len(ranked), universe['count']),
                     'liquidity_daily': _stat(liquidity.daily_min, universe['count']),
                     'tier_cut': cut, 'eligible': len(graded), 'selected': len(selected),
                     'trading_window': [window[0], window[-1]]},
        'members': selected,
    }


def _parse_time(value: Any) -> datetime:
    if not isinstance(value, str) or not _TIMESTAMP.fullmatch(value):
        raise ValueError(f'invalid timestamp: {value!r}')
    try:
        return instant(value)
    except OverflowError:
        raise ValueError(f'timestamp out of range: {value!r}') from None


def validate_prosperity_pool(payload: Any, *, now: Optional[datetime] = None, check_freshness: bool = True) -> None:
    """Raise ValueError (invalid), RuntimeError (criteria mismatch) or TimeoutError (stale)."""
    if not isinstance(payload, dict):
        raise ValueError('pool payload must be an object')
    if payload.get('schema_version') != POOL_SCHEMA_VERSION or payload.get('name') != POOL_NAME:
        raise ValueError('pool schema/name mismatch')
    if payload.get('criteria') != criteria_payload():
        raise PoolCriteriaMismatch('pool criteria mismatch')
    as_of = payload.get('as_of')
    _day(as_of)
    generated = _parse_time(payload.get('generated_at'))
    if check_freshness:
        current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
        age = (current - generated).total_seconds()
        board_age = (current.astimezone(CST).date() - _day(as_of)).days
        if age < -300 or board_age < 0:
            raise ValueError('pool generated_at/as_of in the future')
        if age > POOL_MAX_AGE_DAYS * 86400 or board_age > POOL_MAX_AGE_DAYS:
            raise PoolStale('pool snapshot stale')
    source = payload.get('source')
    if (not isinstance(source, dict) or source.get('board_type') != 'weekly'
            or not isinstance(source.get('code_version'), str) or not source['code_version']
            or not isinstance(source.get('content_hash'), str) or len(source['content_hash']) != 64
            or any(c not in '0123456789abcdef' for c in source['content_hash'])):
        raise ValueError('pool source invalid')
    computed = _parse_time(source.get('computed_at'))
    if computed > generated:
        raise ValueError('pool generated before its board was computed')
    universe, coverage, members = payload.get('universe'), payload.get('coverage'), payload.get('members')
    if not isinstance(universe, dict) or universe.get('name') != 'extended':
        raise ValueError('pool universe invalid')
    count, digest = universe.get('count'), universe.get('symbols_sha256')
    if (not _is_int(count) or count <= 0 or not isinstance(digest, str) or len(digest) != 64
            or any(c not in '0123456789abcdef' for c in digest)):
        raise ValueError('pool universe metadata invalid')
    if not isinstance(coverage, dict):
        raise ValueError('pool coverage invalid')
    for key in ('ranked', 'liquidity_daily'):
        stat = coverage.get(key)
        if not isinstance(stat, dict):
            raise ValueError('pool coverage stat invalid')
        covered, total, ratio = stat.get('covered'), stat.get('total'), stat.get('ratio')
        if not _is_int(covered) or not _is_int(total) or total != count or not 0 <= covered <= total:
            raise ValueError('pool coverage counts invalid')
        if not _finite(ratio) or not math.isclose(ratio, covered / total, abs_tol=1e-12):
            raise ValueError('pool coverage ratio invalid')
        if ratio < COVERAGE_MIN:
            # ranked comes from the stored board, so a publish-only retry cannot change it.
            # liquidity_daily comes from market.db prices; backfilling them and retrying can fix it.
            side = 'ranked' if key == 'ranked' else 'liquidity'
            raise ValueError(f'pool_{side}_coverage_below_floor: {covered}/{total} < {COVERAGE_MIN}')
    eligible, selected = coverage.get('eligible'), coverage.get('selected')
    if (not _is_int(eligible) or not _is_int(selected) or not isinstance(members, list)
            or selected != len(members) or not 0 <= selected <= eligible <= coverage['ranked']['covered']):
        raise ValueError('pool selection counts invalid')
    cut = coverage.get('tier_cut')
    if not _is_int(cut) or cut != tier_cut(coverage['ranked']['covered']) or eligible > cut:
        raise ValueError('pool tier cut invalid')
    window = coverage.get('trading_window')
    if not isinstance(window, list) or len(window) != 2 or not all(isinstance(d, str) for d in window):
        raise ValueError('pool trading window invalid')
    if _window_error(as_of, window[0], window[1]):
        raise ValueError('pool trading window not bound to as_of')
    seen, last_rank = set(), 0
    for m in members:
        if not isinstance(m, dict) or set(m) != _MEMBER_KEYS:
            raise ValueError('pool member must be an object with exact schema keys')
        symbol = m.get('symbol')
        if not isinstance(symbol, str) or not symbol or symbol != symbol.upper() or symbol in seen:
            raise ValueError('pool member symbol invalid or duplicated')
        seen.add(symbol)
        if m.get('grade') not in POOL_GRADES:
            raise ValueError('pool member grade invalid')
        rank = m.get('rank')
        if not _is_int(rank) or rank <= last_rank or rank > cut:
            raise ValueError('pool member rank invalid or out of order')
        if not all(m.get(k) is None or isinstance(m.get(k), str) for k in ('name', 'sector', 'industry')):
            raise ValueError('pool member descriptive fields invalid')
        last_rank = rank
        top_days = m.get('top100_days')
        if not _is_int(top_days) or not LIQUIDITY_MIN_TOP_DAYS <= top_days <= LIQUIDITY_DAYS:
            raise ValueError('pool member top-100 days invalid')
        if not _finite(m.get('score')):
            raise ValueError('pool member number invalid')
        badges = m.get('badges')
        if not isinstance(badges, list) or not all(isinstance(b, str) for b in badges):
            raise ValueError('pool member badges invalid')
        families = m.get('family_scores')
        if not isinstance(families, dict) or not all(
                isinstance(k, str) and (v is None or _finite(v)) for k, v in families.items()):
            raise ValueError('pool member family scores invalid')


def board_mismatch(pool: dict, stored) -> Optional[str]:
    """Board-level counts the pool file reports, recomputed from the board itself.

    validate_prosperity_pool only proves the file is self-consistent: raising coverage.ranked
    raises tier_cut with it and lets a real out-of-tier row in. A reader that can open the board
    must call this too. Returns the first mismatched field name, or None. Assumes the pool already
    passed validate_prosperity_pool. It does NOT check members: each member's presence, status,
    rank and grade must still be compared with the board row by the caller.
    """
    if pool['as_of'] != stored.board.as_of:
        return 'as_of'
    if pool['source']['content_hash'] != content_hash(stored):
        return 'content_hash'
    universe, coverage = pool['universe'], pool['coverage']
    expected = _universe(stored.meta.members)
    if universe['count'] != expected['count'] or universe['symbols_sha256'] != expected['symbols_sha256']:
        return 'universe'
    ranked = [r for r in stored.board.rows if r.status == 'ranked']
    if coverage['ranked']['covered'] != len(ranked):
        return 'ranked'
    cut = tier_cut(len(ranked))
    if coverage['tier_cut'] != cut:
        return 'tier_cut'
    if coverage['eligible'] != sum(r.rank <= cut and r.grade in POOL_GRADES for r in ranked):
        return 'eligible'
    return None


def load_prosperity_pool(path=POOL_PATH, *, now: Optional[datetime] = None) -> dict:
    target = Path(path)
    if not target.is_file():
        return {'available': False, 'reason': 'prosperity_pool_missing', 'members': []}
    try:
        payload = json.loads(target.read_text(encoding='utf-8'))
        validate_prosperity_pool(payload, now=now)
    except PoolCriteriaMismatch:
        return {'available': False, 'reason': 'prosperity_pool_criteria_mismatch', 'members': []}
    except PoolStale:
        return {'available': False, 'reason': 'prosperity_pool_stale', 'members': []}
    except (OSError, ValueError, TypeError, KeyError, OverflowError, RecursionError, json.JSONDecodeError):
        return {'available': False, 'reason': 'prosperity_pool_invalid', 'members': []}
    return {**payload, 'available': True, 'reason': None}


def _fsync_dir(directory: Path) -> None:
    fd = os.open(str(directory), os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def publish_prosperity_pool(payload: dict, path=POOL_PATH, *, now: Optional[datetime] = None) -> Path:
    """Atomic replace. A failure before os.replace leaves the old file; after it, the new
    (validated) file is visible even though the call raises (plan §8.2 failure semantics)."""
    validate_prosperity_pool(payload, now=now)
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    temp_path = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=target.parent,
                                         prefix='.prosperity_pool.', suffix='.tmp', delete=False) as handle:
            temp_path = Path(handle.name)
            json.dump(payload, handle, ensure_ascii=False, indent=2, allow_nan=False)
            handle.write('\n')
            handle.flush()
            os.fsync(handle.fileno())
        validate_prosperity_pool(json.loads(temp_path.read_text(encoding='utf-8')), now=now)
        os.replace(temp_path, target)
        temp_path = None
        _fsync_dir(target.parent)
        return target
    finally:
        if temp_path is not None:
            temp_path.unlink(missing_ok=True)


def pool_publisher(*, store, market, path=POOL_PATH, now=None):
    """M9 publish hook: build this week's pool from the stored F1 live board and publish it.

    run_week calls it only for live weeks and marks the boards published after every hook succeeds.
    """
    clock = now or (lambda: datetime.now(timezone.utc))

    def publish(keys):
        sources = [k for k in keys if k.board_type == 'weekly'
                   and k.scheme_hash == scheme_hash(SCHEMES[POOL_SCHEME_ID])]
        if len(sources) != 1:
            raise ValueError('pool_source_missing')
        stored = store.get_board(sources[0])
        if stored is None:
            raise ValueError('pool_source_missing')
        at = clock()
        liquidity = liquidity_top_days(market, stored.meta.members, stored.board.as_of)
        publish_prosperity_pool(build_pool(stored, liquidity, generated_at=_utc(at)), path, now=at)

    return publish
