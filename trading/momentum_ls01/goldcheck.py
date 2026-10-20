"""Explicit research parity checks; missing evidence is never a pass."""
import json
from pathlib import Path

import numpy as np
import pandas as pd

from .frozen import anchor_cases, load_evidence, sha256
from .kernel import LivePolicy, cost_after_trade, weekly_regimes


def check_targets(cases, lifetimes):
    compared = 0
    for c in cases:
        p = LivePolicy(pd.DataFrame(c['signals']),c['regime']['regime'],c['anchor'],
                       cooldown=c['cooldown'],lifetimes=lifetimes)
        got = p(c['anchor'],c['equity'],c['positions'],c['prices'])
        if set(got) != set(c['expected']):
            raise ValueError('GC2 names differ at '+c['anchor'])
        for s,q in got.items():
            if not np.isclose(q,c['expected'][s],rtol=1e-9,atol=1e-12):
                raise ValueError('GC2 units differ: '+s+' '+c['anchor'])
            compared += 1
    if not compared:
        raise ValueError('GC2 empty comparison')
    return dict(status='PASS',anchors=len(cases),positions=compared)


def load_weekly(paths):
    """Merge archived public native-weekly responses in supplied source order.

    Later pulls may replace the earlier pull's last (then open) candle. Source
    hashes and the complete Fisher comparison record the resulting evidence.
    """
    by_time = {}
    for path in paths:
        raw = json.loads(Path(path).read_text())
        rows = raw['rows'] if isinstance(raw,dict) else raw
        for r in rows:
            by_time[r[0]] = r
    rows = [by_time[t] for t in sorted(by_time)]
    return pd.DataFrame({'high':[float(r[2]) for r in rows], 'low':[float(r[3]) for r in rows]},
                        index=pd.to_datetime([r[0] for r in rows],unit='ms',utc=True))


def check_cooldown(e, cases):
    actual = []
    # Each stop is checked separately, including later stops of the same coin.
    for row in e['stops'].itertuples():
        t = row.first_touch_minute if pd.notna(row.first_touch_minute) else row.time
        anchor = t.normalize()+pd.Timedelta(days=(7-t.weekday())%7,hours=4)
        if anchor <= t:
            anchor += pd.Timedelta(days=7)
        c = cases[0]
        p = LivePolicy(pd.DataFrame(c['signals']),c['regime']['regime'],c['anchor'],cooldown={row.symbol:t})
        if p.can_enter(row.symbol,anchor-pd.Timedelta(days=7),set()):
            raise ValueError('GC4 premature cooldown release')
        if not p.can_enter(row.symbol,anchor,set()):
            raise ValueError('GC4 cooldown not released')
        entries = e['ledger'][(e['ledger'].symbol == row.symbol) & (e['ledger'].time > row.time)
                              & (e['ledger'].units_before == 0) & (e['ledger'].units_after != 0)]
        first = None if entries.empty else entries.iloc[0].time
        if first is not None and (first < anchor or first.weekday() != 0 or first.hour != 4):
            raise ValueError('GC4 frozen reentry violates boundary')
        actual.append(dict(symbol=row.symbol,stop=str(t),eligible_at=str(anchor),actual_entry=None if first is None else str(first)))
    if not actual:
        raise ValueError('GC4 missing stops')
    # GC2 compares every actual weekly selection, so this is not a forced-reentry test.
    return dict(status='PASS',stops=actual)


def check_budgets():
    rows = [dict(date='2026-10-18',horizon=14,symbol=f'{side}:{i:02}',status='ok',
                 slope=side,drawdown=.1,score_v4=100-i,**{'return':side*.1})
            for side in (-1,1) for i in range(12)]
    s = pd.DataFrame(rows)
    for regime,long_count in [(1,10),(-1,0)]:
        p=LivePolicy(s,regime,'2026-10-19 04:00Z')
        got=p(p.start,10000,{},dict.fromkeys(s.symbol,100))
        if sum(q<0 for q in got.values())!=10 or sum(q>0 for q in got.values())!=long_count or not all(q in (-10,5) for q in got.values()):
            raise ValueError('GC6 budget mismatch')
    p=LivePolicy(s.iloc[[0,12]],1,'2026-10-19 04:00Z')
    got=p(p.start,10000,{},dict.fromkeys(s.symbol,100))
    if sum(abs(q)*100 for q in got.values())!=1500:
        raise ValueError('GC6 empty slots redistributed')
    return dict(status='PASS',bull_short_slots=10,bull_long_slots=10,bear_short_slots=10,empty_slots='cash')


