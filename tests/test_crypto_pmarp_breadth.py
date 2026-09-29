import json
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

from scripts import crypto_pmarp_breadth as breadth

ASOF = pd.Timestamp('2026-09-27', tz='UTC')
DAY = pd.Timedelta(days=1)


def meta(symbol, start='2020-01-01', end='2100-01-01', kind='COIN'):
    return dict(symbol=symbol, underlyingType=kind, quoteAsset='USDT',
                contractType='PERPETUAL', status='TRADING',
                onboardDate=int(pd.Timestamp(start, tz='UTC').timestamp()*1000),
                deliveryDate=int(pd.Timestamp(end, tz='UTC').timestamp()*1000))


def indicator(prices, ema_period=20, lookback=150):
    # Independent mathematical oracle (recursive EMA, NumPy comparison).
    values = np.asarray(prices, dtype=float)
    ema = np.empty(len(values)); ema[0] = values[0]
    alpha = 2/(ema_period+1)
    for i in range(1, len(values)):
        ema[i] = alpha*values[i] + (1-alpha)*ema[i-1]
    ratios = values/ema
    out = np.full(len(values), np.nan)
    for i in range(lookback, len(values)):
        out[i] = 100*np.mean(ratios[i-lookback:i] <= ratios[i])
    return pd.Series(out)


class Market:
    def __init__(self, records=None):
        self.records = records or [meta('BTCUSDT'), meta('ETHUSDT')]
        self.scanner = SimpleNamespace(calculate_pmarp=indicator)
        self.requests = 0
        self.archive_requests = 0
        self.pending_symbols = set()
        self.calls = []
        self.transform = lambda s, f: f
    def catalog(self, as_of):
        return self.records, {'BTCUSDT'}, {'method':'fixture historical reconstruction'}
    def fetch_daily(self, symbol, start, end, **kwargs):
        self.calls.append((symbol,start,end,kwargs))
        record = next(r for r in self.records if r['symbol']==symbol)
        first = max(start, pd.Timestamp(record['onboardDate'],unit='ms',tz='UTC').normalize())
        last = min(end, pd.Timestamp(record['deliveryDate'],unit='ms',tz='UTC').ceil('D'))
        dates = pd.date_range(first, last-DAY, freq='D')
        # Sufficient curvature to avoid asymptotic floating-point ties.
        x=np.arange(len(dates),dtype=float)
        close=np.exp((1 if symbol=='BTCUSDT' else -1)*x*x/1e6)
        return self.transform(symbol,pd.DataFrame({'timestamp':dates,'close':close}))


def test_full_year_separate_breadths_include_btc_and_not_only_active():
    market=Market([meta('BTCUSDT'),meta('ETHUSDT',end='2026-09-20'),
                   meta('GOLDUSDT',kind='COMMODITY')])
    result=breadth.build_report(market,ASOF)
    rows=result['history']
    assert len(rows)==366
    assert rows[0]['date']=='2025-09-27'
    assert rows[-1]['valid_count']==1
    assert rows[-9]['valid_count']==2
    assert rows[-1]['strong_count']==1 and rows[-1]['weak_count']==0
    assert rows[-1]['strong_pct']==100
    assert result['strong_percentile']==100
    assert result['weak_percentile']==pytest.approx(100*7/365)
    assert {x[0] for x in market.calls}=={'BTCUSDT','ETHUSDT'}
    assert result['lookback_days']==365


def test_percentile_excludes_today_and_uses_right_inclusive_ties():
    assert breadth.percentile(20,[10,20,30,40])==50
    assert breadth.percentile(0,[0]*365)==100
    with pytest.raises(ValueError): breadth.percentile(1,[])
    with pytest.raises(ValueError): breadth.percentile(1,[np.nan])


def test_new_listing_enters_only_after_150_previous_daily_bars():
    start=str((ASOF-150*DAY).date())
    result=breadth.build_report(Market([meta('BTCUSDT'),meta('NEWUSDT',start=start)]),ASOF)
    assert result['history'][-2]['valid_count']==1
    assert result['history'][-2]['warmup_count']==1
    assert result['history'][-1]['valid_count']==2
    assert result['history'][-1]['warmup_count']==0


