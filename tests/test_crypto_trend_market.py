import json
from types import SimpleNamespace

import pandas as pd
import pytest
import requests

from scripts.crypto_trend_market import TrendMarket, eligible_metadata

ASOF = pd.Timestamp('2026-09-06', tz='UTC')


def metadata(symbol='AUSDT'):
    return dict(symbol=symbol, quoteAsset='USDT', underlyingType='COIN',
                contractType='PERPETUAL', status='TRADING',
                onboardDate=1500000000000, deliveryDate=4133404800000)


class Scanner:
    CONFIG = {'base_url':'https://fixture.invalid'}
    def __init__(self):
        self.reply = {'symbols':[metadata()]}
        self.calls = []
    def api_request_with_retry(self,url,params=None):
        self.calls.append((url,params))
        return self.reply
    def klines_to_dataframe(self,raw):
        return pd.DataFrame({'timestamp':pd.to_datetime([r[0] for r in raw],unit='ms',utc=True),
                             'close':[float(r[4]) for r in raw], 'quote_volume':[float(r[7]) for r in raw]})


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    monkeypatch.setattr('scripts.crypto_trend_market.time.sleep', lambda seconds:None)


def test_unknown_archive_active_symbol_is_not_silently_dropped(tmp_path,monkeypatch):
    market = TrendMarket(Scanner(),tmp_path,ASOF)
    monkeypatch.setattr(market,'archive_symbols',lambda:{'AUSDT','GONEUSDT'})
    monkeypatch.setattr(market,'has_archive_activity',lambda *args:True)
    with pytest.raises(ValueError,match='元数据'): market.catalog(ASOF)


def test_unknown_archive_failure_is_not_empty_evidence(tmp_path,monkeypatch):
    market = TrendMarket(Scanner(),tmp_path,ASOF)
    monkeypatch.setattr(market,'archive_symbols',lambda:{'GONEUSDT'})
    def broken(*args): raise RuntimeError('offline')
    monkeypatch.setattr(market,'has_archive_activity',broken)
    with pytest.raises(RuntimeError,match='offline'): market.catalog(ASOF)


def test_persisted_delisted_contract_remains_historical_competitor(tmp_path,monkeypatch):
    scanner=Scanner()
    market=TrendMarket(scanner,tmp_path,ASOF)
    old=metadata('GONEUSDT')
    old.update(status='SETTLING',deliveryDate=int((ASOF-pd.Timedelta(days=1)).timestamp()*1000))
    (tmp_path/'catalog_2026-09-05.json').write_text(json.dumps({'symbols':[old]}))
    monkeypatch.setattr(market,'archive_symbols',lambda:{'AUSDT','GONEUSDT'})
    records,active,evidence=market.catalog(ASOF)
    assert {r['symbol'] for r in records}=={'AUSDT','GONEUSDT'}
    assert active=={'AUSDT'}
    assert evidence['unknown_active_symbols']==[]
    assert len(scanner.calls)==1


def test_catalog_missing_lifetime_duplicate_and_corrupt_cache_fail(tmp_path,monkeypatch):
    scanner=Scanner()
    market=TrendMarket(scanner,tmp_path,ASOF)
    monkeypatch.setattr(market,'archive_symbols',lambda:set())
    scanner.reply['symbols'][0].pop('onboardDate')
    with pytest.raises(ValueError):market.catalog(ASOF)
    scanner.reply={'symbols':[metadata(),metadata()]}
    with pytest.raises(ValueError):market.catalog(ASOF)
    (tmp_path/'catalog_2026-09-05.json').write_text('{')
    scanner.reply={'symbols':[metadata()]}
    with pytest.raises(ValueError):market.catalog(ASOF)


def test_daily_fetch_time_bounds_and_empty_not_network_failure(tmp_path):
    scanner=Scanner();market=TrendMarket(scanner,tmp_path,ASOF)
    scanner.reply=[]
    assert market.fetch_daily('AUSDT',ASOF-pd.Timedelta(days=29),ASOF+pd.Timedelta(days=1)).empty
    args=scanner.calls[-1][1]
    assert args['interval']=='1d' and args['limit']==32
    assert args['endTime']==int((ASOF+pd.Timedelta(days=1)).timestamp()*1000)-1
    scanner.reply=None
    with pytest.raises(RuntimeError):market.fetch_daily('AUSDT',ASOF,ASOF+pd.Timedelta(days=1))


