"""Explicit-path, isolated SQLite result store. No market.db or network access."""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path

import json
from dataclasses import asdict

from src.data.prosperity_storage import (BoardKey, BoardMeta, StoredBoard, PublicationState, SCORE_REALS, SCORE_MAPS,
    canonical, check_key, content_hash, input_hash, input_records, instant, key_for, normalize_board, require,
    validate_batch, validate_board, day, utc)
from terminal.prosperity.factors import FactorRow
from terminal.prosperity.kernel import BoardResult, ScoredRow, params_from_dict, params_to_dict
from terminal.prosperity.schemes import FactorSpec, Scheme


_SCHEMA = Path(__file__).with_name('prosperity_schema.sql').read_text(encoding='utf-8')


def _schema_signature(conn):
    # Include CHECK clauses and index SQL as well as columns/FKs/unique constraints.
    return tuple((r[0], r[1], ' '.join((r[2] or '').split())) for r in conn.execute(
        "SELECT type,name,sql FROM sqlite_master WHERE type IN ('table','index') "
        "AND name NOT GLOB 'sqlite_*' ORDER BY type,name"))


with sqlite3.connect(':memory:') as _reference:
    _reference.executescript(_SCHEMA)
    _EXPECTED_SCHEMA = _schema_signature(_reference)
_reference.close()


