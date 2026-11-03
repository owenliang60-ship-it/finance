#!/usr/bin/env python3
"""M11 景气网页: prosperity.db snapshot → one static HTML page (local, read-only).

Plan: docs/plans/2026-11-02-prosperity-m11-web.md. Exit codes: 0 written; 3 the database is
missing or has no board for the code version; 4 invalid arguments (--out is a directory, its
parent is missing, or it is the same file or sidecar name as the database (after following a
symlinked --db), its -wal/-shm/.state, or --pool), or the
database has WAL content: pass a self-contained `.backup` snapshot so its SHA256 covers every byte
SQLite reads; 6 the database changed while it was read. Nothing is written unless the exit code
is 0. An unreadable --pool is not an error: the page says why the pool is not marked.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import tempfile
import time
import unicodedata
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.data.prosperity_backup import _sha, wal_bytes as _wal_bytes  # noqa: E402
from src.data.prosperity_store import ProsperityStore  # noqa: E402
from src.data.prosperity_web import load_pages, render_page  # noqa: E402
from terminal.prosperity.version import code_version  # noqa: E402


def _name_key(name: str) -> str:
    """APFS treats names as equal regardless of case and of Unicode normalization (NFC/NFD, Codex P2)."""
    return unicodedata.normalize('NFC', unicodedata.normalize('NFC', name).casefold())


def _overwrites_input(out: Path, db: Path, pool: Optional[Path]) -> bool:
    """True if writing `out` could replace the database, its -wal/-shm/.state, or the pool file.

    `db` must already be resolved: SQLite opens the link target, so its sidecars live there (Codex P1).
    Path strings are not enough (review 2026-11-03): macOS's default APFS is case- and
    normalization-insensitive and a hardlink has its own path. Existing files are compared by
    identity (samefile); a sidecar that does not exist yet is matched by directory identity plus a
    normalized, case-folded name.
    """
    targets = [db, *(db.with_name(db.name + s) for s in ('-wal', '-shm', '.state'))]
    if pool is not None:
        targets.append(pool)
    for target in targets:
        if out.exists() and target.exists():
            if os.path.samefile(out, target):
                return True
        elif (out.parent.exists() and target.parent.exists() and os.path.samefile(out.parent, target.parent)
              and _name_key(out.name) == _name_key(target.name)):
            return True
    return False


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description='Prosperity web page (local, read-only)')
    parser.add_argument('--db', type=Path, required=True, help='prosperity.db snapshot')
    parser.add_argument('--out', type=Path, required=True, help='HTML file to write')
    parser.add_argument('--pool', type=Path, default=None, help='prosperity_pool.json to mark (optional)')
    parser.add_argument('--code-version', default=None)
    args = parser.parse_args(argv)

    out = args.out.resolve()
    # Resolve once and use the real path everywhere: ProsperityStore resolves too, so a symlinked --db
    # would otherwise be read at the target while its WAL and sidecars were checked at the alias (Codex P1).
    db = args.db.resolve()
    pool_path = args.pool.resolve() if args.pool is not None else None
    if out.is_dir() or not out.parent.is_dir():
        print(f'--out must be a file in an existing directory: {args.out}', file=sys.stderr)
        return 4
    if _overwrites_input(out, db, pool_path):                  # review P1: never replace an input
        print(f'--out would overwrite an input: {args.out}', file=sys.stderr)
        return 4
    if not db.is_file():
        print(f'database not found: {args.db}', file=sys.stderr)
        return 3
    if _wal_bytes(db):
        print(f'{args.db} has WAL content; pass a self-contained snapshot (sqlite3 .backup)', file=sys.stderr)
        return 4
    version = args.code_version or code_version()
    started = time.monotonic()
    before = _sha(db)

    pool, pool_error, pool_sha = None, None, None
    if pool_path is not None:
        try:
            raw = pool_path.read_bytes()
            pool_sha = hashlib.sha256(raw).hexdigest()
            pool = json.loads(raw.decode('utf-8'))
        except (OSError, UnicodeDecodeError, ValueError, RecursionError):
            pool, pool_error = None, '池文件无法读取'

    with ProsperityStore(db, read_only=True) as store:
        pages = load_pages(store, code_version=version, pool=pool, pool_error=pool_error)
    if not pages:
        print(f'no board for code_version {version}', file=sys.stderr)
        return 3
    if _sha(db) != before or _wal_bytes(db):
        print('the database changed while it was read; nothing written', file=sys.stderr)
        return 6

    generated = datetime.now(timezone.utc).isoformat(timespec='seconds').replace('+00:00', 'Z')
    html = render_page(pages, generated_at=generated, pool_error=pool_error,
                       source={'db_sha256': before, 'code_version': version, 'pool_sha256': pool_sha or ''})
    temp = None
    try:
        with tempfile.NamedTemporaryFile('w', encoding='utf-8', dir=out.parent, prefix='.prosperity-web.',
                                         suffix='.tmp', delete=False) as handle:
            temp = Path(handle.name)
            handle.write(html)
        os.replace(temp, out)
        temp = None
    finally:
        if temp is not None:
            temp.unlink(missing_ok=True)
    rows = sum(len(p.rows) for p in pages)
    print(json.dumps({'out': str(out), 'sections': len(pages), 'rows': rows, 'bytes': len(html.encode('utf-8')),
                      'elapsed_seconds': round(time.monotonic() - started, 1)}, ensure_ascii=False),
          file=sys.stderr)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
