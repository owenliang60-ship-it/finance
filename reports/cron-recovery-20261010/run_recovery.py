"""One approved recovery; wraps production kernels without changing their rules."""
import json
import gzip
import hashlib
import os
from pathlib import Path
import shutil
import sys
import threading
import time
from datetime import datetime, timezone

ROOT = Path('/root/workspace/Finance')
sys.path.insert(0, str(ROOT))
from scripts.backfill_extended_fundamentals import FileLock
import scripts.backfill_index_pe_history as history
from scripts.verify_index_pe_history import connect_readonly, verify_database
from scripts.update_fmp_forward import parse_args as valuation_args, run_valuation_phase
from scripts.verify_fmp_forward import verify_run
from src.data.fmp_client import FMPClient
from src.data.market_store import MarketStore
from src.data.fiscal_repair import apply_reviewed_identity_updates, _recompute_metrics_in_conn
from src.data.fundamental_collector import collect_fundamentals_for_symbol
from scripts.check_fundamental_quality import run_quality

DAY = '2026-10-10'
ATTEMPT = int(os.environ.get('RECOVERY_ATTEMPT', '1'))
BASE_JOB = ROOT / 'data/cron-recovery-20261010'
JOB = BASE_JOB / ('attempt-' + str(ATTEMPT))
DB = ROOT / 'data/market.db'
LIMIT = 1000
STATE = {'stage': 'starting', 'basket': None}


def write_json(path, value):
    temporary = path.with_suffix(path.suffix + '.tmp')
    with temporary.open('w') as stream:
        json.dump(value, stream, indent=2, default=str)
        stream.flush()
        os.fsync(stream.fileno())
    temporary.replace(path)


class RecoveryClient(FMPClient):
    def __init__(self, **kwargs):
        budget = json.loads((BASE_JOB/'budget.json').read_text()) if (BASE_JOB/'budget.json').exists() else None
        if ATTEMPT > 1 and budget is None:
            raise RuntimeError('Prior durable request ledger is missing')
        if budget is not None and (budget['limit'] != LIMIT or not 0 <= budget['reserved_requests'] <= LIMIT):
            raise RuntimeError('Invalid durable request ledger')
        used = budget['reserved_requests'] if budget else 0
        self._initializing = True
        super().__init__(**kwargs)
        self._initializing = False
        self.request_count = used

    # FMP increments this property immediately before every HTTP attempt.
    # Persist the reservation before the request, so a crash cannot hide use.
    @property
    def request_count(self):
        return self._reserved

    @request_count.setter
    def request_count(self, value):
        if not self._initializing:
            if shutil.disk_usage(ROOT/'data').free < 250_000_000:
                raise RuntimeError('Disk reserve breached before next HTTP request')
            write_json(BASE_JOB/'budget.json', {'reserved_requests': value, 'limit': LIMIT})
        self._reserved = value


def verify_history(baskets, name):
    conn = connect_readonly(DB)
    try:
        conn.execute('BEGIN')
        result = verify_database(conn, baskets, DAY, 5, sample=50)
        write_json(JOB/name, result)
        return result['passed']
    finally:
        conn.close()


