import hashlib
import json
import sqlite3
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.data.prosperity_storage import canonical
from src.data.prosperity_backup import (backup_bundle, check_space, publish_bundle,
                                       validate_bundle)


def database(path, value='old'):
    conn = sqlite3.connect(path)
    conn.execute('PRAGMA journal_mode=WAL')
    conn.execute('CREATE TABLE IF NOT EXISTS sample (value TEXT)')
    conn.execute('INSERT INTO sample VALUES (?)', (value,))
    conn.commit()
    return conn


def candidate(db, value):
    folder = Path(str(db) + '.state') / 'candidates'
    folder.mkdir(parents=True, exist_ok=True)
    payload = {'value': value}
    path = folder / 'one.json'
    path.write_text(canonical({'payload': payload,
                              'sha256': hashlib.sha256(canonical(payload).encode()).hexdigest()}))
    return path


def values(db):
    with sqlite3.connect(db) as conn:
        return [r[0] for r in conn.execute('SELECT value FROM sample')]


def bundle_values(bundle, tmp_path):
    import gzip
    import shutil
    raw = tmp_path / 'inflated.sqlite'
    with gzip.open(bundle / 'database.sqlite.gz', 'rb') as src, raw.open('wb') as dst:
        shutil.copyfileobj(src, dst)
    return values(raw)


def test_space_refuses_before_creating_any_output(tmp_path, monkeypatch):
    monkeypatch.setattr('src.data.prosperity_backup.shutil.disk_usage',
                        lambda _: SimpleNamespace(free=2 * 1024**3 - 1))
    with pytest.raises(ValueError, match='insufficient_space'):
        check_space(tmp_path / 'missing' / 'prosperity.db')
    assert not (tmp_path / 'missing').exists()


def test_backup_includes_uncheckpointed_wal_and_matching_candidates(tmp_path):
    db = tmp_path / 'prosperity.db'
    conn = database(db, 'wal-value')
    candidate(db, 'same-generation')
    try:
        bundle = backup_bundle(db, tmp_path / 'backups')
        validate_bundle(bundle)
        assert bundle_values(bundle, tmp_path) == ['wal-value']
        assert json.loads((bundle / 'state/candidates/one.json').read_text())['payload']['value'] == 'same-generation'
    finally:
        conn.close()


def test_backup_is_gzip_with_uncompressed_checksum(tmp_path):
    import gzip
    db = tmp_path / 'prosperity.db'
    database(db, 'value').close()
    bundle = backup_bundle(db, tmp_path / 'backups')
    assert (bundle / 'database.sqlite.gz').is_file() and not (bundle / 'database.sqlite').exists()
    manifest = validate_bundle(bundle)
    raw = gzip.decompress((bundle / 'database.sqlite.gz').read_bytes())
    assert hashlib.sha256(raw).hexdigest() == manifest['database_sha256']
    assert bundle_values(bundle, tmp_path) == ['value']


def test_gzip_of_another_database_fails_uncompressed_checksum(tmp_path):
    import gzip
    db, other = tmp_path / 'prosperity.db', tmp_path / 'other.db'
    database(db, 'value').close()
    database(other, 'forged').close()
    bundle = backup_bundle(db, tmp_path / 'backups')
    forged = bundle / 'database.sqlite.gz'
    forged.write_bytes(gzip.compress(other.read_bytes()))
    manifest = json.loads((bundle / 'manifest.json').read_text())
    manifest['files']['database.sqlite.gz'] = hashlib.sha256(forged.read_bytes()).hexdigest()
    (bundle / 'manifest.json').write_text(canonical(manifest))
    with pytest.raises(ValueError, match='bundle_database_sha'):
        validate_bundle(bundle)


def test_bad_candidate_never_publishes_or_prunes(tmp_path):
    db = tmp_path / 'prosperity.db'
    database(db).close()
    path = candidate(db, 'original')
    backups = tmp_path / 'backups'
    old = backup_bundle(db, backups)
    path.write_text('{"payload":{},"sha256":"wrong"}')
    with pytest.raises(ValueError, match='candidate'):
        backup_bundle(db, backups)
    assert list(backups.iterdir()) == [old]


