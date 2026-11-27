"""Morning section 0d: the prosperity pool bound to its source board, and its weekly momentum picks.

Plans: docs/plans/2026-11-03-prosperity-m14-morning.md (pool binding) and
docs/plans/2026-11-19-morning-0d-prosperity-momentum.md (景气动量精选池 = pool ∩ the M16 14-week
momentum top board_top_cut, Boss 2026-11-26; read from the Saturday delivery output). Read-only on prosperity.db and on the
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

from backtest.research.momentum_board import check_board, check_header, reject_non_finite
from config.settings import DATA_DIR
from src.data.prosperity_pool import POOL_PATH, POOL_SCHEME_ID, _day, _universe, load_prosperity_pool
from src.data.prosperity_storage import BoardKey
from src.data.prosperity_store import ProsperityStore
from src.data.prosperity_web import pool_binding
from terminal.prosperity.schemes import SCHEMES, scheme_hash

PROSPERITY_DB_PATH = DATA_DIR / 'prosperity.db'
PROSPERITY_DELIVERY_DIR = DATA_DIR / 'prosperity-delivery'   # run_prosperity.sh --delivery-dir; a test pins the pair
MOMENTUM_WINDOW_DAYS = 70          # backtest/research/momentum_board.py WINDOWS['14w']; a test pins it
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


# --- weekly M16 momentum picks (景气精选池 ∩ 14周动量榜前 board_top_cut) ---------------------------

class _MomentumPoolMismatch(ValueError):
    """A well-formed momentum board for another week or another universe than the bound pool."""


def _momentum_unavailable(reason: str, **extra) -> dict:
    return {'available': False, 'reason': reason, 'picks': [], 'unscored': [], **extra}


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


def _check_board(payload, as_of: str, pool_universe: dict, members: set) -> dict:
    """The 14-week board of a payload bound to this pool, or ValueError / _MomentumPoolMismatch.
    Board and row checks are the sender's own (momentum_board.check_board, review P2)."""
    symbols = check_header(payload)
    if payload['as_of'] != as_of:
        raise _MomentumPoolMismatch('momentum board is for another week')
    expected = _universe(symbols)
    if (expected['count'] != pool_universe['count'] or expected['symbols_sha256'] != pool_universe['symbols_sha256']
            or not members <= set(symbols)):
        raise _MomentumPoolMismatch('momentum universe differs from the pool')
    return check_board(payload)


def load_prosperity_momentum_picks(pool: dict, delivery_dir=PROSPERITY_DELIVERY_DIR) -> dict:
    """This week's 景气动量精选池: bound-pool members in the M16 14-week momentum top board_top_cut, by rank.

    Reads the JSON the Saturday delivery already wrote for the pool's own as_of (never an older
    week). Fail-closed: anything that does not prove the board belongs to this pool and is
    complete makes the picks unavailable. Zero picks is a valid result. Read-only.
    """
    from src.data.prosperity_delivery import MOMENTUM_WINDOW, _top_rows, board_top_cut

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
        payload = json.loads(path.read_text(encoding='utf-8'), parse_constant=reject_non_finite)
        board = _check_board(payload, as_of, pool['universe'], members)
    except _MomentumPoolMismatch:
        return _momentum_unavailable('prosperity_momentum_pool_mismatch')
    except (OSError, UnicodeDecodeError, ValueError, TypeError, KeyError, OverflowError, RecursionError,
            json.JSONDecodeError) as exc:
        return _momentum_unavailable('prosperity_momentum_invalid', error=type(exc).__name__)
    picks = [{'symbol': r['symbol'], 'momentum_rank': r['rank'], 'momentum_score': r['score'],
              'momentum_return': r['return']} for r in _top_rows(board) if r['symbol'] in members]
    unscored = [{'symbol': s, 'reason': board['unavailable'][s]} for s in sorted(board['unavailable']) if s in members]
    return {'available': True, 'reason': None, 'as_of': as_of, 'price_date': payload['price_date'],
            'window': MOMENTUM_WINDOW, 'days': MOMENTUM_WINDOW_DAYS, 'top_n': board_top_cut(board),
            'picks': picks, 'unscored': unscored}