@pytest.mark.parametrize('problem',['gap','duplicate','nan','zero','empty','off_grid'])
def test_invalid_prices_do_not_shrink_the_denominator(problem):
    market=Market()
    def damage(symbol, frame):
        if symbol!='ETHUSDT':return frame
        if problem=='gap':return frame.drop(frame.index[-20])
        if problem=='duplicate':return pd.concat([frame,frame.iloc[[-20]]])
        if problem=='nan':frame.loc[frame.index[-20],'close']=np.nan
        if problem=='zero':frame.loc[frame.index[-20],'close']=0
        if problem=='empty':return pd.DataFrame()
        if problem=='off_grid':frame.loc[frame.index[-20],'timestamp']+=pd.Timedelta(hours=1)
        return frame
    market.transform=damage
    with pytest.raises((ValueError,RuntimeError)):
        breadth.build_report(market,ASOF)


def test_future_candles_and_prelisting_padding_cannot_enter_calculation():
    market=Market()
    def append(symbol,frame):
        return pd.concat([frame,pd.DataFrame({'timestamp':[ASOF+DAY], 'close':[np.nan]})])
    market.transform=append
    result=breadth.build_report(market,ASOF)
    assert result['history'][-1]['valid_count']==2
    for _,start,end,kw in market.calls:
        assert end==ASOF+DAY
        assert (end-start).days<=1000
        assert kw['limit']==1000


def test_thresholds_are_inclusive_and_percentile_uses_fraction_not_rounded_display():
    market=Market()
    market.scanner.calculate_pmarp=lambda prices,**kw: pd.Series(
        [np.nan]*150+[98 if prices.iloc[-1]>1 else 2]*(len(prices)-150))
    result=breadth.build_report(market,ASOF)
    assert result['history'][-1]['strong_count']==1
    assert result['history'][-1]['weak_count']==1
    assert result['history'][-1]['strong_pct']==50
    assert result['history'][-1]['weak_pct']==50


def test_catalog_failure_never_becomes_current_universe_replay():
    market=Market()
    def fail(*args):raise ValueError('期内归档合约元数据缺失: GONEUSDT')
    market.catalog=fail
    with pytest.raises(ValueError,match='GONEUSDT'):breadth.build_report(market,ASOF)
    assert not market.calls


def test_message_shows_date_counts_two_percentiles_and_tie_convention():
    report=breadth.build_report(Market(),ASOF)
    text=breadth.message(report)
    assert '2026-09-27' in text and '365' in text and 'BTC' in text
    assert '≥98' in text and '≤2' in text and '1/2' in text
    assert len(text)<4000


@pytest.mark.parametrize('weak_rank,alert', [(89.9,False),(90.0,False),(90.1,True),(100.0,True)])
def test_weak_percentile_alert_is_strict_and_prominent(weak_rank,alert):
    report=breadth.build_report(Market(),ASOF)
    report['strong_percentile']=100.0
    report['weak_percentile']=weak_rank
    # Trigger is the weak historical percentile, not the raw breadth percentage.
    report['current']['weak_pct']=0.5 if alert else 95.0
    text=breadth.message(report)
    weak_line=next(line for line in text.splitlines() if '极弱 ≤2：' in line)
    if alert:
        assert text.startswith('*🚨🚨 极弱宽度高位警报 | 一年分位 > P90 🚨🚨*\n')
        assert weak_line.startswith('*极弱 ≤2：') and weak_line.endswith('*')
        assert f'P{weak_rank:.1f}' in weak_line
    else:
        assert '🚨' not in text
        assert not weak_line.startswith('*')


def test_run_dry_run_saves_artifacts_without_sending(tmp_path,monkeypatch):
    report=breadth.build_report(Market(),ASOF)
    sent=[]
    scanner=SimpleNamespace(send_telegram_alert=lambda text:sent.append(text) or True,
                            CONFIG={'ema_period':20,'lookback':150})
    monkeypatch.setattr(breadth,'BreadthMarket',lambda *a,**k:Market())
    result=breadth.run(tmp_path,tmp_path,dry_run=True,scanner=scanner,as_of=ASOF)
    assert result['as_of']==report['as_of'] and not sent
    assert json.loads((tmp_path/'crypto_pmarp_breadth_2026-09-27.json').read_text())['status']=='ok'


