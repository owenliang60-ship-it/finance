#!/usr/bin/env python3
"""Pull a matched prosperity SQLite/candidate bundle; no remote installation needed."""
from __future__ import annotations

import argparse
import inspect
from pathlib import Path, PurePosixPath
import re
import shlex
import shutil
import subprocess
import sys
import tarfile
import tempfile

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.data import prosperity_backup as backup
from src.data.prosperity_storage import canonical


def remote_program():
    """Send the same tested implementation plus M8's canonical function over stdin.

    The cloud may not have M8/M9 installed yet. No repository imports, API clients
    or credential/environment loading are needed to snapshot its SQLite file.
    """
    source = Path(backup.__file__).read_text(encoding='utf-8')
    source = source.replace('from src.data.prosperity_storage import canonical',
                            inspect.getsource(canonical))
    return source + '''
import fcntl
import sys
import tarfile
db, lock_path = map(Path, sys.argv[1:])
if not db.is_file():
    print('prosperity database is not deployed; skipping', file=sys.stderr)
    sys.exit(44)
lock_path.parent.mkdir(parents=True, exist_ok=True)
with lock_path.open('a') as job_lock:
    try:
        fcntl.flock(job_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        print('finance_prosperity job lock is busy', file=sys.stderr)
        sys.exit(75)
    with tempfile.TemporaryDirectory(prefix='.prosperity-pull-', dir=db.parent) as temporary:
        directory = Path(temporary)
        bundle = backup_bundle(db, directory / 'bundles')
        archive = directory / 'transfer.tar'
        with tarfile.open(archive, 'w') as output:
            output.add(bundle, arcname='bundle')
        print('PROSPERITY_SHA256 ' + _sha(archive), file=sys.stderr)
        with archive.open('rb') as stream:
            shutil.copyfileobj(stream, sys.stdout.buffer)
        sys.stdout.buffer.flush()
'''


def extract_bundle(archive, destination):
    destination = Path(destination)
    with tarfile.open(archive, 'r:') as stream:
        members = stream.getmembers()
        seen = set()
        for item in members:
            path = PurePosixPath(item.name)
            if (path.is_absolute() or '..' in path.parts or not path.parts or path.parts[0] != 'bundle'
                    or not (item.isdir() or item.isfile()) or item.name in seen):
                raise ValueError('unsafe_bundle_archive')
            seen.add(item.name)
        for item in members:
            target = destination / item.name
            if item.isdir():
                target.mkdir(parents=True, exist_ok=True)
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                with stream.extractfile(item) as src, target.open('xb') as dst:
                    shutil.copyfileobj(src, dst)
    result = destination / 'bundle'
    backup.validate_bundle(result)
    return result


def pull_bundle(*, host, remote_db, db_path, lock_path='/tmp/finance-cron-locks/finance_prosperity.lock',
                lsof_path='/usr/sbin/lsof'):
    db_path = Path(db_path).absolute()
    backup.check_space(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    command = 'python3 - ' + shlex.quote(str(remote_db)) + ' ' + shlex.quote(str(lock_path))
    with tempfile.TemporaryDirectory(prefix='.prosperity-download-', dir=db_path.parent) as temporary:
        temporary = Path(temporary)
        archive = temporary / 'transfer.tar'
        with archive.open('wb') as output:
            result = subprocess.run(['ssh', host, command], input=remote_program().encode('utf-8'),
                                    stdout=output, stderr=subprocess.PIPE)
        if result.returncode == 44:
            print('[SKIP] prosperity.db 云端未部署，保留本地副本')
            return False
        if result.returncode:
            raise RuntimeError('prosperity_snapshot_failed: ' + result.stderr.decode('utf-8', errors='replace'))
        matches = re.findall(rb'^PROSPERITY_SHA256 ([0-9a-f]{64})$', result.stderr, flags=re.MULTILINE)
        if len(matches) != 1 or backup._sha(archive) != matches[0].decode():
            raise ValueError('prosperity_transfer_checksum')
        bundle = extract_bundle(archive, temporary / 'unpacked')
        backup.publish_bundle(bundle, db_path, lsof_path=lsof_path)
    print('[INFO] prosperity.db 与候选状态已匹配发布（sha256 + quick_check + 候选校验通过）')
    return True


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--host', default='aliyun')
    parser.add_argument('--remote-db', default='/root/workspace/Finance/data/prosperity.db')
    parser.add_argument('--db', type=Path, required=True)
    parser.add_argument('--lock-path', default='/tmp/finance-cron-locks/finance_prosperity.lock')
    parser.add_argument('--lsof', default='/usr/sbin/lsof')
    args = parser.parse_args(argv)
    try:
        pull_bundle(host=args.host, remote_db=args.remote_db, db_path=args.db,
                    lock_path=args.lock_path, lsof_path=args.lsof)
    except (OSError, ValueError, RuntimeError) as exc:
        print('[ERROR] ' + str(exc), file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
