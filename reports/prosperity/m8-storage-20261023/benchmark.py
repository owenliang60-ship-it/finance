"""Reproducible, synthetic M8 size/timing acceptance; temporary databases only."""
import json
import sqlite3
import sys
import tempfile
import time
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from src.data.prosperity_store import ProsperityStore
from tests.prosperity_fixtures import storage_batch


def main():
    weeks = []
    with tempfile.TemporaryDirectory(prefix='prosperity-m8-acceptance-') as tmp:
        path = Path(tmp) / 'research.db'
        all_keys = []
        with ProsperityStore(path, read_only=False) as store:
            for week in range(8):
                as_of = (date(2026, 9, 26) + timedelta(days=7*week)).isoformat()
                start = time.perf_counter()
                batch = storage_batch(as_of=as_of, count=1000)
                built = time.perf_counter()
                keys = store.save_batch(batch)
                written = time.perf_counter()
                assert tuple(store.get_board(k) for k in keys) == batch
                read = time.perf_counter()
                weeks.append({'week': week+1, 'fixture_seconds': built-start,
                              'write_seconds': written-built, 'read_seconds': read-written})
                all_keys.extend(keys)
                print(json.dumps(weeks[-1]), flush=True)
            wal = Path(str(path)+'-wal')
            wal_bytes = wal.stat().st_size if wal.exists() else 0
            store._conn.execute('PRAGMA wal_checkpoint(TRUNCATE)')
        start = time.perf_counter()
        with ProsperityStore(path) as store:
            assert all(store.get_board(k) is not None for k in all_keys)
        reopen_seconds = time.perf_counter()-start
        with sqlite3.connect(path) as conn:
            tables = ('board', 'input_set', 'stock_input', 'stock_period', 'factor_value', 'score', 'scheme', 'params', 'publication')
            counts = {t: conn.execute('SELECT count(*) FROM '+t).fetchone()[0] for t in tables}
            size = conn.execute('PRAGMA page_count').fetchone()[0] * conn.execute('PRAGMA page_size').fetchone()[0]
            assert conn.execute('PRAGMA quick_check').fetchone()[0] == 'ok'
            assert conn.execute('PRAGMA foreign_key_check').fetchall() == []
        assert counts['board'] == 32 and counts['factor_value'] == 320000
        output = {'synthetic_only': True, 'stocks': 1000, 'schemes': 4, 'weeks': 8,
                  'rows': counts, 'checkpoint_bytes': size, 'wal_bytes_before_checkpoint': wal_bytes,
                  'annual_52_week_plus_4_freeze_bytes_linear_estimate': size * 56 / 8,
                  'all_boards_read_after_reopen_seconds': reopen_seconds, 'timings': weeks}
        Path(__file__).with_name('benchmark.json').write_text(json.dumps(output, indent=2)+'\n')
        print(json.dumps({k:v for k,v in output.items() if k != 'timings'}, indent=2))


if __name__ == '__main__':
    main()
