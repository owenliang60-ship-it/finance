import copy
from types import SimpleNamespace

import pandas as pd
import pytest

from trading.momentum_ls01.prepare import prepare, signal_frame
from trading.momentum_ls01.market_public import PublicScanner, PublicMarket, klines_to_frame


def weekly():
    ix=pd.date_range('2026-05-04',periods=24,freq='7D',tz='UTC')
    return pd.DataFrame({'high':[100+i for i in range(24)],'low':[90+i for i in range(24)]},index=ix)


def row(symbol,ret,slope):
    return dict(symbol=symbol,status='ok',eligible=True,er=.5,r_squared=.8,
                drawdown=.1,quote_volume=1e8,**{'return':ret},slope=slope)


def report():
    return dict(as_of='2026-10-18',periods={'14d':dict(pool=['L','S'],pool_size=2,valid_count=2,
                rows=[row('L',.2,.01),row('S',-.3,-.02)])})


def test_adapter_supplies_date_horizon_and_full_pool_before_direction_filter():
    f=signal_frame(report())
    assert set(f.symbol)=={'L','S'} and set(f.horizon)=={14}
    assert set(f.date)=={'2026-10-18'} and f.score_v4.notna().all()


def test_prepare_calls_shared_build_report_for_14d_and_freezes_rankings(monkeypatch):
    calls=[]
    def build(market,as_of,periods):
        calls.append((market,as_of,periods));return report()
    monkeypatch.setattr('trading.momentum_ls01.prepare.build_report',build)
    m=SimpleNamespace(input_hashes={'catalog':'abc'})
    r=prepare(m,weekly(),'2026-10-19 04:00Z')
    assert calls==[(m,pd.Timestamp('2026-10-18',tz='UTC'),(14,))]
    assert r['long_ranked']==['L'] and r['short_ranked']==['S']
    assert r['signal_day']=='2026-10-18' and len(r['input_hash'])==64


def test_incomplete_pool_is_failure_even_when_report_has_valid_members():
    r=report();r['periods']['14d']['rows'][0]['status']='unavailable';r['periods']['14d']['valid_count']=1
    with pytest.raises(ValueError,match='incomplete'):
        signal_frame(r)
    r=report();r['periods']['14d']['rows'].pop()
    with pytest.raises(ValueError,match='incomplete'):
        signal_frame(r)


def test_scanner_uses_only_own_client_and_does_not_fallback_after_error():
    calls=[]
    class Client:
        env='prod'
        def request(self,*args,**kwargs):calls.append((args,kwargs));return []
    s=PublicScanner(Client())
    assert s.api_request_with_retry('https://fapi.binance.com/fapi/v1/klines',{'symbol':'A'})==[]
    assert calls==[(('GET','/fapi/v1/klines'),{'params':{'symbol':'A'}})]
    with pytest.raises(ValueError,match='origin'):
        s.api_request_with_retry('https://untrusted.invalid/fapi/v1/klines')


def test_raw_conversion_preserves_quote_volume_and_utc():
    f=klines_to_frame([[0,'1','3','1','2','7',1,'13']])
    assert f.iloc[0].quote_volume==13 and f.iloc[0].volume==7
    assert str(f.iloc[0].timestamp.tz)=='UTC'
    with pytest.raises(ValueError):klines_to_frame([[0,'1']])
    with pytest.raises(ValueError):klines_to_frame([[0,'1','3','1','NaN','7',1,'13']])


def test_public_adapter_runs_complete_shared_pool_pipeline(tmp_path,monkeypatch):
    from trading.momentum_ls01.binance import Endpoints
    monkeypatch.setattr('scripts.crypto_trend_market.time.sleep',lambda _:None)
    symbols=['BTCUSDT','L','S']+[f'FLAT{i:03}USDT' for i in range(98)]
    class Client:
        env='prod'
        def request(self,method,path,params=None,**kwargs):
            assert method=='GET'
            if path==Endpoints.EXCHANGE_INFO:
                return {'symbols':[dict(symbol=s,quoteAsset='USDT',underlyingType='COIN',contractType='PERPETUAL',
                    status='TRADING',onboardDate=1500000000000,deliveryDate=4133404800000) for s in symbols]}
            assert path==Endpoints.KLINES
            end=pd.Timestamp(params['endTime'],unit='ms',tz='UTC')
            interval=params['interval'];freq='D' if interval=='1d' else '4h'
            ix=pd.date_range(start=pd.Timestamp(params['startTime'],unit='ms',tz='UTC'),end=end,freq=freq) if 'startTime' in params else pd.date_range(end=end.floor('4h'),periods=params['limit'],freq=freq)
            rows=[]
            for i,t in enumerate(ix):
                close=100 if params['symbol'].startswith('FLAT') else (100+i*.1 if params['symbol']!='S' else 100-i*.1)
                rows.append([int(t.timestamp()*1000),str(close),str(close+1),str(close-1),str(close),'100',0,'1000' if params['symbol'].startswith('FLAT') else '10000000'])
            return rows
    m=PublicMarket(Client(),tmp_path,pd.Timestamp('2026-10-18',tz='UTC'))
    monkeypatch.setattr(m,'archive_symbols',lambda:set(symbols))
    result=prepare(m,weekly(),'2026-10-19 04:00Z')
    assert result['long_ranked']==['L'] and result['short_ranked']==['S']
    assert 'BTCUSDT' not in result['report']['periods']['14d']['pool']
    assert {'catalog','daily:L','daily:S','4h:L','4h:S'}<=set(result['input_hashes'])
