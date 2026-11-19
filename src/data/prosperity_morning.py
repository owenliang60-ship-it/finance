"""Morning section 0d: the prosperity pool bound to its source board, and its weekly momentum picks.

Plans: docs/plans/2026-11-03-prosperity-m14-morning.md (pool binding) and
docs/plans/2026-11-19-morning-0d-prosperity-momentum.md (景气动量精选池 = pool ∩ the M16 14-week
momentum top 30, read from the Saturday delivery output). Read-only on prosperity.db and on the
delivery files. Not part of the engine code_version. The caller (scripts/morning_report.py) wraps
every use in try/except, so a failure here only marks the 0d section unavailable.
"""
from __future__ import annotations

from datetime import datetime
import json
from pathlib import Path
import re
import sqlite3
from typing import Optional

from config.settings import DATA_DIR
from src.data.prosperity_pool import (COVERAGE_MIN, POOL_PATH, POOL_SCHEME_ID, _day, _finite, _is_int, _universe,
                                      load_prosperity_pool)
from src.data.prosperity_storage import BoardKey
from src.data.prosperity_store import ProsperityStore
from src.data.prosperity_web import pool_binding
from terminal.prosperity.schemes import SCHEMES, scheme_hash

PROSPERITY_DB_PATH = DATA_DIR / 'prosperity.db'
PROSPERITY_DELIVERY_DIR = DATA_DIR / 'prosperity-delivery'   # run_prosperity.sh --delivery-dir; a test pins the pair
MOMENTUM_WINDOW_DAYS = 70          # backtest/research/momentum_board.py WINDOWS['14w']; a test pins it
MOMENTUM_MAX_LAG_DAYS = 3          # momentum_board.MAX_LAG_DAYS; a test pins it
_SCORING = 'v4'                    # momentum_board.SCORING
_SYMBOL = re.compile(r'[A-Z0-9.-]+')   # no "_": the 0d text goes out with Telegram legacy Markdown (issue 016)


def _unavailable(reason: str, **extra) -> dict:
    return {'available': False, 'reason': reason, 'members': [], **extra}


def load_bound_prosperity_pool(pool_path=POOL_PATH, db_path=PROSPERITY_DB_PATH, *,
                               now: Optional[datetime] = None) -> dict:
    """The pool file only if it is fresh and matches its source board (north star §4.1 reader rule).

    The board key uses the pool's own code_version: the pool is checked against the board it was
    computed from, so an engine upgrade mid-week does not blank the section (D5 keeps the previous
    live version in the database).
    """
    pool = load_prosperity_pool(pool_path, now=now)
    if not pool.get('available'):
        return pool
    if not Path(db_path).is_file():
        return _unavailable('prosperity_db_unavailable')
    key = BoardKey('weekly', pool['as_of'], scheme_hash(SCHEMES[POOL_SCHEME_ID]), pool['source']['code_version'])
    try:
        with ProsperityStore(Path(db_path), read_only=True) as store:
            stored = store.get_board(key)
    except (sqlite3.Error, OSError, ValueError) as exc:
        return _unavailable('prosperity_db_unavailable', error=type(exc).__name__)
    if stored is None:
        return _unavailable('prosperity_board_missing')
    _, note = pool_binding(pool, stored)
    if not note.startswith('入池'):                 # positive check: an unknown note is never trusted
        return _unavailable('prosperity_pool_board_mismatch', binding_note=note)
    return pool


# --- weekly M16 momentum picks (景气精选池 ∩ 14周动量榜前 30) -------------------------------------

class _MomentumPoolMismatch(ValueError):
    """A well-formed momentum board for another week or another universe than the bound pool."""


def _momentum_unavailable(reason: str, **extra) -> dict:
    return {'available': False, 'reason': reason, 'picks': [], 'unscored': [], **extra}


def _reject_non_finite(token: str):
    raise ValueError(f'non-finite number in momentum board: {token}')


def momentum_board_path(as_of: str, delivery_dir=PROSPERITY_DELIVERY_DIR) -> Path:
    """The file the Saturday delivery writes for this board date (prosperity_delivery.deliver_week)."""
    _day(as_of)
    return Path(delivery_dir) / 'out' / as_of / f'momentum-{as_of.replace("-", "")}.json'


def _symbol_list(values) -> list:
    if (not isinstance(values, list) or len(set(values)) != len(values)
            or not all(isinstance(s, str) and _SYMBOL.fullmatch(s) for s in values)):
        raise ValueError('symbol list invalid or duplicated')
    return values


def _pool_symbols(pool: dict) -> Optional[set]:
    """Member symbols of a bound pool, or None when it lacks what the momentum binding needs."""
    universe, members = pool.get('universe'), pool.get('members')
    try:
        _day(pool.get('as_of'))
        if not isinstance(universe, dict) or 'count' not in universe or 'symbols_sha256' not in universe:
            return None
        if not isinstance(members, list) or not all(isinstance(m, dict) for m in members):
            return None
        return set(_symbol_list([m.get('symbol') for m in members]))
    except ValueError:
        return None


