import json
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

from scripts import crypto_trend_rankings as trend

ASOF = pd.Timestamp('2026-09-06', tz='UTC')


def meta(symbol, **extra):
    return dict(symbol=symbol, quoteAsset='USDT', underlyingType='COIN',
                contractType='PERPETUAL', status='TRADING',
                onboardDate=1500000000000, deliveryDate=4133404800000, **extra)


def frames():
    dates = pd.date_range(end=ASOF, periods=30, freq='D')
    return {s: pd.DataFrame({'timestamp': dates, 'quote_volume': v})
            for s, v in [('AUSDT', 300.), ('BUSDT', 200.), ('CUSDT', 100.)]}


def prices(slope=.01):
    dates = pd.date_range(end=ASOF+pd.Timedelta(hours=20), periods=181, freq='4h')
    return pd.Series(100*np.exp(slope*np.arange(181)), index=dates)


def test_daily_intersections_are_not_sum_volume_or_current_ranking():
    data = frames()
    # B has one day outside Top2, 10 days ago, and qualifies again recently.
    data['BUSDT'].loc[19, 'quote_volume'] = 50
    rankings = trend.daily_volume_rankings(data, [meta(s) for s in data], ASOF, top_n=2)
    pools = trend.build_pools(rankings, ASOF, set(data))
    assert pools == {'7d':['AUSDT','BUSDT'], '14d':['AUSDT'], '30d':['AUSDT']}
    assert len(rankings) == 30


def test_partial_delisting_day_still_competes():
    data = frames()
    records = [meta(s) for s in data]
    records[0].update(status='SETTLING', deliveryDate=int((ASOF+pd.Timedelta(hours=8)).timestamp()*1000))
    rankings = trend.daily_volume_rankings(data, records, ASOF, top_n=2)
    assert [r['symbol'] for r in rankings['2026-09-06']] == ['AUSDT','BUSDT']
    pools = trend.build_pools(rankings, ASOF, {'BUSDT','CUSDT'})
    assert pools['7d'] == ['BUSDT']  # C must not be promoted because A is now gone.


def test_new_listing_missing_prior_days_is_not_corrupt_but_cannot_enter_long_pool():
    data = frames()
    records = [meta(s) for s in data]
    records[0]['onboardDate'] = int((ASOF-pd.Timedelta(days=6)+pd.Timedelta(hours=12)).timestamp()*1000)
    data['AUSDT'] = data['AUSDT'].iloc[-7:]
    rankings = trend.daily_volume_rankings(data, records, ASOF, top_n=2)
    pools = trend.build_pools(rankings, ASOF, set(data))
    assert pools['7d'] == ['AUSDT','BUSDT']
    assert pools['14d'] == ['BUSDT']


@pytest.mark.parametrize('problem', ['gap','duplicate','nan','negative','stale'])
def test_volume_bad_coverage_blocks_ranking(problem):
    data = frames()
    if problem == 'gap': data['AUSDT'] = data['AUSDT'].drop(10)
    if problem == 'duplicate': data['AUSDT'] = pd.concat([data['AUSDT'], data['AUSDT'].iloc[[10]]])
    if problem == 'nan': data['AUSDT'].loc[10, 'quote_volume'] = float('nan')
    if problem == 'negative': data['AUSDT'].loc[10, 'quote_volume'] = -1
    if problem == 'stale': data['AUSDT']['timestamp'] -= pd.Timedelta(days=1)
    with pytest.raises(ValueError):
        trend.daily_volume_rankings(data, [meta(s) for s in data], ASOF, top_n=2)


def test_unknown_lifetime_rejected_and_future_day_ignored():
    data = frames()
    records = [meta(s) for s in data]
    records[0]['onboardDate'] = None
    with pytest.raises(ValueError):
        trend.daily_volume_rankings(data, records, ASOF, top_n=2)
    data['AUSDT'] = pd.concat([data['AUSDT'], pd.DataFrame({'timestamp':[ASOF+pd.Timedelta(days=1)], 'quote_volume':[-1]})])
    ranks = trend.daily_volume_rankings(data, [meta(s) for s in data], ASOF, top_n=2)
    assert len(ranks) == 30


