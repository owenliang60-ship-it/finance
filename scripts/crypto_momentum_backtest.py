"""Canonical OOS crypto momentum backtest, sharing live scoring rules.

Wraps the proven fixed-unit ledger and holding policy; never acquires data,
reads funding, or modifies the frozen input directory.
"""
import argparse
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
import numpy as np
import pandas as pd
from backtest.crypto_alpha import simulate
from backtest.crypto_momentum_metrics import performance
from scripts.crypto_holding_policy import HoldingPolicy, utc
from scripts.crypto_trend_rankings import (CURRENT_SCORING_VERSION, SCORING_WEIGHTS,
    LONG_DRAWDOWN_LIMIT, TURNOVER_HALF_SCORE, score_signal_frame)

OOS_START = '2025-02-01 04:00+00:00'
OOS_END = '2026-09-20 04:00+00:00'


class DailyLegBalance(HoldingPolicy):
    """Existing side-dollar balancing policy, promoted without formula changes."""
    def __call__(self,date,equity,positions,snapshot):
        targets=super().__call__(date,equity,positions,snapshot)
        if not self.daily_anchor(date):return targets
        sides={side:{s:q for s,q in targets.items() if q*side>0} for side in (1,-1)}
        budget=.05*equity*min(map(len,sides.values()))
        result={}
        for side,basket in sides.items():
            amount=sum(abs(q)*self.price(s,snapshot) for s,q in basket.items())
            if amount and budget:
                result.update({s:q*budget/amount for s,q in basket.items()})
        return result


def digest(path):
    h=hashlib.sha256()
    with Path(path).open('rb') as f:
        for chunk in iter(lambda:f.read(1024*1024),b''):h.update(chunk)
    return h.hexdigest()


def write_json(path,value):
    Path(path).write_text(json.dumps(value,ensure_ascii=False,indent=2,default=str)+'\n')


def run_backtest(analysis,out,start=OOS_START,end=OOS_END,horizon=14,mode='fisher',holding='weekly',cost=.001):
    analysis,out=Path(analysis),Path(out);a,b=utc(start),utc(end)
    if a<utc(OOS_START) or b<=a or mode not in ('fisher','both','balanced'):
        raise ValueError('requires OOS bounds and a supported strategy mode')
    if holding not in HoldingPolicy.MODES or (mode=='balanced' and holding!='weekly'):
        raise ValueError('daily side balance requires weekly name selection')
    files=('opens.parquet','ohlcv.parquet','signals.parquet','regimes.parquet',
           'lifetimes.json','settlement_evidence.json','invalid_universe_dates.json','source_quality.json')
    manifest={name:digest(analysis/name) for name in files}
    quality=json.loads((analysis/'source_quality.json').read_text())
    if quality.get('status')!='ok' or quality.get('issues'):
        raise ValueError('source quality unknown/failed')
    failed=json.loads((analysis/'invalid_universe_dates.json').read_text())
    if not isinstance(failed,list) or any(a.normalize()-pd.Timedelta(days=1)<=utc(d)<b.normalize() for d in failed):
        raise ValueError('universe qualification unknown/failed')
    prices=pd.read_parquet(analysis/'opens.parquet');prices.index=pd.to_datetime(prices.index,utc=True)
    prices=prices.loc[a:b]
    if not prices.index.equals(pd.date_range(a,b,freq='4h')):raise ValueError('incomplete 4h execution clock')
    bars=pd.read_parquet(analysis/'ohlcv.parquet')
    closes=bars['close'].unstack('symbol') if isinstance(bars.index,pd.MultiIndex) else bars.pivot(index='time',columns='symbol',values='close')
    closes.index=pd.to_datetime(closes.index,utc=True);closes=closes.sort_index()
    f=pd.read_parquet(analysis/'signals.parquet');f['date']=pd.to_datetime(f.date,utc=True).dt.normalize()
    f=f[(f.horizon==horizon)&(f.date>=a.normalize()-pd.Timedelta(days=1))&(f.date<b.normalize())]
    if f.empty:raise ValueError('requested signal horizon has no evidence')
    signals=score_signal_frame(f)
    regimes=pd.read_parquet(analysis/'regimes.parquet')
    if 'time' in regimes.columns:regimes=regimes.set_index('time')
    regimes.index=pd.to_datetime(regimes.index,utc=True)
    life=json.loads((analysis/'lifetimes.json').read_text())
    settlements=json.loads((analysis/'settlement_evidence.json').read_text())
    if isinstance(settlements,list):settlements={r['symbol']:r for r in settlements}
    policy_type=DailyLegBalance if mode=='balanced' else HoldingPolicy
    policy=policy_type(signals,closes,regimes['regime'] if mode=='fisher' else None,a,horizon,holding=holding,lifetimes=life)
    out.mkdir(parents=True,exist_ok=False)
    code_root=Path(__file__).resolve().parents[1]
    code={name:digest(code_root/name) for name in ('scripts/crypto_momentum_backtest.py','scripts/crypto_trend_rankings.py',
          'scripts/crypto_holding_policy.py','backtest/crypto_alpha.py','backtest/crypto_momentum_metrics.py')}
    metadata=dict(period='oos',start=str(a),end=str(b),horizon=horizon,mode=mode,holding=holding,cost=cost,
        funding_mode='exclude',scoring_version=CURRENT_SCORING_VERSION,weights=SCORING_WEIGHTS[CURRENT_SCORING_VERSION],
        long_drawdown_limit=LONG_DRAWDOWN_LIMIT,turnover_half_score=TURNOVER_HALF_SCORE,input_manifest=manifest,code=code,
        source_status='ok',universe_status='ok',research_status='previously viewed OOS; retrospective optimization')
    write_json(out/'manifest.json',metadata);signals.to_parquet(out/'signals.parquet',index=False)
    try:
        result=simulate(prices,pd.DataFrame(index=pd.DatetimeIndex([],tz='UTC')),
                        pd.DataFrame(columns=['time','symbol','rate','mark']),cost=cost,
                        forced_exits=forced_exits(SimpleNamespace(prices=prices,lifetimes=life),a,b),position_policy=policy)
        for name,table in result.items():table.to_csv(out/f'{name}.csv',index=name=='nav')
        stats,returns=performance(result['nav']);returns.to_csv(out/'daily_returns.csv')
        ledger=result['ledger'];cash=float((-ledger.delta_units*ledger.price-ledger['cost']).sum())
        if abs(cash-stats['total_return'])>1e-9:raise ValueError('cash identity failed')
        evidence=price_quality(ledger,settlements,life)
        if evidence not in ('observed','bounded_settlement_midpoint','bounded_settlement_adverse','exact_settlement'):
            raise ValueError('unresolved terminal price evidence')
        holding_episodes(ledger).to_csv(out/'holding_episodes.csv',index=False)
        pd.DataFrame(policy.events,columns=['time','symbol','reason']).to_csv(out/'policy_events.csv',index=False)
        row=dict(metadata,**stats,status='ok',cash_identity_error=cash-stats['total_return'],price_evidence=evidence,
                 fees=float(ledger['cost'].sum()),funding_cash=0.,trade_count=len(ledger),
                 mean_gross=float(result['nav'].gross.mean()),mean_net=float(result['nav'].net.mean()))
        if manifest!={name:digest(analysis/name) for name in files}:raise ValueError('input changed during run')
        write_json(out/'summary.json',row)
        return row
    except (ValueError,KeyError,TypeError) as exc:
        write_json(out/'failure.json',dict(metadata,status='failed',error=str(exc)))
        raise


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--analysis',required=True,type=Path);p.add_argument('--out',required=True,type=Path)
    p.add_argument('--start',default=OOS_START);p.add_argument('--end',default=OOS_END)
    p.add_argument('--horizon',type=int,default=14);p.add_argument('--mode',choices=('fisher','both','balanced'),default='fisher')
    p.add_argument('--holding',choices=HoldingPolicy.MODES,default='weekly');p.add_argument('--cost',type=float,default=.001)
    args=p.parse_args();row=run_backtest(**vars(args))
    print(json.dumps({k:row[k] for k in ('scoring_version','mode','period','total_return','max_drawdown','sharpe','status')},ensure_ascii=False))


