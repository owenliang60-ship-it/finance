"""SQLite state for the two LS01 jobs: four tables, WAL, explicit transactions.

The exchange is the source of truth for positions and entry price. This file is
the source of truth for weeks, cooldowns and requests whose outcome is pending.
Rule: write the row first, then send the request.
"""
from contextlib import contextmanager
import fcntl
import json
import os
from pathlib import Path
import sqlite3
import time

SCHEMA_VERSION = 1
TERMINAL_STATES = frozenset({'FILLED', 'PARTIAL_FINAL', 'CANCELED', 'EXPIRED', 'REJECTED',
                             'NOT_ACCEPTED', 'ABANDONED'})
WEEK_JSON = {'short_ranked': 'short_ranked_json', 'long_ranked': 'long_ranked_json',
             'targets': 'targets_json', 'deviations': 'deviations_json'}
WEEK_COLUMNS = ('rule_version', 'rule_hash', 'release_commit', 'prep_status', 'prep_error', 'prep_started_at',
                'prep_finished_at', 'signal_day', 'input_hash', 'fisher_week_start', 'fisher', 'trigger', 'regime',
                'short_ranked_json', 'long_ranked_json', 'run_started_at', 'cutoff_ms', 'wallet_usdt', 'upnl_usdt',
                'equity_usdt', 'targets_json', 'wave_b_scale', 'status', 'deviations_json', 'report_status')
ACTION_COLUMNS = ('state', 'exchange_id', 'child_order_id', 'executed_qty', 'avg_price', 'error_code', 'error_msg',
                  'check_count', 'last_checked_at')

SCHEMA = """
CREATE TABLE IF NOT EXISTS weeks (
  anchor_utc TEXT PRIMARY KEY, rule_version TEXT, rule_hash TEXT, release_commit TEXT,
  prep_status TEXT, prep_error TEXT, prep_started_at REAL, prep_finished_at REAL, signal_day TEXT,
  input_hash TEXT, fisher_week_start TEXT, fisher REAL, trigger REAL, regime INTEGER,
  short_ranked_json TEXT, long_ranked_json TEXT, run_started_at REAL, cutoff_ms INTEGER,
  wallet_usdt TEXT, upnl_usdt TEXT, equity_usdt TEXT, targets_json TEXT, wave_b_scale TEXT,
  status TEXT, deviations_json TEXT, report_status TEXT);
CREATE TABLE IF NOT EXISTS actions (
  id INTEGER PRIMARY KEY AUTOINCREMENT, client_id TEXT NOT NULL UNIQUE, kind TEXT NOT NULL, purpose TEXT NOT NULL,
  anchor_utc TEXT, symbol TEXT, side TEXT, qty TEXT, lane TEXT, request_json TEXT, state TEXT NOT NULL,
  exchange_id TEXT, child_order_id TEXT, executed_qty TEXT, avg_price TEXT, error_code INTEGER, error_msg TEXT,
  check_count INTEGER NOT NULL DEFAULT 0, replaces_client_id TEXT, created_at REAL, sent_at REAL,
  last_checked_at REAL, terminal_at REAL);
CREATE TABLE IF NOT EXISTS state (key TEXT PRIMARY KEY, value_json TEXT NOT NULL, updated_at REAL);
CREATE TABLE IF NOT EXISTS evidence (
  id INTEGER PRIMARY KEY AUTOINCREMENT, kind TEXT NOT NULL, dedupe_key TEXT NOT NULL UNIQUE, symbol TEXT,
  time_ms INTEGER, payload_json TEXT, status TEXT, created_at REAL);
CREATE INDEX IF NOT EXISTS actions_state ON actions(state);
CREATE INDEX IF NOT EXISTS evidence_kind ON evidence(kind, status);
"""


class LockBusy(RuntimeError):
    pass


class TerminalActionError(RuntimeError):
    pass


def _dump(value):
    return json.dumps(value, ensure_ascii=False, default=str, allow_nan=False)


@contextmanager
def process_lock(state_dir):
    """One writer at a time across rebalance and guard. Released when the process dies."""
    state_dir = Path(state_dir)
    state_dir.mkdir(parents=True, exist_ok=True)
    handle = open(state_dir / 'mls01.lock', 'a')
    try:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            raise LockBusy('another LS01 job holds the lock') from None
        yield
    finally:
        handle.close()


