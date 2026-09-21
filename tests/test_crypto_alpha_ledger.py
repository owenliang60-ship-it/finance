import numpy as np
import pandas as pd
import pytest
from backtest.crypto_alpha import simulate, target_weights


def fixture():
    idx=pd.date_range('2025-01-01 04:00',periods=4,freq='D',tz='UTC')
    prices=pd.DataFrame({'A': [100,110,121,100], 'B':[100,90,81,100]},index=idx)
    targets=pd.DataFrame({'A':[.5, .5], 'B':[-.5,-.5]},index=idx[[0,2]])
    fund=pd.DataFrame(columns=['time','symbol','rate','mark'])
    return idx,prices,targets,fund


def test_signed_fixed_unit_profit_cost_and_terminal_close():
    idx,p,t,f=fixture()
    t=t.iloc[:1]
    result=simulate(p,t,f,cost=.001)
    # initial .005A,-.005B, fee .001; final price both100, pnl0, fee .001
    assert result['nav'].iloc[-1]['nav']==pytest.approx(.998)
    assert result['nav'].iloc[1]['nav']==pytest.approx(1.099)
    assert result['ledger']['cost'].sum()==pytest.approx(.002)


def test_funding_precedes_same_instant_rebalance_and_uses_signed_mark():
    idx,p,t,f=fixture()
    f=pd.DataFrame([dict(time=idx[0],symbol='A',rate=.5,mark=100),
                    dict(time=idx[1],symbol='A',rate=.01,mark=110),
                    dict(time=idx[1],symbol='B',rate=.01,mark=90)])
    r=simulate(p,t.iloc[:1],f,cost=0)
    assert r['nav'].iloc[-1]['nav']==pytest.approx(.999)
    assert r['funding']['cashflow'].sum()==pytest.approx(-.001)


def test_rebalance_turnover_accounts_for_drift_not_target_delta():
    idx,p,t,f=fixture()
    r=simulate(p,t,f,cost=0)
    trades=r['ledger'].loc[r['ledger']['time']==idx[2]]
    assert trades['notional'].sum()>0
    assert trades['notional'].sum()==pytest.approx(.20)  # equity1.20: target .60 each; drift .605 / -.405


def test_future_missing_selected_asset_is_error_not_removed_or_filled():
    idx,p,t,f=fixture();p.loc[idx[1],'A']=np.nan
    with pytest.raises(ValueError,match='held'):
        simulate(p,t,f,cost=0)


def test_missing_fill_fails():
    idx,p,t,f=fixture();p.loc[idx[0],'A']=np.nan
    with pytest.raises(ValueError,match='fill'):
        simulate(p,t,f,cost=0)


def test_insolvency_stops_path():
    idx,p,t,f=fixture();p.loc[idx[1],'B']=400
    with pytest.raises(ValueError,match='insolvent'):
        simulate(p,t,f,cost=0)


def test_top_slot_cash_and_ls_all_valid_pool():
    rows=[dict(symbol='A',score=90,eligible=True,status='ok',slope=1,**{'return':.1}),
          dict(symbol='B',score=10,eligible=False,status='ok',slope=-1,**{'return':-.1})]
    w=target_weights(rows,'published_long',top_n=10)
    assert w=={'A':.1}
    w=target_weights(rows,'composite_ls',top_n=1)
    assert w=={'A':.5,'B':-.5}


def test_long_short_legs_never_overlap_in_small_pool():
    rows=[dict(symbol='A',score=90,eligible=True,status='ok',slope=1,**{'return':.1})]
    w=target_weights(rows,'composite_ls',top_n=10)
    assert not w  # cannot form non-overlapping long/short baskets


def test_forced_exit_stress_is_adverse_for_either_position_sign():
    idx,p,t,f=fixture()
    t=t.iloc[:1]
    exits={idx[1]:['A','B']}
    base=simulate(p,t,f,cost=0,forced_exits=exits)
    stressed=simulate(p,t,f,cost=0,forced_exits=exits,forced_exit_cost=.05)
    assert base['nav'].iloc[-1]['nav']==pytest.approx(1.1)
    assert stressed['nav'].iloc[-1]['nav']==pytest.approx(1.05)


def test_legacy_target_selection_also_excludes_btc():
    rows=[dict(symbol=s,score=v,eligible=True,status='ok',slope=1,**{'return':.1})
          for s,v in [('BTCUSDT',100),('ALTUSDT',90)]]
    assert target_weights(rows,'published_long')=={'ALTUSDT':.1}
    assert target_weights(rows,'pool_long')=={'ALTUSDT':1.}
