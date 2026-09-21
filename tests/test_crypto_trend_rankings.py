import json
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

from scripts import crypto_trend_rankings as trend

ASOF = pd.Timestamp('2026-09-06', tz='UTC')

# Historical mathematical fixtures explicitly request v1; live defaults are tested below.
def legacy_rank_period(*args, **kwargs):
    if len(args) < 5: kwargs.setdefault('scoring_version','v1')
    return trend.rank_period(*args, **kwargs)



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


def test_majority_days_allows_an_occasional_day_outside_top100():
    data = frames()
    # B has one day outside Top2, 10 days ago, and qualifies again recently.
    data['BUSDT'].loc[19, 'quote_volume'] = 50
    rankings = trend.daily_volume_rankings(data, [meta(s) for s in data], ASOF, top_n=2)
    pools = trend.build_pools(rankings, ASOF, set(data))
    assert pools == {'7d':['AUSDT','BUSDT'], '14d':['AUSDT','BUSDT'], '30d':['AUSDT','BUSDT']}
    assert len(rankings) == 30


@pytest.mark.parametrize('days,minimum',[(7,4),(14,8),(30,16)])
def test_strict_majority_boundary_uses_full_window_denominator(days,minimum):
    data={}
    for i,day in enumerate(pd.date_range(end=ASOF,periods=days)):
        symbols=['ALWAYSUSDT','PASSUSDT' if i<minimum else 'LATEUSDT',
                 'FAILUSDT' if i<minimum-1 else 'RESTUSDT']
        data[str(day.date())]=[dict(symbol=s,rank=j) for j,s in enumerate(symbols,1)]
    pool=trend.build_pools(data,ASOF,{'ALWAYSUSDT','PASSUSDT','FAILUSDT'},periods=(days,))[f'{days}d']
    assert pool==['ALWAYSUSDT','PASSUSDT']


def test_majority_pools_need_not_be_nested_or_have_at_most_top_n_members():
    ranks={}
    for i,day in enumerate(pd.date_range(end=ASOF,periods=30)):
        # Long was liquid early but absent recently; Short only recently.
        symbols=['LONGUSDT'] if i<16 else ['OTHERUSDT']
        if i>=26:symbols=['SHORTUSDT']
        ranks[str(day.date())]=[dict(symbol=s,rank=1) for s in symbols]
    pools=trend.build_pools(ranks,ASOF,{'LONGUSDT','SHORTUSDT','OTHERUSDT'})
    assert pools['7d']==['SHORTUSDT']
    assert pools['30d']==['LONGUSDT']
    # Top2 each day, but three symbols can each appear on 4 of the 7 days.
    seq=[['A','B'],['A','B'],['A','C'],['A','C'],['B','C'],['B','C'],['D','E']]
    seven={str(d.date()):[dict(symbol=s,rank=j) for j,s in enumerate(names,1)]
           for d,names in zip(pd.date_range(end=ASOF,periods=7),seq)}
    assert trend.build_pools(seven,ASOF,set('ABCDE'),periods=(7,))['7d']==['A','B','C']


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
    r = legacy_rank_period(['UPUSDT','FLATUSDT','DOWNUSDT'],
                          {'UPUSDT':prices(.01),'FLATUSDT':prices(0),'DOWNUSDT':prices(-.01)}, ASOF, 7)
    by = {row['symbol']:row for row in r['rows']}
    assert r['valid_count'] == 3 and r['uptrend_count'] == 1
    assert by['FLATUSDT']['percentiles']['return'] == pytest.approx(200/3)
    assert by['DOWNUSDT']['percentiles']['return'] == pytest.approx(100/3)
    assert [row['symbol'] for row in r['top10']] == ['UPUSDT']
    assert set(by['UPUSDT']['percentiles']) == {'return','er','r_squared','drawdown'}
    assert by['UPUSDT']['score'] == pytest.approx(sum(by['UPUSDT']['percentiles'][k]*v for k,v in trend.WEIGHTS.items()))


def test_bad_prices_unavailable_without_replacement():
    r = legacy_rank_period(['GOODUSDT','BADUSDT'], {'GOODUSDT':prices(),'BADUSDT':prices().iloc[:-1]}, ASOF, 30)
    assert (r['pool_size'],r['valid_count']) == (2,1)
    assert next(x for x in r['rows'] if x['symbol']=='BADUSDT')['status'] == 'unavailable'
    with pytest.raises(ValueError): legacy_rank_period(['BADUSDT'],{},ASOF,7)
    assert legacy_rank_period([],{},ASOF,7)['top10'] == []
    assert legacy_rank_period(['DOWNUSDT'],{'DOWNUSDT':prices(-.01)},ASOF,7)['top10'] == []


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