class Store:
    def __init__(self, state_dir, clock=time.time, readonly=False):
        self.state_dir = Path(state_dir)
        self.clock = clock
        self.path = self.state_dir / 'state.db'
        if readonly:
            self.db = sqlite3.connect('file:' + str(self.path) + '?mode=ro', uri=True, isolation_level=None)
        else:
            self.state_dir.mkdir(parents=True, exist_ok=True)
            os.chmod(self.state_dir, 0o700)
            self.db = sqlite3.connect(self.path, isolation_level=None, timeout=10)
            self.db.execute('PRAGMA journal_mode=WAL')
            version = self.db.execute('PRAGMA user_version').fetchone()[0]
            if version not in (0, SCHEMA_VERSION):
                raise RuntimeError('unsupported state.db schema version ' + str(version))
            self.db.executescript(SCHEMA)
            self.db.execute('PRAGMA user_version=' + str(SCHEMA_VERSION))
        self.db.row_factory = sqlite3.Row
        self._depth = 0

    @contextmanager
    def tx(self):
        """BEGIN IMMEDIATE. Nested use joins the outer transaction."""
        if self._depth:
            self._depth += 1
            try:
                yield
            finally:
                self._depth -= 1
            return
        self.db.execute('BEGIN IMMEDIATE')
        self._depth = 1
        try:
            yield
        except BaseException:
            self.db.execute('ROLLBACK')
            raise
        else:
            self.db.execute('COMMIT')
        finally:
            self._depth = 0

    # ---- weeks ---------------------------------------------------------
    def week(self, anchor):
        row = self.db.execute('SELECT * FROM weeks WHERE anchor_utc=?', (str(anchor),)).fetchone()
        if row is None:
            return None
        week = dict(row)
        for name, column in WEEK_JSON.items():
            raw = week.pop(column)
            week[name] = json.loads(raw) if raw else ([] if name != 'targets' else {})
        return week

    def latest_week(self):
        row = self.db.execute('SELECT anchor_utc FROM weeks ORDER BY anchor_utc DESC LIMIT 1').fetchone()
        return None if row is None else self.week(row['anchor_utc'])

    def save_week(self, anchor, **fields):
        values = {}
        for name, value in fields.items():
            column = WEEK_JSON.get(name, name)
            if column not in WEEK_COLUMNS:
                raise ValueError('unknown weeks column: ' + name)
            values[column] = _dump(value) if name in WEEK_JSON else value
        with self.tx():
            self.db.execute('INSERT OR IGNORE INTO weeks(anchor_utc) VALUES (?)', (str(anchor),))
            if values:
                self.db.execute('UPDATE weeks SET ' + ', '.join(c + '=?' for c in values) + ' WHERE anchor_utc=?',
                                (*values.values(), str(anchor)))

    def add_deviation(self, anchor, code, symbol=None, **detail):
        with self.tx():
            week = self.week(anchor)
            deviations = week['deviations'] if week else []
            entry = {'code': code}
            if symbol is not None:
                entry['symbol'] = symbol
            entry.update(detail)
            deviations.append(entry)
            self.save_week(anchor, deviations=deviations)

    # ---- actions -------------------------------------------------------
    def plan_action(self, client_id, kind, purpose, anchor, symbol, side, qty, lane, request, replaces=None):
        with self.tx():
            cursor = self.db.execute(
                'INSERT INTO actions(client_id, kind, purpose, anchor_utc, symbol, side, qty, lane, request_json,'
                ' state, replaces_client_id, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)',
                (client_id, kind, purpose, None if anchor is None else str(anchor), symbol, side,
                 None if qty is None else str(qty), lane, _dump(request), 'PLANNED', replaces, self.clock()))
            return cursor.lastrowid

    def mark_sending(self, action_id):
        """Commit SENDING before the HTTP call. If this raises, the request must not be sent."""
        with self.tx():
            changed = self.db.execute("UPDATE actions SET state='SENDING', sent_at=? WHERE id=? AND state='PLANNED'",
                                      (self.clock(), action_id)).rowcount
            if changed != 1:
                raise ValueError('only a PLANNED action can start sending')

    def update_action(self, action_id, **fields):
        unknown = set(fields) - set(ACTION_COLUMNS)
        if unknown:
            raise ValueError('unknown actions column: ' + ', '.join(sorted(unknown)))
        with self.tx():
            current = self.db.execute('SELECT state FROM actions WHERE id=?', (action_id,)).fetchone()
            if current is None:
                raise KeyError(action_id)
            if current['state'] in TERMINAL_STATES:
                raise TerminalActionError('action %s is terminal (%s)' % (action_id, current['state']))
            values = {k: (None if v is None else str(v)) if k in ('executed_qty', 'avg_price', 'exchange_id',
                                                                    'child_order_id') else v
                      for k, v in fields.items()}
            if fields.get('state') in TERMINAL_STATES:
                values['terminal_at'] = self.clock()
            self.db.execute('UPDATE actions SET ' + ', '.join(c + '=?' for c in values) + ' WHERE id=?',
                            (*values.values(), action_id))

    def action(self, action_id):
        row = self.db.execute('SELECT * FROM actions WHERE id=?', (action_id,)).fetchone()
        return None if row is None else self._action(row)

    def actions(self, **where):
        clauses, params = [], []
        for column, value in where.items():
            if isinstance(value, (tuple, list, set, frozenset)):
                clauses.append(column + ' IN (' + ','.join('?' * len(value)) + ')')
                params.extend(value)
            else:
                clauses.append(column + '=?')
                params.append(value)
        sql = 'SELECT * FROM actions' + (' WHERE ' + ' AND '.join(clauses) if clauses else '') + ' ORDER BY id'
        return [self._action(row) for row in self.db.execute(sql, params)]

    @staticmethod
    def _action(row):
        action = dict(row)
        action['request'] = json.loads(action.pop('request_json') or 'null')
        return action

    def next_sequence(self, tag):
        with self.tx():
            value = self.get('seq:' + tag, 0) + 1
            self.set('seq:' + tag, value)
            return value

    # ---- state ---------------------------------------------------------
    def get(self, key, default=None):
        row = self.db.execute('SELECT value_json FROM state WHERE key=?', (key,)).fetchone()
        return default if row is None else json.loads(row['value_json'])

    def set(self, key, value):
        with self.tx():
            self.db.execute('INSERT INTO state(key, value_json, updated_at) VALUES (?,?,?) '
                            'ON CONFLICT(key) DO UPDATE SET value_json=excluded.value_json, updated_at=excluded.updated_at',
                            (key, _dump(value), self.clock()))

    def delete(self, key):
        with self.tx():
            self.db.execute('DELETE FROM state WHERE key=?', (key,))

    def keys(self, prefix):
        rows = self.db.execute("SELECT key FROM state WHERE key LIKE ? ESCAPE '\\' ORDER BY key",
                               (prefix.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_') + '%',))
        return [row['key'] for row in rows]

    # ---- evidence ------------------------------------------------------
    def add_evidence(self, kind, dedupe_key, payload, symbol=None, time_ms=None, status=None):
        """Returns False when this dedupe key is already recorded."""
        with self.tx():
            cursor = self.db.execute(
                'INSERT OR IGNORE INTO evidence(kind, dedupe_key, symbol, time_ms, payload_json, status, created_at)'
                ' VALUES (?,?,?,?,?,?,?)', (kind, dedupe_key, symbol, time_ms, _dump(payload), status, self.clock()))
            return cursor.rowcount == 1

    def evidence(self, kind, status=None):
        sql, params = 'SELECT * FROM evidence WHERE kind=?', [kind]
        if status is not None:
            sql, params = sql + ' AND status=?', params + [status]
        rows = []
        for row in self.db.execute(sql + ' ORDER BY id', params):
            item = dict(row)
            item['payload'] = json.loads(item.pop('payload_json') or 'null')
            rows.append(item)
        return rows

    def pending_notifications(self):
        return self.evidence('notification', status='pending')

    def mark_notification_sent(self, evidence_id):
        with self.tx():
            self.db.execute("UPDATE evidence SET status='sent' WHERE id=? AND kind='notification'", (evidence_id,))

    # ---- backup --------------------------------------------------------
    def backup(self, dest_dir, keep=30):
        """WAL-safe copy through the SQLite backup API; prune to the newest ``keep`` files."""
        dest_dir = Path(dest_dir)
        dest_dir.mkdir(parents=True, exist_ok=True)
        path = dest_dir / ('state-' + time.strftime('%Y%m%dT%H%M%SZ', time.gmtime(self.clock())) + '.db')
        target = sqlite3.connect(path)
        try:
            self.db.backup(target)
            if target.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
                raise RuntimeError('backup failed integrity check')
        finally:
            target.close()
        for old in sorted(dest_dir.glob('state-*.db'))[:-keep]:
            old.unlink()
        return path
