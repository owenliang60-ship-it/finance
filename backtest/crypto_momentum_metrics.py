"""Performance function extracted unchanged from verified research commit b2219ea0."""
import numpy as np
import pandas as pd
from backtest.pipeline.primitives.evaluation import newey_west_tstat

def performance(nav):
    """Entry fee belongs to first completed day; exit fee to final day.

    CAGR uses actual elapsed time. Drawdown includes initial capital and all
    supplied intraday observations, including the initial transaction cost.
    """
    s = nav['nav'].astype(float)
    if s.empty or not s.index.is_monotonic_increasing or s.index.has_duplicates:
        raise ValueError('invalid NAV clock')
    if not np.isfinite(s).all() or (s <= 0).any():
        raise ValueError('nonpositive/nonfinite NAV')
    duration = (s.index[-1] - s.index[0]).total_seconds() / 86400
    clock = pd.date_range(s.index[0], s.index[-1], freq='D')
    if duration and clock[-1] != s.index[-1]:
        raise ValueError('daily statistics require complete 24h slice')
    daily = s.reindex(clock)
    if daily.isna().any():
        raise ValueError('missing daily NAV')
    if len(daily) > 1:
        r = daily.iloc[1:].div(daily.shift().iloc[1:]) - 1
        r.iloc[0] = daily.iloc[1] - 1  # initial collateral=1, before entry cost
    else:
        r = daily - 1
    r.name = 'return'
    mean = float(r.mean())
    std = float(r.std(ddof=1)) if len(r) > 1 else np.nan
    t, p = newey_west_tstat(r, 30)
    peak = np.maximum.accumulate(np.r_[1., s.to_numpy()])
    stats = dict(total_return=float(s.iloc[-1] - 1), days=duration, observations=len(r),
                 mean=mean, volatility=std*np.sqrt(365),
                 sharpe=mean/std*np.sqrt(365) if std > 0 else np.nan,
                 cagr=float(s.iloc[-1]**(365/duration)-1) if duration > 0 else np.nan,
                 max_drawdown=float(np.min(np.r_[1., s.to_numpy()]/peak-1)),
                 nw_t=t, nw_p=p)
    return stats, r