def _check_board(payload, as_of: str, pool_universe: dict, members: set, *, schema_version: int,
                 window_key: str) -> dict:
    """The 14-week board of a payload bound to this pool, or ValueError / _MomentumPoolMismatch."""
    if (not isinstance(payload, dict) or not _is_int(payload.get('schema_version'))
            or payload['schema_version'] != schema_version or payload.get('scoring') != _SCORING):
        raise ValueError('momentum header invalid')
    boards = payload.get('boards')
    if not isinstance(boards, dict) or set(boards) != {window_key}:
        raise ValueError('momentum boards invalid')
    _day(payload.get('as_of'))
    if payload['as_of'] != as_of:
        raise _MomentumPoolMismatch('momentum board is for another week')
    universe = payload.get('universe')
    if not isinstance(universe, dict):
        raise ValueError('momentum universe invalid')
    symbols = _symbol_list(universe.get('symbols'))
    if not symbols or not _is_int(universe.get('count')) or universe['count'] != len(symbols):
        raise ValueError('momentum universe count invalid')
    expected = _universe(symbols)
    if (expected['count'] != pool_universe['count'] or expected['symbols_sha256'] != pool_universe['symbols_sha256']
            or not members <= set(symbols)):
        raise _MomentumPoolMismatch('momentum universe differs from the pool')
    price_day = _day(payload.get('price_date'))
    if not 0 <= (_day(as_of) - price_day).days <= MOMENTUM_MAX_LAG_DAYS:
        raise ValueError('momentum price_date not bound to as_of')
    board = boards[window_key]
    if not isinstance(board, dict):
        raise ValueError('momentum board invalid')
    window = board.get('window')
    if not isinstance(window, list) or len(window) != 2 or not _day(window[0]) < _day(window[1]) == price_day:
        raise ValueError('momentum window invalid')
    if not _is_int(board.get('days')) or board['days'] != MOMENTUM_WINDOW_DAYS:
        raise ValueError('momentum window length invalid')
    scored, unavailable = board.get('scored'), board.get('unavailable')
    if not isinstance(scored, list) or not isinstance(unavailable, dict):
        raise ValueError('momentum rows invalid')
    if not all(_SYMBOL.fullmatch(s) and isinstance(why, str) and why for s, why in unavailable.items()):
        raise ValueError('momentum unavailable rows invalid')
    for r in scored:
        if (not isinstance(r, dict) or type(r.get('eligible')) is not bool
                or not _finite(r.get('score')) or not _finite(r.get('return'))):
            raise ValueError('momentum scored row invalid')
        if r['eligible']:
            if not _is_int(r.get('rank')) or r['rank'] < 1 or r['return'] <= 0:
                raise ValueError('momentum eligible row invalid')
        elif r.get('rank') is not None:
            raise ValueError('momentum ineligible row has a rank')
    names = set(_symbol_list([r.get('symbol') for r in scored]))
    keys = [(-r['score'], r['symbol']) for r in scored]
    if keys != sorted(keys):
        raise ValueError('momentum rows not in score order')
    if not names.isdisjoint(unavailable) or names | set(unavailable) != set(symbols):
        raise ValueError('momentum rows do not partition the universe')
    if len(names) < COVERAGE_MIN * len(symbols):
        raise ValueError('momentum coverage below floor')
    ranks = [r['rank'] for r in scored if r['eligible']]       # the producer counts eligible rows in score order
    if ranks != list(range(1, len(ranks) + 1)):
        raise ValueError('momentum ranks not contiguous')
    return board


def load_prosperity_momentum_picks(pool: dict, delivery_dir=PROSPERITY_DELIVERY_DIR) -> dict:
    """This week's 景气动量精选池: bound-pool members in the M16 14-week momentum top 30, by momentum rank.

    Reads the JSON the Saturday delivery already wrote for the pool's own as_of (never an older
    week). Fail-closed: anything that does not prove the board belongs to this pool and is
    complete makes the picks unavailable. Zero picks is a valid result. Read-only.
    """
    from src.data.prosperity_delivery import MOMENTUM_SCHEMA_VERSION, MOMENTUM_TOP, MOMENTUM_WINDOW, _top_rows

    if not isinstance(pool, dict) or not pool.get('available'):
        return _momentum_unavailable((pool.get('reason') if isinstance(pool, dict) else None)
                                     or 'prosperity_pool_invalid')
    members = _pool_symbols(pool)
    if members is None:
        return _momentum_unavailable('prosperity_pool_invalid')
    as_of = pool['as_of']
    path = momentum_board_path(as_of, delivery_dir)
    if not path.is_file():
        return _momentum_unavailable('prosperity_momentum_missing')
    try:
        payload = json.loads(path.read_text(encoding='utf-8'), parse_constant=_reject_non_finite)
        board = _check_board(payload, as_of, pool['universe'], members,
                             schema_version=MOMENTUM_SCHEMA_VERSION, window_key=MOMENTUM_WINDOW)
    except _MomentumPoolMismatch:
        return _momentum_unavailable('prosperity_momentum_pool_mismatch')
    except (OSError, UnicodeDecodeError, ValueError, TypeError, KeyError, OverflowError, RecursionError,
            json.JSONDecodeError) as exc:
        return _momentum_unavailable('prosperity_momentum_invalid', error=type(exc).__name__)
    picks = [{'symbol': r['symbol'], 'momentum_rank': r['rank'], 'momentum_score': r['score'],
              'momentum_return': r['return']} for r in _top_rows(board) if r['symbol'] in members]
    unscored = [{'symbol': s, 'reason': board['unavailable'][s]} for s in sorted(board['unavailable']) if s in members]
    return {'available': True, 'reason': None, 'as_of': as_of, 'price_date': payload['price_date'],
            'window': MOMENTUM_WINDOW, 'days': MOMENTUM_WINDOW_DAYS, 'top_n': MOMENTUM_TOP,
            'picks': picks, 'unscored': unscored}
