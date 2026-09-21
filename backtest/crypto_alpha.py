"""Isolated linear-perpetual research ledger; existing producers remain unchanged."""
from __future__ import annotations
import numpy as np
import pandas as pd
from scripts.crypto_trend_rankings import EXCLUDED_TRADING_SYMBOLS

VARIANTS=('published_long','return_long','pool_long','composite_ls','directional_ls',
          'return_ls','signed_ls','no_er_ls','no_r2_ls','no_dd_ls')


def target_weights(rows, variant, top_n=10):
    valid=[r for r in rows if r['status']=='ok' and r['symbol'] not in EXCLUDED_TRADING_SYMBOLS]
    if not valid:
        return {}
    if variant=='pool_long':
        return {r['symbol']:1/len(valid) for r in valid}
    key={'return_long':'return','return_ls':'return','signed_ls':'signed',
         'no_er_ls':'no_er','no_r2_ls':'no_r2','no_dd_ls':'no_dd'}.get(variant,'score')
    if variant in ('published_long','return_long'):
        selected=sorted((r for r in valid if r['eligible']),key=lambda r:(-r[key],r['symbol']))[:top_n]
        return {r['symbol']:1/top_n for r in selected}
    if variant=='directional_ls':
        longs=[r for r in valid if r['eligible']]
        shorts=[r for r in valid if r['return']<0 and r['slope']<0]
    else:
        longs=shorts=valid
    n=min(top_n,len(valid)//2)
    longs=sorted(longs,key=lambda r:(-r[key],r['symbol']))[:n]
    long_symbols={r['symbol'] for r in longs}
    shorts=sorted((r for r in shorts if r['symbol'] not in long_symbols),key=lambda r:(r[key],r['symbol']))[:n]
    return {**{r['symbol']:.5/top_n for r in longs},
            **{r['symbol']:-.5/top_n for r in shorts}}


def simulate(prices, targets, funding, cost=.001, forced_exits=None, forced_exit_cost=0., position_policy=None):
    """Linear USDT perp: fixed signed units, equity mark-to-market, event funding.

    Price observations are executable bar opens; targets occur after funding at
    that timestamp. Initial capital1; terminal trades liquidate all positions.
    Missing held/fill prices stop the path rather than bias selection or P&L.
    forced_exits contains independently evidenced instrument terminal timestamps;
    caller must supply settlement price and label any approximation in the report.
    """
    if prices.empty or not prices.index.is_monotonic_increasing or prices.index.has_duplicates:
        raise ValueError('invalid price clock')
    if not targets.index.isin(prices.index).all() or targets.index.has_duplicates:
        raise ValueError('target clock not on price clock')
    if not 0<=cost<1 or not 0<=forced_exit_cost<1:
        raise ValueError('invalid cost')
    if position_policy is not None and not targets.empty:
        raise ValueError('unit policy and weight targets are mutually exclusive')
    symbols=list(prices.columns)
    pos={s:i for i,s in enumerate(symbols)}
    px=prices.to_numpy(dtype=float)
    units=np.zeros(len(symbols));last=np.zeros(len(symbols))
    equity=1.; nav=[];ledger=[];fund_log=[]
    target_map={date:row.dropna().to_dict() for date,row in targets.iterrows()}
    events={}
    records=funding.to_dict('records')
    # Batch the identical timestamp lookup; event order and cash arithmetic stay unchanged.
    if records and pd.api.types.is_datetime64_any_dtype(funding['time'].dtype):
        buckets=prices.index.searchsorted(pd.DatetimeIndex(funding['time']),side='left')
    else:
        buckets=[prices.index.searchsorted(pd.Timestamp(e['time']),side='left') for e in records]
    for at,event in zip(buckets,records):
        # Initial funding precedes first possible position, terminal is included.
        if 0<at<len(prices):events.setdefault(int(at),[]).append(event)
    forced_exits=forced_exits or {}
    for i,date in enumerate(prices.index):
        held=units!=0
        good=np.isfinite(px[i])&(px[i]>0)
        if (held&~good).any():
            raise ValueError('missing held price '+str(date)+' '+str(np.array(symbols)[held&~good].tolist()))
        equity+=float(np.dot(units[held],px[i,held]-last[held]))
        funding_cash=0.
        for event in events.get(i,[]):
            j=pos.get(event['symbol'])
            if j is None or units[j]==0:continue
            rate,mark=float(event['rate']),float(event['mark'])
            if not np.isfinite(rate) or not np.isfinite(mark) or mark<=0:
                raise ValueError('invalid held funding')
            cash=-units[j]*mark*rate
            equity+=cash;funding_cash+=cash
            fund_log.append(dict(time=event['time'],symbol=event['symbol'],units=units[j],rate=rate,mark=mark,cashflow=cash))
        if equity<=0:raise ValueError('insolvent '+str(date))
        before=equity
        desired=units.copy()
        reason=''
        if position_policy is not None and i<len(prices)-1:
            positions={symbols[j]:float(units[j]) for j in np.flatnonzero(units)}
            snapshot={symbols[j]:float(px[i,j]) for j in np.flatnonzero(good)}
            proposed=position_policy(date,before,positions,snapshot)
            if not isinstance(proposed,dict):raise ValueError('invalid unit policy output')
            desired=np.zeros(len(symbols))
            for symbol,qty in proposed.items():
                if symbol not in pos or not np.isfinite(qty):raise ValueError('invalid unit policy position')
                if qty and not good[pos[symbol]]:raise ValueError('missing policy fill '+symbol)
                desired[pos[symbol]]=qty
            reason='policy'
        if date in target_map and i<len(prices)-1:
            weights=target_map[date]
            if sum(abs(w) for w in weights.values())>1+1e-10:raise ValueError('gross target >1')
            desired=np.zeros(len(symbols))
            for s,w in weights.items():
                if w==0:continue
                if s not in pos or not good[pos[s]]:raise ValueError('missing fill '+s+' '+str(date))
                desired[pos[s]]=before*w/px[i,pos[s]]
            reason='rebalance'
        for s in forced_exits.get(date,[]):
            if s in pos:desired[pos[s]]=0;reason='terminal_instrument'
        if i==len(prices)-1:
            desired[:]=0;reason='terminal_portfolio'
        delta=desired-units
        traded=np.flatnonzero(abs(delta)>1e-14)
        for j in traded:
            if not good[j]:raise ValueError('missing fill '+symbols[j])
            notional=abs(delta[j])*px[i,j]
            fee=notional*(cost+(forced_exit_cost if symbols[j] in forced_exits.get(date,[]) else 0.))
            equity-=fee
            ledger.append(dict(time=date,symbol=symbols[j],delta_units=delta[j],price=px[i,j],
                               notional=notional,cost=fee,reason=reason,units_after=float(desired[j])))
        units=desired
        last[good]=px[i,good]
        if equity<=0:raise ValueError('insolvent after cost '+str(date))
        signed=np.zeros(len(symbols));signed[good]=units[good]*px[i,good]
        nav.append(dict(time=date,nav=equity,gross=float(abs(signed).sum()/equity),
                        net=float(signed.sum()/equity),funding=funding_cash,
                        turnover=sum(x['notional'] for x in ledger[-len(traded):]) if len(traded) else 0))
    return dict(nav=pd.DataFrame(nav).set_index('time'),
                ledger=pd.DataFrame(ledger,columns=['time','symbol','delta_units','price','notional','cost','reason','units_after']),
                funding=pd.DataFrame(fund_log,columns=['time','symbol','units','rate','mark','cashflow']))
