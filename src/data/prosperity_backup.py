"""WAL-safe result/state bundles. Call backup while holding the prosperity job lock.

Only SQLite's online backup reads the source database. The bundle stores it
gzip-compressed (D5); the manifest pins the uncompressed SHA256 as well. Bundle validation and
publication never open a source database for writing. A pending publish marker
blocks writers after a process crash between the two local path replacements.
"""
from __future__ import annotations

import gzip
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import tempfile
from datetime import datetime, timezone
from uuid import uuid4

from src.data.prosperity_storage import canonical

FORMAT = 'prosperity-bundle-v2'
DATABASE = 'database.sqlite.gz'


def state_path(db_path):
    return Path(str(db_path) + '.state')


def _pending(db_path):
    return Path(str(db_path) + '.publish-pending')


def _sha(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def check_space(db_path: Path) -> None:
    db_path = Path(db_path).absolute()
    if _pending(db_path).exists():
        raise ValueError('publish_incomplete')
    parent = db_path.parent
    while not parent.exists():
        parent = parent.parent
    size = db_path.stat().st_size if db_path.exists() else 0
    if shutil.disk_usage(parent).free < max(2 * 1024**3, 3 * size):
        raise ValueError('insufficient_space')


def _files(root):
    root = Path(root)
    if root.is_symlink():
        raise ValueError('bundle_symlink')
    if not root.exists():
        return {}
    result = {}
    for path in sorted(root.rglob('*')):
        if path.is_symlink() or not (path.is_dir() or path.is_file()):
            raise ValueError('bundle_unsafe_file')
        if path.is_file():
            result[path.relative_to(root).as_posix()] = _sha(path)
    return result


def _validate_candidates(state):
    candidates = Path(state) / 'candidates'
    for path in candidates.rglob('*.json') if candidates.exists() else ():
        try:
            envelope = json.loads(path.read_text(encoding='utf-8'))
            if (set(envelope) != {'payload', 'sha256'} or not isinstance(envelope['payload'], dict)
                    or hashlib.sha256(canonical(envelope['payload']).encode('utf-8')).hexdigest()
                    != envelope['sha256']):
                raise ValueError('candidate_checksum')
        except (TypeError, KeyError, ValueError) as exc:
            raise ValueError('candidate_invalid:' + str(path)) from exc


def _quick_check(path):
    # Our private snapshot, never the live source. DELETE mode leaves no sidecars.
    conn = sqlite3.connect(str(path))
    try:
        if conn.execute('PRAGMA quick_check').fetchall() != [('ok',)]:
            raise ValueError('bundle_quick_check')
        conn.execute('PRAGMA journal_mode=DELETE')
    finally:
        conn.close()


def _inflate(bundle, target, expected):
    with gzip.open(Path(bundle) / DATABASE, 'rb') as src, Path(target).open('xb') as dst:
        shutil.copyfileobj(src, dst, 1024 * 1024)
    if _sha(target) != expected:
        raise ValueError('bundle_database_sha256')
    # read-only plus immutable is safe here: the inflated copy never has a WAL.
    conn = sqlite3.connect(Path(target).resolve().as_uri() + '?mode=ro&immutable=1', uri=True)
    try:
        if conn.execute('PRAGMA quick_check').fetchall() != [('ok',)]:
            raise ValueError('bundle_quick_check')
    finally:
        conn.close()


def validate_bundle(bundle: Path, *, inflate_to=None):
    """Check the manifest, candidates and the decompressed database.

    inflate_to keeps the verified uncompressed copy (publish uses it); otherwise
    it is written to a temporary file next to the bundle and removed.
    """
    bundle = Path(bundle)
    manifest = json.loads((bundle / 'manifest.json').read_text(encoding='utf-8'))
    if (manifest.get('format') != FORMAT or not isinstance(manifest.get('files'), dict)
            or not isinstance(manifest.get('database_sha256'), str)):
        raise ValueError('bundle_manifest')
    actual = _files(bundle)
    actual.pop('manifest.json', None)
    if actual != manifest['files'] or DATABASE not in actual:
        raise ValueError('bundle_checksum')
    _validate_candidates(bundle / 'state')
    if inflate_to is not None:
        _inflate(bundle, inflate_to, manifest['database_sha256'])
        return manifest
    with tempfile.TemporaryDirectory(prefix='.prosperity-inflate-', dir=bundle.parent) as temporary:
        _inflate(bundle, Path(temporary) / 'database.sqlite', manifest['database_sha256'])
    return manifest


def _sync_directory(path):
    fd = os.open(str(path), os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def backup_bundle(db_path, backup_dir, keep=2) -> Path:
    """Return a validated immutable directory; prune only this tool's same-source bundles.

    Caller holds finance_prosperity's job lock, so candidate records and SQLite
    are a matched point. Missing databases are explicit FileNotFoundError.
    """
    db_path, backup_dir = Path(db_path).absolute(), Path(backup_dir).absolute()
    if keep < 1:
        raise ValueError('invalid_backup_keep')
    if not db_path.is_file():
        raise FileNotFoundError(db_path)
    check_space(db_path)
    source_id = hashlib.sha256(str(db_path.resolve()).encode()).hexdigest()
    prefix = 'prosperity-' + source_id[:12] + '-'
    source_state = state_path(db_path)
    before = _files(source_state)
    _validate_candidates(source_state)
    backup_dir.mkdir(parents=True, exist_ok=True)
    temp = Path(tempfile.mkdtemp(prefix='.prosperity-backup-', dir=backup_dir))
    try:
        src = sqlite3.connect(db_path.resolve().as_uri() + '?mode=ro', uri=True)
        try:
            logical = src.execute('PRAGMA page_count').fetchone()[0] * src.execute('PRAGMA page_size').fetchone()[0]
            if shutil.disk_usage(backup_dir).free < max(2 * 1024**3, 3 * logical):
                raise ValueError('insufficient_space')
            dst = sqlite3.connect(str(temp / 'database.sqlite'))
            try:
                src.backup(dst)
            finally:
                dst.close()
        finally:
            src.close()
        raw = temp / 'database.sqlite'
        _quick_check(raw)
        database_sha256 = _sha(raw)
        with raw.open('rb') as src, gzip.open(temp / DATABASE, 'wb', compresslevel=6) as dst:
            shutil.copyfileobj(src, dst, 1024 * 1024)
        raw.unlink()
        if source_state.exists():
            shutil.copytree(source_state, temp / 'state')
        else:
            (temp / 'state' / 'candidates').mkdir(parents=True)
        if before != _files(source_state) or before != _files(temp / 'state'):
            raise ValueError('candidate_state_changed_during_backup')
        manifest = {'format': FORMAT, 'source_id': source_id, 'source_name': db_path.name,
                    'database_sha256': database_sha256, 'files': _files(temp)}
        (temp / 'manifest.json').write_text(canonical(manifest), encoding='utf-8')
        validate_bundle(temp)
        for name in _files(temp):
            with (temp / name).open('rb') as stream:
                os.fsync(stream.fileno())
        stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
        final = backup_dir / (prefix + stamp + '-' + uuid4().hex)
        os.rename(temp, final)
        _sync_directory(backup_dir)
    except BaseException:
        shutil.rmtree(temp, ignore_errors=True)
        raise
    ours = []
    for path in sorted(backup_dir.glob(prefix + '*')):
        if not path.is_dir() or path.is_symlink():
            continue
        try:
            old = validate_bundle(path)
            if old['source_id'] == source_id:
                ours.append(path)
        except (OSError, ValueError, KeyError, sqlite3.Error):
            continue  # damaged or unrecognized evidence is never a pruning target
    for path in ours[:-keep]:
        if path != final:
            shutil.rmtree(path)
    return final


def _publishable(db_path, lsof_path):
    if _pending(db_path).exists():
        raise ValueError('publish_incomplete')
    if not os.access(lsof_path, os.X_OK):
        raise ValueError('lsof_unavailable')
    paths = [Path(str(db_path) + suffix) for suffix in ('', '-wal', '-shm')]
    for path in paths:
        if not path.exists():
            continue
        result = subprocess.run([str(lsof_path), '-t', str(path)], capture_output=True)
        if result.returncode == 0 or result.stdout:
            raise ValueError('database_in_use')
        if result.returncode != 1 or result.stderr:
            raise ValueError('lsof_failed')
    wal = paths[1]
    if wal.exists() and wal.stat().st_size:
        raise ValueError('nonempty_wal')


def publish_bundle(bundle, db_path, *, lsof_path='/usr/sbin/lsof') -> None:
    """Install a validated matched pair, refusing readers/WAL; rollback ordinary failures.

    A crash leaves the .publish-pending marker and private old/new copies for
    explicit recovery. check_space and further publication refuse that state.
    """
    bundle, db_path = Path(bundle), Path(db_path).absolute()
    validate_bundle(bundle)
    check_space(db_path)
    _publishable(db_path, lsof_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix='.prosperity-publish-', dir=db_path.parent))
    marker = _pending(db_path)
    target_state = state_path(db_path)
    old_db, old_state = stage / 'previous.sqlite', stage / 'previous.state'
    installed_db = installed_state = False
    marker_owned = False
    try:
        shutil.copytree(bundle, stage / 'new')
        validate_bundle(stage / 'new', inflate_to=stage / 'database.sqlite')
        _publishable(db_path, lsof_path)
        with marker.open('x', encoding='utf-8') as out:
            marker_owned = True
            out.write(canonical({'stage': str(stage), 'db': str(db_path)}))
            out.flush()
            os.fsync(out.fileno())
        _sync_directory(db_path.parent)
        for suffix in ('-wal', '-shm'):
            Path(str(db_path) + suffix).unlink(missing_ok=True)
        if db_path.exists():
            os.replace(db_path, old_db)
        if target_state.exists():
            os.replace(target_state, old_state)
        os.replace(stage / 'new' / 'state', target_state)
        installed_state = True
        os.replace(stage / 'database.sqlite', db_path)
        installed_db = True
        _sync_directory(db_path.parent)
        marker.unlink()
    except BaseException:
        if installed_db:
            db_path.unlink()
        if installed_state:
            shutil.rmtree(target_state)
        if old_db.exists():
            os.replace(old_db, db_path)
        if old_state.exists():
            os.replace(old_state, target_state)
        if marker_owned:
            marker.unlink(missing_ok=True)
        raise
    finally:
        if not marker_owned or not marker.exists():
            shutil.rmtree(stage, ignore_errors=True)
