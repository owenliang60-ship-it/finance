"""Independent raw-source cash ledger and causal weekly Fisher verification.

No imports from producers or financial kernels. This verifies the recorded
scenario, including explicitly bounded midpoint settlements; it does not make
those proxies exact prices or verify strategy selection decisions.
"""
from __future__ import annotations
import argparse
from collections import defaultdict
import json
import math
from pathlib import Path
import numpy as np
import pandas as pd


def utc(value):
    value=pd.Timestamp(value)
    return value.tz_localize('UTC') if value.tzinfo is None else value.tz_convert('UTC')


def rows(path):
    value=json.loads(Path(path).read_text())
    return value['rows'] if isinstance(value,dict) else value


class Checks:
    def __init__(self):
        self.errors=[]; self.max_errors={}; self.counts=defaultdict(int)

    def require(self,condition,message):
        if not condition and len(self.errors)<100:
            self.errors.append(message)

    def equal(self,key,actual,expected,context):
        actual,expected=float(actual),float(expected)
        error=abs(actual-expected) if math.isfinite(actual) and math.isfinite(expected) else float('inf')
        self.max_errors[key]=max(self.max_errors.get(key,0.),error)
        self.require(error<=1e-10+1e-9*abs(expected),f'{context}: {key} actual={actual} expected={expected}')

    def result(self):
        return dict(status='fail' if self.errors else 'pass',errors=self.errors,
                    max_errors={k:v if math.isfinite(v) else None for k,v in self.max_errors.items()},counts=dict(self.counts))


def settlement_price(directory,symbol,when):
    """Independently reconstruct contemporaneous INDEX-minute midpoint bound."""
    if directory is None:
        return None
    path=Path(directory)/(symbol+'.json')
    if not path.exists():
        return None
    payload=json.loads(path.read_text())
    delivery=pd.Timestamp(int(payload['delivery']),unit='ms',tz='UTC')
    if utc(when)!=delivery.ceil('4h'):
        return None
    minutes=60 if delivery<utc('2024-11-11 08:00') else 30
    sample=payload['rows']
    actual=pd.to_datetime([int(row[0]) for row in sample],unit='ms',utc=True)
    expected=pd.date_range(delivery-pd.Timedelta(minutes=minutes),periods=minutes,freq='min')
    if not actual.equals(expected):
        raise ValueError(f'{symbol}: settlement window must contain exactly {minutes} consecutive minutes')
    extrema=np.asarray([[float(row[2]),float(row[3])] for row in sample])
    if not np.isfinite(extrema).all() or (extrema<=0).any() or (extrema[:,0]<extrema[:,1]).any():
        raise ValueError(f'{symbol}: invalid settlement extrema')
    return float(extrema.mean())


class RawSources:
    def __init__(self,root,settlements=None,funding_mode="include"):
        if funding_mode not in ("include","exclude"):raise ValueError("unknown funding mode")
        self.funding_mode=funding_mode
        self.root=Path(root); self.settlements=settlements
        self.prices={}; self.funds={}; self.terminal={}

    def load(self,symbol):
        if symbol in self.prices:
            return
        data=rows(self.root/'four_hour'/(symbol+'.json'))
        result={int(row[0]):float(row[1]) for row in data}
        if len(result)!=len(data):
            raise ValueError('duplicate raw price '+symbol)
        self.prices[symbol]=result
        evidence=Path(self.settlements)/(symbol+'.json') if self.settlements else None
        delivery=None
        if evidence and evidence.exists():
            payload=json.loads(evidence.read_text())
            delivery=int(payload['delivery'])
            date=pd.Timestamp(delivery,unit='ms',tz='UTC').ceil('4h')
            self.terminal[symbol]=(date,settlement_price(self.settlements,symbol,date))
        fund=rows(self.root/'funding'/(symbol+'.json')) if self.funding_mode=='include' else []
        events={}
        for row in fund:
            if delivery is not None and int(row['fundingTime'])>=delivery:
                continue
            at=pd.Timestamp(int(row['fundingTime']),unit='ms',tz='UTC').floor('min')
            if at in events:
                raise ValueError('duplicate raw funding '+symbol)
            events[at]=row
        self.funds[symbol]=events

    def price(self,symbol,date):
        self.load(symbol)
        terminal=self.terminal.get(symbol)
        if terminal and terminal[0]==date:
            return terminal[1]
        price=self.prices[symbol].get(int(date.timestamp()*1000))
        if price is None or not math.isfinite(price) or price<=0:
            raise ValueError(f'missing raw executable price {symbol} {date}')
        return price