def test_closed_4h_fetch_is_bounded_and_reused(tmp_path):
    scanner=Scanner();market=TrendMarket(scanner,tmp_path,ASOF)
    scanner.reply=[[int(ASOF.timestamp()*1000), '1','1','1','1','1',0,'1']]
    market.fetch('AUSDT',188,interval='4h')
    market.fetch('AUSDT',188,interval='4h')
    assert len(scanner.calls)==1
    assert scanner.calls[0][1]['endTime']==int((ASOF+pd.Timedelta(days=1)).timestamp()*1000)-1


def test_xml_pagination_requires_next_marker(tmp_path,monkeypatch):
    market=TrendMarket(Scanner(),tmp_path,ASOF)
    xml='<ListBucketResult xmlns="http://s3.amazonaws.com/doc/2006-03-01/"><IsTruncated>true</IsTruncated></ListBucketResult>'
    response=SimpleNamespace(text=xml,raise_for_status=lambda:None)
    monkeypatch.setattr('scripts.crypto_trend_market.requests.get',lambda *a,**k:response)
    with pytest.raises(ValueError,match='分页'):market.archive_symbols()


def test_archive_activity_date_boundaries(tmp_path,monkeypatch):
    market=TrendMarket(Scanner(),tmp_path,ASOF)
    prefix='data/futures/um/daily/klines/GONEUSDT/1d/'
    monkeypatch.setattr(market,'archive_keys',lambda **kw:iter([prefix+'GONEUSDT-1d-2026-09-06.zip']))
    assert market.has_archive_activity('GONEUSDT',ASOF,ASOF+pd.Timedelta(days=1))
    assert not market.has_archive_activity('GONEUSDT',ASOF-pd.Timedelta(days=1),ASOF)


def archive_response(status=200, text=None):
    response = requests.Response()
    response.status_code = status
    response._content = (text or '<ListBucketResult xmlns="http://s3.amazonaws.com/doc/2006-03-01/">'
                         '<IsTruncated>false</IsTruncated><CommonPrefixes>'
                         '<Prefix>data/futures/um/daily/klines/AUSDT/</Prefix>'
                         '</CommonPrefixes></ListBucketResult>').encode()
    return response


@pytest.mark.parametrize('failure', [requests.ConnectionError('reset by peer'),
                                    requests.Timeout('timeout'), 429, 500, 502, 503, 504])
def test_archive_transient_failure_retries_same_page_and_caches_only_success(tmp_path, monkeypatch, caplog, failure):
    market = TrendMarket(Scanner(), tmp_path, ASOF)
    calls, sleeps = [], []
    def get(url, **kwargs):
        calls.append((url, kwargs))
        if len(calls) == 1:
            assert not list(tmp_path.rglob('*.xml'))
            if isinstance(failure, Exception):
                raise failure
            return archive_response(failure)
        return archive_response()
    monkeypatch.setattr('scripts.crypto_trend_market.requests.get', get)
    monkeypatch.setattr('scripts.crypto_trend_market.time.sleep', sleeps.append)
    assert list(market.archive_keys(prefix='test/', marker='page2')) == ['data/futures/um/daily/klines/AUSDT/']
    assert len(calls) == market.archive_requests == 2
    assert calls[0] == calls[1]
    assert 2 in sleeps
    assert 'test/' in caplog.text and 'page2' in caplog.text and '1/3' in caplog.text
    assert len(list(tmp_path.rglob('*.xml'))) == 1
    list(market.archive_keys(prefix='test/', marker='page2'))
    assert len(calls) == 2


@pytest.mark.parametrize('failure', [requests.ConnectionError('reset'), requests.Timeout('timeout'), 503])
def test_archive_retry_exhaustion_preserves_failure_and_writes_no_cache(tmp_path, monkeypatch, failure):
    market = TrendMarket(Scanner(), tmp_path, ASOF)
    calls = []
    def get(*args, **kwargs):
        calls.append(kwargs)
        if isinstance(failure, Exception):
            raise failure
        return archive_response(failure)
    monkeypatch.setattr('scripts.crypto_trend_market.requests.get', get)
    with pytest.raises(RuntimeError, match='归档.*3次.*test/') as error:
        list(market.archive_keys(prefix='test/'))
    assert isinstance(error.value.__cause__, requests.RequestException)
    assert len(calls) == market.archive_requests == 3
    assert not list(tmp_path.rglob('*.xml'))


