"""Quality repairs must be bounded and verified by the same auditor afterwards."""
import importlib
import json
from datetime import datetime, timedelta, timezone

import pytest


@pytest.fixture
def module():
    return importlib.import_module('scripts.check_fundamental_quality')


class Lock:
    def __init__(self, busy=False):
        self.busy = busy
        self.released = False
    def acquire(self):
        return not self.busy
    def release(self):
        self.released = True


def audit(targets=(), verification=(), issues=None):
    symbols = sorted(set(targets) | set(verification) | set(issues or {}))
    return {'schema_version': 1, 'status': 'FAIL' if targets else 'WARN',
            'repair_targets': list(targets), 'verification_targets': list(verification),
            'deferred': [], 'universe': {'count': len(symbols), 'symbols': symbols},
            'symbols': {s: {'issues': (issues or {}).get(s, []), 'fundamental_ready': True} for s in symbols}}


def test_read_only_has_zero_calls(module, monkeypatch):
    monkeypatch.setattr(module, 'collect_fundamentals_for_symbol', lambda *a, **k: pytest.fail('API'))
    report = module.run_quality(object(), auditor=lambda *a, **k: audit(['A']), repair=False)
    assert report['exit_code'] == 1
    assert report['mode'] == 'audit'


def test_lock_busy_before_audit_or_calls(module):
    report = module.run_quality(object(), repair=True, lock=Lock(True),
                               auditor=lambda *a, **k: pytest.fail('audited'))
    assert report['exit_code'] == 75


def test_requests_success_with_unchanged_stale_data_is_not_resolved(module, monkeypatch):
    before = audit(['A'], issues={'A': ['fiscal_stale']})
    after = audit(issues={'A': ['fiscal_stale']})
    after['deferred'] = ['A']
    reports = iter([before, after])
    monkeypatch.setattr(module, 'collect_fundamentals_for_symbol', lambda *a, **k: dict.fromkeys(['income','balance','cashflow','profile','ratios'], 'ok'))
    monkeypatch.setattr(module, 'compute_all_metrics', lambda *a, **k: {'A': 8})
    lock = Lock()
    report = module.run_quality(object(), client=object(), repair=True, lock=lock, auditor=lambda *a, **k: next(reports))
    assert report['requests_successful'] == ['A']
    assert report['truly_resolved'] == []
    assert report['unresolved'] == ['A']
    assert report['exit_code'] == 0
    assert report['status'] == 'WARN'
    assert lock.released


def test_cap_repairs_first_then_oldest_proactive_and_reaudits(module, monkeypatch):
    reports = iter([audit(['A'], ['B','C'], {'A':['metrics_not_current']}), audit(issues={'A':[], 'B':[], 'C':[]})])
    called=[]
    def collect(symbol, **kwargs):
        called.append(symbol)
        assert 'T' in kwargs['observed_at']
        return dict.fromkeys(['income','balance','cashflow','profile','ratios'], 'ok')
    monkeypatch.setattr(module, 'collect_fundamentals_for_symbol', collect)
    monkeypatch.setattr(module, 'compute_all_metrics', lambda *a, **k: {'A':8,'B':8})
    report=module.run_quality(object(), client=object(), repair=True, lock=Lock(), max_targets=2,
                              auditor=lambda *a, **k: next(reports))
    assert called == ['A','B']
    assert report['truly_resolved'] == ['A']
    assert report['periodic_verified'] == ['B']
    assert report['deferred_by_budget'] == ['C']
    assert report['exit_code'] == 0


def test_collection_failure_does_not_compute_partial_data(module, monkeypatch):
    monkeypatch.setattr(module, 'collect_fundamentals_for_symbol', lambda *a, **k: {'income':'ok','balance':'fetch_failed','cashflow':'ok'})
    monkeypatch.setattr(module, 'compute_all_metrics', lambda *a, **k: pytest.fail('computed'))
    report=module.run_quality(object(), client=object(), repair=True, lock=Lock(),
                              auditor=lambda *a, **k: audit(['A'], issues={'A':['collection_failed']}))
    assert report['exit_code'] == 1
    assert report['errors'][0]['symbol'] == 'A'


