import json
import sqlite3
from pathlib import Path

import pytest

from src.data.prosperity_backup import validate_bundle
from src.data.prosperity_storage import canonical, content_hash
from src.data.prosperity_store import ProsperityStore
from tests.prosperity_fixtures import storage_batch


def seeded(tmp_path):
    """v1 (oldest live), v2 (previous live), v3 (current, replay only so far)."""
    db = tmp_path / 'prosperity.db'
    with ProsperityStore(db, read_only=False) as store:
        store.save_batch(storage_batch(as_of='2026-09-26', code_version='v1', freeze=True))
        store.save_batch(storage_batch(as_of='2026-10-03', code_version='v2'))
        from dataclasses import replace
        store.save_batch(tuple(replace(b, meta=replace(b.meta, mode='replay'))
                               for b in storage_batch(as_of='2026-10-10', code_version='v3')))
    state = Path(str(db) + '.state')
    for version in ('v1', 'v2', 'v3'):
        for kind, body in (('candidates', {'identity': {'code_version': version}}),
                           ('runs', None)):
            folder = state / kind
            folder.mkdir(parents=True, exist_ok=True)
            if kind == 'candidates':
                import hashlib
                text = canonical({'payload': body,
                                  'sha256': hashlib.sha256(canonical(body).encode()).hexdigest()})
            else:
                text = canonical({'code_version': version, 'exit_code': 0})
            (folder / f'{version}.json').write_text(text)
    return db


def hashes(db, version):
    with ProsperityStore(db) as store, sqlite3.connect(db) as conn:
        keys = conn.execute('SELECT board_type,as_of,scheme_hash FROM board WHERE code_version=?',
                            (version,)).fetchall()
        from src.data.prosperity_storage import BoardKey
        return sorted(content_hash(store.get_board(BoardKey(t, a, s, version))) for t, a, s in keys)


def test_retained_versions_are_current_plus_previous_live(tmp_path):
    from scripts.prosperity_archive import retained_versions
    db = seeded(tmp_path)
    assert retained_versions(db, current='v3') == {'v3', 'v2'}
    assert retained_versions(db, current='v2') == {'v2', 'v1'}


def test_export_contains_only_that_version_and_reads_back(tmp_path):
    from scripts.prosperity_archive import export_version, version_counts
    db = seeded(tmp_path)
    before = {v: version_counts(db, v) for v in ('v1', 'v2', 'v3')}
    manifest_path = export_version(db, 'v1', tmp_path / 'archive')
    manifest = json.loads(manifest_path.read_text())
    archive = manifest_path.parent / manifest['database']
    assert version_counts(archive, 'v1') == before['v1'] == manifest['counts']
    assert all(version_counts(archive, v)['board'] == 0 for v in ('v2', 'v3'))
    assert {v: version_counts(db, v) for v in ('v1', 'v2', 'v3')} == before
    assert hashes(archive, 'v1') == hashes(db, 'v1') == manifest['board_hashes']
    assert manifest['verified'] is False
    assert sorted(p.name for p in (manifest_path.parent / 'state').rglob('*.json')) == ['v1.json', 'v1.json']


def test_verify_checks_archive_copy_before_marking(tmp_path):
    from scripts.prosperity_archive import export_version, verify_archive
    db = seeded(tmp_path)
    manifest_path = export_version(db, 'v1', tmp_path / 'archive')
    archive = manifest_path.parent / json.loads(manifest_path.read_text())['database']
    with sqlite3.connect(archive) as conn:
        conn.execute("DELETE FROM publication")
        conn.execute("INSERT INTO publication SELECT board_id, '2026-09-27T00:00:00Z' FROM board LIMIT 1")
    with pytest.raises(ValueError, match='archive_mismatch'):
        verify_archive(manifest_path)
    assert json.loads(manifest_path.read_text())['verified'] is False
    clean = export_version(db, 'v1', tmp_path / 'archive2')
    (clean.parent / 'state' / 'runs' / 'v1.json').write_text('{}')
    with pytest.raises(ValueError, match='archive_mismatch'):
        verify_archive(clean)
    good = export_version(db, 'v1', tmp_path / 'archive3')
    verify_archive(good)
    assert json.loads(good.read_text())['verified'] is True


def test_export_refuses_existing_output_and_unknown_version(tmp_path):
    from scripts.prosperity_archive import export_version
    db = seeded(tmp_path)
    export_version(db, 'v1', tmp_path / 'archive')
    with pytest.raises(FileExistsError):
        export_version(db, 'v1', tmp_path / 'archive')
    with pytest.raises(ValueError, match='version_not_found'):
        export_version(db, 'nope', tmp_path / 'other')


def test_remove_requires_verified_matching_manifest(tmp_path):
    from scripts.prosperity_archive import export_version, remove_version, verify_archive, version_counts
    db = seeded(tmp_path)
    manifest_path = verify_archive(export_version(db, 'v1', tmp_path / 'archive'))
    before = version_counts(db, 'v1')
    for change in ({'verified': False}, {'counts': {**before, 'board': 1}}, {'manifest_sha256': '0' * 64}):
        bad = tmp_path / 'bad.json'
        bad.write_text(canonical({**json.loads(manifest_path.read_text()), **change}))
        with pytest.raises(ValueError, match='manifest'):
            remove_version(db, bad, backup_dir=tmp_path / 'backups', current='v3')
    with pytest.raises(FileNotFoundError):
        remove_version(db, tmp_path / 'missing.json', backup_dir=tmp_path / 'backups', current='v3')
    assert version_counts(db, 'v1') == before
    assert not (tmp_path / 'backups').exists()


