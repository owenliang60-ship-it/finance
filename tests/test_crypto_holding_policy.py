import numpy as np
import pandas as pd
import pytest
from scripts.crypto_holding_policy import HoldingPolicy


def ts(s): return pd.Timestamp(s, tz='UTC')
def signals(days=('2024-01-07',), rows=None):
    rows=rows or [('L',1,1,10,90),('S',-1,-1,-10,90)]
    return pd.DataFrame([dict(date=day,horizon=7,symbol=s,status='ok',**{'return':r},slope=slope,score_v1=v1,score_v2=v2)
                         for day in days for s,r,slope,v1,v2 in rows])
def closes():
    ix=pd.date_range('2023-10-01', '2024-02-01',freq='4h',tz='UTC')
    return pd.DataFrame({'L':np.arange(len(ix))+100.,'S':1000.-np.arange(len(ix))*.1},index=ix)
def policy(holding='weekly', **kwargs):
    return HoldingPolicy(kwargs.pop('signals',signals()),kwargs.pop('closes',closes()),kwargs.pop('regimes',None),
                         kwargs.pop('start',ts('2024-01-08 04:00')),7,holding=holding,scoring_version=kwargs.pop('scoring_version','v2'),**kwargs)


def test_weekly_entry_cash_and_no_resize_between_mondays():
    p=policy(); first=p(ts('2024-01-08 04:00'),1000,{},dict(L=100.,S=100.))
    assert first=={'L':.5,'S':-.5}
    assert p(ts('2024-01-09 04:00'),2000,first,dict(L=200.,S=200.))==first


def test_initial_slice_entry_exception_on_wednesday():
    p=policy(start=ts('2024-01-10 04:00'),signals=signals(('2024-01-09',)))
    assert p(ts('2024-01-10 04:00'),1000,{},dict(L=100.,S=100.))=={'L':.5,'S':-.5}


def test_regime_exit_immediate_but_nonanchor_never_enters_new_direction():
    regimes=pd.Series([1,-1],index=[ts('2024-01-08 04:00'),ts('2024-01-08 08:00')])
    p=policy(regimes=regimes)
    first=p(ts('2024-01-08 04:00'),1000,{},dict(L=100.,S=100.))
    assert first=={'L':1.}
    assert p(ts('2024-01-08 08:00'),1000,first,dict(L=100.,S=100.))=={}
    assert p.events[-1]['reason']=='regime'


def test_v2_both_sides_descending_v1_short_ascending():
    rows=[(f'S{i:02}',-i-1,-1,-i,i) for i in range(12)]
    p=policy(signals=signals(rows=rows))
    result=p(ts('2024-01-08 04:00'),1000,{}, {s:100 for s,*_ in rows})
    assert set(result)=={f'S{i:02}' for i in range(2,12)}
    # Opposing v1 ordering, independent of v2 strength.
    rows=[(s,r,m,-v1,v2) for s,r,m,v1,v2 in rows]
    p=policy(signals=signals(rows=rows),scoring_version='v1')
    assert set(p(ts('2024-01-08 04:00'),1000,{}, {s:100 for s,*_ in rows}))=={f'S{i:02}' for i in range(10)}


def test_global_biweekly_anchor_does_not_restart_with_slice():
    p=policy('biweekly',start=ts('2024-01-09 04:00'),signals=signals(('2024-01-08','2024-01-21')))
    original=p(ts('2024-01-09 04:00'),1000,{},dict(L=100.,S=100.))
    assert p(ts('2024-01-15 04:00'),2000,original,dict(L=100.,S=100.))==original
    assert p(ts('2024-01-22 04:00'),2000,original,dict(L=100.,S=100.))=={'L':1.,'S':-1.}


def test_ema_four_hour_completion_plus_four_hour_lag():
    c=closes(); event=ts('2024-01-08 00:00'); c.loc[event,'L']=1.
    p=policy('ema30',closes=c)
    before=p(ts('2024-01-08 04:00'),1000,{},dict(L=100.,S=100.))
    assert 'L' in before
    assert 'L' not in p(ts('2024-01-08 08:00'),1000,before,dict(L=100.,S=100.))
    assert p.events[-1]['reason']=='ema30'


def test_missing_ema_held_position_fails_but_terminal_exits_first():
    c=closes(); c.loc[ts('2024-01-08 00:00'),'L']=np.nan
    p=policy('ema30',closes=c)
    with pytest.raises(ValueError,match='EMA'):
        p(ts('2024-01-08 08:00'),1000,{'L':1.},dict(L=100.))
    p=policy('ema30',closes=c,lifetimes={'L':{'delivery':int(ts('2024-01-08 05:00').timestamp()*1000)}})
    assert p(ts('2024-01-08 08:00'),1000,{'L':1.},dict(L=100.))=={}
    assert p.events[-1]['reason']=='terminal'


