"""Approved one-time retirement deployment; no market-data HTTP or data edits."""
import hashlib
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
from datetime import datetime, timezone

ROOT = Path('/root/workspace/Finance')
BASE = '9f229c7192cf94e5dbd9da48bab7b077b2e6f738'
TARGET = '1a3cd3228590a4a2b5b669fab6f2b1a960409711'
JOB = ROOT / 'data/index-valuation-archive-20261024'
DB = ROOT / 'data/market.db'
TABLES = ('basket_weekly_pe_history', 'basket_pe_backfill_runs', 'fmp_basket_valuation')
sys.path.insert(0, str(ROOT))
from scripts.backfill_extended_fundamentals import FileLock
from scripts.build_company_concept_registry import _backup_sqlite


def git(*args):
    return subprocess.check_output(['git', *args], cwd=ROOT, text=True).strip()


def save(name, value):
    path = JOB / name
    temp = path.with_suffix('.tmp')
    temp.write_text(json.dumps(value, indent=2) + '\n')
    temp.replace(path)


def fingerprint():
    conn = sqlite3.connect(DB.as_uri() + '?mode=ro', uri=True)
    conn.row_factory = sqlite3.Row
    out = {}
    try:
        for table in TABLES:
            digest, count = hashlib.sha256(), 0
            for row in conn.execute('SELECT * FROM ' + table + ' ORDER BY rowid'):
                digest.update(json.dumps(dict(row), sort_keys=True, separators=(',', ':')).encode())
                digest.update(b'\n')
                count += 1
            out[table] = {'rows':count, 'sha256':digest.hexdigest()}
    finally:
        conn.close()
    return out


def main():
    lock = FileLock()
    if not lock.acquire():
        print('BLOCKED: market_db_writer busy; no deployment or backup performed')
        return 75
    result = {'status':'running', 'base':BASE, 'target':TARGET,
              'started_at':datetime.now(timezone.utc).isoformat(), 'market_data_http_requests':0}
    created = False
    try:
        if JOB.exists():
            raise RuntimeError('archive job already exists; inspect its receipt before retrying')
        if git('rev-parse','HEAD') != BASE or git('status','--porcelain','--untracked-files=no'):
            raise RuntimeError('production checkout differs from approved baseline')
        if git('rev-parse','origin/main') != TARGET:
            raise RuntimeError('origin/main does not match approved retirement commit')
        subprocess.run(['git','merge-base','--is-ancestor',BASE,TARGET],cwd=ROOT,check=True)
        JOB.mkdir()
        created = True
        result['phase'] = 'backup'
        before = fingerprint()
        cron = subprocess.check_output(['crontab','-l'], text=True)
        backup = _backup_sqlite(DB, label='pre-index-valuation-archive')
        if backup is None:
            raise RuntimeError('consistent backup was not created')
        digest = hashlib.sha256()
        with backup.open('rb') as stream:
            for block in iter(lambda:stream.read(1 << 20), b''):
                digest.update(block)
        result['backup'] = {'path':str(backup), 'bytes':backup.stat().st_size,
                            'sha256':digest.hexdigest(), 'quick_check':'ok'}
        result['before'] = before
        save('receipt.json', result)
        result['phase'] = 'deploy'
        print('BACKUP_VERIFIED', result['backup'], flush=True)
        subprocess.run(['git','merge','--ff-only',TARGET],cwd=ROOT,check=True)
        if git('rev-parse','HEAD') != TARGET:
            raise RuntimeError('deployment revision mismatch')
        result['phase'] = 'verify'
        with (JOB/'smoke.log').open('w') as log:
            subprocess.run([sys.executable,'-m','pytest',
                'tests/test_index_valuation_retirement.py',
                'tests/test_forward_cron_entrypoint.py',
                'tests/test_morning_html_report.py','-q','--tb=short'],
                cwd=ROOT, stdout=log, stderr=subprocess.STDOUT, check=True)
        result['after'] = fingerprint()
        if result['after'] != before:
            raise RuntimeError('historical valuation data changed during retirement')
        if subprocess.check_output(['crontab','-l'], text=True) != cron:
            raise RuntimeError('crontab changed during retirement')
        result.update(status='complete', phase='complete', production_head=git('rev-parse','HEAD'),
                      historical_data_unchanged=True, crontab_unchanged=True)
        return 0
    except Exception as exc:
        result.update(status='failed', error=str(exc), production_head=git('rev-parse','HEAD'))
        return 1
    finally:
        result['finished_at'] = datetime.now(timezone.utc).isoformat()
        try:
            if created:
                save('receipt.json',result)
            print(json.dumps(result,indent=2),flush=True)
        finally:
            lock.release()


if __name__ == '__main__':
    raise SystemExit(main())
