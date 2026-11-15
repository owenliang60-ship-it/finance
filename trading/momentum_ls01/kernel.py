"""Frozen LS01 policy adapter. No transport, persistence or order submission."""
from dataclasses import dataclass
from decimal import Decimal

import numpy as np
import pandas as pd

from scripts.crypto_holding_policy import HoldingPolicy, utc
from src.indicators.fisher import fisher_transform


def weekly_regimes(btc_weekly, execution_clock):
    """Frozen weekly_regime, plus an explicit effective-week completeness gate."""
    clock = pd.DatetimeIndex(execution_clock)
    if clock.empty or clock.tz is None or not clock.is_monotonic_increasing or clock.has_duplicates:
        raise ValueError('invalid execution clock')
    history = btc_weekly.copy().sort_index()
    if history.index.tz is None or history.index.has_duplicates:
        raise ValueError('invalid weekly clock')
    if any(t.weekday() != 0 or t != t.normalize() for t in history.index):
        raise ValueError('native weekly bars must start Monday00UTC')
    history = history[history.index+pd.Timedelta(days=7,hours=4) <= clock.max()]
    for t in clock:
        effective = (t-pd.Timedelta(hours=4)).normalize()
        effective -= pd.Timedelta(days=effective.weekday())
        if effective-pd.Timedelta(days=7) not in history.index:
            raise ValueError('fisher_week_missing: '+str(effective))
    if len(history) < 9:
        raise ValueError('Fisher unknown: insufficient history')
    if not history.index.equals(pd.date_range(history.index[0],history.index[-1],freq='7D')):
        raise ValueError('weekly gap: cannot compress missing market history')
    if (not np.isfinite(history[['high','low']].to_numpy()).all()
            or (history.low <= 0).any() or (history.high < history.low).any()):
        raise ValueError('invalid weekly prices')
    result = fisher_transform(history.high.to_numpy(),history.low.to_numpy(),9)
    table = pd.DataFrame({'fisher':result['fisher'],'trigger':result['trigger']},index=history.index)
    delta = table.fisher-table.trigger
    table['regime'] = np.sign(delta).where(delta.ne(0)).ffill()
    table['week_end'] = table.index+pd.Timedelta(days=7)
    table['available_at'] = table.week_end+pd.Timedelta(hours=4)
    # Explicit nanosecond clocks avoid pandas unit differences across input formats.
    left = pd.DataFrame({'time':clock.as_unit('ns')})
    table['available_at'] = table['available_at'].astype('datetime64[ns, UTC]')
    out = pd.merge_asof(left,table.reset_index(drop=True),left_on='time',right_on='available_at',direction='backward').set_index('time')
    if out.regime.isna().any() or not out.regime.isin([-1,1]).all():
        raise ValueError('Fisher unknown: no valid preceding direction')
    out['regime'] = out.regime.astype(int)
    return out


def weekly_regime_live(btc_weekly, anchor):
    row = weekly_regimes(btc_weekly, pd.DatetimeIndex([utc(anchor)])).iloc[0]
    return dict(fisher=float(row.fisher), trigger=float(row.trigger), regime=int(row.regime),
                week_end=str(row.week_end), available_at=str(row.available_at))


class LivePolicy(HoldingPolicy):
    """One cold-start evaluation per logical Monday04 anchor.

    AsymmetricFisher direction mapping and Policy short doubling are copied
    from the pinned research implementations. Selection stays in HoldingPolicy.
    """
    def __init__(self, signals, regime, anchor, *, cooldown=None, lifetimes=None, blocked=()):
        anchor = utc(anchor)
        if anchor.weekday() != 0 or anchor != anchor.normalize()+pd.Timedelta(hours=4):
            raise ValueError('anchor must be Monday 04:00 UTC')
        if regime not in (-1,1):
            raise ValueError('unknown Fisher regime')
        super().__init__(signals, pd.DataFrame(index=pd.DatetimeIndex([],tz='UTC')),
                         pd.Series([regime],index=[anchor]), anchor,14,
                         scoring_version='v4',holding='weekly',lifetimes=lifetimes)
        self.cooldown = {s:utc(t) for s,t in (cooldown or {}).items()}
        # Live-only: symbols the exchange will not open now. Skipped in rank order, like research terminal symbols.
        self.blocked = frozenset(blocked)
        self._called = False

    def can_enter(self,symbol,date,forced):
        return symbol not in self.blocked and super().can_enter(symbol,date,forced)

    def direction(self,date):
        state = super().direction(date)
        return None if state == 1 else state

    def __call__(self,date,equity,positions,snapshot):
        if utc(date) != self.start:
            raise ValueError('must use the logical anchor')
        if self._called:
            raise ValueError('LivePolicy may be evaluated only once')
        self._called = True
        targets = super().__call__(date,equity,positions,snapshot)
        if self.direction(utc(date)) is None:
            return {s:q*2 if q<0 else q for s,q in targets.items()}
        return targets


def decimal(value):
    value = Decimal(str(value))
    if not value.is_finite():
        raise ValueError('nonfinite quantity or price')
    return value


def cost_after_trade(before,basis,delta,price):
    before,delta,price = map(decimal,(before,delta,price))
    if price <= 0:
        raise ValueError('nonpositive price')
    after = before+delta
    # Match the frozen engine's numerical-zero convention for research replay.
    if abs(after) < Decimal('1e-14'):
        return None
    if before*after < 0:
        raise ValueError('same-trade reversal unsupported')
    if before == 0:
        return price
    if basis is None or decimal(basis) <= 0:
        raise ValueError('missing basis')
    basis = decimal(basis)
    if before*delta > 0:
        return (abs(before)*basis+abs(delta)*price)/abs(after)
    return basis


@dataclass(frozen=True)
class Intent:
    symbol: str
    side: str
    qty: Decimal
    purpose: str


def split_waves(current,target):
    reductions, increases = [], []
    for symbol in sorted(set(current)|set(target)):
        before, after = decimal(current.get(symbol,0)), decimal(target.get(symbol,0))
        if before*after < 0:
            raise ValueError('same-anchor reversal: '+symbol)
        delta = after-before
        if not delta:
            continue
        reducing = abs(after) < abs(before)
        purpose = ('wave_a_close' if not after else 'wave_a_reduce') if reducing else ('wave_b_open' if not before else 'wave_b_add')
        intent = Intent(symbol,'BUY' if delta>0 else 'SELL',abs(delta),purpose)
        (reductions if reducing else increases).append(intent)
    return reductions,increases
