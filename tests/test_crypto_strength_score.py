import copy
import math
import pytest
from scripts import crypto_trend_rankings as trend


def rows():
    return [dict(symbol=s,status='ok',eligible=r>0,score=0,
                 **{'return':r,'er':.8,'r_squared':.9,'drawdown':.1,'slope':r})
            for s,r in [('UP',.2),('DOWN',-.2),('SMALL',.1)]]


def test_absolute_return_and_volume_credit_are_direction_neutral():
    scored=trend.score_rows(rows(),version='v2',quote_turnover={'UP':100.,'DOWN':100.,'SMALL':10.})
    assert scored[0]['percentiles']['absolute_return']==scored[1]['percentiles']['absolute_return']
    assert scored[0]['score']==scored[1]['score']
    assert scored[0]['score']>scored[2]['score']
    assert sum(trend.STRENGTH_WEIGHTS.values())==pytest.approx(1.)
    assert scored[1]['eligible'] is False


@pytest.mark.parametrize('value',[None,float('nan'),-1.])
def test_missing_or_invalid_turnover_cannot_silently_drop_a_scored_member(value):
    with pytest.raises(ValueError,match='turnover'):
        trend.score_rows(rows(),version='v2',quote_turnover={'UP':value,'DOWN':100.,'SMALL':10.})


def test_zero_turnover_is_observed_zero_not_missing_and_v1_unchanged():
    a=trend.score_rows(rows(),version='v2',quote_turnover={'UP':0.,'DOWN':100.,'SMALL':10.})
    assert math.isfinite(a[0]['score'])
    legacy=trend.score_rows(rows(),version='v1')
    assert legacy[0]['score']>legacy[1]['score']
    assert set(legacy[0]['percentiles'])==set(trend.WEIGHTS)


def test_v2_report_uses_matching_volume_window_and_discloses_new_weights():
    import runpy
    fixtures=runpy.run_path('tests/test_crypto_trend_rankings.py')
    market=fixtures['FakeMarket'](); day=fixtures['ASOF']
    report=trend.build_report(market,day,top_n=2,scoring_version='v2')
    assert report['schema_version']==3
    assert report['weights']==trend.STRENGTH_WEIGHTS
    for h in (7,14,30):
        for row in report['periods'][f'{h}d']['rows']:
            expected=trend.daily_frame(market.frames[row['symbol']]).iloc[-h:]['quote_volume'].mean()
            assert row['quote_volume']==expected
        text=trend.message(report,h)
        assert '绝对涨跌幅50%' in text and '成交额10%' in text and '日均额' in text


def test_weekly_compatibility_scoring_retains_explicit_signed_return_weights():
    valid=rows()
    for row in valid:row['quote_volume']=100.
    trend.score_valid_rows(valid,{'return':.5,'er':.15,'r_squared':.15,'drawdown':.1,'quote_volume':.1})
    assert valid[0]["score"]>valid[1]["score"]


def test_v3_uses_absolute_dollar_saturation_not_volume_percentile():
    same=rows()[:2]
    scored=trend.score_rows(same,'v3',{'UP':1e9,'DOWN':3e9})
    assert scored[0]['turnover_score']==50
    assert scored[1]['turnover_score']==75
    assert scored[0]['score']==pytest.approx(85)
    assert scored[1]['score']==pytest.approx(92.5)
    assert 'quote_volume' not in scored[0]['percentiles']


@pytest.mark.parametrize('value',[None,float('nan'),float('inf'),-1.])
def test_v3_invalid_volume_blocks_publication(value):
    with pytest.raises(ValueError,match='turnover'):
        trend.score_rows(rows(),'v3',{'UP':value,'DOWN':1e9,'SMALL':1e8})


def test_v3_report_discloses_weights_formula_and_excludes_btc():
    import runpy
    f=runpy.run_path('tests/test_crypto_trend_rankings.py')
    report=trend.build_report(f['FakeMarket'](),f['ASOF'],top_n=2,scoring_version='v3',periods=(10,14))
    assert report['schema_version']==4
    assert report['weights']==dict(absolute_return=.35,er=.15,r_squared=.15,drawdown=.05,quote_volume=.30)
    assert report['turnover_scoring']['half_score_daily_quote_volume']==1e9
    for h in (10,14):
        text=trend.message(report,h)
        assert '绝对涨跌幅35%' in text and '成交额30%' in text and '10亿USDT' in text
        assert 'BTCUSDT' in text and len(text)<4000