class ProsperityStore:
    def __init__(self, db_path: Path, *, read_only: bool = True):
        self.read_only = read_only
        path = Path(db_path).resolve()
        if read_only and not path.is_file():
            raise FileNotFoundError(path)
        if not read_only:
            path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(path.as_uri() + ('?mode=ro' if read_only else '?mode=rwc'),
                                     uri=True, isolation_level=None)
        self._conn.row_factory = sqlite3.Row
        try:
            version = self._conn.execute('PRAGMA user_version').fetchone()[0]
            signature = _schema_signature(self._conn)
            has_objects = self._conn.execute(
                "SELECT 1 FROM sqlite_master WHERE name NOT GLOB 'sqlite_*' LIMIT 1").fetchone() is not None
            if version == 0 and not has_objects and not read_only:
                self._conn.executescript('BEGIN IMMEDIATE;\n' + _SCHEMA + '\nPRAGMA user_version=1;\nCOMMIT;')
                version, signature = 1, _schema_signature(self._conn)
            if version != 1 or signature != _EXPECTED_SCHEMA:
                raise ValueError('prosperity_schema_mismatch')
            self._conn.execute('PRAGMA foreign_keys=ON')
            if read_only:
                self._conn.execute('PRAGMA query_only=ON')
            else:
                self._conn.execute('PRAGMA journal_mode=WAL')
        except BaseException:
            self._conn.close()
            raise

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        self.close()

    def close(self):
        self._conn.close()

    @contextmanager
    def _transaction(self, *, write=False):
        if write and self.read_only:
            raise PermissionError('read_only_store')
        if self._conn.in_transaction:
            raise RuntimeError('nested_store_transaction')
        self._conn.execute('BEGIN IMMEDIATE' if write else 'BEGIN')
        try:
            yield
            self._conn.commit()
        except BaseException:
            self._conn.rollback()
            raise

    def save_batch(self, boards):
        normalized = tuple(normalize_board(b) for b in boards)
        validate_batch(normalized)
        keys = tuple(key_for(b) for b in normalized)
        pending = dict(zip(keys, normalized))
        with self._transaction(write=True):
            existing = {}
            for key, b in pending.items():
                old = self._read(key)
                existing[key] = old
                if old is not None:
                    require(content_hash(old) == content_hash(b), 'board_content_conflict')
            for b in normalized:
                self._check_frozen(b, pending)
            for key, b in pending.items():
                if existing[key] is None and key.board_type == 'quarter_final':
                    taken = self._conn.execute(
                        "SELECT as_of FROM board WHERE board_type='quarter_final' AND scheme_hash=? "
                        'AND code_version=? AND quarter_label=?',
                        (key.scheme_hash, key.code_version, b.meta.quarter_label)).fetchone()
                    require(taken is None, 'quarter_final_exists')
            # Parents before children: a current freeze can be referenced by its weekly board.
            for b in normalized:
                self._save_scheme_and_params(b)
            for key, b in pending.items():
                if existing[key] is None:
                    self._insert_board(b)
            # Verify the actual relational records, not just the in-memory payload.
            for key in keys:
                self._read(key)
        return keys

    def get_board(self, key):
        check_key(key)
        with self._transaction():
            return self._read(key)

    def list_boards(self, *, scheme_hash, code_version, board_type, through=None):
        require(bool(scheme_hash) and bool(code_version), 'empty_version')
        require(board_type in ('weekly', 'quarter_final'), 'invalid_board_type')
        if through is not None:
            day(through)
        with self._transaction():
            query = 'SELECT as_of FROM board WHERE scheme_hash=? AND code_version=? AND board_type=?'
            args = [scheme_hash, code_version, board_type]
            if through is not None:
                query += ' AND as_of<=?'
                args.append(through)
            return tuple(BoardKey(board_type, r[0], scheme_hash, code_version)
                         for r in self._conn.execute(query + ' ORDER BY as_of', args))

    def get_frozen_params(self, *, scheme_hash, code_version, as_of, effective_at):
        day(as_of)
        bound = instant(effective_at)
        require(bool(scheme_hash) and bool(code_version), 'empty_version')
        with self._transaction():
            candidates = self._conn.execute(
                "SELECT as_of FROM board WHERE board_type='quarter_final' "
                'AND scheme_hash=? AND code_version=? AND as_of<=? ORDER BY as_of DESC',
                (scheme_hash, code_version, as_of)).fetchall()
            # Newest quarter wins (Boss 2026-10-23 #2); as_of is unique per freeze, so no tie-break.
            # Each candidate is verified before its frozen_at is trusted: a corrupt timestamp must not
            # turn a damaged freeze into a silent cold start (Codex 2026-10-23 P2). Older quarters
            # cannot change the answer once a verified eligible one is found, so they are not read.
            for r in candidates:
                b = self._read(BoardKey('quarter_final', r['as_of'], scheme_hash, code_version))
                # Same point-in-time rule as the write path (Boss 2026-10-23 #1).
                if instant(b.meta.frozen_at) <= bound and utc(b.meta.frozen_at)[:10] <= as_of:
                    return b.board.params
            return None

    def _board_id(self, key):
        return self._conn.execute('SELECT board_id FROM board WHERE board_type=? AND as_of=? '
                                  'AND scheme_hash=? AND code_version=?',
                                  (key.board_type, key.as_of, key.scheme_hash, key.code_version)).fetchone()[0]

    def _receipt(self, identity, computed_at):
        row = self._conn.execute('SELECT published_at FROM publication WHERE board_id=?', (identity,)).fetchone()
        if row is None:
            return None
        at = utc(row[0])
        require(instant(at) >= instant(computed_at), 'publication_before_computation')
        return at

    def publication_state(self, key):
        check_key(key)
        with self._transaction():
            board = self._read(key)
            if board is None:
                return PublicationState(False, None)
            return PublicationState(True, self._receipt(self._board_id(key), board.meta.computed_at))

    def mark_published(self, keys, *, published_at):
        keys = tuple(keys)
        require(bool(keys) and len(set(keys)) == len(keys), 'empty_or_duplicate_publication_keys')
        at = utc(published_at)
        with self._transaction(write=True):
            missing = []
            for key in keys:
                check_key(key)
                board = self._read(key)
                require(board is not None, 'publication_board_missing')
                require(instant(at) >= instant(board.meta.computed_at), 'publication_before_computation')
                identity = self._board_id(key)
                if self._receipt(identity, board.meta.computed_at) is None:
                    missing.append(identity)
            self._conn.executemany('INSERT INTO publication (board_id,published_at) VALUES (?,?)',
                                   [(identity, at) for identity in missing])

    def _insert(self, table, values):
        names = ','.join(values)
        marks = ','.join('?' for _ in values)
        return self._conn.execute(f'INSERT INTO {table} ({names}) VALUES ({marks})', tuple(values.values()))

    def _save_scheme_and_params(self, s):
        config = asdict(s.scheme)
        description, changes = config.pop('description'), config.pop('changes')
        values = {'scheme_hash': s.board.scheme_hash, 'scheme_id': s.scheme.scheme_id,
                  'config_json': canonical(config),
                  'factor_specs_json': canonical({f: asdict(v) for f, v in s.factor_specs.items()}),
                  'description': description, 'changes_json': canonical(changes)}
        old = self._conn.execute('SELECT * FROM scheme WHERE scheme_hash=?', (s.board.scheme_hash,)).fetchone()
        if old is None:
            self._insert('scheme', values)
        else:
            require(all(old[k] == v for k, v in values.items() if k not in ('description', 'changes_json')),
                    'scheme_content_conflict')
        p = s.board.params
        values = {'params_version': p.params_version, 'scheme_hash': p.scheme_hash, 'code_version': p.code_version,
                  'as_of': p.as_of, 'payload_json': canonical(params_to_dict(p))}
        old = self._conn.execute('SELECT * FROM params WHERE params_version=?', (p.params_version,)).fetchone()
        if old is None:
            self._insert('params', values)
        else:
            require(dict(old) == values, 'params_content_conflict')

    def _input_set(self, s):
        """The week's shared input archive: reuse an identical one, else insert it once."""
        records = input_records(s)
        digest = input_hash(records)
        row = self._conn.execute('SELECT input_set_id FROM input_set WHERE as_of=? AND code_version=? '
                                 'AND content_hash=?', (s.board.as_of, s.board.code_version, digest)).fetchone()
        if row is not None:
            return row[0]
        identity = self._insert('input_set', {'as_of': s.board.as_of, 'code_version': s.board.code_version,
                                              'content_hash': digest, 'stock_count': len(records)}).lastrowid
        self._conn.executemany('INSERT INTO stock_input (input_set_id,symbol,input_json,factor_meta_json) '
                               'VALUES (?,?,?,?)', [(identity, *r) for r in records])
        return identity

    def _insert_board(self, s):
        b, m = s.board, s.meta
        fv = factor_records(s)
        values = {'input_set_id': self._input_set(s),
                  'board_type': m.board_type, 'as_of': b.as_of, 'scheme_hash': b.scheme_hash,
                  'code_version': b.code_version, 'scheme_id': b.scheme_id,
                  'params_version': b.params.params_version, 'frozen_params_version': b.frozen_params_version,
                  'quarter_label': m.quarter_label, 'frozen_at': m.frozen_at, 'computed_at': m.computed_at,
                  'git_sha': m.git_sha, 'mode': m.mode, 'params_bootstrap': int(b.params_bootstrap),
                  'frozen_as_of': b.frozen_as_of, 'counts_json': canonical(b.counts),
                  'members_json': canonical(m.members), 'errors_json': canonical(m.errors),
                  'source_evidence_json': canonical(m.source_evidence), 'content_hash': content_hash(s),
                  'stock_count': len(s.stocks), 'factor_count': len(fv)}
        board_id = self._insert('board', values).lastrowid
        for p in s.stocks:
            values = {'board_id': board_id, **{k: p[k] for k in
                      ('symbol', 'name', 'sector', 'industry', 'current_fiscal', 'pit_basis')}}
            self._insert('stock_period', values)
        # One executemany for the large factor table; no implicit transaction boundary.
        if fv:
            columns = ('board_id',) + tuple(fv[0])
            self._conn.executemany('INSERT INTO factor_value (' + ','.join(columns) + ') VALUES (' +
                                   ','.join('?' for _ in columns) + ')',
                                   [(board_id,) + tuple(row.values()) for row in fv])
        for i, r in enumerate(b.rows):
            values = {'board_id': board_id, 'symbol': r.symbol, 'row_index': i,
                      'status': r.status, 'observe_reason': r.observe_reason,
                      **{k: getattr(r, k) for k in SCORE_REALS if k != 'within'},
                      'grade': r.grade, 'within_score': r.within, 'rank': r.rank,
                      'family_scores_json': canonical(r.family_scores),
                      'demotions_json': canonical(r.demotions), 'badges_json': canonical(r.badges)}
            self._insert('score', values)

    def _read(self, key, *, dependencies=True):
        try:
            return self._read_records(key, dependencies=dependencies)
        except (KeyError, TypeError, AttributeError, IndexError, OverflowError) as exc:
            raise ValueError('corrupt_board:' + str(exc)) from exc

    def _read_records(self, key, *, dependencies):
        h = self._conn.execute('SELECT * FROM board WHERE board_type=? AND as_of=? AND scheme_hash=? '
                               'AND code_version=?', (key.board_type, key.as_of, key.scheme_hash,
                                                     key.code_version)).fetchone()
        if h is None:
            return None
        identity = h['board_id']
        sr = self._conn.execute('SELECT * FROM scheme WHERE scheme_hash=?', (h['scheme_hash'],)).fetchone()
        require(sr is not None, 'missing_scheme')
        config = json.loads(sr['config_json'])
        config.update(description=sr['description'], changes=json.loads(sr['changes_json']))
        for field in ('weights', 'slope_tiers', 'changes'):
            config[field] = tuple(tuple(x) for x in config[field])
        for field in ('gates', 'universe_exclude_industries'):
            config[field] = tuple(config[field])
        scheme = Scheme(**config)
        require(scheme.scheme_id == sr['scheme_id'], 'scheme_id_mismatch')
        specs = {f: FactorSpec(**{**v, 'exempt_industries': tuple(v['exempt_industries'])})
                 for f, v in json.loads(sr['factor_specs_json']).items()}
        pr = self._conn.execute('SELECT * FROM params WHERE params_version=?', (h['params_version'],)).fetchone()
        require(pr is not None, 'missing_params')
        params = params_from_dict(json.loads(pr['payload_json']))
        require(all(pr[k] == getattr(params, k) for k in ('params_version', 'scheme_hash', 'code_version', 'as_of')),
                'params_columns_mismatch')
        meta = BoardMeta(h['board_type'], h['quarter_label'], h['frozen_at'], h['computed_at'], h['git_sha'],
                         h['mode'], tuple(json.loads(h['members_json'])), json.loads(h['errors_json']),
                         json.loads(h['source_evidence_json']))
        stock_rows = self._conn.execute('SELECT * FROM stock_period WHERE board_id=? ORDER BY symbol',
                                        (identity,)).fetchall()
        inputs = self._conn.execute('SELECT * FROM input_set WHERE input_set_id=?', (h['input_set_id'],)).fetchone()
        require(inputs is not None and (inputs['as_of'], inputs['code_version']) == (h['as_of'], h['code_version']),
                'input_set_mismatch')
        input_rows = self._conn.execute('SELECT symbol,input_json,factor_meta_json FROM stock_input '
                                        'WHERE input_set_id=? ORDER BY symbol', (h['input_set_id'],)).fetchall()
        require(len(input_rows) == inputs['stock_count'] and
                input_hash([tuple(r) for r in input_rows]) == inputs['content_hash'], 'input_set_hash_mismatch')
        require([r['symbol'] for r in input_rows] == [r['symbol'] for r in stock_rows], 'input_members_mismatch')
        factor_rows = self._conn.execute('SELECT * FROM factor_value WHERE board_id=? ORDER BY symbol,factor',
                                         (identity,)).fetchall()
        scores = self._conn.execute('SELECT * FROM score WHERE board_id=? ORDER BY row_index', (identity,)).fetchall()
        require(len(stock_rows) == h['stock_count'] and len(factor_rows) == h['factor_count'], 'detail_count_mismatch')
        require([r['row_index'] for r in scores] == list(range(len(scores))), 'row_index_mismatch')
        by_symbol = {}
        for r in factor_rows:
            by_symbol.setdefault(r['symbol'], []).append(r)
        stocks, factors, rows = [], [], []
        for r, stored_input in zip(stock_rows, input_rows):
            p = json.loads(stored_input['input_json'])
            require(all(r[k] == p[k] for k in ('symbol', 'name', 'sector', 'industry', 'current_fiscal', 'pit_basis')),
                    'stock_columns_mismatch')
            f = json.loads(stored_input['factor_meta_json'])
            require(f['symbol'] == r['symbol'], 'factor_symbol_mismatch')
            for field in ('labels', 'packet_flags'):
                f[field] = tuple(f[field])
            values, missing = {}, {}
            for v in by_symbol.get(r['symbol'], []):
                if v['raw_present']:
                    values[v['factor']] = v['raw_value']
                if v['raw_missing_present']:
                    missing[v['factor']] = v['raw_missing']
            factors.append(FactorRow(**f, values=values, missing=missing))
            stocks.append(p)
        for r in scores:
            maps = {k: {} for k in SCORE_MAPS}
            for v in by_symbol.get(r['symbol'], []):
                f = v['factor']
                if v['scheme_present']:
                    maps['values'][f] = v['scheme_value']
                if v['scheme_missing_present']:
                    maps['missing'][f] = v['scheme_missing']
                for col, attr in (('z', 'z'), ('weight', 'weights'), ('contribution', 'contributions')):
                    if v[col] is not None:
                        maps[attr][f] = v[col]
            rows.append(ScoredRow(symbol=r['symbol'], status=r['status'], observe_reason=r['observe_reason'],
                                  **maps, **{k: r[k] for k in SCORE_REALS if k != 'within'},
                                  grade=r['grade'], within=r['within_score'], rank=r['rank'],
                                  family_scores=json.loads(r['family_scores_json']),
                                  demotions=tuple(json.loads(r['demotions_json'])),
                                  badges=tuple(json.loads(r['badges_json']))))
        require(h['params_bootstrap'] in (0, 1), 'invalid_bootstrap')
        board = BoardResult(h['scheme_id'], h['scheme_hash'], h['code_version'], h['as_of'], params,
                            bool(h['params_bootstrap']), tuple(rows), json.loads(h['counts_json']),
                            h['frozen_as_of'], h['frozen_params_version'])
        result = normalize_board(StoredBoard(meta, board, tuple(factors), tuple(stocks), scheme, specs))
        validate_board(result)
        # A hidden value under a false presence bit is corruption too. Check all physical columns.
        actual_factors = [{k: v for k, v in dict(r).items() if k != 'board_id'} for r in factor_rows]
        require(actual_factors == factor_records(result), 'factor_columns_mismatch')
        require(content_hash(result) == h['content_hash'], 'board_content_hash_mismatch')
        if dependencies:
            self._check_frozen(result, {})
        return result

    def _check_frozen(self, s, pending):
        b = s.board
        if b.frozen_params_version is None:
            return
        key = BoardKey('quarter_final', b.frozen_as_of, b.scheme_hash, b.code_version)
        ref = pending.get(key)
        if ref is None:
            ref = self._read(key, dependencies=False)
        require(ref is not None and ref.board.params.params_version == b.frozen_params_version,
                'missing_or_invalid_frozen_reference')
        require(instant(ref.meta.frozen_at) <= instant(s.meta.computed_at), 'frozen_not_effective')
        # A board may only use a package already in effect on its own date (same rule as get_frozen_params).
        require(utc(ref.meta.frozen_at)[:10] <= b.as_of, 'frozen_not_effective')
        p = ref.board.params
        for f, spec in s.factor_specs.items():
            if spec.params_timing == 'quarterly':
                require(b.params.winsor.get(f) == p.winsor.get(f) and
                        b.params.reference.get(f) == p.reference.get(f), 'frozen_params_content_mismatch')
        require(b.params.slope_bounds == p.slope_bounds, 'frozen_slope_mismatch')



def factor_records(s):
    raw = {f.symbol: f for f in s.factors}
    records = []
    in_scheme = {f for f, _ in s.scheme.weights}
    for r in sorted(s.board.rows, key=lambda r: r.symbol):
        f = raw[r.symbol]
        for name in sorted(set(f.values) | set(f.missing) | set(r.values) | set(r.missing)):
            records.append({'symbol': r.symbol, 'factor': name,
                            'raw_value': f.values.get(name), 'raw_missing': f.missing.get(name),
                            'scheme_value': r.values.get(name), 'scheme_missing': r.missing.get(name),
                            'z': r.z.get(name), 'weight': r.weights.get(name),
                            'contribution': r.contributions.get(name), 'in_scheme': int(name in in_scheme),
                            'raw_present': int(name in f.values), 'raw_missing_present': int(name in f.missing),
                            'scheme_present': int(name in r.values), 'scheme_missing_present': int(name in r.missing)})
    return records
