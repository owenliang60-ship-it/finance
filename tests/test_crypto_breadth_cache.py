import hashlib
import json
from types import SimpleNamespace

import pandas as pd
import pytest
from scripts.crypto_breadth_cache import daily_rows

DAY=86400000
START=pd.Timestamp('2025-01-01',tz='UTC')


def bars(start,end):
    return [[x,'1','1','1','1','0',x+DAY-1,'0',0,'0','0','0'] for x in range(start,end,DAY)]


def make_market(tmp_path):
    calls=[]
    def fetch(symbol,start,end,limit):
        calls.append((start,end,limit))
        return bars(int(start.timestamp()*1000),int(end.timestamp()*1000))
    return SimpleNamespace(cache_dir=tmp_path,fetch_daily_raw=fetch,calls=calls)


def meta(end='2100-01-01'):
    return dict(symbol='BTCUSDT',onboardDate=int(START.timestamp()*1000),
                deliveryDate=int(pd.Timestamp(end,tz='UTC').timestamp()*1000),status='TRADING')


def test_next_day_fetches_one_bar_and_keeps_one_bounded_file(tmp_path):
    market=make_market(tmp_path)
    end=START+pd.Timedelta(days=731)
    assert len(daily_rows(market,'BTCUSDT',START,end,meta()))==731
    assert market.calls[0][2]==731
    old=market.calls[:]
    assert len(daily_rows(market,'BTCUSDT',START,end,meta()))==731
    assert market.calls==old
    assert len(daily_rows(market,'BTCUSDT',START+pd.Timedelta(days=1),end+pd.Timedelta(days=1),meta()))==731
    assert market.calls[-1]==(end,end+pd.Timedelta(days=1),1)
    files=list(tmp_path.rglob('*.json'))
    assert len(files)==1
    assert len(json.loads(files[0].read_text())['rows'])==731


def test_retired_complete_cache_never_contacts_failed_api(tmp_path):
    market=make_market(tmp_path)
    end=START+pd.Timedelta(days=10)
    daily_rows(market,'BTCUSDT',START,end,meta())
    def fail(*a,**k):pytest.fail('retired history contacted REST')
    market.fetch_daily_raw=fail
    assert len(daily_rows(market,'BTCUSDT',START,end+pd.Timedelta(days=10),meta(str(end.date()))))==10


def test_existing_dated_snapshot_imports_without_network_or_deletion(tmp_path):
    market=make_market(tmp_path)
    end=START+pd.Timedelta(days=5)
    path=tmp_path/'daily'/'2025-01-05'/'BTCUSDT_1d_2025-01-01_2025-01-06_breadth_731_ema20_pmarp150.json'
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(bars(int(START.timestamp()*1000),int(end.timestamp()*1000))))
    assert len(daily_rows(market,'BTCUSDT',START,end,meta()))==5
    assert not market.calls and path.exists()


@pytest.mark.parametrize('mode',['empty','nan','duplicate','network'])
def test_failed_update_preserves_previous_good_cache(tmp_path,mode):
    market=make_market(tmp_path)
    end=START+pd.Timedelta(days=5)
    daily_rows(market,'BTCUSDT',START,end,meta())
    path=next((tmp_path/'rolling_1d').glob('*.json'))
    before=path.read_bytes()
    def bad(*a,**k):
        if mode=='network':raise RuntimeError('offline')
        r=bars(int(end.timestamp()*1000),int((end+pd.Timedelta(days=1)).timestamp()*1000))
        if mode=='empty':return []
        if mode=='nan':r[0][4]='nan'
        if mode=='duplicate':r+=r
        return r
    market.fetch_daily_raw=bad
    with pytest.raises((ValueError,RuntimeError)):
        daily_rows(market,'BTCUSDT',START+pd.Timedelta(days=1),end+pd.Timedelta(days=1),meta())
    assert path.read_bytes()==before


def test_corrupt_cache_is_not_silently_replaced(tmp_path):
    market=make_market(tmp_path)
    end=START+pd.Timedelta(days=5)
    daily_rows(market,'BTCUSDT',START,end,meta())
    path=next((tmp_path/'rolling_1d').glob('*.json'))
    doc=json.loads(path.read_text());doc['rows'][0][4]='7'
    path.write_text(json.dumps(doc))
    with pytest.raises(ValueError,match='hash'):daily_rows(market,'BTCUSDT',START,end,meta())


def test_past_rerun_does_not_replace_newer_rolling_cache(tmp_path):
    market=make_market(tmp_path)
    end=START+pd.Timedelta(days=10)
    daily_rows(market,'BTCUSDT',START,end,meta())
    path=next((tmp_path/'rolling_1d').glob('*.json'));before=path.read_bytes()
    assert len(daily_rows(market,'BTCUSDT',START,end-pd.Timedelta(days=2),meta()))==8
    assert path.read_bytes()==before


def test_existing_prices_prevent_pending_error_from_becoming_unopened(tmp_path):
    from scripts.crypto_trend_market import InactiveSymbolError
    market=make_market(tmp_path)
    end=START+pd.Timedelta(days=2)
    daily_rows(market,'BTCUSDT',START,end,meta())
    before=next((tmp_path/'rolling_1d').glob('*.json')).read_bytes()
    def inactive(*a,**k):raise InactiveSymbolError('exchange -1122')
    market.fetch_daily_raw=inactive
    pending=dict(meta(),status='PENDING_TRADING')
    with pytest.raises(ValueError,match='已有'):
        daily_rows(market,'BTCUSDT',START,end+pd.Timedelta(days=1),pending)
    assert next((tmp_path/'rolling_1d').glob('*.json')).read_bytes()==before
