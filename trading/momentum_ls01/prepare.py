"""Stage-1 prepare core: JSON evidence only, no database and no orders."""
import hashlib

import pandas as pd

from scripts.crypto_holding_policy import utc
from scripts.crypto_trend_rankings import build_report, score_signal_frame
from .kernel import LivePolicy, weekly_regime_live
from .market_public import digest


def signal_frame(report):
    period=report['periods']['14d']
    rows=period['rows']
    pool=period['pool']
    names=[row['symbol'] for row in rows]
    if (len(set(names))!=len(names) or set(names)!=set(pool)
            or len(rows)!=period['pool_size'] or period['valid_count']!=period['pool_size']
            or any(r['status']!='ok' for r in rows)):
        raise ValueError('incomplete candidate signal pool')
    if not rows:
        raise ValueError('empty candidate signal pool')
    frame=pd.DataFrame(rows)
    frame['date']=report['as_of']
    frame['horizon']=14
    # All pool members enter shared percentile calculation before direction filtering.
    return score_signal_frame(frame,version='v4')


def prepare(market,btc_weekly,anchor):
    anchor=utc(anchor)
    if anchor.weekday()!=0 or anchor!=anchor.normalize()+pd.Timedelta(hours=4):
        raise ValueError('anchor must be Monday 04:00 UTC')
    fisher=weekly_regime_live(btc_weekly,anchor)
    signal_day=anchor.normalize()-pd.Timedelta(days=1)
    report=build_report(market,signal_day,periods=(14,))
    if report['as_of']!=str(signal_day.date()):
        raise ValueError('signal day mismatch')
    frame=signal_frame(report)
    p=LivePolicy(frame,fisher['regime'],anchor)
    ranked=p.ranks(anchor)
    inputs=dict(getattr(market,'input_hashes',{}))
    inputs['weekly']=hashlib.sha256(btc_weekly.to_json(date_format='iso',double_precision=15).encode()).hexdigest()
    inputs['report']=digest(report)
    return dict(strategy_id='momentum-ls-01',rule_version='2026-10-19.v2',
                anchor_utc=str(anchor),signal_day=str(signal_day.date()),fisher=fisher,
                short_ranked=ranked[-1],long_ranked=ranked[1],
                input_hash=digest(inputs),input_hashes=inputs,report=report)
