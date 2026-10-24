"""Run real M9 acceptance against an explicit, fixed read-only input snapshot."""
import argparse
from dataclasses import asdict
import hashlib
import json
from pathlib import Path
import resource
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from src.data.market_store import MarketStore
from src.data.prosperity_run import rebuild_freeze_chain, run_week, this_saturday, utcnow
from src.data.prosperity_store import ProsperityStore
from src.data.prosperity_storage import normalize_board
from terminal.prosperity.inputs import build_packets
from terminal.prosperity.version import code_version


def sha(path):
    h = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024*1024), b''):
            h.update(block)
    return h.hexdigest()


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--market-db', type=Path, required=True)
    p.add_argument('--db', type=Path, required=True)
    p.add_argument('--out', type=Path, required=True)
    p.add_argument('--live', action='store_true')
    p.add_argument('--observed-at')
    p.add_argument('--quality-dir', type=Path)
    p.add_argument('--start-quarter', default='2021Q2')
    a = p.parse_args()
    source_hash, version = sha(a.market_db), code_version()
    started, saves = time.monotonic(), []
    def build(*args, **kw):
        before = time.monotonic()
        result = build_packets(*args, **kw)
        print(json.dumps({'as_of': args[1], 'members': result[1]['members_resolved'],
                          'errors': len(result[1]['errors']), 'build_seconds': round(time.monotonic()-before, 2)}), flush=True)
        return result
    market = MarketStore(a.market_db, read_only=True)
    try:
        with ProsperityStore(a.db, read_only=False) as store:
            original = store.save_batch
            def verified_save(boards):
                expected = tuple(normalize_board(b) for b in boards)
                keys = original(boards)
                actual = tuple(store.get_board(k) for k in keys)
                if actual != expected:
                    raise AssertionError('real roundtrip mismatch')
                saves.append({'as_of': keys[0].as_of, 'boards': len(keys), 'round_trip_equal': True})
                print(json.dumps(saves[-1]), flush=True)
                return keys
            store.save_batch = verified_save
            common = dict(market=market, store=store, version=version, packet_builder=build,
                          start_quarter=a.start_quarter,
                          git_sha=subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip())
            if a.live:
                out = run_week(saturday=this_saturday(utcnow()).isoformat(), mode='live',
                    quality_dir=a.quality_dir, observed_at=a.observed_at, snapshot_sha256=source_hash, **common)
            else:
                out = rebuild_freeze_chain(through='2026-10-17', **common)
            quarters = [dict(r) for r in store._conn.execute("SELECT quarter_label,as_of,frozen_at,counts_json "
                "FROM board WHERE board_type='quarter_final' AND scheme_id='F1' AND code_version=? ORDER BY as_of", (version,))]
    finally:
        market.close()
    unchanged = source_hash == sha(a.market_db)
    result = {'outcome': asdict(out), 'source': str(a.market_db), 'source_sha256': source_hash,
              'source_unchanged': unchanged, 'code_version': version, 'db': str(a.db),
              'seconds': round(time.monotonic()-started, 2), 'db_bytes': a.db.stat().st_size,
              'peak_rss_bytes': resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * (1 if sys.platform == 'darwin' else 1024),
              'saves': saves, 'quarters': quarters}
    a.out.write_text(json.dumps(result, indent=2, ensure_ascii=False))
    print(json.dumps({k: result[k] for k in ('code_version', 'seconds', 'db_bytes', 'peak_rss_bytes', 'source_unchanged')}), flush=True)
    return out.exit_code if unchanged else 9


if __name__ == '__main__':
    raise SystemExit(main())