def test_build_report_reuses_cache_and_fetches_pool_union_once():
    market = FakeMarket()
    before = market.frames['AUSDT'].copy()
    r = trend.build_report(market,ASOF,top_n=2,scoring_version='v1')
    assert market.history_calls == ['AUSDT','BUSDT']
    pd.testing.assert_frame_equal(before,market.frames['AUSDT'])
    assert r['top_n']==2 and r['schema_version']==2
    assert r['periods']['30d']['pool_size']==2
    assert [r['periods'][f'{h}d']['min_top100_days'] for h in (7,14,30)]==[4,8,16]
    assert r['periods']['7d']['top100_day_counts']=={'AUSDT':7,'BUSDT':7}
    assert '至少4/7天' in trend.message(r,7)
    assert '交集' not in trend.message(r,7)
    assert r['weights']==trend.WEIGHTS
    json.dumps(r, allow_nan=False)


def test_live_run_saves_before_send_and_dry_run_never_sends(monkeypatch,tmp_path):
    sent=[]
    def send(text):
        assert len(list(tmp_path.glob('crypto_trend_top10*.md')))==2
        assert len(list(tmp_path.glob('crypto_trend_top10*.json')))==1
        sent.append(text)
        return True
    market=FakeMarket()
    report=trend.build_report(market,ASOF,top_n=2,scoring_version='v2',periods=(10,14))
    monkeypatch.setattr(trend,'build_report',lambda *args,**kw: report)
    monkeypatch.setattr(trend,'TrendMarket',lambda *args,**kw:market)
    monkeypatch.setattr(trend.importlib,'import_module',lambda name:SimpleNamespace(send_telegram_alert=send))
    trend.run(tmp_path,tmp_path,dry_run=True)
    assert sent==[]
    trend.run(tmp_path,tmp_path)
    assert len(sent)==2 and '10天' in sent[0] and '14天' in sent[1]
    assert all('绝对涨跌幅50%' in text and '2026-09-07 08:00' in text for text in sent)
    assert all(len(t)<4000 and 'RS' not in t and 'Beta' not in t for t in sent)


def test_live_run_send_failure_propagates(monkeypatch,tmp_path):
    report=trend.build_report(FakeMarket(),ASOF,top_n=2,scoring_version='v2',periods=(10,14))
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


@pytest.mark.parametrize('fail_at',[1,2])
def test_later_send_failure_propagates_and_stops(monkeypatch,tmp_path,fail_at):
    report=trend.build_report(FakeMarket(),ASOF,top_n=2,scoring_version='v2',periods=(10,14))
    sent=[]
    def send(text):
        sent.append(text)
        return len(sent)!=fail_at
    monkeypatch.setattr(trend,'build_report',lambda *args,**kw:report)
    monkeypatch.setattr(trend,'TrendMarket',lambda *args,**kw:FakeMarket())
    monkeypatch.setattr(trend.importlib,'import_module',lambda name:SimpleNamespace(send_telegram_alert=send))
    with pytest.raises(RuntimeError,match='发送失败'):trend.run(tmp_path,tmp_path)
    assert len(sent)==fail_at


@pytest.mark.parametrize('pending,has_history,passes',[(True,False,True),(True,True,False),(False,False,False)])
def test_invalid_symbol_status_needs_pending_and_no_archive(pending,has_history,passes):
    from scripts.crypto_trend_market import InactiveSymbolError
    market=FakeMarket()
    records=[meta(s) for s in market.frames]
    records[0]['status']='PENDING_TRADING' if pending else 'TRADING'
    market.frames['AUSDT']=pd.DataFrame()
    market.catalog=lambda asof:(records,{'BUSDT','CUSDT'}, {'method':'fixture'})
    def invalid(*args):raise InactiveSymbolError('explicit -1122')
    market.fetch_daily=invalid
    market.has_archive_activity=lambda *args:has_history
    if passes:
        report=trend.build_report(market,ASOF,top_n=2)
        assert report['confirmed_unopened']==['AUSDT']
        assert report['periods']['7d']['pool']==['BUSDT','CUSDT']
    else:
        with pytest.raises(ValueError,match='历史成交额覆盖不完整'):trend.build_report(market,ASOF,top_n=2)