def test_volume_ties_are_symbol_order_and_no_padding():
    data = frames()
    data['BUSDT']['quote_volume'] = 300
    ranks = trend.daily_volume_rankings(data, [meta(s) for s in data], ASOF, top_n=2)
    assert [r['symbol'] for r in ranks['2026-09-06']] == ['AUSDT','BUSDT']
    with pytest.raises(ValueError):
        trend.daily_volume_rankings(data, [meta(s) for s in data], ASOF, top_n=4)
    assert trend.build_pools(ranks, ASOF, set())['7d'] == []


def test_pending_needs_explicit_unopened_evidence():
    data = frames()
    records = [meta(s) for s in data]
    records[0]['status'] = 'PENDING_TRADING'
    data['AUSDT'] = pd.DataFrame()
    with pytest.raises(ValueError):
        trend.daily_volume_rankings(data, records, ASOF, top_n=2)
    ranks = trend.daily_volume_rankings(data, records, ASOF, top_n=2, confirmed_unopened={'AUSDT'})
    assert [r['symbol'] for r in ranks['2026-09-06']] == ['BUSDT','CUSDT']


def test_pool_input_missing_day_or_duplicate_symbol_fails():
    data = frames()
    ranks = trend.daily_volume_rankings(data, [meta(s) for s in data], ASOF, top_n=2)
    broken = dict(ranks)
    broken.pop('2026-09-03')
    with pytest.raises(ValueError): trend.build_pools(broken, ASOF, set(data))
    ranks['2026-09-03'][1]['symbol'] = 'AUSDT'
    with pytest.raises(ValueError): trend.build_pools(ranks, ASOF, set(data))


def test_percentile_denominator_includes_valid_downtrends():
    r = trend.rank_period(['UPUSDT','FLATUSDT','DOWNUSDT'],
                          {'UPUSDT':prices(.01),'FLATUSDT':prices(0),'DOWNUSDT':prices(-.01)}, ASOF, 7)
    by = {row['symbol']:row for row in r['rows']}
    assert r['valid_count'] == 3 and r['uptrend_count'] == 1
    assert by['FLATUSDT']['percentiles']['return'] == pytest.approx(200/3)
    assert by['DOWNUSDT']['percentiles']['return'] == pytest.approx(100/3)
    assert [row['symbol'] for row in r['top10']] == ['UPUSDT']
    assert set(by['UPUSDT']['percentiles']) == {'return','er','r_squared','drawdown'}
    assert by['UPUSDT']['score'] == pytest.approx(sum(by['UPUSDT']['percentiles'][k]*v for k,v in trend.WEIGHTS.items()))


def test_bad_prices_unavailable_without_replacement():
    r = trend.rank_period(['GOODUSDT','BADUSDT'], {'GOODUSDT':prices(),'BADUSDT':prices().iloc[:-1]}, ASOF, 30)
    assert (r['pool_size'],r['valid_count']) == (2,1)
    assert next(x for x in r['rows'] if x['symbol']=='BADUSDT')['status'] == 'unavailable'
    with pytest.raises(ValueError): trend.rank_period(['BADUSDT'],{},ASOF,7)
    assert trend.rank_period([],{},ASOF,7)['top10'] == []
    assert trend.rank_period(['DOWNUSDT'],{'DOWNUSDT':prices(-.01)},ASOF,7)['top10'] == []


class FakeMarket:
    def __init__(self, bad_price=False):
        self.requests = 0
        self.frames = frames()
        self.history_calls = []
        self.bad_price = bad_price
    def catalog(self, as_of):
        return [meta(s) for s in self.frames], set(self.frames), {'method':'frozen verified fixture'}
    def cached(self, symbol): return self.frames[symbol].copy()
    def fetch_daily(self, symbol, start, end):
        raise AssertionError('complete cache should be reused')
    def has_archive_activity(self, symbol, start, end): return False
    def fetch(self, symbol, limit, interval):
        self.history_calls.append(symbol)
        if self.bad_price: raise RuntimeError('no prices')
        return pd.DataFrame({'timestamp':prices().index,'close':prices().values})