def test_metric_error_is_explicit(module, monkeypatch):
    monkeypatch.setattr(module, 'collect_fundamentals_for_symbol', lambda *a, **k: dict.fromkeys(['income','balance','cashflow','profile','ratios'],'ok'))
    def compute(*a, **kwargs):
        kwargs['collect_failures'].append('A')
        return {}
    monkeypatch.setattr(module, 'compute_all_metrics', compute)
    report=module.run_quality(object(), client=object(), repair=True, lock=Lock(), auditor=lambda *a, **k: audit(['A']))
    assert report['exit_code'] == 1
    assert any(e['stage']=='metrics' for e in report['errors'])


def test_atomic_report_preserves_previous_on_failure(module, tmp_path, monkeypatch):
    p=tmp_path/'report.json'; p.write_text('{"old":true}')
    monkeypatch.setattr(module.os, 'replace', lambda *a: (_ for _ in ()).throw(OSError('injected')))
    with pytest.raises(OSError): module.write_report({'status':'FAIL'}, p)
    assert json.loads(p.read_text()) == {'old':True}
    assert list(tmp_path.glob('.*.tmp')) == []


def test_cli_historical_repair_rejected_before_writes(module, monkeypatch):
    monkeypatch.setattr(module, 'MarketStore', lambda **k: pytest.fail('store opened'))
    yesterday=(datetime.now(timezone.utc)-timedelta(days=1)).date().isoformat()
    with pytest.raises(SystemExit): module.main(['--repair','--as-of',yesterday])


def test_real_sqlite_collector_metrics_and_reaudit_resolve_stale_aligned_rows(module, tmp_path, monkeypatch):
    from src.data.market_store import MarketStore
    import src.data.fundamental_quality as fq
    store=MarketStore(tmp_path/'market.db')
    monkeypatch.setattr(fq, 'current_base_universe', lambda store: ['A'])
    now=datetime.now(timezone.utc)
    def rows_ending(age):
        return [{'date':(now-timedelta(days=age+91*i)).date().isoformat(),
                 'period':'Q'+str(4-i%4), 'fiscalYear':str(2026-i//4),
                 'epsDiluted':2-i*.1,'revenue':200-i*10,'netIncome':50-i*2}
                for i in range(7)]
    old=rows_ending(45)[2:]
    for fn in [store.upsert_income,store.upsert_balance_sheet,store.upsert_cash_flow]: fn('A',old)
    store.upsert_metrics('A',[{'date':old[0]['date']}])
    store.upsert_coverage_status([{'symbol':'A','dataset':t,'status':'ok'} for t in
        ['income_quarterly','balance_sheet_quarterly','cash_flow_quarterly']])
    stamp=(now-timedelta(days=45)).isoformat()
    store._get_conn().execute('UPDATE coverage_status SET last_attempt_at=?,last_success_at=?',(stamp,stamp))
    store._get_conn().commit()
    fresh=rows_ending(45)
    class Client:
        calls=0
        def get_dataset_with_status(self, kind, symbol, **kwargs):
            self.calls+=1
            if kind=='profile': return [{'symbol':'A','companyName':'A'}], 'ok'
            return fresh, 'ok'
    client=Client()
    before=fq.audit_fundamentals(store,as_of=now.isoformat())
    assert before['repair_targets']==['A']
    result=module.run_quality(store,repair=True,client=client,lock=Lock())
    assert result['truly_resolved']==['A']
    assert result['after']['repair_targets']==[]
    assert result['exit_code']==0
    assert client.calls==5
    assert store.get_metrics('A',limit=1)[0]['date']==fresh[0]['date']
    assert store._get_conn().execute('SELECT COUNT(*) FROM fundamental_vintage').fetchone()[0]>0
    second=module.run_quality(store,repair=True,client=client,lock=Lock())
    assert second['requested']==[]
    assert client.calls==5
    store.close()