def test_remove_refuses_retained_versions(tmp_path):
    from scripts.prosperity_archive import export_version, remove_version, verify_archive, version_counts
    db = seeded(tmp_path)
    for version in ('v2', 'v3'):
        manifest = verify_archive(export_version(db, version, tmp_path / f'archive-{version}'))
        with pytest.raises(ValueError, match='retained_version'):
            remove_version(db, manifest, backup_dir=tmp_path / 'backups', current='v3')
        assert version_counts(db, version)['board'] == 4


def test_remove_deletes_only_archived_version_and_keeps_others_intact(tmp_path):
    from scripts.prosperity_archive import export_version, remove_version, verify_archive, version_counts
    db = seeded(tmp_path)
    kept = {v: (version_counts(db, v), hashes(db, v)) for v in ('v2', 'v3')}
    size = db.stat().st_size
    manifest = verify_archive(export_version(db, 'v1', tmp_path / 'archive'))
    bundle = remove_version(db, manifest, backup_dir=tmp_path / 'backups', current='v3')
    assert validate_bundle(bundle)['database_sha256']
    assert all(n == 0 for n in version_counts(db, 'v1').values())
    assert {v: (version_counts(db, v), hashes(db, v)) for v in ('v2', 'v3')} == kept
    with sqlite3.connect(db) as conn:
        assert conn.execute('PRAGMA quick_check').fetchall() == [('ok',)]
        assert conn.execute('PRAGMA foreign_key_check').fetchall() == []
        assert conn.execute('SELECT count(*) FROM scheme').fetchone()[0] == 4
    assert db.stat().st_size < size
    state = Path(str(db) + '.state')
    assert sorted(p.name for p in state.rglob('*.json')) == ['v2.json', 'v2.json', 'v3.json', 'v3.json']
    # The archive is the only remaining copy of v1 and still reads back.
    archive = manifest.parent / json.loads(manifest.read_text())['database']
    assert version_counts(archive, 'v1') == json.loads(manifest.read_text())['counts']


def test_remove_rejects_when_cloud_rows_differ_from_archive(tmp_path):
    from scripts.prosperity_archive import export_version, remove_version, verify_archive, version_counts
    db = seeded(tmp_path)
    manifest = verify_archive(export_version(db, 'v1', tmp_path / 'archive'))
    with sqlite3.connect(db) as conn:
        conn.execute("DELETE FROM publication")
        conn.execute("INSERT INTO publication SELECT board_id, '2026-09-27T00:00:00Z' FROM board "
                     "WHERE code_version='v1'")
    with pytest.raises(ValueError, match='manifest_does_not_match_database'):
        remove_version(db, manifest, backup_dir=tmp_path / 'backups', current='v3')
    assert version_counts(db, 'v1')['board'] == 8


def test_remove_holds_job_lock(tmp_path):
    import fcntl
    from scripts.prosperity_archive import export_version, remove_version, verify_archive, version_counts
    db = seeded(tmp_path)
    manifest = verify_archive(export_version(db, 'v1', tmp_path / 'archive'))
    lock = tmp_path / 'job.lock'
    with lock.open('w') as held:
        fcntl.flock(held, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises(BlockingIOError):
            remove_version(db, manifest, backup_dir=tmp_path / 'backups', current='v3', lock_path=lock)
    assert version_counts(db, 'v1')['board'] == 8


def test_space_hint_lists_archivable_versions_without_removing(tmp_path):
    from scripts.prosperity_archive import archivable_hint, version_counts
    db = seeded(tmp_path)
    hint = archivable_hint(db, current='v3')
    assert 'v1' in hint and 'v2' not in hint.split('可归档')[1] and 'MB' in hint
    assert version_counts(db, 'v1')['board'] == 8


def test_no_cron_or_weekly_path_calls_remove():
    root = Path(__file__).resolve().parents[1]
    for rel in ('scripts/run_prosperity.sh', 'scripts/prosperity_weekly.py', 'src/data/prosperity_run.py',
                'scripts/cron_wrapper.sh', 'sync_to_cloud.sh'):
        assert 'remove_version' not in (root / rel).read_text() and 'prosperity_archive.py remove' not in (root / rel).read_text()


def test_cli_space_failure_prints_archive_hint(tmp_path, monkeypatch, capsys):
    from datetime import datetime, timezone
    from scripts import prosperity_weekly as cli
    db = seeded(tmp_path)
    def no_space(path):
        raise ValueError('insufficient_space')
    monkeypatch.setattr(cli, 'check_space', no_space)
    monkeypatch.setattr(cli, 'code_version', lambda: 'v3')
    now = datetime(2026, 10, 24, 9, tzinfo=timezone.utc)
    assert cli.main(['--live', '--db', str(db)], now=lambda: now) == 6
    err = capsys.readouterr().err
    assert '可归档' in err and 'v1' in err
