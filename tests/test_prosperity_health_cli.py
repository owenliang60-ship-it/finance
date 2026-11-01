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