@pytest.mark.parametrize('status', [400, 403, 404])
def test_archive_permanent_http_failure_does_not_retry(tmp_path, monkeypatch, status):
    market = TrendMarket(Scanner(), tmp_path, ASOF)
    calls = []
    def get(*args, **kwargs):
        calls.append(kwargs)
        return archive_response(status)
    monkeypatch.setattr('scripts.crypto_trend_market.requests.get', get)
    with pytest.raises(requests.HTTPError):
        list(market.archive_keys(prefix='test/'))
    assert len(calls) == market.archive_requests == 1
    assert not list(tmp_path.rglob('*.xml'))


@pytest.mark.parametrize('field',['quoteAsset','underlyingType','contractType'])
def test_missing_classification_is_unknown_not_out_of_universe(field):
    record=metadata()
    record.pop(field)
    with pytest.raises(ValueError,match='分类'): eligible_metadata([record])


def test_explicit_pending_status_error_is_distinct_from_transport_failure(tmp_path,monkeypatch):
    from scripts.crypto_trend_market import InactiveSymbolError
    scanner=Scanner();scanner.reply=None
    market=TrendMarket(scanner,tmp_path,ASOF)
    market.pending_symbols={'AUSDT'}
    response=SimpleNamespace(status_code=400,json=lambda:{'code':-1122,'msg':'Invalid symbol status.'})
    monkeypatch.setattr('scripts.crypto_trend_market.requests.get',lambda *a,**k:response)
    with pytest.raises(InactiveSymbolError):market.fetch_daily('AUSDT',ASOF,ASOF+pd.Timedelta(days=1))
    response.json=lambda:{'code':-1000,'msg':'Unknown error'}
    with pytest.raises(RuntimeError) as error:market.fetch_daily('AUSDT',ASOF,ASOF+pd.Timedelta(days=1))
    assert not isinstance(error.value,InactiveSymbolError)


def test_weekly_catalog_audits_removed_competitors_older_than_daily_horizon(tmp_path, monkeypatch):
    start_seen = []
    market = TrendMarket(Scanner(), tmp_path, ASOF, history_days=210)
    monkeypatch.setattr(market, 'archive_symbols', lambda: {'AUSDT', 'GONEUSDT'})
    def activity(symbol, start, end):
        start_seen.append(start)
        return start <= ASOF-pd.Timedelta(days=60) < end
    monkeypatch.setattr(market, 'has_archive_activity', activity)
    with pytest.raises(ValueError, match='GONEUSDT'):
        market.catalog(ASOF)
    assert start_seen == [ASOF-pd.Timedelta(days=209)]


def test_catalog_readonly_extra_directory_cannot_override_newer_lifecycle(tmp_path, monkeypatch):
    own, extra = tmp_path/'weekly', tmp_path/'daily'
    own.mkdir(); extra.mkdir()
    old = metadata('GONEUSDT')
    old.update(status='SETTLING', deliveryDate=int((ASOF-pd.Timedelta(days=10)).timestamp()*1000))
    newer = dict(old, deliveryDate=int((ASOF-pd.Timedelta(days=1)).timestamp()*1000))
    old_path = extra/'catalog_2026-09-01.json'
    old_path.write_text(json.dumps({'symbols':[old]}))
    old_bytes = old_path.read_bytes()
    (own/'catalog_2026-09-05.json').write_text(json.dumps({'symbols':[newer]}))
    market = TrendMarket(Scanner(), own, ASOF, catalog_dirs=[extra])
    monkeypatch.setattr(market, 'archive_symbols', lambda: {'AUSDT', 'GONEUSDT'})
    records, _, _ = market.catalog(ASOF)
    assert next(m for m in records if m['symbol']=='GONEUSDT')['deliveryDate']==newer['deliveryDate']
    assert old_path.read_bytes()==old_bytes
    assert list(extra.iterdir())==[old_path]
