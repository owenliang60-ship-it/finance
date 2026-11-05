"""M14 morning trial section: the prosperity pool, bound to its source board, and its Premium comparison.

Plan: docs/plans/2026-11-03-prosperity-m14-morning.md. Read-only on prosperity.db. Not part of the
engine code_version. The caller (scripts/morning_report.py) wraps every use in try/except, so a
failure here only marks the 0d section unavailable.
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
import sqlite3
from typing import Optional

from config.settings import DATA_DIR
from src.data.prosperity_pool import POOL_PATH, POOL_SCHEME_ID, load_prosperity_pool
from src.data.prosperity_storage import BoardKey
from src.data.prosperity_store import ProsperityStore
from src.data.prosperity_web import pool_binding
from terminal.prosperity.schemes import SCHEMES, scheme_hash

PROSPERITY_DB_PATH = DATA_DIR / 'prosperity.db'


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


def _symbols(pool: dict) -> set:
    return {m['symbol'] for m in pool.get('members') or [] if isinstance(m, dict) and m.get('symbol')}


def pool_comparison(prosperity: dict, premium: dict) -> dict:
    """Both / prosperity only (new) / Premium only (dropped), each sorted by symbol."""
    if not prosperity.get('available'):
        return {'status': 'prosperity_unavailable'}
    if not premium.get('available'):
        return {'status': 'premium_unavailable'}
    ours, theirs = _symbols(prosperity), _symbols(premium)
    return {'status': 'ok', 'both': sorted(ours & theirs), 'prosperity_only': sorted(ours - theirs),
            'premium_only': sorted(theirs - ours)}