def test_mathematical_score_ties_use_symbol_not_floating_sum_noise(monkeypatch):
    values={
        'AUSDT':{'return':.01,'er':.1,'r_squared':.9,'drawdown':.01,'slope':.01},
        'BUSDT':{'return':.02,'er':.2,'r_squared':.1,'drawdown':.02,'slope':.01},
        'CUSDT':{'return':.03,'er':.3,'r_squared':.5,'drawdown':.03,'slope':.01},
    }
    monkeypatch.setattr(trend,'trend_metrics',lambda sample:values[sample.name])
    report=legacy_rank_period(list(values),{s:prices().rename(s) for s in values},ASOF,7)
    assert [r['symbol'] for r in report['ranked']]==['CUSDT','AUSDT','BUSDT']
    assert [r['score'] for r in report['ranked']]==[80.,60.,60.]


def test_explicit_v1_weights_and_message_remain_reproducible():
    """The shared kernel must keep the exact daily 40/20/20/20 behavior."""
    assert trend.WEIGHTS=={'return':.4,'er':.2,'r_squared':.2,'drawdown':.2}
    report=trend.build_report(FakeMarket(),ASOF,top_n=2,scoring_version='v1')
    assert report['weights']==trend.WEIGHTS
    msg=trend.message(report,7)
    assert '权重：涨幅40% / ER20% / R²20% / 回撤20%' in msg
    assert '成交额10%' not in msg
    rows=[dict(symbol='A',**{'return':1.0,'er':1.0,'r_squared':1.0,'drawdown':0.0})]
    trend.score_valid_rows(rows)
    assert set(rows[0]['percentiles'])=={'return','er','r_squared','drawdown'}
    assert rows[0]['score']==pytest.approx(100.0)


def test_selected_daily_windows_need_only_fourteen_volume_days():
    market=FakeMarket()
    market.frames={s:f.iloc[-14:].copy() for s,f in market.frames.items()}
    report=trend.build_report(market,ASOF,top_n=2,scoring_version='v2',periods=(10,14))
    assert list(report['periods'])==['10d','14d']
    assert len(report['daily_top100'])==14
    assert report['periods']['10d']['min_top100_days']==6
    assert report['periods']['14d']['min_top100_days']==8
    assert report['weights']==trend.STRENGTH_WEIGHTS


def test_live_entry_explicitly_selects_only_ten_and_fourteen(monkeypatch,tmp_path):
    selected=[]
    def build(market,asof,**kw):
        selected.append(kw)
        return trend_build(FakeMarket(),ASOF,top_n=2,**kw)
    trend_build=trend.build_report
    monkeypatch.setattr(trend,'build_report',build)
    monkeypatch.setattr(trend,'TrendMarket',lambda *args,**kw:FakeMarket())
    monkeypatch.setattr(trend.importlib,'import_module',lambda name:SimpleNamespace(send_telegram_alert=lambda text:True))
    result=trend.run(tmp_path,tmp_path,dry_run=True)
    assert selected==[dict(scoring_version='v4',periods=(10,14))]
    assert set(result['periods'])=={'10d','14d'}
    assert not list(tmp_path.glob('*30d*')) and not list(tmp_path.glob('*7d*'))


def test_btc_is_excluded_before_daily_scoring_and_needs_no_price():
    result = legacy_rank_period(['BTCUSDT', 'ALTUSDT'], {'ALTUSDT': prices(.01)},
                               ASOF, 14, 'v2', {'ALTUSDT': 1e8})
    assert result['pool'] == ['ALTUSDT']
    assert result['valid_count'] == result['pool_size'] == 1
    assert [r['symbol'] for r in result['rows']] == ['ALTUSDT']
    assert result['top10'][0]['score'] == 100
    assert legacy_rank_period(['BTCUSDT'], {}, ASOF, 14)['pool'] == []


def test_shared_daily_scoring_removes_btc_before_percentiles():
    rows = [dict(symbol=s, status='ok', **{'return': r}, er=r, r_squared=r,
                 drawdown=1-r) for s, r in [('BTCUSDT', .9), ('ALTUSDT', .1)]]
    trend.score_rows(rows, 'v2', {'ALTUSDT': 1e8})
    assert [r['symbol'] for r in rows] == ['ALTUSDT']
    assert rows[0]['score'] == 100


def test_report_drops_btc_before_price_fetch_but_keeps_market_volume_reference():
    market = FakeMarket()
    market.frames['BTCUSDT'] = market.frames.pop('AUSDT')
    report = trend.build_report(market, ASOF, top_n=2, scoring_version='v2', periods=(10,14))
    assert market.history_calls == ['BUSDT']
    assert report['excluded_trading_symbols'] == ['BTCUSDT']
    assert report['daily_top100'][str(ASOF.date())][0]['symbol'] == 'BTCUSDT'
    for h in (10,14):
        assert report['periods'][f'{h}d']['pool'] == ['BUSDT']
        assert report['periods'][f'{h}d']['top10'][0]['score'] == 100
        assert '永久排除：BTCUSDT' in trend.message(report,h)
