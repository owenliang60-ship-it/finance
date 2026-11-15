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


def prepare_job(rt, anchor, compute):
    """11:00 job: freeze this week's candidates and direction in ``weeks``. Read-only on the exchange.

    ``compute`` returns the ``prepare()`` result or raises. A failure means no new
    positions this week (D5); existing positions and stops are left alone.
    """
    from . import config as C, notify
    anchor = utc(anchor)
    started = rt.clock()
    existing = rt.store.week(str(anchor))
    rerunnable = existing and existing['status'] == 'no_new' and not rt.store.actions(anchor_utc=str(anchor), lane='risk')
    if existing and existing['status'] not in (None, 'prepared') and not rerunnable:
        return dict(status='locked', reason='week already ' + existing['status'])
    try:
        result = compute()
        if result['anchor_utc'] != str(anchor) or result['fisher']['regime'] not in (-1, 1):
            raise ValueError('prepared result does not belong to this anchor')
        signals = signal_frame(result['report']).to_dict('records')
        if not all(isinstance(r['score_v4'], float) and r['score_v4'] == r['score_v4'] for r in signals):
            raise ValueError('incomplete candidate scores')
    except Exception as error:   # any failure is the same outcome: nothing new this week
        rt.store.save_week(str(anchor), rule_version=C.RULE_VERSION, release_commit=rt.release_commit,
                           prep_status='failed', prep_error=(type(error).__name__ + ': ' + str(error))[:300],
                           prep_started_at=started, prep_finished_at=rt.clock(), status='prepared')
        notify.enqueue(rt, '⚠️ 动量多空1号 准备失败（%s）：%s\n本周不新增；已有持仓和止损不动。修好后可在锚点前重跑准备。'
                       % (anchor, str(error)[:200]), key='prep_failed:%s:%s' % (anchor, started))
        notify.flush(rt)
        return dict(status='failed', error=str(error))
    with rt.store.tx():
        rt.store.add_evidence('snapshot', 'signals:%s:%s' % (anchor, result['input_hash']), signals)
        rt.store.save_week(str(anchor), rule_version=C.RULE_VERSION, release_commit=rt.release_commit,
                           prep_status='ready', prep_error=None, prep_started_at=started,
                           prep_finished_at=rt.clock(), signal_day=result['signal_day'],
                           input_hash=result['input_hash'], fisher_week_start=result['fisher'].get('week_end'),
                           fisher=result['fisher']['fisher'], trigger=result['fisher']['trigger'],
                           regime=result['fisher']['regime'], short_ranked=result['short_ranked'],
                           long_ranked=result['long_ranked'], status='prepared')
    return dict(status='ready', anchor=str(anchor), regime=result['fisher']['regime'],
                shorts=len(result['short_ranked']), longs=len(result['long_ranked']))
