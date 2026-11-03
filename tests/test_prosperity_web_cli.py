"""M11 CLI: snapshot checks, output-path guard, atomic write."""
import hashlib

import pytest

from src.data.prosperity_store import ProsperityStore
from tests.prosperity_fixtures import storage_batch


def sha(p):
    return hashlib.sha256(p.read_bytes()).hexdigest()


@pytest.fixture()
def db(tmp_path):
    path = tmp_path / 'prosperity.db'
    with ProsperityStore(path, read_only=False) as s:
        s.save_batch(storage_batch(freeze=True, quarter_label='2026Q2'))
        s.save_batch(storage_batch(as_of='2026-10-03', freeze=True, quarter_label='2026Q3'))
    for suffix in ('-wal', '-shm'):                     # closed cleanly: the WAL is checkpointed away
        extra = tmp_path / ('prosperity.db' + suffix)
        if extra.exists() and extra.stat().st_size == 0:
            extra.unlink()
    assert not (tmp_path / 'prosperity.db-wal').exists()
    return path


def run(db, out, *extra):
    from scripts.prosperity_web import main
    return main(['--db', str(db), '--out', str(out), '--code-version', 'test-v1', *extra])


def test_writes_one_page_and_leaves_db_unchanged(db, tmp_path):
    before = sha(db)
    assert run(db, tmp_path / 'web.html') == 0
    assert sha(db) == before
    html = (tmp_path / 'web.html').read_text(encoding='utf-8')
    assert html.count('<section id=') == 12
    assert before[:16] in html and '未标入池：未提供池文件' in html
    assert not list(tmp_path.glob('*.tmp*')) and not list(tmp_path.glob('.prosperity-web.*'))


def test_input_errors_write_nothing(db, tmp_path):
    assert run(tmp_path / 'missing.db', tmp_path / 'a.html') == 3
    assert run(db, tmp_path / 'a.html', '--code-version', 'nope') == 3
    assert run(db, tmp_path) == 4                          # --out is a directory
    assert run(db, tmp_path / 'no' / 'a.html') == 4
    assert not (tmp_path / 'a.html').exists()


def test_output_may_not_overwrite_an_input(db, tmp_path):
    pool = tmp_path / 'pool.json'; pool.write_text('{}')
    before = (sha(db), sha(pool))
    assert run(db, db) == 4
    assert run(db, tmp_path / 'prosperity.db-wal') == 4
    assert run(db, tmp_path / 'prosperity.db.state') == 4
    assert run(db, pool, '--pool', str(pool)) == 4
    assert run(db, tmp_path / '.' / 'prosperity.db') == 4              # same file via another spelling
    assert (sha(db), sha(pool)) == before
    assert not (tmp_path / 'prosperity.db-wal').exists()


def test_wal_content_is_refused(db, tmp_path):
    (tmp_path / 'prosperity.db-wal').write_bytes(b'x')
    assert run(db, tmp_path / 'a.html') == 4
    assert not (tmp_path / 'a.html').exists()


def test_db_changed_while_reading(db, tmp_path, monkeypatch):
    import scripts.prosperity_web as cli
    real = cli.load_pages

    def load_then_touch(*a, **k):
        out = real(*a, **k)
        db.write_bytes(db.read_bytes() + b'\0')
        return out
    monkeypatch.setattr(cli, 'load_pages', load_then_touch)
    assert run(db, tmp_path / 'a.html') == 6 and not (tmp_path / 'a.html').exists()


def test_unreadable_pool_is_reported_not_fatal(db, tmp_path):
    (tmp_path / 'pool.json').write_text('{not json')
    assert run(db, tmp_path / 'a.html', '--pool', str(tmp_path / 'pool.json')) == 0
    assert '未标入池：池文件无法读取' in (tmp_path / 'a.html').read_text(encoding='utf-8')
    assert run(db, tmp_path / 'b.html', '--pool', str(tmp_path / 'missing.json')) == 0
    assert '未标入池：池文件无法读取' in (tmp_path / 'b.html').read_text(encoding='utf-8')


def test_pool_hash_in_header(db, tmp_path):
    pool = tmp_path / 'pool.json'; pool.write_text('{"schema_version": 9}')
    assert run(db, tmp_path / 'a.html', '--pool', str(pool)) == 0
    html = (tmp_path / 'a.html').read_text(encoding='utf-8')
    assert sha(pool)[:16] in html and '未标入池：池文件校验失败' in html