def test_retention_only_deletes_own_same_database_bundles(tmp_path):
    db, other = tmp_path / 'p.db', tmp_path / 'research.db'
    database(db).close()
    database(other).close()
    backups = tmp_path / 'backups'
    other_bundle = backup_bundle(other, backups)
    unrelated = backups / 'manual-keep'
    unrelated.mkdir()
    ours = [backup_bundle(db, backups) for _ in range(3)]
    assert not ours[0].exists()
    assert all(p.exists() for p in [*ours[1:], other_bundle, unrelated])


def test_publish_checks_hash_before_touching_existing_pair(tmp_path, monkeypatch):
    source, target = tmp_path / 'source.db', tmp_path / 'target.db'
    database(source, 'new').close()
    candidate(source, 'new')
    database(target, 'old').close()
    old_candidate = candidate(target, 'old').read_bytes()
    bundle = backup_bundle(source, tmp_path / 'backups')
    (bundle / 'state/candidates/one.json').write_text('{}')
    with pytest.raises(ValueError):
        publish_bundle(bundle, target)
    assert values(target) == ['old']
    assert (Path(str(target) + '.state') / 'candidates/one.json').read_bytes() == old_candidate


def test_publish_refuses_live_reader_and_nonempty_wal(tmp_path, monkeypatch):
    source, target = tmp_path / 'source.db', tmp_path / 'target.db'
    database(source, 'new').close()
    database(target, 'old').close()
    bundle = backup_bundle(source, tmp_path / 'backups')
    monkeypatch.setattr('src.data.prosperity_backup.subprocess.run',
                        lambda *a, **k: SimpleNamespace(returncode=0, stdout=b'123\n', stderr=b''))
    with pytest.raises(ValueError, match='database_in_use'):
        publish_bundle(bundle, target, lsof_path='/usr/bin/true')
    monkeypatch.setattr('src.data.prosperity_backup.subprocess.run',
                        lambda *a, **k: SimpleNamespace(returncode=1, stdout=b'', stderr=b''))
    Path(str(target) + '-wal').write_bytes(b'pending')
    with pytest.raises(ValueError, match='nonempty_wal'):
        publish_bundle(bundle, target, lsof_path='/usr/bin/true')


def test_publish_replaces_pair_and_rolls_back_mid_publish(tmp_path, monkeypatch):
    source, target = tmp_path / 'source.db', tmp_path / 'target.db'
    database(source, 'new').close()
    candidate(source, 'new')
    database(target, 'old').close()
    candidate(target, 'old')
    bundle = backup_bundle(source, tmp_path / 'backups')
    monkeypatch.setattr('src.data.prosperity_backup.subprocess.run',
                        lambda *a, **k: SimpleNamespace(returncode=1, stdout=b'', stderr=b''))
    import src.data.prosperity_backup as backup
    original = backup.os.replace
    def fail_new_db(src, dst):
        if Path(src).name == 'database.sqlite' and Path(dst) == target:
            raise OSError('injected publish failure')
        return original(src, dst)
    monkeypatch.setattr(backup.os, 'replace', fail_new_db)
    with pytest.raises(OSError, match='injected'):
        publish_bundle(bundle, target, lsof_path='/usr/bin/true')
    assert values(target) == ['old']
    assert json.loads((Path(str(target)+'.state')/'candidates/one.json').read_text())['payload']['value'] == 'old'
    monkeypatch.setattr(backup.os, 'replace', original)
    publish_bundle(bundle, target, lsof_path='/usr/bin/true')
    assert values(target) == ['new']
    assert json.loads((Path(str(target)+'.state')/'candidates/one.json').read_text())['payload']['value'] == 'new'