def test_failure_artifact_is_unavailable_and_send_failure_propagates(tmp_path,monkeypatch):
    sent=[]
    scanner=SimpleNamespace(send_telegram_alert=lambda text:sent.append(text) or True,
                            CONFIG={'ema_period':20,'lookback':150})
    monkeypatch.setattr(breadth,'BreadthMarket',lambda *a,**k:Market())
    def fail(*a,**k):raise ValueError('missing historical metadata')
    monkeypatch.setattr(breadth,'build_report',fail)
    with pytest.raises(RuntimeError,match='不可用'):
        breadth.run(tmp_path,tmp_path,scanner=scanner,as_of=ASOF)
    payload=json.loads((tmp_path/'crypto_pmarp_breadth_2026-09-27.json').read_text())
    assert payload['status']=='unavailable' and 'strong_percentile' not in payload
    assert len(sent)==1 and '不可用' in sent[0]


def test_intraday_delisting_does_not_count_as_a_closed_daily_contract():
    market=Market([meta('BTCUSDT'),meta('ETHUSDT',end='2026-09-27 06:30')])
    result=breadth.build_report(market,ASOF)
    assert result['current']['valid_count']==1
    assert result['history'][-2]['valid_count']==2


def test_daily_runner_sends_trends_then_breadth_using_same_day(monkeypatch,tmp_path):
    from scripts import crypto_daily_rankings as daily
    from scripts import crypto_trend_rankings as trend
    calls=[]
    report={'as_of':str(ASOF.date())}
    monkeypatch.setattr(trend,'run',lambda *a,**kw:calls.append(('trend',kw)) or report)
    monkeypatch.setattr(breadth,'run',lambda *a,**kw:calls.append(('breadth',kw)) or {})
    assert daily.run(tmp_path,tmp_path,dry_run=True) is report
    assert [c[0] for c in calls]==['trend','breadth']
    assert calls[1][1]=={'dry_run':True,'as_of':report['as_of']}


def test_retired_source_is_hash_checked_and_never_falls_back_silently(tmp_path):
    import hashlib
    raw=json.dumps([[0,'1','1','1','1','0',0,'0']]).encode()
    (tmp_path/'GONEUSDT.json').write_bytes(raw)
    market=object.__new__(breadth.BreadthMarket)
    market.retired_dir=tmp_path
    market.retired_sources={'GONEUSDT':{'sha256':hashlib.sha256(raw).hexdigest()}}
    market.scanner=SimpleNamespace(klines_to_dataframe=lambda value:pd.DataFrame(value))
    assert len(market.fetch_daily('GONEUSDT',ASOF,ASOF+DAY))==1
    (tmp_path/'GONEUSDT.json').write_text('[]')
    with pytest.raises(ValueError,match='hash'):market.fetch_daily('GONEUSDT',ASOF,ASOF+DAY)


def test_retirement_exclusion_must_predate_requested_window(tmp_path,monkeypatch):
    from tests.test_crypto_trend_market import Scanner, metadata
    scanner=Scanner()
    supplements=[metadata('GONEUSDT')]
    supplements[0]['deliveryDate']=int((ASOF-10*DAY).timestamp()*1000)
    market=breadth.BreadthMarket(scanner,tmp_path,ASOF,history_days=366,
                                manifest={'symbols':supplements,
                                          'archive_retirements':{'BTCSTUSDT':{'deliveryDate':1615514400000,'source':'official'}},
                                          'retired_prices':{}},retired_dir=tmp_path)
    monkeypatch.setattr(market,'archive_symbols',lambda:{'AUSDT','GONEUSDT','BTCSTUSDT','UNKNOWNUSDT'})
    monkeypatch.setattr(market,'has_archive_activity',lambda *a:True)
    with pytest.raises(ValueError,match='UNKNOWNUSDT'):market.catalog(ASOF)
    monkeypatch.setattr(market,'archive_symbols',lambda:{'AUSDT','GONEUSDT','BTCSTUSDT'})
    records,_,ev=market.catalog(ASOF)
    assert {m['symbol'] for m in records}=={'AUSDT','GONEUSDT'}
    assert 'BTCSTUSDT' in ev['archive_retirements']