def run_goldcheck(frozen_dir, weekly_sources=()):
    e = load_evidence(frozen_dir)
    cases = anchor_cases(e)
    checks = {}
    if weekly_sources:
        weekly = load_weekly(weekly_sources)
        got = weekly_regimes(weekly,e['regimes'].index)
        expected = e['regimes']
        error = float(np.max(np.abs(got[['fisher','trigger']].to_numpy()-expected[['fisher','trigger']].to_numpy())))
        if error > 1e-9 or not np.array_equal(got.regime.to_numpy(),expected.regime.to_numpy()):
            raise ValueError('GC1 Fisher mismatch')
        checks['GC1'] = dict(status='PASS',rows=len(got),max_error=error,
                            sources={str(p):sha256(p) for p in weekly_sources})
    else:
        checks['GC1'] = dict(status='BLOCKED',reason='native weekly sources required')
    checks['GC2'] = check_targets(cases,e['lifetimes'])
    max_error = 0.
    for row in e['ledger'].itertuples():
        got = cost_after_trade(row.units_before,None if pd.isna(row.basis_before) else row.basis_before,row.delta_units,row.price)
        if pd.isna(row.basis_after):
            if got is not None:
                raise ValueError('GC3 basis not cleared')
        elif got is None or not np.isclose(float(got),row.basis_after,rtol=1e-12,atol=1e-12):
            raise ValueError('GC3 basis mismatch at sequence '+str(row.sequence))
        else:
            max_error = max(max_error,abs(float(got)-row.basis_after))
    checks['GC3'] = dict(status='PASS',rows=len(e['ledger']),max_absolute_error=max_error)
    checks['GC4'] = check_cooldown(e,cases)
    checks['GC5'] = dict(status='BLOCKED',reason='full raw market replay required; archived ranking rows alone are insufficient')
    checks['GC6'] = check_budgets()
    return dict(strategy_id='momentum-ls-01',rule_version='2026-10-19.v2',
                status='PASS' if all(c['status']=='PASS' for c in checks.values()) else 'BLOCKED',
                integrity={k:v for k,v in e['provenance'].items() if k!='sha256'},checks=checks)


def compare_reports(expected, actual):
    a,b = expected['periods']['14d'],actual['periods']['14d']
    if set(a['pool']) != set(b['pool']):
        raise ValueError('GC5 pool mismatch')
    ar,br = ({r['symbol']:r for r in p['rows']} for p in (a,b))
    if set(ar)!=set(br):
        raise ValueError('GC5 row coverage mismatch')
    for symbol,r in ar.items():
        q=br[symbol]
        if r['status']!=q['status']:
            raise ValueError('GC5 status mismatch: '+symbol)
        if r['status']=='ok' and not np.isclose(r['score'],q['score'],rtol=0,atol=1e-10):
            raise ValueError('GC5 score mismatch: '+symbol)
    if [r['symbol'] for r in a['ranked']] != [r['symbol'] for r in b['ranked']]:
        raise ValueError('GC5 ranking mismatch')
    expected_ranks=directional_rankings(a['rows'])
    if expected_ranks!=directional_rankings(b['rows']):
        raise ValueError('GC5 directional ranking mismatch')
    return dict(status='PASS',pool=len(ar),unavailable=[s for s,r in ar.items() if r['status']!='ok'],
                directional_rankings=expected_ranks)


