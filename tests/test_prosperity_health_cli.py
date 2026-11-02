"""M12 CLI: synthetic prosperity.db + market.db → four HTML reports and summary.json."""
import hashlib
import json

import pytest

from src.data.prosperity_store import ProsperityStore
from tests.prosperity_fixtures import seed_db, seed_prices, storage_batch, weekdays

DAYS = weekdays('2027-07-30', 560)


def sha(p):
    return hashlib.sha256(p.read_bytes()).hexdigest()


@pytest.fixture()
def dbs(tmp_path):
    db = tmp_path / 'prosperity.db'
    with ProsperityStore(db, read_only=False) as store:
        store.save_batch(storage_batch(freeze=True, quarter_label='2026Q2'))
        store.save_batch(storage_batch(as_of='2026-10-03', freeze=True, quarter_label='2026Q3'))
    rows = {f'S{i:04d}': [(d, 10.0 + k * 0.01 * (i + 1), 1000) for k, d in enumerate(DAYS)] for i in range(12)}
    rows['SPY'] = [(d, 400.0 + k * 0.1, 10**6) for k, d in enumerate(DAYS)]
    market = seed_prices(seed_db(tmp_path / 'market.db'), rows)
    return db, market


def run(dbs, out, *extra):
    from scripts.prosperity_health import main
    db, market = dbs
    return main(['--db', str(db), '--market-db', str(market), '--out', str(out),
                 '--code-version', 'test-v1', *extra])


def test_writes_four_fixed_format_reports_and_keeps_databases_unchanged(dbs, tmp_path):
    from backtest.research.prosperity_health import CHAPTERS
    before = [sha(p) for p in dbs]
    out = tmp_path / 'out'
    assert run(dbs, out) == 0
    assert [sha(p) for p in dbs] == before
    summary = json.loads((out / 'summary.json').read_text())
    assert list(summary['schemes']) == ['F1', 'F1-rank', 'F1-exfin', 'F0']
    assert summary['bh_family'] == {'family': 24, 'estimated': 0}      # 2 periods < 5: no p values
    for scheme in summary['schemes']:
        html = (out / f'{scheme}.html').read_text()
        positions = [html.index(f'{k}. {title}') for k, title in enumerate(CHAPTERS, 1)]
        assert positions == sorted(positions)
        for note in ('不作上线门槛', '价格收益，不含分红', '置信区间宽'):
            assert note in html
        assert '<script src' not in html and '<link' not in html and 'url(' not in html
        assert len(html.encode()) <= 5 * 1024 * 1024
    assert summary['schemes']['F1']['header']['periods'] == 2


def test_input_and_argument_errors_write_nothing(dbs, tmp_path):
    out = tmp_path / 'out'
    assert run(dbs, out, '--schemes', 'F9') == 4
    from scripts.prosperity_health import main
    db, market = dbs
    assert main(['--db', str(db), '--market-db', str(market), '--out', str(out),
                 '--code-version', 'other']) == 3
    assert main(['--db', str(tmp_path / 'missing.db'), '--market-db', str(market), '--out', str(out)]) == 3
    assert not out.exists()


def test_scheme_subset_keeps_the_full_bh_family(dbs, tmp_path):
    """Review #4: --schemes F1 must not shrink the BH family; all four schemes are always tested."""
    out = tmp_path / 'one'
    assert run(dbs, out, '--schemes', 'F1') == 0
    summary = json.loads((out / 'summary.json').read_text())
    assert list(summary['schemes']) == ['F1'] and summary['bh_family']['family'] == 24
    assert (out / 'F1.html').exists() and not (out / 'F0.html').exists()


def test_input_hashes_are_taken_before_reading(dbs, tmp_path, monkeypatch):
    """Review #7: the recorded SHA256 must describe the data that was read, so hash first."""
    import scripts.prosperity_health as cli
    db, market = dbs
    before = sha(db)
    real = cli.PriceBook.load

    def load_then_change(*args, **kwargs):
        out = real(*args, **kwargs)
        with open(db, 'ab') as f:                        # the file changes after it was read
            f.write(b'\0' * 16)
        return out
    monkeypatch.setattr(cli.PriceBook, 'load', staticmethod(load_then_change))
    assert run(dbs, tmp_path / 'out') == 6
    assert not (tmp_path / 'out').exists()
    assert sha(db) != before


def test_period_status_is_shown_in_chinese(dbs, tmp_path):
    """Review #8: plan §8.7 labels unfinished periods 进行中, not raw status codes."""
    out = tmp_path / 'out'
    assert run(dbs, out) == 0
    html = (out / 'F1.html').read_text()
    assert 'in_progress' not in html and 'not_started' not in html and '>complete<' not in html
    assert '已满期' in html or '进行中' in html


def test_input_with_a_non_empty_wal_is_refused(dbs, tmp_path):
    """Second review #1: SQLite also reads committed WAL frames, which the main-file hash misses.
    The CLI only accepts self-contained snapshots (no WAL content), so the hash covers every byte read."""
    import sqlite3
    db, market = dbs
    conn = sqlite3.connect(market)
    conn.execute('PRAGMA journal_mode=WAL')
    conn.execute('PRAGMA wal_autocheckpoint=0')
    conn.execute("UPDATE daily_price SET close = close * 2 WHERE symbol = 'S0000'")
    conn.commit()                                   # committed, still only in market.db-wal
    assert (market.parent / (market.name + '-wal')).stat().st_size > 0
    out = tmp_path / 'out'
    assert run(dbs, out) == 4
    assert not out.exists()
    conn.close()


def test_missing_calendar_is_an_input_error(dbs, tmp_path):
    """Second review #2: without SPY there is no calendar; every window would read as not started."""
    import sqlite3
    db, market = dbs
    conn = sqlite3.connect(market)
    conn.execute("DELETE FROM daily_price WHERE symbol = 'SPY'")
    conn.commit()
    conn.close()
    out = tmp_path / 'out'
    assert run(dbs, out) == 3
    assert not out.exists()
