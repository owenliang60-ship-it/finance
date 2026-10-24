"""Exact five-row production proposal, exercised on a new local copy only."""
import json
from pathlib import Path
import shutil
import sqlite3
import sys
import time

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from src.data.market_store import MarketStore
from src.data.fiscal_repair import apply_reviewed_statement_quarantine
import scripts.backfill_index_pe_history as history

ARTIFACTS = Path(__file__).resolve().parent
DB = ARTIFACTS / 'recovery.db'
assert DB.resolve().parent == ARTIFACTS and not DB.exists()
shutil.copyfile(ARTIFACTS / 'baseline.db', DB)
store = MarketStore(DB)
plan = json.loads((ROOT / 'docs/audit/cron-recovery-20261024/quarantine-plan.json').read_text())
before = [dict(r) for r in store._get_conn().execute("SELECT * FROM fundamental_vintage WHERE symbol='CRH' ORDER BY statement,fiscal_date,observed_at")]
result = apply_reviewed_statement_quarantine(store, plan)
assert before == [dict(r) for r in store._get_conn().execute("SELECT * FROM fundamental_vintage WHERE symbol='CRH' ORDER BY statement,fiscal_date,observed_at")]
(ARTIFACTS / 'recovery-repair.json').write_text(json.dumps(result, indent=2))
print('FIVE_ROW_REPAIR', result, flush=True)

for name in ('_run_fundamentals', '_run_mcap', '_run_splits', '_run_sanity', '_run_fx', '_compute_weekly_rows'):
    original = getattr(history, name)
    def traced(*args, _name=name, _fn=original, **kwargs):
        print('STAGE', _name, flush=True)
        return _fn(*args, **kwargs)
    setattr(history, name, traced)
started = time.monotonic()
args = history.parse_args(['--baskets', 'SPY', '--as-of', '2026-10-24', '--years', '5',
                           '--db', str(DB), '--run-id', 'index-pe-recovery-trial-20261024-spy'])
report = history.run_backfill(args, client=None, store=store, conn=store._get_conn())
(ARTIFACTS / 'recovery-spy-history.json').write_text(json.dumps(report, indent=2, default=str))
print('RESULT', json.dumps({'failed':report['failed_baskets'],
    'baskets':{b:{k:v.get(k) for k in ['status','error','weekly_rows']} for b,v in report['baskets'].items()},
    'elapsed':time.monotonic()-started}), flush=True)
store.close()
raise SystemExit(bool(report['failed_baskets']))