def test_build_report_reuses_cache_and_fetches_nested_pool_union_once():
    market = FakeMarket()
    before = market.frames['AUSDT'].copy()
    r = trend.build_report(market,ASOF,top_n=2)
    assert market.history_calls == ['AUSDT','BUSDT']
    pd.testing.assert_frame_equal(before,market.frames['AUSDT'])
    assert r['top_n']==2 and r['schema_version']==1
    assert r['periods']['30d']['pool_size']==2
    assert r['weights']==trend.WEIGHTS
    json.dumps(r, allow_nan=False)


def test_live_run_saves_before_send_and_dry_run_never_sends(monkeypatch,tmp_path):
    sent=[]
    def send(text):
        assert len(list(tmp_path.glob('crypto_trend_top10*.md')))==3
        assert len(list(tmp_path.glob('crypto_trend_top10*.json')))==1
        sent.append(text)
        return True
    market=FakeMarket()
    report=trend.build_report(market,ASOF,top_n=2)
    monkeypatch.setattr(trend,'build_report',lambda *args,**kw: report)
    monkeypatch.setattr(trend,'TrendMarket',lambda *args,**kw:market)
    monkeypatch.setattr(trend.importlib,'import_module',lambda name:SimpleNamespace(send_telegram_alert=send))
    trend.run(tmp_path,tmp_path,dry_run=True)
    assert sent==[]
    trend.run(tmp_path,tmp_path)
    assert ['30天' in sent[0], '14天' in sent[1], '7天' in sent[2]] == [True]*3
    assert all(len(t)<4000 and 'RS' not in t and 'Beta' not in t for t in sent)


def test_live_run_send_failure_propagates(monkeypatch,tmp_path):
    report=trend.build_report(FakeMarket(),ASOF,top_n=2)
    monkeypatch.setattr(trend,'build_report',lambda *args,**kw:report)
    monkeypatch.setattr(trend,'TrendMarket',lambda *args,**kw:FakeMarket())
    monkeypatch.setattr(trend.importlib,'import_module',lambda name:SimpleNamespace(send_telegram_alert=lambda text:False))
    with pytest.raises(RuntimeError,match='发送失败'): trend.run(tmp_path,tmp_path)


def test_existing_daily_entry_dispatches_new_pipeline(monkeypatch,tmp_path):
    from scripts import crypto_daily_rankings as daily
    marker={'new_pipeline':True}
    monkeypatch.setattr(trend,'run',lambda scanner_dir,output_dir,dry_run=False:marker)
    assert daily.run(tmp_path,tmp_path,dry_run=True) is marker


def test_volume_network_failure_blocks_before_price_fetch():
    market=FakeMarket()
    market.frames['AUSDT']=market.frames['AUSDT'].iloc[:-1]
    def failed(*args):raise RuntimeError('network failure')
    market.fetch_daily=failed
    with pytest.raises(ValueError,match='历史成交额覆盖不完整'):trend.build_report(market,ASOF,top_n=2)
    assert market.history_calls==[]


@pytest.mark.parametrize('fail_at',[2,3])
def test_later_send_failure_propagates_and_stops(monkeypatch,tmp_path,fail_at):
    report=trend.build_report(FakeMarket(),ASOF,top_n=2)
    sent=[]
    def send(text):
        sent.append(text)
        return len(sent)!=fail_at
    monkeypatch.setattr(trend,'build_report',lambda *args,**kw:report)
    monkeypatch.setattr(trend,'TrendMarket',lambda *args,**kw:FakeMarket())
    monkeypatch.setattr(trend.importlib,'import_module',lambda name:SimpleNamespace(send_telegram_alert=send))
    with pytest.raises(RuntimeError,match='发送失败'):trend.run(tmp_path,tmp_path)
    assert len(sent)==fail_at