def verify_strategy(raw,strategy,settlements=None,cost=.001,sources=None,funding_mode="include"):
    check=Checks(); strategy=Path(strategy)
    source=sources or RawSources(raw,settlements,funding_mode)
    try:
        nav=pd.read_csv(strategy/'nav.csv'); nav['time']=pd.to_datetime(nav['time'],utc=True)
        ledger=pd.read_csv(strategy/'ledger.csv'); ledger['time']=pd.to_datetime(ledger['time'],utc=True)
        charged=pd.read_csv(strategy/'funding.csv'); charged['time']=pd.to_datetime(charged['time'],utc=True)
        check.require(source.funding_mode==funding_mode,'source funding mode mismatch')
        if funding_mode=='exclude':check.require(charged.empty,'funding charged despite exclusion')
        clock=pd.DatetimeIndex(nav['time'])
        check.require(len(clock)>1 and clock.equals(pd.date_range(clock[0],clock[-1],freq='4h')),'invalid execution clock')
        check.require(ledger['time'].isin(clock).all(),'trade outside execution clock')
        check.require(not ledger.duplicated(['time','symbol']).any(),'duplicate trade')
        check.require(not charged.duplicated(['time','symbol']).any(),'duplicate charged funding')
        check.require(ledger['time'].is_monotonic_increasing,'unordered trades')
        check.require(charged['time'].gt(clock[0]).all() and charged['time'].le(clock[-1]).all(),'funding outside path')
        event_buckets=defaultdict(list)
        for symbol in sorted(set(ledger['symbol'])|set(charged['symbol'])):
            source.load(symbol)
            for at,row in source.funds[symbol].items():
                if clock[0]<at<=clock[-1]:
                    i=int(clock.searchsorted(at,side='left'))
                    event_buckets[clock[i]].append((at,symbol,row))
        fund_map={(row['time'],row['symbol']):row for row in charged.to_dict('records')}
        trade_groups={date:group.to_dict('records') for date,group in ledger.groupby('time',sort=False)}
        used_funding=set(); positions=defaultdict(float); cash=1.
        for observation in nav.to_dict('records'):
            date=observation['time']; funding_cash=0.; turnover=0.
            for at,symbol,event in event_buckets.get(date,[]):
                qty=positions[symbol]
                if qty==0:
                    continue
                check.counts['raw_funding_held']+=1
                rate=float(event['fundingRate']);mark=float(event['markPrice'])
                if not math.isfinite(rate) or not math.isfinite(mark) or mark<=0:
                    raise ValueError(f'invalid held raw funding {symbol} {at}')
                flow=-qty*rate*mark; cash+=flow; funding_cash+=flow
                key=(at,symbol); actual=fund_map.get(key)
                check.require(actual is not None,f'missing charged funding {symbol} {at}')
                if actual is not None:
                    used_funding.add(key)
                    for name,expected in [('units',qty),('rate',rate),('mark',mark),('cashflow',flow)]:
                        check.equal('funding_'+name,actual[name],expected,f'{symbol} {at}')
                if event.get('mark_source','API settlement mark')!='API settlement mark':
                    check.counts['proxy_funding_marks']+=1
            for trade in trade_groups.get(date,[]):
                symbol=trade['symbol'];px=source.price(symbol,date)
                delta=float(trade['delta_units']); notional=abs(delta)*px; fee=notional*cost
                check.require(math.isfinite(delta),f'nonfinite delta {symbol} {date}')
                for name,expected in [('price',px),('notional',notional),('cost',fee),('units_after',positions[symbol]+delta)]:
                    check.equal('trade_'+name,trade[name],expected,f'{symbol} {date}')
                positions[symbol]+=delta
                # Tiny CSV roundoff at closes must not create a phantom position.
                if abs(positions[symbol])<1e-14 or (trade['units_after']==0 and abs(positions[symbol]*px)<=1e-12):
                    positions[symbol]=0.
                cash-=delta*px+fee; turnover+=notional;check.counts['trades']+=1
                if symbol in source.terminal and source.terminal[symbol][0]==date:
                    check.equal('settlement_closed',positions[symbol],0.,f'{symbol} {date}')
                    check.counts['midpoint_settlement_trades']+=1
            values=[qty*source.price(symbol,date) for symbol,qty in positions.items() if qty]
            equity=cash+sum(values)
            check.equal('nav',observation['nav'],equity,str(date));check.counts['nav']+=1
            for name,expected in [('gross',sum(abs(value) for value in values)/equity),('net',sum(values)/equity),('funding',funding_cash),('turnover',turnover)]:
                if name in observation:
                    check.equal(name,observation[name],expected,str(date))
        check.require(set(fund_map)==used_funding,'unexpected charged funding or event without a held position')
        for symbol,qty in positions.items():
            check.equal('terminal_units',qty,0.,symbol)
    except (ValueError,KeyError,TypeError,OSError,IndexError,ZeroDivisionError) as exc:
        check.require(False,f'{type(exc).__name__}: {exc}')
    return check.result()


