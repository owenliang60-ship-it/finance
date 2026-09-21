import json
import numpy as np
import pandas as pd
import pytest
from scripts.crypto_momentum_backtest import run_backtest


def fixture(tmp_path):
    root=tmp_path/'analysis';root.mkdir()
    clock=pd.date_range('2025-02-03 04:00','2025-02-04 04:00',freq='4h',tz='UTC')
    px=pd.DataFrame({'BAD':[100.]*7,'GOOD':[100.,102,104,106,108,110,112], 'SHORT':[100.,99,98,97,96,95,94]},index=clock)
    px.to_parquet(root/'opens.parquet')
    bars=px.rename_axis('time').stack().rename('close').to_frame();bars.index=bars.index.set_names(['time','symbol'])
    bars.to_parquet(root/'ohlcv.parquet')
    f=pd.DataFrame([dict(date='2025-02-02',horizon=14,symbol=s,status='ok',**{'return':r},slope=r,
                        er=.9,r_squared=.9,drawdown=d,quote_volume=1e9)
                    for s,r,d in [('BAD',3.,.65),('GOOD',.5,.1),('SHORT',-.5,.8)]])
    f.to_parquet(root/'signals.parquet');pd.DataFrame({'regime':[1]},index=clock[:1]).to_parquet(root/'regimes.parquet')
    for name,value in [('lifetimes.json',{}),('settlement_evidence.json',{}),('invalid_universe_dates.json',[]),('source_quality.json',{'status':'ok','issues':[]})]:
        (root/name).write_text(json.dumps(value))
    return root


def test_default_oos_runner_rescores_missing_v4_cache_and_excludes_bad_long(tmp_path):
    root=fixture(tmp_path);out=tmp_path/'out'
    row=run_backtest(root,out,start='2025-02-03 04:00+00:00',end='2025-02-04 04:00+00:00',mode='both')
    trades=pd.read_csv(out/'ledger.csv')
    assert row['status']=='ok' and row['scoring_version']=='v4'
    assert row['funding_mode']=='exclude' and row['period']=='oos'
    assert set(trades.symbol)=={'GOOD','SHORT'}
    # Price PnL .006+.003, fees .0001 at entry + .000103 at exit.
    assert row['total_return']==pytest.approx(.008797,abs=1e-10)
    assert not (root/'funding.parquet').exists()


def test_unknown_universe_stops_before_backtest(tmp_path):
    root=fixture(tmp_path);(root/'invalid_universe_dates.json').unlink()
    with pytest.raises((ValueError,FileNotFoundError)):
        run_backtest(root,tmp_path/'out',start='2025-02-03 04:00+00:00',end='2025-02-04 04:00+00:00')
