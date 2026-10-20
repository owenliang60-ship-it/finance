from decimal import Decimal as D
import json
from pathlib import Path

import pandas as pd
import pytest

from trading.momentum_ls01.kernel import LivePolicy, weekly_regime_live, split_waves, cost_after_trade

FIXTURE = json.loads((Path(__file__).parent/'fixtures/momentum_ls01/anchors.json').read_text())


def policy(case):
    return LivePolicy(pd.DataFrame(case['signals']), case['regime']['regime'], case['anchor'],
                      cooldown=case['cooldown'], lifetimes=FIXTURE['lifetimes'])


@pytest.mark.parametrize('case', FIXTURE['cases'], ids=lambda c: c['anchor'][:10])
def test_cold_start_matches_frozen_inventory(case):
    p = policy(case)
    got = p(case['anchor'], case['equity'], case['positions'], case['prices'])
    assert set(got) == set(case['expected'])
    for sym, qty in got.items():
        assert qty == pytest.approx(case['expected'][sym], rel=1e-9, abs=1e-12)
    with pytest.raises(ValueError, match='once'):
        p(case['anchor'], case['equity'], case['positions'], case['prices'])


def signals():
    rows=[]
    for side in (1,-1):
        for i in range(12):
            rows.append(dict(date='2026-10-18',horizon=14,symbol=f'{side}:{i:02}',
                             status='ok',slope=side,drawdown=.1,score_v4=100-i, **{'return':side*.1}))
    return pd.DataFrame(rows)


@pytest.mark.parametrize('regime,shorts,longs', [(1,10,10),(-1,10,0)])
def test_frozen_slot_budget_and_bear_exit(regime,shorts,longs):
    s=signals();p=LivePolicy(s,regime,'2026-10-19 04:00Z')
    result=p('2026-10-19 04:00Z',10000, {'1:00':1}, dict.fromkeys(s.symbol,100))
    assert len([x for x in result.values() if x<0])==shorts
    assert len([x for x in result.values() if x>0])==longs
    assert all(x in (-10,5) for x in result.values())


def test_missing_slots_leave_cash_and_same_anchor_stop_cannot_reenter():
    s=signals().iloc[[0,12]].copy()
    p=LivePolicy(s,1,'2026-10-19 04:00Z',cooldown={'-1:00':'2026-10-19 04:00Z'})
    assert p('2026-10-19 04:00Z',10000,{},dict.fromkeys(s.symbol,100))=={'1:00':5}


def test_same_anchor_reversal_is_not_filled_with_opposite_direction():
    s=signals();p=LivePolicy(s,1,'2026-10-19 04:00Z')
    got=p('2026-10-19 04:00Z',10000,{'-1:00':1},dict.fromkeys(s.symbol,100))
    assert '-1:00' not in got and '-1:10' in got


def test_wrong_clock_cannot_change_frozen_logical_anchor():
    c=FIXTURE['cases'][0];p=policy(c)
    with pytest.raises(ValueError,match='anchor'):
        p(pd.Timestamp(c['anchor'])+pd.Timedelta(seconds=2),c['equity'],{},c['prices'])
    with pytest.raises(ValueError,match='Monday'):
        LivePolicy(signals(),1,'2026-10-20 04:00Z')


def weekly():
    ix=pd.date_range('2026-05-04',periods=24,freq='7D',tz='UTC')
    return pd.DataFrame({'high':[100+i+(i%4) for i in range(24)],'low':[90+i for i in range(24)]},index=ix)


def test_weekly_fisher_requires_effective_week_and_ignores_future():
    w=weekly(); a=pd.Timestamp('2026-10-19 04:00Z')
    expected=weekly_regime_live(w,a)
    future=pd.DataFrame({'high':[1e9],'low':[1e8]},index=[a.normalize()])
    assert weekly_regime_live(pd.concat([w,future]),a)==expected
    with pytest.raises(ValueError,match='fisher_week_missing'):
        weekly_regime_live(w.iloc[:-1],a)
    with pytest.raises(ValueError,match='weekly gap'):
        weekly_regime_live(w.drop(w.index[10]),a)


def test_decimal_cost_is_weighted_on_add_unchanged_on_reduce_and_cleared_on_zero():
    assert cost_after_trade(D('-2'),D('100'),D('-1'),D('130'))==D('110')
    assert cost_after_trade(D('-3'),D('110'),D('1'),D('90'))==D('110')
    assert cost_after_trade(D('-2'),D('110'),D('2'),D('90')) is None
    with pytest.raises(ValueError,match='reversal'):
        cost_after_trade(D('-2'),D('110'),D('3'),D('90'))
    with pytest.raises(ValueError):
        cost_after_trade(D('0'),None,D('-1'),D('NaN'))


def test_split_waves_preserves_signed_deltas_without_reversal():
    a,b=split_waves({'A':D('-10'),'B':D('5'),'C':D('-3')}, {'A':D('-4'),'B':D('7'),'D':D('-2')})
    assert [(x.symbol,x.side,x.qty) for x in a]==[('A','BUY',D('6')),('C','BUY',D('3'))]
    assert [(x.symbol,x.side,x.qty) for x in b]==[('B','BUY',D('2')),('D','SELL',D('2'))]
    with pytest.raises(ValueError,match='reversal'):
        split_waves({'A':D('-1')},{'A':D('1')})
