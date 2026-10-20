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
