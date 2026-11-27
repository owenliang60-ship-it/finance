#!/usr/bin/env python3
"""End-to-end acceptance runner (A5) on real 2026-11-21 snapshot + 5 tamper negative cases."""
from __future__ import annotations

import copy
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from src.data.market_store import MarketStore  # noqa: E402
from src.data.prosperity_pool import _utc, build_pool, liquidity_top_days, publish_prosperity_pool  # noqa: E402
from src.data.prosperity_storage import BoardKey  # noqa: E402
from src.data.prosperity_store import ProsperityStore  # noqa: E402
from terminal.prosperity.schemes import SCHEMES, scheme_hash  # noqa: E402

HERE = Path(__file__).resolve().parent
VERIFIER = HERE / 'verify_pool_days.py'


def run_verifier(db: Path, mdb: Path, html: Path, pool: Path, cv: str, momentum: Path | None = None):
    cmd = [sys.executable, '-I', str(VERIFIER), '--db', str(db), '--market-db', str(mdb),
           '--html', str(html), '--pool', str(pool), '--code-version', cv]
    if momentum is not None:
        cmd += ['--momentum', str(momentum)]
    proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
    return proc.returncode, (proc.stdout + proc.stderr).strip()


def snapshot_db(src: Path, dst: Path) -> Path:
    s = sqlite3.connect(f'file:{src.resolve()}?mode=ro', uri=True)
    try:
        t = sqlite3.connect(str(dst))
        try:
            s.backup(t)
        finally:
            t.close()
    finally:
        s.close()
    c = sqlite3.connect(str(dst))
    try:
        c.execute('PRAGMA journal_mode=DELETE').fetchall()
    finally:
        c.close()
    for ext in ('-wal', '-shm'):
        p = Path(str(dst) + ext)
        if p.exists():
            p.unlink()
    return dst


