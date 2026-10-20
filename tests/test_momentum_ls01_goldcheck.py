import copy
import json
from pathlib import Path

import pytest

from trading.momentum_ls01.goldcheck import check_targets

FIXTURE=json.loads((Path(__file__).parent/'fixtures/momentum_ls01/anchors.json').read_text())


def test_goldcheck_compares_all_names_and_quantities():
    result=check_targets(FIXTURE['cases'], FIXTURE['lifetimes'])
    assert result['status']=='PASS' and result['anchors']==6
    bad=copy.deepcopy(FIXTURE['cases']);sym=next(iter(bad[0]['expected']));bad[0]['expected'][sym]*=1.01
    with pytest.raises(ValueError,match='GC2'):
        check_targets(bad,FIXTURE['lifetimes'])


def test_goldcheck_rejects_missing_target_instead_of_comparing_intersection():
    bad=copy.deepcopy(FIXTURE['cases']);bad[0]['expected'].pop(next(iter(bad[0]['expected'])))
    with pytest.raises(ValueError,match='GC2'):
        check_targets(bad,FIXTURE['lifetimes'])


def test_gc5_detects_score_and_membership_drift():
    from trading.momentum_ls01.goldcheck import compare_reports
    r=dict(periods={'14d':dict(pool=['A'],rows=[dict(symbol='A',status='ok',score=12,slope=.01,drawdown=.1,**{'return':.2})],ranked=[dict(symbol='A')])})
    assert compare_reports(r,copy.deepcopy(r))['status']=='PASS'
    bad=copy.deepcopy(r);bad['periods']['14d']['rows'][0]['score']=13
    with pytest.raises(ValueError,match='GC5'):
        compare_reports(r,bad)
    bad=copy.deepcopy(r);bad['periods']['14d']['pool']=['B']
    with pytest.raises(ValueError,match='GC5'):
        compare_reports(r,bad)


def test_replay_daily_uses_milliseconds_independent_of_pandas_datetime_unit(tmp_path):
    from trading.momentum_ls01.goldcheck import replay_daily
    p=tmp_path/'BTCUSDT.csv'
    p.write_text('timestamp,open,high,low,close,volume,quote_volume\n2026-09-07,1,3,1,2,7,13\n')
    rows=replay_daily(p,{'startTime':1788739200000,'endTime':1788825599999,'limit':32})
    assert rows==[[1788739200000,'1','3','1','2','7',1788825599999,'13']]


def test_gc5_compares_short_direction_even_when_long_ranking_is_unchanged():
    from trading.momentum_ls01.goldcheck import compare_reports
    r=dict(periods={'14d':dict(pool=['S'],rows=[dict(symbol='S',status='ok',score=12,
        slope=-.01,drawdown=.1,**{'return':-.2})],ranked=[])})
    bad=copy.deepcopy(r);bad['periods']['14d']['rows'][0]['slope']=.01
    with pytest.raises(ValueError,match='GC5 directional'):
        compare_reports(r,bad)
