#!/usr/bin/env python3
"""Move an old code_version's boards out of prosperity.db (D5: cloud keeps two versions).

Three manual steps; nothing here runs from cron:
  export  (cloud)  copy one version into a standalone archive + manifest; source untouched
  verify  (local)  recheck the pulled archive copy, then mark the manifest verified
  remove  (cloud)  with the verified manifest: backup, delete that version, VACUUM

Boards are never rewritten. The archive is the only copy after remove, so it
keeps M8's schema and reads back through ProsperityStore.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import fcntl
import json
import os
from pathlib import Path
import shutil
import sqlite3
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.data.prosperity_backup import _sha, backup_bundle, state_path
from src.data.prosperity_storage import BoardKey, canonical, content_hash
from src.data.prosperity_store import ProsperityStore

FORMAT = 'prosperity-archive-v1'
DATABASE = 'archive.sqlite'
JOB_LOCK = Path('/tmp/finance-cron-locks/finance_prosperity.lock')

_BOARDS = 'SELECT board_id FROM board WHERE code_version=:v'
# FK order: parents first. scheme rows are shared across versions and never removed.
_TABLES = (
    ('params', 'code_version=:v'),
    ('input_set', 'code_version=:v'),
    ('stock_input', 'input_set_id IN (SELECT input_set_id FROM input_set WHERE code_version=:v)'),
    ('board', 'code_version=:v'),
    ('stock_period', 'board_id IN (' + _BOARDS + ')'),
    ('factor_value', 'board_id IN (' + _BOARDS + ')'),
    ('score', 'board_id IN (' + _BOARDS + ')'),
    ('publication', 'board_id IN (' + _BOARDS + ')'),
)


def _ro(path):
    conn = sqlite3.connect(Path(path).resolve().as_uri() + '?mode=ro', uri=True)
    conn.execute('PRAGMA query_only=ON')
    return conn


def version_counts(db, version):
    conn = _ro(db)
    try:
        return {t: conn.execute(f'SELECT count(*) FROM {t} WHERE {w}', {'v': version}).fetchone()[0]
                for t, w in _TABLES}
    finally:
        conn.close()


def _board_hashes(db, version):
    with ProsperityStore(db) as store:
        conn = _ro(db)
        try:
            keys = conn.execute('SELECT board_type,as_of,scheme_hash FROM board WHERE code_version=?',
                                (version,)).fetchall()
        finally:
            conn.close()
        # get_board re-validates every row against the stored content_hash.
        return sorted(content_hash(store.get_board(BoardKey(t, a, s, version))) for t, a, s in keys)


def retained_versions(db, *, current):
    """Current version plus the newest other version that wrote a live board."""
    conn = _ro(db)
    try:
        row = conn.execute("SELECT code_version FROM board WHERE mode='live' AND code_version!=? "
                           'ORDER BY computed_at DESC, as_of DESC LIMIT 1', (current,)).fetchone()
    finally:
        conn.close()
    return {current} | ({row[0]} if row else set())


def _state_files(db, version):
    """State JSON belonging to one version: candidates by identity, runs by top level."""
    root = state_path(db)
    found = {}
    for kind in ('candidates', 'runs'):
        for path in sorted((root / kind).glob('*.json')) if (root / kind).exists() else ():
            try:
                body = json.loads(path.read_text(encoding='utf-8'))
                owner = body['payload']['identity']['code_version'] if kind == 'candidates' else body['code_version']
            except (ValueError, KeyError, TypeError):
                continue  # unattributable records stay with the source
            if owner == version:
                found[path.relative_to(root).as_posix()] = _sha(path)
    return found


def _seal(manifest):
    body = {k: v for k, v in manifest.items() if k != 'manifest_sha256'}
    return {**body, 'manifest_sha256': _sha_text(canonical(body))}


def _sha_text(text):
    import hashlib
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


def _load(manifest_path):
    manifest = json.loads(Path(manifest_path).read_text(encoding='utf-8'))
    if manifest.get('format') != FORMAT or _seal(manifest)['manifest_sha256'] != manifest.get('manifest_sha256'):
        raise ValueError('manifest_checksum')
    return manifest


def _write(manifest_path, manifest):
    temp = Path(str(manifest_path) + '.tmp')
    temp.write_text(canonical(_seal(manifest)), encoding='utf-8')
    os.replace(temp, manifest_path)


def export_version(db, version, out_dir):
    db, out_dir = Path(db).resolve(), Path(out_dir)
    counts = version_counts(db, version)
    if not counts['board']:
        raise ValueError('version_not_found')
    out_dir.mkdir(parents=True)  # FileExistsError: never mix two exports
    archive = out_dir / DATABASE
    ProsperityStore(archive, read_only=False).close()
    conn = sqlite3.connect('file:' + str(archive.resolve()), uri=True, isolation_level=None)
    try:
        conn.execute('ATTACH DATABASE ? AS src', (db.as_uri() + '?mode=ro',))
        conn.execute('BEGIN')  # one read snapshot of the source for every table
        conn.execute('INSERT INTO scheme SELECT * FROM src.scheme WHERE scheme_hash IN '
                     '(SELECT scheme_hash FROM src.board WHERE code_version=:v '
                     ' UNION SELECT scheme_hash FROM src.params WHERE code_version=:v)', {'v': version})
        for table, where in _TABLES:
            conn.execute(f'INSERT INTO {table} SELECT * FROM src.{table} WHERE '
                         + where.replace('FROM ', 'FROM src.'), {'v': version})
        conn.execute('COMMIT')
        conn.execute('DETACH DATABASE src')
        if conn.execute('PRAGMA foreign_key_check').fetchall():
            raise ValueError('archive_foreign_key')
        conn.execute('PRAGMA journal_mode=DELETE')  # single self-contained file
    finally:
        conn.close()
    files = _state_files(db, version)
    for rel in files:
        target = out_dir / 'state' / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(state_path(db) / rel, target)
    if version_counts(archive, version) != counts or {r: _sha(out_dir / 'state' / r) for r in files} != files:
        raise ValueError('source_changed_during_export')
    manifest = {'format': FORMAT, 'code_version': version, 'source_name': db.name,
                'exported_at': datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'),
                'database': DATABASE, 'database_sha256': _sha(archive), 'counts': counts,
                'board_hashes': _board_hashes(archive, version), 'state_files': files,
                'verified': False, 'verified_at': None}
    path = out_dir / 'manifest.json'
    _write(path, manifest)
    return path


def verify_archive(manifest_path):
    """Recheck this copy of the archive (run after pulling it locally)."""
    manifest_path = Path(manifest_path)
    manifest = _load(manifest_path)
    root, version = manifest_path.parent, manifest['code_version']
    archive = root / manifest['database']
    conn = _ro(archive)
    try:
        ok = (conn.execute('PRAGMA quick_check').fetchall() == [('ok',)]
              and [r[0] for r in conn.execute('SELECT DISTINCT code_version FROM board')] == [version])
    finally:
        conn.close()
    files = {p.relative_to(root / 'state').as_posix(): _sha(p)
             for p in sorted((root / 'state').rglob('*')) if p.is_file()} if (root / 'state').exists() else {}
    if not (ok and _sha(archive) == manifest['database_sha256'] and files == manifest['state_files']
            and version_counts(archive, version) == manifest['counts']
            and _board_hashes(archive, version) == manifest['board_hashes']):
        raise ValueError('archive_mismatch')
    _write(manifest_path, {**manifest, 'verified': True,
                           'verified_at': datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')})
    return manifest_path


def remove_version(db, manifest_path, *, backup_dir, current, lock_path=JOB_LOCK):
    db = Path(db).resolve()
    manifest = _load(manifest_path)
    version = manifest['code_version']
    if manifest.get('verified') is not True:
        raise ValueError('manifest_not_verified')
    if version in retained_versions(db, current=current):
        raise ValueError('retained_version')
    lock_path = Path(lock_path)
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open('a') as job_lock:
        fcntl.flock(job_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)  # BlockingIOError while a job runs
        if (version_counts(db, version) != manifest['counts']
                or _board_hashes(db, version) != manifest['board_hashes']
                or _state_files(db, version) != manifest['state_files']):
            raise ValueError('manifest_does_not_match_database')
        bundle = backup_bundle(db, backup_dir)
        conn = sqlite3.connect(str(db), isolation_level=None)
        try:
            conn.execute('PRAGMA foreign_keys=ON')
            conn.execute('BEGIN IMMEDIATE')
            try:
                for table, where in reversed(_TABLES):
                    conn.execute(f'DELETE FROM {table} WHERE {where}', {'v': version})
                if conn.execute('PRAGMA foreign_key_check').fetchall():
                    raise ValueError('remove_foreign_key')
                conn.execute('COMMIT')
            except BaseException:
                conn.execute('ROLLBACK')
                raise
            conn.execute('VACUUM')
            conn.execute('PRAGMA wal_checkpoint(TRUNCATE)')
        finally:
            conn.close()
        for rel in manifest['state_files']:
            (state_path(db) / rel).unlink()
    return bundle


def archivable_hint(db, *, current):
    db = Path(db)
    kept = retained_versions(db, current=current)
    conn = _ro(db)
    try:
        versions = [r[0] for r in conn.execute('SELECT DISTINCT code_version FROM board ORDER BY code_version')]
        total = conn.execute('SELECT (SELECT count(*) FROM factor_value) + (SELECT count(*) FROM stock_input) '
                             '+ (SELECT count(*) FROM score)').fetchone()[0] or 1
    finally:
        conn.close()
    size = db.stat().st_size
    parts = []
    for v in versions:
        if v in kept:
            continue
        c = version_counts(db, v)
        mb = size * (c['factor_value'] + c['stock_input'] + c['score']) / total / 1e6
        parts.append(f'{v}（{c["board"]} 榜，约 {mb:.1f} MB）')
    head = 'prosperity: 剩余空间不足。保留版本 ' + '、'.join(sorted(kept)) + '；可归档：'
    tail = '；'.join(parts) if parts else '无（需另行清理空间）'
    return head + tail + '。步骤见 docs/runbooks/prosperity-weekly.md「按版本归档」，移除需 Boss 确认。'


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest='command', required=True)
    e = sub.add_parser('export')
    e.add_argument('--db', type=Path, required=True)
    e.add_argument('--version', required=True)
    e.add_argument('--out', type=Path, required=True)
    v = sub.add_parser('verify')
    v.add_argument('--manifest', type=Path, required=True)
    r = sub.add_parser('remove')
    r.add_argument('--db', type=Path, required=True)
    r.add_argument('--manifest', type=Path, required=True)
    r.add_argument('--backup-dir', type=Path, required=True)
    h = sub.add_parser('hint')
    h.add_argument('--db', type=Path, required=True)
    args = p.parse_args(argv)
    try:
        if args.command == 'export':
            print(export_version(args.db, args.version, args.out))
        elif args.command == 'verify':
            print(verify_archive(args.manifest))
        else:
            from terminal.prosperity.version import code_version
            if args.command == 'hint':
                print(archivable_hint(args.db, current=code_version()))
            else:
                print(remove_version(args.db, args.manifest, backup_dir=args.backup_dir, current=code_version()))
    except (OSError, ValueError, sqlite3.Error) as exc:
        print(f'[ERROR] {type(exc).__name__}: {exc}', file=sys.stderr)
        return 4 if isinstance(exc, ValueError) else 6
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
