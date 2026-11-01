"""M10 景气精选池: F1 live weekly board ∩ top-100 average dollar volume → prosperity_pool.json.

Reads prosperity.db (one board) and market.db (daily_price) read-only. Not in code_version:
pool selection never changes a score, only which already-scored rows are published.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import tempfile
from typing import Any, Mapping, Optional, Sequence, Tuple

from config.settings import DATA_DIR
from src.data.prosperity_storage import content_hash
from terminal.prosperity.schemes import SCHEMES, scheme_hash

CST = timezone(timedelta(hours=8))

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


def criteria_payload() -> dict:
    return {'scheme_id': POOL_SCHEME_ID, 'scheme_hash': scheme_hash(SCHEMES[POOL_SCHEME_ID]),
            'grades': list(POOL_GRADES),
            'liquidity': {'metric': 'avg_dollar_volume', 'trading_days': LIQUIDITY_DAYS,
                          'min_days': LIQUIDITY_MIN_DAYS, 'top': LIQUIDITY_TOP}}


def _window_error(as_of: str, start: str, end: str) -> Optional[str]:
    """The price window must belong to this board date: end ≤ as_of, at most 7 days stale."""
    day, first, last = (date.fromisoformat(x) for x in (as_of, start, end))
    if not first < last <= day:
        return 'window_order'
    if (day - last).days > LIQUIDITY_MAX_LAG_DAYS:
        return 'window_stale'
    if (day - first).days >= LIQUIDITY_LOOKBACK_CALENDAR_DAYS:
        return 'window_too_long'
    return None


def _stat(covered: int, total: int) -> dict:
    return {'covered': covered, 'total': total, 'ratio': covered / total}


def _utc(at: datetime) -> str:
    return at.astimezone(timezone.utc).isoformat().replace('+00:00', 'Z')


def build_pool(stored, liquidity: LiquidityResult, *, generated_at: str) -> dict:
    board, meta = stored.board, stored.meta
    if (board.scheme_id != POOL_SCHEME_ID or meta.board_type != 'weekly' or meta.mode != 'live'
            or board.scheme_hash != scheme_hash(SCHEMES[POOL_SCHEME_ID])):
        raise ValueError('pool_source_invalid')
    window = tuple(liquidity.window)
    members = set(meta.members)
    if (liquidity.as_of != board.as_of or len(window) != LIQUIDITY_DAYS or list(window) != sorted(set(window))
            or _window_error(board.as_of, window[0], window[-1]) or not set(liquidity.ranks) <= members):
        raise ValueError('pool_liquidity_mismatch')
    stocks = {p['symbol']: p for p in stored.stocks}
    ranked = [r for r in board.rows if r.status == 'ranked']
    graded = sorted((r for r in ranked if r.grade in POOL_GRADES), key=lambda r: r.rank)
    selected = []
    for r in graded:
        dv_rank = liquidity.ranks.get(r.symbol)
        if dv_rank is None or dv_rank > LIQUIDITY_TOP:
            continue
        stock = stocks.get(r.symbol, {})
        selected.append({
            'symbol': r.symbol, 'name': stock.get('name'), 'sector': stock.get('sector'),
            'industry': stock.get('industry'), 'rank': r.rank, 'grade': r.grade, 'score': r.score,
            'family_scores': dict(r.family_scores), 'badges': list(r.badges),
            'dollar_volume_rank': dv_rank, 'avg_dollar_volume': liquidity.averages[r.symbol]})
    symbols = sorted(members)
    return {
        'schema_version': POOL_SCHEMA_VERSION, 'name': POOL_NAME, 'as_of': board.as_of,
        'generated_at': generated_at, 'criteria': criteria_payload(),
        'source': {'board_type': meta.board_type, 'code_version': board.code_version,
                   'computed_at': meta.computed_at, 'content_hash': content_hash(stored)},
        'universe': {'name': 'extended', 'count': len(symbols),
                     'symbols_sha256': hashlib.sha256('\n'.join(symbols).encode('utf-8')).hexdigest()},
        'coverage': {'ranked': _stat(len(ranked), len(symbols)),
                     'liquidity_ranked': _stat(len(liquidity.ranks), len(symbols)),
                     'eligible': len(graded), 'selected': len(selected),
                     'trading_window': [window[0], window[-1]]},
        'members': selected,
    }


def _parse_time(value: Any) -> datetime:
    if not isinstance(value, str) or not value:
        raise ValueError('missing timestamp')
    parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if parsed.tzinfo is None:
        raise ValueError('timestamp must include timezone')
    return parsed.astimezone(timezone.utc)


def _is_int(value) -> bool:
    return type(value) is int


def _finite(value) -> bool:
    return type(value) in (int, float) and math.isfinite(value)


def validate_prosperity_pool(payload: Any, *, now: Optional[datetime] = None, check_freshness: bool = True) -> None:
    """Raise ValueError (invalid), RuntimeError (criteria mismatch) or TimeoutError (stale)."""
    if not isinstance(payload, dict):
        raise ValueError('pool payload must be an object')
    if payload.get('schema_version') != POOL_SCHEMA_VERSION or payload.get('name') != POOL_NAME:
        raise ValueError('pool schema/name mismatch')
    if payload.get('criteria') != criteria_payload():
        raise RuntimeError('pool criteria mismatch')
    as_of = payload.get('as_of')
    if not isinstance(as_of, str) or date.fromisoformat(as_of).isoformat() != as_of:
        raise ValueError('pool as_of invalid')
    generated = _parse_time(payload.get('generated_at'))
    if check_freshness:
        current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
        age = (current - generated).total_seconds()
        board_age = (current.astimezone(CST).date() - date.fromisoformat(as_of)).days
        if age < -300 or board_age < 0:
            raise ValueError('pool generated_at/as_of in the future')
        if age > POOL_MAX_AGE_DAYS * 86400 or board_age > POOL_MAX_AGE_DAYS:
            raise TimeoutError('pool snapshot stale')
    universe, coverage, members = payload.get('universe'), payload.get('coverage'), payload.get('members')
    if not isinstance(universe, dict) or universe.get('name') != 'extended':
        raise ValueError('pool universe invalid')
    count, digest = universe.get('count'), universe.get('symbols_sha256')
    if (not _is_int(count) or count <= 0 or not isinstance(digest, str) or len(digest) != 64
            or any(c not in '0123456789abcdef' for c in digest)):
        raise ValueError('pool universe metadata invalid')
    if not isinstance(coverage, dict):
        raise ValueError('pool coverage invalid')
    for key in ('ranked', 'liquidity_ranked'):
        stat = coverage.get(key)
        if not isinstance(stat, dict):
            raise ValueError('pool coverage stat invalid')
        covered, total, ratio = stat.get('covered'), stat.get('total'), stat.get('ratio')
        if not _is_int(covered) or not _is_int(total) or total != count or not 0 <= covered <= total:
            raise ValueError('pool coverage counts invalid')
        if not _finite(ratio) or not math.isclose(ratio, covered / total, abs_tol=1e-12) or ratio < COVERAGE_MIN:
            raise ValueError('pool coverage ratio invalid')
    eligible, selected = coverage.get('eligible'), coverage.get('selected')
    if (not _is_int(eligible) or not _is_int(selected) or not isinstance(members, list)
            or selected != len(members) or not 0 <= selected <= eligible <= coverage['ranked']['covered']):
        raise ValueError('pool selection counts invalid')
    window = coverage.get('trading_window')
    if not isinstance(window, list) or len(window) != 2 or not all(isinstance(d, str) for d in window):
        raise ValueError('pool trading window invalid')
    if _window_error(as_of, window[0], window[1]):
        raise ValueError('pool trading window not bound to as_of')
    seen, last_rank = set(), 0
    for m in members:
        if not isinstance(m, dict):
            raise ValueError('pool member must be an object')
        symbol = m.get('symbol')
        if not isinstance(symbol, str) or not symbol or symbol != symbol.upper() or symbol in seen:
            raise ValueError('pool member symbol invalid or duplicated')
        seen.add(symbol)
        if m.get('grade') not in POOL_GRADES:
            raise ValueError('pool member grade invalid')
        rank = m.get('rank')
        if not _is_int(rank) or rank <= last_rank:
            raise ValueError('pool member rank invalid or out of order')
        last_rank = rank
        dv_rank = m.get('dollar_volume_rank')
        if not _is_int(dv_rank) or not 1 <= dv_rank <= LIQUIDITY_TOP:
            raise ValueError('pool member dollar-volume rank invalid')
        if not _finite(m.get('score')) or not _finite(m.get('avg_dollar_volume')) or m['avg_dollar_volume'] <= 0:
            raise ValueError('pool member number invalid')
        badges = m.get('badges')
        if not isinstance(badges, list) or not all(isinstance(b, str) for b in badges):
            raise ValueError('pool member badges invalid')
        if not isinstance(m.get('family_scores'), dict):
            raise ValueError('pool member family scores invalid')


def load_prosperity_pool(path=POOL_PATH, *, now: Optional[datetime] = None) -> dict:
    target = Path(path)
    if not target.is_file():
        return {'available': False, 'reason': 'prosperity_pool_missing', 'members': []}
    try:
        payload = json.loads(target.read_text(encoding='utf-8'))
        validate_prosperity_pool(payload, now=now)
    except RuntimeError:
        return {'available': False, 'reason': 'prosperity_pool_criteria_mismatch', 'members': []}
    except TimeoutError:
        return {'available': False, 'reason': 'prosperity_pool_stale', 'members': []}
    except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError):
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