def test_rank_exit_only_daily_04_and_requires_complete_daily_signal_frame():
    p=policy('rank20_ema120',signals=signals(('2024-01-07',)))
    held={'L':.5}
    assert p(ts('2024-01-08 08:00'),1000,held,dict(L=100.))==held
    with pytest.raises(ValueError,match='signal'):
        p(ts('2024-01-09 04:00'),1000,held,dict(L=100.))


def test_same_bar_regime_exit_cannot_reverse_symbol():
    reg=pd.Series([-1],index=[ts('2024-01-08 04:00')])
    sig=signals(rows=[('L',-1,-1,-10,90)])
    p=policy(regimes=reg,signals=sig)
    assert p(ts('2024-01-08 04:00'),1000,{'L':1.},dict(L=100.))=={}


def test_retained_ema_positions_shrink_before_new_slots_and_never_top_up():
    sig=signals(rows=[('L',1,1,1,100),('NEW',1,1,1,90),('S',-1,-1,-1,100)])
    c=closes(); c['NEW']=c['L']
    p=policy('ema30',signals=sig,closes=c)
    result=p(ts('2024-01-08 04:00'),1000,{'L':6.},dict(L=100.,NEW=100.,S=100.))
    assert result['L']==pytest.approx(5.)
    assert 'NEW' not in result
    assert sum(abs(q)*100 for q in result.values())<=1000
    p=policy('ema30',signals=sig,closes=c)
    result=p(ts('2024-01-08 04:00'),1000,{'L':.2},dict(L=100.,NEW=100.,S=100.))
    assert result['L']==.2 and result['NEW']==.5


def test_rank20_directional_exit_and_pure_ema_ignores_pool_departure():
    sig=signals(('2024-01-08',),rows=[('S',-1,-1,-10,90)])
    p=policy('rank20_ema120',signals=sig,start=ts('2024-01-01 04:00'))
    assert p(ts('2024-01-09 04:00'),1000,{'L':.5},dict(L=100.))=={}
    assert p.events[-1]['reason']=='rank20'
    p=policy('ema120',signals=sig,start=ts('2024-01-01 04:00'))
    assert p(ts('2024-01-09 04:00'),1000,{'L':.5},dict(L=100.))=={'L':.5}


def test_rank_twentieth_kept_twenty_first_removed_with_lexical_ties():
    rows=[(f'L{i:02}',1,1,5,5) for i in range(21)]
    c=closes()
    for symbol,*_ in rows: c[symbol]=c['L']
    p=policy('rank20_ema120',signals=signals(('2024-01-08',),rows=rows),closes=c,start=ts('2024-01-01 04:00'))
    result=p(ts('2024-01-09 04:00'),1000,{'L19':.5,'L20':.5},dict(L19=100.,L20=100.))
    assert result=={'L19':.5}
    assert p.events[-1]['symbol']=='L20'


def test_ema_cannot_see_recent_or_future_bar_close():
    c1=closes(); c2=c1.copy()
    c2.loc[ts('2024-01-08 00:00'):,'L']=.01
    p1=policy('ema120',closes=c1);p2=policy('ema120',closes=c2)
    assert p1(ts('2024-01-08 04:00'),1000,{},dict(L=100.,S=100.))==p2(ts('2024-01-08 04:00'),1000,{},dict(L=100.,S=100.))


def test_regime_forced_exit_cooldown_lasts_until_strict_later_weekly_anchor():
    reg=pd.Series([-1,1],index=[ts('2024-01-08 04:00'),ts('2024-01-09 04:00')])
    sig=signals(('2024-01-07','2024-01-08','2024-01-14'))
    p=policy('daily',regimes=reg,signals=sig)
    out=p(ts('2024-01-08 04:00'),1000,{'L':1.},dict(L=100.,S=100.))
    assert 'L' not in out
    out=p(ts('2024-01-09 04:00'),1000,out,dict(L=100.,S=100.))
    assert 'L' not in out
    out=p(ts('2024-01-15 04:00'),1000,out,dict(L=100.,S=100.))
    assert out=={'L':1.}


def test_regular_fixed_rerank_exit_has_no_weekly_cooldown():
    records=pd.concat([signals(('2024-01-07',)),signals(('2024-01-08',),rows=[('S',-1,-1,-1,90)]),signals(('2024-01-09',))])
    p=policy('daily',signals=records)
    first=p(ts('2024-01-08 04:00'),1000,{},dict(L=100.,S=100.))
    second=p(ts('2024-01-09 04:00'),1000,first,dict(L=100.,S=100.))
    assert 'L' not in second
    third=p(ts('2024-01-10 04:00'),1000,second,dict(L=100.,S=100.))
    assert 'L' in third