# The following helpers are extracted unchanged from validated research b2219ea0.

def holding_episodes(ledger):
    """Position lifetimes, including resizing; a reversal starts a new episode."""
    records=[]
    for symbol,group in ledger.groupby('symbol',sort=True):
        quantity=0.; start=None
        for row in group.sort_values('time',kind='stable').to_dict('records'):
            after=float(row['units_after']); date=utc(row['time'])
            if quantity and (not after or quantity*after<0):
                records.append(dict(symbol=symbol,entry_time=start,exit_time=date,
                                    side=int(np.sign(quantity)),days=(date-start).total_seconds()/86400))
                start=None
            if after and (not quantity or quantity*after<0):
                start=date
            quantity=after
        if abs(quantity)>1e-12:
            raise ValueError('unclosed terminal holding '+symbol)
    return pd.DataFrame(records,columns=['symbol','entry_time','exit_time','side','days'])

def forced_exits(inputs,a,b):
    result={}
    for symbol,meta in inputs.lifetimes.items():
        if symbol not in inputs.prices:
            continue
        when=pd.Timestamp(meta['delivery'],unit='ms',tz='UTC').ceil('4h')
        if a<when<=b:
            result.setdefault(when,[]).append(symbol)
    return result

def price_quality(ledger,settlements,lifetimes=None):
    labels=[]
    for symbol,meta in (lifetimes or {}).items():
        booking=pd.Timestamp(meta['delivery'],unit='ms',tz='UTC').ceil('4h')
        used=ledger[(ledger['symbol']==symbol)&(pd.to_datetime(ledger['time'],utc=True)==booking)]
        proof=settlements.get(symbol,{})
        if not used.empty and ('booking_time' not in proof or utc(proof['booking_time'])!=booking):
            return 'unresolved'
    for symbol,proof in settlements.items():
        if 'booking_time' not in proof:
            continue
        used=ledger[(ledger['symbol']==symbol)&(pd.to_datetime(ledger['time'],utc=True)==utc(proof['booking_time']))]
        if not used.empty:
            labels.append(proof.get('price_evidence','unresolved'))
    if not labels:
        return 'observed'
    if any(label not in ('exact_settlement','bounded_settlement_midpoint','bounded_settlement_adverse') for label in labels):
        return 'unresolved'
    return next((label for label in ('bounded_settlement_adverse','bounded_settlement_midpoint') if label in labels),'exact_settlement')

if __name__=='__main__':
    main()
