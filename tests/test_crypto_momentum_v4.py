import numpy as np
import pandas as pd
import pytest
from scripts import crypto_trend_rankings as trend
from scripts.crypto_holding_policy import HoldingPolicy


def rows():
    return [dict(symbol=s,status='ok',**{'return':r},slope=r,er=e,r_squared=e,
                 drawdown=d,quote_volume=v) for s,r,e,d,v in
            [('BAD',2.,.9,.65,1e9),('EDGE',.5,.7,.3,2e9),('GOOD',.2,.5,.1,1e8),('SHORT',-.3,.6,.8,1e9)]]


def test_current_default_is_v4_and_keeps_all_valid_members_in_percentiles():
    f=rows();trend.score_rows(f,quote_turnover={r['symbol']:r['quote_volume'] for r in f})
    assert trend.CURRENT_SCORING_VERSION=='v4'
    assert trend.V4_WEIGHTS==dict(absolute_return=.45,er=.125,r_squared=.125,drawdown=.05,quote_volume=.25)
    by={r['symbol']:r for r in f}
    assert by['EDGE']['percentiles']['absolute_return']==75
    assert by['EDGE']['score']==pytest.approx(45*.75+12.5*.75+12.5*.75+5*.75+25*2/3)
    assert trend.direction_eligible(by['EDGE'],1)
    assert not trend.direction_eligible(by['BAD'],1)
    assert trend.direction_eligible(by['SHORT'],-1)


@pytest.mark.parametrize('d',[np.nan,np.inf,-.1,1.1])
def test_v4_unknown_drawdown_does_not_pass(d):
    r=rows()[1];r['drawdown']=d
    with pytest.raises(ValueError,match='drawdown'):
        trend.direction_eligible(r,1)


def test_current_policy_uses_v4_and_weekday_is_not_stop():
    f=pd.DataFrame(rows());f['date']='2025-02-02';f['horizon']=14
    f['score_v4']=[100,90,80,70]
    start=pd.Timestamp('2025-02-03 04:00',tz='UTC')
    p=HoldingPolicy(f,pd.DataFrame(index=pd.DatetimeIndex([],tz='UTC')),None,start,14)
    assert p.ranks(start)=={1:['EDGE','GOOD'],-1:['SHORT']}
    assert p(start+pd.Timedelta(days=1),1000,{'BAD':1},{'BAD':10})=={'BAD':1}


def test_v3_remains_explicit_historical_replay():
    f=rows();trend.score_rows(f,'v3',{r['symbol']:r['quote_volume'] for r in f})
    assert trend.direction_eligible(f[0],1,'v3')


def test_v4_invalid_quality_metric_cannot_silently_receive_zero_percentile():
    f=rows();f[-1]['er']=float('nan')
    with pytest.raises(ValueError,match='metric'):
        trend.score_rows(f,quote_turnover={r['symbol']:r['quote_volume'] for r in f})


def test_daily_v4_removes_crashed_winner_and_preserves_watch_evidence():
    day=pd.Timestamp('2026-09-20',tz='UTC')
    dates=pd.date_range(end=day+pd.Timedelta(hours=20),periods=85,freq='4h')
    bad=pd.Series(np.linspace(100,1000,85),index=dates);bad.iloc[-1]=350
    good=pd.Series(np.exp(np.linspace(0,.2,85))*100,index=dates)
    result=trend.rank_period(['BAD','GOOD'],{'BAD':bad,'GOOD':good},day,14,
                             quote_turnover={'BAD':1e9,'GOOD':1e9})
    assert result['valid_count']==2
    assert [r['symbol'] for r in result['top10']]==['GOOD']
    assert result['top10'][0]['percentiles']['absolute_return']==50
    assert [r['symbol'] for r in result['drawdown_watch']]==['BAD']


def test_weekly_trend_uses_same_gate_and_daily_amount_unit(monkeypatch):
    from scripts import crypto_weekly_report as weekly
    cutoff=pd.Timestamp('2026-09-21',tz='UTC')
    dates=pd.date_range(end=cutoff-pd.Timedelta(days=1),periods=50,freq='D')
    closes={s:pd.Series(np.arange(50)+100.,index=dates,name=s) for s in ('BAD','GOOD')}
    frames={s:pd.DataFrame({'timestamp':dates,'quote_volume':1e9}) for s in closes}
    metrics={r['symbol']:r for r in rows()}
    monkeypatch.setattr(weekly,'trend_metrics',lambda x:{k:metrics['BAD' if x.name=='BAD' else 'GOOD'][k] for k in ('return','slope','er','r_squared','drawdown')})
    result=weekly.rank_weeks(['BTCUSDT','BAD','GOOD'],closes,frames,cutoff,7)
    assert result['pool']==['BAD','GOOD']
    assert [r['symbol'] for r in result['top10']]==['GOOD']
    assert all(r['quote_volume']==1e9 and r['turnover_score']==50 for r in result['rows'])