def check_market_replay(replay_dir, reports_dir, weekly_sources):
    """Replay public inputs through the actual adapter. All HTTP is forbidden.

    replay_dir contains copied cloud trend_cache and sliced daily CSV evidence.
    Temporary cache writes never mutate either source directory.
    """
    import shutil
    import tempfile
    from unittest.mock import patch
    from scripts.crypto_trend_rankings import build_report
    from .binance import Endpoints, ExchangeError
    from .market_public import PublicMarket
    from .prepare import prepare

    root=Path(replay_dir).resolve()
    manifest=json.loads((root/'manifest.json').read_text())
    if not manifest.get('sha256'):
        raise ValueError('GC5 missing source manifest')
    hashes=dict(manifest['sha256'])
    supplement=root/'supplement_manifest.json'
    if supplement.is_file():
        extra=json.loads(supplement.read_text())['sha256']
        if set(extra)&set(hashes):raise ValueError('GC5 duplicate supplement paths')
        hashes.update(extra)
    for name,h in hashes.items():
        path=(root/name).resolve()
        if not path.is_relative_to(root) or not path.is_file() or sha256(path)!=h:
            raise ValueError('GC5 source hash mismatch: '+name)
    weekly=load_weekly(weekly_sources)
    checks=[]

    class ReplayClient:
        env='prod'
        def __init__(self,day):self.day=day
        def request(self,method,path,params=None,**kwargs):
            if method!='GET':raise ValueError('GC5 writes forbidden')
            if path==Endpoints.EXCHANGE_INFO:
                return json.loads((root/'trend_cache'/('catalog_'+self.day+'.json')).read_text())
            if path!=Endpoints.KLINES or params['interval']!='1d':
                raise ValueError('GC5 uncached request '+path)
            p=root/'daily'/(params['symbol']+'.csv')
            if p.is_file():return replay_daily(p,params)
            saved=root/'supplement'/(params['symbol']+'.json')
            if not saved.is_file() or str(saved.relative_to(root)) not in hashes:
                raise ValueError('GC5 missing daily evidence: '+params['symbol'])
            payload=json.loads(saved.read_text())
            if 'error_code' in payload:
                raise ExchangeError('REJECTED','recorded public response',code=payload['error_code'])
            return [r for r in payload['rows'] if params['startTime']<=r[0]<=params['endTime']][-params['limit']:]

    with tempfile.TemporaryDirectory(prefix='mls01-gc5-') as tmp:
        cache=Path(tmp)/'trend_cache';shutil.copytree(root/'trend_cache',cache)
        with patch('scripts.crypto_trend_market.requests.get',side_effect=ValueError('GC5 network forbidden; missing archive evidence')), patch('scripts.crypto_trend_market.time.sleep',lambda _:None):
            for day in ('2026-09-20','2026-09-27','2026-10-04','2026-10-11'):
                source=Path(reports_dir)/('crypto_trend_top10_'+day+'.json')
                expected=json.loads(source.read_text())
                as_of=pd.Timestamp(day,tz='UTC')
                market=PublicMarket(ReplayClient(day),cache,as_of)
                actual=build_report(market,as_of,periods=(14,))
                result=compare_reports(expected,actual)
                if result['unavailable']:
                    # Matching an old incomplete report never authorizes live preparation.
                    try:prepare(market,weekly,as_of+pd.Timedelta(days=1,hours=4))
                    except ValueError as exc:
                        if 'incomplete candidate signal pool' not in str(exc):raise
                    else:raise ValueError('GC5 incomplete inputs incorrectly permitted live preparation')
                    result['live_prepare']='BLOCKED_BY_D5'
                else:
                    ready=prepare(market,weekly,as_of+pd.Timedelta(days=1,hours=4))
                    if ready['short_ranked']!=result['directional_rankings']['short'] or ready['long_ranked']!=result['directional_rankings']['long']:
                        raise ValueError('GC5 prepared directional ranking mismatch')
                    result['live_prepare']='READY'
                    result['short_ranked']=ready['short_ranked'];result['long_ranked']=ready['long_ranked']
                result.update(day=day,report_sha256=sha256(source))
                checks.append(result)
    return dict(status='PASS',days=checks,source_manifest_sha256=sha256(root/'manifest.json'),
                supplement_manifest_sha256=sha256(supplement) if supplement.is_file() else None,
                note='Historical unavailable rows must match; they are not a live readiness pass.')


def replay_daily(path,params):
    frame=pd.read_csv(path)
    times=pd.to_datetime(frame.timestamp,utc=True).astype('datetime64[ns, UTC]')
    milliseconds=times.astype('int64')//1_000_000
    frame=frame[(milliseconds>=params['startTime']) & (milliseconds<=params['endTime'])].tail(params['limit'])
    rows=[]
    for r in frame.itertuples():
        ms=int(pd.Timestamp(r.timestamp).timestamp()*1000)
        rows.append([ms,str(r.open),str(r.high),str(r.low),str(r.close),str(r.volume),ms+86400000-1,str(r.quote_volume)])
    return rows


def directional_rankings(rows):
    """Independent rule expression for published evidence; do not call LivePolicy."""
    eligible=[r for r in rows if r['status']=='ok' and r['symbol']!='BTCUSDT']
    ranked=sorted(eligible,key=lambda r:(-r['score'],r['symbol']))
    return dict(short=[r['symbol'] for r in ranked if r['return']<0 and r['slope']<0],
                long=[r['symbol'] for r in ranked if r['return']>0 and r['slope']>0 and r['drawdown']<=.30])