@pytest.mark.parametrize('has_archive',[False,True])
def test_pending_invalid_status_needs_archive_proof_before_exclusion(has_archive):
    from scripts.crypto_trend_market import InactiveSymbolError
    pending=meta('PENDINGUSDT')
    pending['status']='PENDING_TRADING'
    market=Market([meta('BTCUSDT'),pending])
    market.pending_symbols={'PENDINGUSDT'}
    fetch=market.fetch_daily
    def get(symbol,*a,**k):
        if symbol=='PENDINGUSDT':raise InactiveSymbolError('exchange -1122')
        return fetch(symbol,*a,**k)
    market.fetch_daily=get
    market.has_archive_activity=lambda *a:has_archive
    if has_archive:
        with pytest.raises(ValueError,match='exchange -1122'):breadth.build_report(market,ASOF)
    else:
        result=breadth.build_report(market,ASOF)
        assert result['confirmed_unopened']==['PENDINGUSDT']
        assert all(row['eligible_count']==1 for row in result['history'])


def test_cli_runner_rejects_unclosed_daily_bar_before_network(tmp_path):
    with pytest.raises(ValueError,match='已收盘'):
        breadth.run(tmp_path,tmp_path,dry_run=True,scanner=SimpleNamespace(),
                    as_of=pd.Timestamp.now(tz='UTC').normalize())


def test_trend_failure_still_runs_breadth_and_both_failures_are_reported(monkeypatch,tmp_path):
    from scripts import crypto_daily_rankings as daily
    from scripts import crypto_trend_rankings as trend
    calls=[]
    def bad_trend(*a,**k):raise ValueError('trend broken')
    def bad_breadth(*a,**k):calls.append(k);raise ValueError('breadth broken')
    monkeypatch.setattr(trend,'run',bad_trend)
    monkeypatch.setattr(breadth,'run',bad_breadth)
    with pytest.raises(RuntimeError,match='trend broken.*breadth broken'):
        daily.run(tmp_path,tmp_path,dry_run=True)
    assert len(calls)==1
    assert list(tmp_path.glob('crypto_daily_status_*.json'))


def test_first_comparison_day_intraday_retirement_never_fetches_prices():
    last=str((ASOF-365*DAY+pd.Timedelta(hours=6)).isoformat())
    gone=meta('GONEUSDT');gone['deliveryDate']=int(pd.Timestamp(last).timestamp()*1000)
    market=Market([meta('BTCUSDT'),gone])
    fetch=market.fetch_daily
    def get(symbol,*a,**k):
        assert symbol!='GONEUSDT'
        return fetch(symbol,*a,**k)
    market.fetch_daily=get
    report=breadth.build_report(market,ASOF)
    assert set(report['constituents'])=={'BTCUSDT'}


def test_price_errors_are_collected_across_all_contracts():
    market=Market([meta('BTCUSDT'),meta('BAD1USDT'),meta('BAD2USDT')])
    fetch=market.fetch_daily
    def get(symbol,*a,**k):
        if symbol.startswith('BAD'):raise ValueError('bad '+symbol)
        return fetch(symbol,*a,**k)
    market.fetch_daily=get
    with pytest.raises(ValueError,match='BAD1USDT.*BAD2USDT'):
        breadth.build_report(market,ASOF)


def test_bundled_retired_prices_default_and_preflight_lists_missing_files(tmp_path):
    manifest=json.loads(breadth.MANIFEST_PATH.read_text())
    with pytest.raises(ValueError,match='AERGOUSDT.*BDXNUSDT.*SXPUSDT'):
        breadth.BreadthMarket(SimpleNamespace(),tmp_path,ASOF,manifest=manifest,retired_dir=tmp_path)