def verify_regimes(raw,path):
    """Raw native-week recursion, using no producer indicator or mapping code."""
    check=Checks()
    try:
        target=pd.read_parquet(path)
        if 'time' in target.columns:
            target=target.set_index('time')
        target.index=pd.to_datetime(target.index,utc=True)
        data=rows(Path(raw)/'btc_weekly.json')
        # Cut incomplete future data before reading its OHLC values.
        data=[row for row in data if pd.Timestamp(int(row[0]),unit='ms',tz='UTC')+pd.Timedelta(days=7,hours=4)<=target.index.max()]
        starts=pd.to_datetime([int(row[0]) for row in data],unit='ms',utc=True)
        check.require(starts.equals(pd.date_range(starts[0],starts[-1],freq='7D')),'missing or unordered native BTC week')
        check.require(all(t.weekday()==0 and t==t.normalize() for t in starts),'BTC week not Monday 00 UTC')
        for row in data:
            high,low=float(row[2]),float(row[3])
            if not math.isfinite(high) or not math.isfinite(low) or low<=0 or high<low:
                raise ValueError('invalid native BTC weekly high/low')
        midpoint=[(float(row[2])+float(row[3]))/2 for row in data]
        value=fish=0.; direction=None; reference=[]
        for i in range(8,len(data)):
            window=midpoint[i-8:i+1]; lo=min(window); hi=max(window)
            normal=0. if hi==lo else (midpoint[i]-lo)/(hi-lo)-.5
            value=.66*normal+.67*value
            value=.999 if value>.99 else (-.999 if value<-.99 else value)
            trigger=fish; fish=math.atanh(value)+.5*trigger
            if fish!=trigger:
                direction=1 if fish>trigger else -1
            reference.append((starts[i]+pd.Timedelta(days=7,hours=4),fish,trigger,direction))
        available=pd.DatetimeIndex([row[0] for row in reference])
        for date,row in target.iterrows():
            j=available.searchsorted(date,side='right')-1
            if j<0:
                raise ValueError(f'unknown prior Fisher at {date}')
            at,fish,trigger,direction=reference[j]
            if direction is None:
                raise ValueError(f'unknown prior direction at {date}')
            for name,expected in [('fisher',fish),('trigger',trigger),('regime',direction)]:
                check.equal(name,row[name],expected,str(date))
            if 'available_at' in row:
                check.require(utc(row['available_at'])==at,f'wrong weekly availability {date}')
            if 'week_end' in row:
                check.require(utc(row['week_end'])==at-pd.Timedelta(hours=4),f'wrong closed week {date}')
            check.counts['regimes']+=1
    except (ValueError,KeyError,TypeError,OSError,IndexError) as exc:
        check.require(False,f'{type(exc).__name__}: {exc}')
    return check.result()


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--raw',type=Path,required=True)
    parser.add_argument('--strategy',type=Path,action='append',default=[])
    parser.add_argument('--study',type=Path,help='Recursively verify every directory containing nav.csv')
    parser.add_argument('--settlements',type=Path)
    parser.add_argument('--regimes',type=Path)
    parser.add_argument('--cost',type=float,default=.001)
    parser.add_argument('--out',type=Path)
    parser.add_argument('--funding-mode',choices=('include','exclude'),default='exclude')
    args=parser.parse_args();results={};source=RawSources(args.raw,args.settlements,args.funding_mode)
    strategies=args.strategy+([p.parent for p in sorted(args.study.rglob('nav.csv'))] if args.study else [])
    for strategy in dict.fromkeys(strategies):
        result=verify_strategy(args.raw,strategy,cost=args.cost,sources=source,funding_mode=args.funding_mode)
        results[str(strategy)]=result
    if args.regimes:
        results['regimes']=verify_regimes(args.raw,args.regimes)
    report=dict(status='pass' if results and all(r['status']=='pass' for r in results.values()) else 'fail',results=results)
    text=json.dumps(report,ensure_ascii=False,indent=2,allow_nan=False)
    if args.out:
        args.out.write_text(text)
    print(text)
    raise SystemExit(0 if report['status']=='pass' else 1)


if __name__=='__main__':
    main()