def file_hash(path, compressed=False):
    digest = hashlib.sha256()
    opener = gzip.open if compressed else open
    with opener(path, 'rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def vintage_fingerprint(conn):
    rows = [dict(row) for row in conn.execute(
        "SELECT * FROM fundamental_vintage WHERE symbol IN ('AEM','CRH') ORDER BY symbol,statement,fiscal_date,observed_at")]
    return {'rows': len(rows), 'sha256': hashlib.sha256(
        json.dumps(rows, sort_keys=True, separators=(',', ':')).encode()).hexdigest()}


def main():
    BASE_JOB.mkdir(exist_ok=True)
    JOB.mkdir(exist_ok=False)
    lock = FileLock()
    if not lock.acquire():
        write_json(JOB/'result.json', {'status': 'busy', 'exit_code': 75})
        return 75
    client, store = None, None
    result = {'status': 'running', 'as_of': DAY, 'request_limit': LIMIT}
    stop = threading.Event()
    started = time.monotonic()
    def progress():
        while not stop.is_set():
            write_json(JOB/'status.json', {
                **STATE, 'reserved_requests': client.request_count if client else 0,
                'request_limit': LIMIT, 'elapsed_seconds': round(time.monotonic()-started),
                'free_bytes': shutil.disk_usage(ROOT/'data').free,
                'at': datetime.now(timezone.utc).isoformat(),
            })
            stop.wait(30)
    thread = threading.Thread(target=progress, daemon=True)
    thread.start()
    try:
        for name in ('_fetch_sources', '_run_fundamentals', '_run_mcap', '_run_splits',
                     '_run_sanity', '_run_fx', '_compute_weekly_rows'):
            original = getattr(history, name)
            def traced(*args, _name=name, _original=original, **kwargs):
                STATE['stage'] = _name.removeprefix('_')
                return _original(*args, **kwargs)
            setattr(history, name, traced)
        required_space = DB.stat().st_size + 600_000_000 if ATTEMPT == 1 else 400_000_000
        if shutil.disk_usage(ROOT/'data').free < required_space:
            raise RuntimeError('Insufficient free space for consistent backup and recovery')
        STATE['stage'] = 'backup'
        if ATTEMPT == 1:
            backup, store = history.open_write_dependencies(DB, label='cron-recovery-20261010')
            checkpoint = {'backup_original': str(backup)}
            write_json(BASE_JOB/'checkpoint.json', checkpoint)
            expected = file_hash(backup)
            compressed = backup.with_suffix(backup.suffix + '.gz')
            with open(backup, 'rb') as source, gzip.open(compressed, 'wb', compresslevel=6) as target:
                for chunk in iter(lambda: source.read(1024 * 1024), b''):
                    if shutil.disk_usage(ROOT/'data').free < 150_000_000:
                        raise RuntimeError('Disk reserve breached while archiving backup')
                    target.write(chunk)
            if file_hash(compressed, compressed=True) != expected:
                raise RuntimeError('Backup round-trip checksum mismatch')
            receipt = {'archive': str(compressed), 'uncompressed_sha256': expected,
                       'bytes': backup.stat().st_size, 'archive_bytes': compressed.stat().st_size,
                       'roundtrip': 'PASS'}
            write_json(BASE_JOB/'backup-receipt.json', receipt)
            checkpoint.update(backup=str(compressed), backup_sha256=expected)
            write_json(BASE_JOB/'checkpoint.json', checkpoint)
            backup.unlink()  # This task's byte-verified recoverable archive remains.
        else:
            checkpoint = json.loads((BASE_JOB/'checkpoint.json').read_text())
            backup = Path(checkpoint['backup'])
            if not backup.is_file() or file_hash(backup, compressed=True) != checkpoint['backup_sha256']:
                raise RuntimeError('Original recovery backup unavailable')
            store = MarketStore(DB)
        result['backup'] = checkpoint['backup']
        write_json(JOB/'checkpoint.json', result)
        client = RecoveryClient(max_requests=LIMIT)
        if not checkpoint.get('identity_completed'):
            STATE['stage'] = 'reviewed_identity_repair'
            before = vintage_fingerprint(store._get_conn())
            plan = json.loads((ROOT/'docs/audit/cron-recovery-20261010/identity-plan.json').read_text())
            repaired = apply_reviewed_identity_updates(store, plan)
            after = vintage_fingerprint(store._get_conn())
            if before != after or repaired['rows_updated'] != 11:
                raise RuntimeError('Identity repair invariant failed')
            checkpoint['identity_completed'] = {'result': repaired, 'vintage_before': before, 'vintage_after': after}
            write_json(BASE_JOB/'checkpoint.json', checkpoint)
        if not checkpoint.get('statements_completed'):
            STATE['stage'] = 'targeted_statements'
            client.max_requests = 12
            statuses = {}
            for symbol, quarters in [('CRH', 40), ('AEM', 8)]:
                statuses[symbol] = collect_fundamentals_for_symbol(
                    symbol, client=client, store=store, limit_quarters=quarters,
                    observed_at=datetime.now(timezone.utc).isoformat(),
                    dataset_keys=['income', 'balance', 'cashflow'])
                if set(statuses[symbol].values()) != {'ok'}:
                    write_json(JOB/'targeted-statements.json', statuses)
                    raise RuntimeError('Targeted statements failed for ' + symbol)
                with store.transaction() as conn:
                    _recompute_metrics_in_conn(store, conn, symbol, 'cron-recovery-20261010')
            checkpoint['statements_completed'] = statuses
            write_json(BASE_JOB/'checkpoint.json', checkpoint)
        client.max_requests = LIMIT
        for basket in ('SPY', 'QQQ'):
            STATE['basket'] = basket
            if ATTEMPT > 1 and checkpoint.get(basket + '_completed'):
                STATE['stage'] = 'recertify_completed_history'
                if verify_history([basket], basket.lower()+'-existing.json'):
                    continue
            args = history.parse_args(['--baskets', basket, '--as-of', DAY,
                '--years', '5', '--db', str(DB), '--max-api-requests', str(LIMIT),
                '--run-id', 'index-pe-recovery-20261010-' + str(ATTEMPT) + '-' + basket.lower()])
            report = history.run_backfill(args, client=client, store=store, conn=store._get_conn())
            report['api_requests_used_total'] = client.request_count
            write_json(JOB/(basket.lower()+'-history.json'), report)
            if report['failed_baskets']:
                raise RuntimeError(basket + ' history failed; see per-basket report')
            checkpoint[basket + '_completed'] = True
            write_json(BASE_JOB/'checkpoint.json', checkpoint)
        STATE.update(basket='SOXX', stage='certify_existing_soxx')
        if not verify_history(['SOXX'], 'soxx-before.json'):
            args = history.parse_args(['--baskets', 'SOXX', '--as-of', DAY,
                '--years', '5', '--db', str(DB), '--max-api-requests', str(LIMIT),
                '--run-id', 'index-pe-recovery-20261010-' + str(ATTEMPT) + '-soxx'])
            report = history.run_backfill(args, client=client, store=store, conn=store._get_conn())
            report['api_requests_used_total'] = client.request_count
            write_json(JOB/'soxx-history.json', report)
            if report['failed_baskets']:
                raise RuntimeError('SOXX recertification failed')
        STATE.update(basket=None, stage='certify_all_history')
        if not verify_history(['SPY','QQQ','SOXX'], 'history-verifier.json'):
            raise RuntimeError('Three-basket history certification failed')
        STATE['stage'] = 'freeze_pit'
        args = valuation_args(['--mode','weekly','--phase','valuation','--snapshot-date',DAY,
                               '--data-root',str(ROOT/'data')])
        rc, report = run_valuation_phase(args)
        write_json(JOB/'pit-valuation.json', report)
        if rc:
            raise RuntimeError('PIT valuation failed')
        STATE['stage'] = 'certify_pit'
        rc, report = verify_run(DB, ROOT/'data', DAY, stage='full')
        write_json(JOB/'pit-verifier.json', report)
        if rc:
            raise RuntimeError('PIT verifier failed')
        if not verify_history(['SPY','QQQ','SOXX'], 'history-final.json'):
            raise RuntimeError('Final historical verifier failed')
        STATE['stage'] = 'fundamental_audit'
        quality = run_quality(store)
        write_json(JOB/'fundamental-quality.json', quality)
        if quality['exit_code']:
            raise RuntimeError('Final fundamental audit failed')
        check = store._get_conn().execute('PRAGMA quick_check').fetchone()[0]
        if check != 'ok':
            raise RuntimeError('SQLite quick_check failed')
        result.update(status='complete', exit_code=0, quick_check=check)
        return 0
    except Exception as exc:
        result.update(status='failed', exit_code=1, error=str(exc))
        return 1
    finally:
        result['api_requests_used'] = client.request_count if client else 0
        result['elapsed_seconds'] = round(time.monotonic()-started)
        STATE['stage'] = result['status']
        stop.set()
        thread.join()
        write_json(JOB/'result.json', result)
        print('RECOVERY_RESULT', json.dumps(result), flush=True)
        if store is not None:
            store.close()
        lock.release()


if __name__ == '__main__':
    raise SystemExit(main())
