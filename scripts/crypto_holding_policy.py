"""Causal unit-target policies; execution, cash, fees and funding stay in ledger."""
from __future__ import annotations
import numpy as np
import pandas as pd
from scripts.crypto_trend_rankings import EXCLUDED_TRADING_SYMBOLS, CURRENT_SCORING_VERSION, SCORING_WEIGHTS, direction_eligible


def utc(value):
    value=pd.Timestamp(value)
    return value.tz_localize('UTC') if value.tzinfo is None else value.tz_convert('UTC')


class HoldingPolicy:
    MODES=('daily','weekly','biweekly','ema30','ema120','rank20_ema120')

    def __init__(self,signals,closes,regimes,start,horizon,scoring_version=CURRENT_SCORING_VERSION,
                 holding='weekly',top_n=10,delay_hours=4,lifetimes=None):
        if holding not in self.MODES or scoring_version not in SCORING_WEIGHTS:
            raise ValueError('unknown holding/scoring specification')
        if not isinstance(top_n,int) or not 1<=top_n<=10 or delay_hours not in (4,8):
            raise ValueError('requires 1..10 slots and preregistered 4h/8h delay')
        self.delay_hours=delay_hours
        self.start=utc(start); self.holding=holding; self.top_n=top_n
        self.score='score_'+scoring_version; self.version=scoring_version
        frame=signals[signals['horizon']==horizon].copy()
        frame['date']=pd.to_datetime(frame['date'],utc=True).dt.normalize()
        if frame.duplicated(['date','symbol']).any():
            raise ValueError('duplicate daily signal symbol')
        self.signals={date:rows for date,rows in frame.groupby('date')}
        self.closes=closes.copy(); self.closes.index=pd.to_datetime(self.closes.index,utc=True)
        if not self.closes.index.is_monotonic_increasing or self.closes.index.has_duplicates:
            raise ValueError('invalid close clock')
        self.span=30 if holding=='ema30' else 120 if holding in ('ema120','rank20_ema120') else None
        self.ema=self.closes.ewm(span=self.span,adjust=False,min_periods=self.span).mean() if self.span else None
        if self.span:
            # Missing interior observations make the recursive state unknown.
            # Initial pre-listing NaNs are harmless; later gaps require repair.
            gaps=(self.closes.isna() & self.closes.notna().cummax()).cummax()
            self.history_gaps=gaps
            self.ema=self.ema.mask(gaps)
        self.regimes=None if regimes is None else regimes.copy()
        if self.regimes is not None:
            self.regimes.index=pd.to_datetime(self.regimes.index,utc=True)
            if not self.regimes.index.is_monotonic_increasing or self.regimes.index.has_duplicates:
                raise ValueError('invalid regime clock')
        self.terminal={s:pd.Timestamp(m['delivery'],unit='ms',tz='UTC').ceil('4h')
                       for s,m in (lifetimes or {}).items()}
        self.events=[]; self.cooldown={}

    def daily_anchor(self,date):
        return date.hour==self.delay_hours and date.minute==date.second==date.microsecond==0

    def weekly_anchor(self,date):
        return self.daily_anchor(date) and date.weekday()==0

    def anchor(self,date):
        if date==self.start:
            return True
        if self.holding=='daily':
            return self.daily_anchor(date)
        if not self.weekly_anchor(date):
            return False
        if self.holding=='biweekly':
            return (date.normalize()-pd.Timestamp('2023-10-02',tz='UTC')).days%14==0
        return True

    def direction(self,date):
        if self.regimes is None:
            return None
        known=self.regimes.loc[self.regimes.index<=date]
        if known.empty or known.iloc[-1] not in (-1,1):
            raise ValueError('missing/invalid causal regime '+str(date))
        return int(known.iloc[-1])

    def ranks(self,date):
        day=date.normalize()-pd.Timedelta(days=1)
        if day not in self.signals or self.signals[day].empty:
            raise ValueError('missing complete previous-day signals '+str(day))
        rows=self.signals[day]
        result={}
        valid=rows[(~rows['symbol'].isin(EXCLUDED_TRADING_SYMBOLS)) & (rows['status']=='ok') & np.isfinite(rows['return']) &
                   np.isfinite(rows['slope']) & np.isfinite(rows[self.score])]
        for side in (1,-1):
            subset=valid[(valid['return']*side>0)&(valid['slope']*side>0)]
            if self.version == 'v4' and side == 1:
                subset = subset.loc[[direction_eligible(r, side, self.version) for r in subset.to_dict('records')]]
            ascending=self.version=='v1' and side==-1
            result[side]=subset.sort_values([self.score,'symbol'],ascending=[ascending,True],kind='stable')['symbol'].tolist()
        return result

    def ema_observation(self,symbol,date):
        # bar OPEN t completes at t+4h; conservative delay executes t+8h.
        source=date-pd.Timedelta(hours=4+self.delay_hours)
        if symbol not in self.closes or source not in self.closes.index:
            return None
        if self.history_gaps.at[source,symbol]:
            raise ValueError('missing causal EMA state after source gap '+symbol)
        close,ema=self.closes.at[source,symbol],self.ema.at[source,symbol]
        if not np.isfinite(close) or not np.isfinite(ema) or close<=0 or ema<=0:
            return None
        return float(close),float(ema)

    def exit(self,symbol,date,reason,positions,forced):
        positions.pop(symbol,None); forced.add(symbol)
        self.events.append(dict(time=date,symbol=symbol,reason=reason))
        if reason!='terminal':
            self.cooldown[symbol]=date

    def can_enter(self,symbol,date,forced):
        if symbol in EXCLUDED_TRADING_SYMBOLS or symbol in forced or date>=self.terminal.get(symbol,pd.Timestamp.max.tz_localize('UTC')):
            return False
        if symbol in self.cooldown:
            # Every forced exit waits for a later weekly anchor, even daily mode.
            if not self.weekly_anchor(date) or date<=self.cooldown[symbol]:
                return False
            del self.cooldown[symbol]
        return True

    @staticmethod
    def price(symbol,snapshot):
        price=snapshot.get(symbol,np.nan)
        if not np.isfinite(price) or price<=0:
            raise ValueError('missing executable open '+symbol)
        return float(price)

    def __call__(self,date,equity,positions,snapshot):
        date=utc(date)
        if not np.isfinite(equity) or equity<=0:
            raise ValueError('nonpositive/nonfinite equity')
        original={s:float(q) for s,q in positions.items() if q!=0}
        if not all(np.isfinite(q) for q in original.values()):
            raise ValueError('invalid held units')
        retained=original.copy(); forced=set()
        for symbol in list(retained):
            if symbol in EXCLUDED_TRADING_SYMBOLS:
                self.exit(symbol,date,'excluded_symbol',retained,forced)
            elif symbol in self.terminal and date>=self.terminal[symbol]:
                self.exit(symbol,date,'terminal',retained,forced)
        direction=self.direction(date)
        for symbol,qty in list(retained.items()):
            if direction is not None and qty*direction<0:
                self.exit(symbol,date,'regime',retained,forced)
        if self.span:
            for symbol,qty in list(retained.items()):
                obs=self.ema_observation(symbol,date)
                if obs is None:
                    raise ValueError('missing causal EMA protection '+symbol+' '+str(date))
                if (obs[0]-obs[1])*np.sign(qty)<0:
                    self.exit(symbol,date,'ema'+str(self.span),retained,forced)
        ranked=None
        if self.holding=='rank20_ema120' and self.daily_anchor(date):
            ranked=self.ranks(date)
            for symbol,qty in list(retained.items()):
                if symbol not in ranked[int(np.sign(qty))][:20]:
                    self.exit(symbol,date,'rank20',retained,forced)
        if not self.anchor(date):
            return retained
        ranked=ranked if ranked is not None else self.ranks(date)
        sides=(direction,) if direction is not None else (1,-1)
        budget=equity if direction is not None else equity*.5
        # top_n limits slot count; the specified per-name caps remain 10%/5%.
        slot=budget/10
        if not self.span:
            desired={}
            for side in sides:
                count=0
                for symbol in ranked[side]:
                    if count>=self.top_n:
                        break
                    if not self.can_enter(symbol,date,forced):
                        continue
                    # A conventional rerank exit has no cooldown, but cannot
                    # reverse the same name during its closing execution bar.
                    if symbol in original and original[symbol]*side<0:
                        continue
                    desired[symbol]=side*slot/self.price(symbol,snapshot); count+=1
            for symbol in retained.keys()-desired.keys():
                self.events.append(dict(time=date,symbol=symbol,reason='rebalance'))
            return desired
        desired=retained.copy()
        for side in sides:
            existing=[s for s,q in desired.items() if q*side>0]
            gross=sum(abs(desired[s])*self.price(s,snapshot) for s in existing)
            if gross>budget:
                scale=budget/gross
                for symbol in existing:
                    desired[symbol]*=scale
                gross=budget
            remaining=max(0.,budget-gross)
            slots=self.top_n-len(existing)
            for symbol in ranked[side]:
                if slots<=0 or remaining<=equity*1e-12:
                    break
                if symbol in desired or not self.can_enter(symbol,date,forced):
                    continue
                obs=self.ema_observation(symbol,date)
                if obs is None or (obs[0]-obs[1])*side<0:
                    continue
                notional=min(slot,remaining)
                desired[symbol]=side*notional/self.price(symbol,snapshot)
                remaining-=notional; slots-=1
        return desired