def test_future_same_day_rank_is_never_consumed_and_direction_requires_both_signs():
    rows=[('GOOD',1,1,1,90),('BAD',1,-1,100,100)]
    sig=pd.concat([signals(rows=rows),signals(('2024-01-08',),rows=[('FUTURE',1,1,1000,1000)])])
    p=policy(signals=sig)
    assert p(ts('2024-01-08 04:00'),1000,{},dict(GOOD=100.,BAD=100.,FUTURE=100.))=={'GOOD':.5}


def test_no_ema_warmup_means_cash_for_new_entry_and_failure_for_existing():
    c=closes().loc[ts('2024-01-07 20:00'):]
    p=policy('ema30',closes=c)
    assert p(ts('2024-01-08 04:00'),1000,{},dict(L=100.,S=100.))=={}
    with pytest.raises(ValueError,match='EMA'):
        p(ts('2024-01-08 04:00'),1000,{'L':.5},dict(L=100.,S=100.))


def test_terminal_symbol_never_reenters_even_if_ranked():
    p=policy(lifetimes={'L':{'delivery':int(ts('2024-01-08 04:00').timestamp()*1000)}})
    result=p(ts('2024-01-08 04:00'),1000,{'L':1.},dict(L=100.,S=100.))
    assert 'L' not in result and result['S']==-.5


def test_two_sides_do_not_borrow_unused_budget_and_cash_caps_survive_small_top_n():
    rows=[(f'L{i}',1,1,i,i) for i in range(12)]
    p=policy(signals=signals(rows=rows))
    result=p(ts('2024-01-08 04:00'),1000,{},dict.fromkeys([s for s,*_ in rows],100.))
    assert len(result)==10
    assert sum(abs(q)*100 for q in result.values())==500.
    p=policy(top_n=2)
    assert p(ts('2024-01-08 04:00'),1000,{},dict(L=100.,S=100.))=={'L':.5,'S':-.5}


def test_short_ema_exit_is_upward_cross_and_new_entry_uses_lagged_close():
    c=closes(); c.loc[ts('2024-01-08 00:00'),'S']=2000.
    p=policy('ema30',closes=c)
    held=p(ts('2024-01-08 04:00'),1000,{},dict(L=100.,S=100.))
    assert held['S']<0
    result=p(ts('2024-01-08 08:00'),1000,held,dict(L=100.,S=100.))
    assert 'S' not in result
    assert p.events[-1]==dict(time=ts('2024-01-08 08:00'),symbol='S',reason='ema30')


def test_internal_price_gap_cannot_be_silently_skipped_by_recursive_ema():
    c=closes();c.loc[ts('2023-12-01 00:00'),'L']=np.nan
    p=policy('ema30',closes=c)
    with pytest.raises(ValueError,match='EMA state'):
        p.ema_observation('L',ts('2024-01-08 04:00'))
    assert p.ema_observation('S',ts('2024-01-08 04:00')) is not None


def test_eight_hour_sensitivity_delays_daily_anchor_and_closed_bar_protection():
    c=closes();c.loc[ts('2024-01-08 00:00'),'L']=1.
    p=policy('ema30',closes=c,delay_hours=8,start=ts('2024-01-08 08:00'))
    assert not p.anchor(ts('2024-01-08 04:00'))
    held=p(ts('2024-01-08 08:00'),1000,{},dict(L=100.,S=100.))
    assert 'L' in held
    assert 'L' not in p(ts('2024-01-08 12:00'),1000,held,dict(L=100.,S=100.))


@pytest.mark.parametrize('side', [1,-1])
def test_btc_never_selected_even_when_cached_rank_is_best(side):
    sig=signals(rows=[('BTCUSDT',side,side,100*side,100),('L',side,side,90*side,90)])
    p=policy(signals=sig,regimes=pd.Series([side],index=[ts('2024-01-08 04:00')]))
    targets=p(ts('2024-01-08 04:00'),1000,{}, {'BTCUSDT':100.,'L':100.})
    assert targets=={'L':float(side)}
    assert p(ts('2024-01-08 08:00'),1000,{'BTCUSDT':1.}, {'BTCUSDT':100.})=={}


def test_btc_only_observed_snapshot_is_cash_not_missing_data():
    p=policy(signals=signals(rows=[('BTCUSDT',1,1,100,100)]))
    assert p(ts('2024-01-08 04:00'),1000,{}, {'BTCUSDT':100.})=={}


def test_v3_policy_selects_v3_column_without_altering_direction_rules():
    sig=signals(rows=[('L',1,1,90,90),('A',1,1,10,10)])
    sig['score_v3']=[1.,99.]
    p=policy(signals=sig,scoring_version='v3',top_n=1)
    assert p(ts('2024-01-08 04:00'),1000,{}, {'L':100.,'A':100.})=={'A':.5}