def main() -> int:
    db_src = ROOT / 'data' / 'prosperity.db'
    mdb_src = ROOT / 'data' / 'market.db'
    as_of = '2026-11-21'
    cv = '13e5f9d5183f1b8e'

    with tempfile.TemporaryDirectory(prefix='pool-d90-accept-') as tmp:
        tdir = Path(tmp)
        db = snapshot_db(db_src, tdir / 'prosperity.db')
        pool_path = tdir / 'prosperity_pool.json'
        html_path = tdir / 'prosperity-web.html'
        mom_path = tdir / 'momentum-20261121.json'

        with ProsperityStore(db, read_only=True) as store:
            stored = store.get_board(BoardKey('weekly', as_of, scheme_hash(SCHEMES['F1']), cv))
        market = MarketStore(db_path=mdb_src, read_only=True)
        try:
            liq = liquidity_top_days(market, stored.meta.members, as_of)
        finally:
            market.close()
        now = datetime(2026, 11, 21, 9, 0, tzinfo=timezone.utc)
        payload = build_pool(stored, liq, generated_at=_utc(now))
        publish_prosperity_pool(payload, pool_path, now=now)

        subprocess.run([sys.executable, str(ROOT / 'scripts' / 'prosperity_web.py'),
                        '--db', str(db), '--out', str(html_path), '--pool', str(pool_path),
                        '--code-version', cv], check=True)
        subprocess.run([sys.executable, str(ROOT / 'scripts' / 'momentum_board.py'),
                        '--db', str(db), '--market-db', str(mdb_src),
                        '--code-version', cv, '--as-of', as_of, '--out', str(mom_path)], check=True)

        rc_pos, out_pos = run_verifier(db, mdb_src, html_path, pool_path, cv, mom_path)
        if rc_pos != 0 or '33 pool days, 33 pool list rows, 90 u70 symbols; differences: 0' not in out_pos:
            print('POSITIVE FAILED:', out_pos, file=sys.stderr)
            return 1

        symbols = [m['symbol'] for m in payload['members']]
        assert payload['coverage']['trading_window'] == ['2026-07-17', '2026-11-20'], payload['coverage']
        assert len(symbols) == 33 and 'HPE' in symbols and 'MDT' not in symbols and 'RTX' not in symbols, symbols

        # Five tamper negative cases — every single one MUST fail (rc == 1)
        negatives = {}

        # 1) Member top100_days - 1
        p1 = copy.deepcopy(payload)
        p1['members'][0]['top100_days'] = max(31, p1['members'][0]['top100_days'] - 1)
        if p1['members'][0]['top100_days'] == payload['members'][0]['top100_days']:
            p1['members'][0]['top100_days'] += 1
        f1 = tdir / 'tamper1.json'; f1.write_text(json.dumps(p1, ensure_ascii=False))
        negatives['member_days_tampered'] = run_verifier(db, mdb_src, html_path, f1, cv, mom_path)

        # 2) Drop one member (keeping selected self-consistent)
        p2 = copy.deepcopy(payload)
        p2['members'].pop()
        p2['coverage']['selected'] = len(p2['members'])
        f2 = tdir / 'tamper2.json'; f2.write_text(json.dumps(p2, ensure_ascii=False))
        negatives['member_dropped'] = run_verifier(db, mdb_src, html_path, f2, cv, mom_path)

        # 3) criteria.liquidity.min_top_days -> 30
        p3 = copy.deepcopy(payload)
        p3['criteria']['liquidity']['min_top_days'] = 30
        f3 = tdir / 'tamper3.json'; f3.write_text(json.dumps(p3, ensure_ascii=False))
        negatives['criteria_min_top_days_30'] = run_verifier(db, mdb_src, html_path, f3, cv, mom_path)

        # 4) HTML cell tampered (/90 -> 67/90 on first member)
        first_days = payload['members'][0]['top100_days']
        wrong_days = 67 if first_days != 67 else 66
        html_bad = tdir / 'tamper4.html'
        html_bad.write_text(html_path.read_text(encoding='utf-8').replace(f'{first_days}/90', f'{wrong_days}/90', 1),
                            encoding='utf-8')
        negatives['html_cell_tampered'] = run_verifier(db, mdb_src, html_bad, pool_path, cv, mom_path)

        # 5) Momentum JSON top100_days + 1
        mp = json.loads(mom_path.read_text(encoding='utf-8'))
        r0 = mp['boards']['14w']['scored'][0]
        r0['top100_days'] = r0['top100_days'] - 1 if r0['top100_days'] == 70 else r0['top100_days'] + 1
        mom_bad = tdir / 'tamper5.json'; mom_bad.write_text(json.dumps(mp, ensure_ascii=False))
        negatives['momentum_days_tampered'] = run_verifier(db, mdb_src, html_path, pool_path, cv, mom_bad)

        # Bonus 6) NaN in pool JSON (Blind Spot #13)
        f6 = tdir / 'tamper6_nan.json'
        f6.write_text(pool_path.read_text(encoding='utf-8').replace(str(payload['members'][0]['score']), 'NaN', 1),
                      encoding='utf-8')
        negatives['pool_nan_score'] = run_verifier(db, mdb_src, html_path, f6, cv, mom_path)

        # Bonus 7) Spoofed liquidity_daily.ratio (review P2)
        p7 = copy.deepcopy(payload)
        p7['coverage']['liquidity_daily']['ratio'] = 1.0
        f7 = tdir / 'tamper7_ratio.json'; f7.write_text(json.dumps(p7, ensure_ascii=False))
        negatives['coverage_ratio_spoofed'] = run_verifier(db, mdb_src, html_path, f7, cv, mom_path)

        for name, (rc, out) in negatives.items():
            if rc != 1:
                print(f'TAMPER {name} DID NOT FAIL: rc={rc} out={out}', file=sys.stderr)
                return 1

        report = {
            'as_of': as_of,
            'code_version': cv,
            'trading_window': payload['coverage']['trading_window'],
            'liquidity_daily': payload['coverage']['liquidity_daily'],
            'pool_selected': len(symbols),
            'pool_members': [{'symbol': m['symbol'], 'rank': m['rank'], 'grade': m['grade'],
                              'top100_days': m['top100_days']} for m in payload['members']],
            'positive_verifier_rc': rc_pos,
            'positive_verifier_output': out_pos,
            'tamper_negatives': {k: {'rc': v[0], 'output': v[1].splitlines()[-1] if v[1] else ''}
                                 for k, v in negatives.items()},
        }
        (HERE / 'acceptance.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        print('PASS:', out_pos)
        return 0


if __name__ == '__main__':
    sys.exit(main())