def test_remote_snapshot_program_is_self_contained_and_missing_database_skips(tmp_path):
    import subprocess
    import sys
    from scripts.sync_prosperity import remote_program, extract_bundle
    missing = subprocess.run([sys.executable, '-', str(tmp_path/'missing.db'), str(tmp_path/'job.lock')],
                             input=remote_program().encode(), capture_output=True)
    assert missing.returncode == 44
    assert not (tmp_path/'missing.db').exists()
    db = tmp_path/'remote.db'
    database(db, 'remote').close()
    candidate(db, 'remote')
    transfer = tmp_path/'transfer.tar'
    with transfer.open('wb') as output:
        result = subprocess.run([sys.executable, '-', str(db), str(tmp_path/'job.lock')],
                                input=remote_program().encode(), stdout=output, stderr=subprocess.PIPE)
    assert result.returncode == 0, result.stderr
    bundle = extract_bundle(transfer, tmp_path/'unpacked')
    validate_bundle(bundle)
    assert bundle_values(bundle, tmp_path) == ['remote']
    assert not list(tmp_path.glob('.prosperity-pull-*'))


def test_remote_snapshot_holds_the_scheduler_job_lock(tmp_path):
    import fcntl
    import subprocess
    import sys
    from scripts.sync_prosperity import remote_program
    db, lock = tmp_path/'remote.db', tmp_path/'job.lock'
    database(db).close()
    with lock.open('w') as held:
        fcntl.flock(held, fcntl.LOCK_EX | fcntl.LOCK_NB)
        result = subprocess.run([sys.executable, '-', str(db), str(lock)],
                                input=remote_program().encode(), capture_output=True)
    assert result.returncode == 75
    assert result.stdout == b''


def test_extract_refuses_links_and_traversal(tmp_path):
    import tarfile
    from scripts.sync_prosperity import extract_bundle
    for name, kind in [('../escape', tarfile.REGTYPE), ('bundle/link', tarfile.SYMTYPE)]:
        archive = tmp_path/'unsafe.tar'
        with tarfile.open(archive, 'w') as out:
            item = tarfile.TarInfo(name)
            item.type = kind
            item.linkname = '/etc/passwd'
            out.addfile(item)
        with pytest.raises(ValueError, match='unsafe_bundle_archive'):
            extract_bundle(archive, tmp_path/'unpacked')


def test_pull_missing_or_corrupt_transfer_preserves_local_pair(tmp_path, monkeypatch, capsys):
    from scripts.sync_prosperity import pull_bundle
    db = tmp_path/'local.db'
    database(db, 'old').close()
    old_state = candidate(db, 'old').read_bytes()
    monkeypatch.setattr('scripts.sync_prosperity.subprocess.run',
                        lambda *a, **k: SimpleNamespace(returncode=44, stderr=b'not deployed'))
    assert pull_bundle(host='unused', remote_db='/missing.db', db_path=db) is False
    assert '[SKIP]' in capsys.readouterr().out
    monkeypatch.setattr('scripts.sync_prosperity.subprocess.run',
                        lambda *a, **k: SimpleNamespace(returncode=0, stderr=b'PROSPERITY_SHA256 '+b'0'*64))
    with pytest.raises(ValueError, match='transfer_checksum'):
        pull_bundle(host='unused', remote_db='/remote.db', db_path=db)
    assert values(db) == ['old']
    assert (Path(str(db)+'.state')/'candidates/one.json').read_bytes() == old_state


def test_crash_marker_blocks_backup_and_followup_publication(tmp_path):
    db = tmp_path/'local.db'
    database(db).close()
    Path(str(db)+'.publish-pending').write_text('{}')
    with pytest.raises(ValueError, match='publish_incomplete'):
        backup_bundle(db, tmp_path/'backups')
    with pytest.raises(ValueError, match='publish_incomplete'):
        check_space(db)


def test_publish_does_not_remove_another_publishers_marker(tmp_path, monkeypatch):
    import src.data.prosperity_backup as backup
    source, target = tmp_path/'source.db', tmp_path/'target.db'
    database(source).close()
    database(target).close()
    bundle = backup_bundle(source, tmp_path/'backups')
    checks = []
    def race(*args):
        checks.append(1)
        if len(checks) == 2:
            Path(str(target)+'.publish-pending').write_text('another publisher')
    monkeypatch.setattr(backup, '_publishable', race)
    with pytest.raises(FileExistsError):
        publish_bundle(bundle, target)
    assert Path(str(target)+'.publish-pending').read_text() == 'another publisher'
