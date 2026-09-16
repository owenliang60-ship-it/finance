import json
from pathlib import Path
from types import SimpleNamespace
import sys

import numpy as np
import pandas as pd
import pytest

from scripts import crypto_daily_rankings as daily


def report():
    return dict(as_of='2026-09-06', selected_count=50, valid_count=50,
                momentum_target_count=49, momentum_valid_count=49,
                rows=[dict(symbol=f'C{i:02}USDT', beta=3-i*.1, status='ok') for i in range(25)],
                momentum_rows=[dict(symbol=f'C{i:02}USDT', score=3-i*.1, rs=.1, status='ok')
                               for i in range(5,25)])


def test_two_top10_with_overlap_shown_three_times():
    data = report()
    beta, rs = daily.top_lists(data)
    assert len(beta) == len(rs) == 10
    assert [r['symbol'] for r in beta] == [f'C{i:02}USDT' for i in range(10)]
    text = daily.message(data)
    assert '双榜同时入选：5 个' in text
    assert 'C05' in text.split('①')[0]
    assert text.count('C05') == 3
    assert 'C24' not in text
    assert len(text) < 4000


def test_beta_threshold_and_rs_is_not_gated_by_beta():
    data=report()
    data['rows']=[dict(symbol='EDGEUSDT',beta=1.,status='ok'),
                  dict(symbol='MISSINGUSDT',beta=None,status='unavailable')]
    beta, rs=daily.top_lists(data)
    assert beta==[] and len(rs)==10
    assert '无共同币种' in daily.message(data)


def test_sparse_results_are_not_padded():
    data=report()
    data['momentum_rows']=data['momentum_rows'][:2]
    data['momentum_valid_count']=2
    text=daily.message(data)
    assert 'RS 4h前十*（实际2个）' in text
    assert '有效2/49' in text
    assert '部分历史不足或数据不可用' in text


def test_symbol_markdown_escape():
    assert daily.symbol_label('A_BUSDT') == 'A\\_B'


def test_4h_history_and_independent_beta_eligibility(tmp_path):
    dates=pd.date_range(end='2026-09-07',periods=188,freq='4h',tz='UTC')
    t=np.arange(188)
    class Market:
        def fetch(self,symbol,limit,interval):
            assert limit==188 and interval=='4h'
            if symbol=='NEWUSDT':
                return pd.DataFrame({'timestamp':dates[-10:],'close':100.})
            closes=100*np.exp(.001*t+.01*np.sin(t))
            if symbol!='BTCUSDT':
                closes*=np.exp(.001*t+.01*np.cos(t))
            return pd.DataFrame({'timestamp':dates,'close':closes})
    data=dict(as_of='2026-09-06', rows=[dict(symbol='BTCUSDT'),dict(symbol='ALTUSDT'),dict(symbol='NEWUSDT')])
    daily.add_momentum(data, Market(), tmp_path)
    assert data['momentum_valid_count']==1
    assert data['momentum_target_count']==2
    assert data['momentum_rows'][0]['observations']==180
    assert data['momentum_rows'][1]['status']=='unavailable'


def dual_report():
    data = report()
    data['seven_day'] = dict(
        period='7d', period_days=7, interval='4h', observations=42, window=42,
        benchmark='BTCUSDT',
        rows=[dict(symbol=f'C{i:02}USDT', beta=3-i*.1, status='ok') for i in range(25)],
        valid_count=25,
        momentum_rows=[dict(symbol=f'C{i:02}USDT', score=3-i*.1, rs=.1, status='ok')
                       for i in range(5, 25)],
        momentum_valid_count=20, momentum_target_count=20,
        beta_top10=[], rs_top10=[], overlap=[])
    return data


def _series_frame(dates, returns, beta=1.0):
    return pd.DataFrame({'timestamp': dates,
                         'close': 100 * np.cumprod(np.r_[1.0, 1.0 + beta * np.asarray(returns)])})


class _FrameMarket:
    def __init__(self, frames):
        self.frames = {k: v.copy() for k, v in frames.items()}
        self.calls = []

    def fetch(self, symbol, limit, interval):
        self.calls.append((symbol, limit, interval))
        return self.frames[symbol].copy()


class _BrokenFetchMarket(_FrameMarket):
    def __init__(self, frames, broken_symbol):
        super().__init__(frames)
        self.broken_symbol = broken_symbol

    def fetch(self, symbol, limit, interval):
        if symbol == self.broken_symbol:
            self.calls.append((symbol, limit, interval))
            raise RuntimeError(f'fetch timeout for {symbol}')
        return super().fetch(symbol, limit, interval)


def _momentum_dates():
    return pd.date_range(end='2026-09-07', periods=188, freq='4h', tz='UTC')


def _momentum_setup(frames):
    market = _FrameMarket(frames)
    data = dict(as_of='2026-09-06', selected_count=len(frames),
                rows=[dict(symbol=s) for s in frames])
    return data, market


def test_seven_day_beta_and_rs_use_42_returns(tmp_path):
    dates = _momentum_dates()
    ret = np.random.default_rng(7).normal(0, .01, len(dates) - 1)
    frames = {'BTCUSDT': _series_frame(dates, ret, 1.),
              'B2USDT': _series_frame(dates, ret, 2.),
              'BNEGUSDT': _series_frame(dates, ret, -1.),
              'B1USDT': _series_frame(dates, ret, 1.)}
    data, market = _momentum_setup(frames)
    daily.add_momentum(data, market, tmp_path)
    seven = data['seven_day']
    by = {r['symbol']: r for r in seven['rows']}
    assert by['B2USDT']['beta'] == pytest.approx(2.)
    assert by['BNEGUSDT']['beta'] == pytest.approx(-1.)
    assert by['B1USDT']['beta'] == 1.0
    assert by['BTCUSDT']['beta'] == 1.0
    assert seven['observations'] == 42
    assert all(r['observations'] == 42 for r in seven['rows'] if r['status'] == 'ok')
    assert all(r['observations'] == 42 for r in seven['momentum_rows'] if r['status'] == 'ok')
    assert 'B2USDT' in seven['beta_top10']
    assert 'B1USDT' not in seven['beta_top10']
    assert 'BTCUSDT' not in seven['beta_top10']


def test_seven_day_rs_not_gated_by_beta():
    data = report()
    source = dict(rows=[dict(symbol='LOWBETAUSDT', beta=.2, status='ok')],
                  momentum_rows=[dict(symbol='LOWBETAUSDT', score=5., rs=.1, status='ok')])
    data['seven_day'] = source
    beta, rs = daily.top_lists(data, source)
    assert beta == []
    assert [r['symbol'] for r in rs] == ['LOWBETAUSDT']


def test_seven_day_intersection_is_period_local():
    data = dual_report()
    text = daily.message(data, '7d')
    assert '双榜同时入选：5 个' in text
    assert '7天' in text
    assert '42个4h收益率' in text
    assert 'C05' in text
    assert len(text) < 4000


def test_thirty_day_message_unchanged_with_seven_day_present():
    assert daily.message(dual_report()) == daily.message(report())


def test_each_symbol_fetched_once_across_periods(tmp_path):
    dates = _momentum_dates()
    ret = np.random.default_rng(3).normal(0, .01, len(dates) - 1)
    frames = {'BTCUSDT': _series_frame(dates, ret, 1.),
              'ALTUSDT': _series_frame(dates, ret, 2.),
              'OTHERUSDT': _series_frame(dates, ret, -1.)}
    data, market = _momentum_setup(frames)
    daily.add_momentum(data, market, tmp_path)
    assert len(market.calls) == len(frames)
    assert sorted(s for s, _, _ in market.calls) == sorted(frames)
    assert all(limit == 188 and interval == '4h' for _, limit, interval in market.calls)


@pytest.mark.parametrize('failure', ['fetch_error', 'corrupt_cache'])
def test_failed_symbol_load_is_isolated_to_its_three_rows(tmp_path, failure):
    dates = _momentum_dates()
    ret = np.random.default_rng(21).normal(0, .01, len(dates) - 1)
    frames = {'BTCUSDT': _series_frame(dates, ret, 1.),
              'GOODUSDT': _series_frame(dates, ret, 2.)}
    market = _BrokenFetchMarket(frames, 'BROKENUSDT')
    if failure == 'corrupt_cache':
        cache = tmp_path / '2026-09-06'
        cache.mkdir(parents=True)
        (cache / 'BROKENUSDT_4h.csv').write_text(
            'timestamp,volume\n2026-09-01T00:00:00+00:00,1\n')
    data = dict(as_of='2026-09-06', selected_count=3,
                rows=[dict(symbol=s) for s in ('BTCUSDT', 'BROKENUSDT', 'GOODUSDT')])
    daily.add_momentum(data, market, tmp_path)

    broken_fetches = [s for s, _, _ in market.calls if s == 'BROKENUSDT']
    assert len(broken_fetches) == (0 if failure == 'corrupt_cache' else 1)

    row_30d = next(r for r in data['momentum_rows'] if r['symbol'] == 'BROKENUSDT')
    beta_7d = {r['symbol']: r for r in data['seven_day']['rows']}['BROKENUSDT']
    rs_7d = {r['symbol']: r for r in data['seven_day']['momentum_rows']}['BROKENUSDT']
    for row in (row_30d, rs_7d):
        assert row['status'] == 'unavailable'
        assert row['score'] is None and row['rs'] is None
    assert beta_7d['status'] == 'unavailable'
    assert beta_7d['beta'] is None and beta_7d['observations'] == 0
    assert row_30d['reason'] == beta_7d['reason'] == rs_7d['reason']
    assert row_30d['reason']

    good_30d = next(r for r in data['momentum_rows'] if r['symbol'] == 'GOODUSDT')
    assert good_30d['status'] == 'ok'
    assert {r['symbol']: r for r in data['seven_day']['rows']}['GOODUSDT']['status'] == 'ok'
    assert {r['symbol']: r for r in data['seven_day']['momentum_rows']}['GOODUSDT']['status'] == 'ok'
    assert data['momentum_valid_count'] == 1 and data['momentum_target_count'] == 2
    assert data['seven_day']['valid_count'] == 2
    assert data['seven_day']['momentum_valid_count'] == 1
    assert data['seven_day']['momentum_target_count'] == 2


def test_gap_outside_last43_breaks_30d_not_7d(tmp_path):
    dates = _momentum_dates()
    ret = np.random.default_rng(11).normal(0, .01, len(dates) - 1)
    full = _series_frame(dates, ret, 2.)
    frames = {'BTCUSDT': _series_frame(dates, ret, 1.),
              'ALTUSDT': full.drop(index=50).reset_index(drop=True)}
    data, market = _momentum_setup(frames)
    daily.add_momentum(data, market, tmp_path)
    row = next(r for r in data['momentum_rows'] if r['symbol'] == 'ALTUSDT')
    assert row['status'] == 'unavailable' and row['score'] is None
    seven = {r['symbol']: r for r in data['seven_day']['rows']}
    assert seven['ALTUSDT']['status'] == 'ok'
    seven_rs = {r['symbol']: r for r in data['seven_day']['momentum_rows']}
    assert seven_rs['ALTUSDT']['status'] == 'ok'


def test_gap_inside_last43_breaks_7d(tmp_path):
    dates = _momentum_dates()
    ret = np.random.default_rng(13).normal(0, .01, len(dates) - 1)
    frames = {'BTCUSDT': _series_frame(dates, ret, 1.),
              'ALTUSDT': _series_frame(dates, ret, 2.).drop(index=180).reset_index(drop=True)}
    data, market = _momentum_setup(frames)
    daily.add_momentum(data, market, tmp_path)
    seven = {r['symbol']: r for r in data['seven_day']['rows']}
    assert seven['ALTUSDT']['status'] == 'unavailable' and seven['ALTUSDT']['beta'] is None
    seven_rs = {r['symbol']: r for r in data['seven_day']['momentum_rows']}
    assert seven_rs['ALTUSDT']['status'] == 'unavailable' and seven_rs['ALTUSDT']['score'] is None


def test_new_coin_7d_valid_30d_invalid(tmp_path):
    dates = _momentum_dates()
    ret = np.random.default_rng(17).normal(0, .01, len(dates) - 1)
    frames = {'BTCUSDT': _series_frame(dates, ret, 1.),
              'NEWUSDT': _series_frame(dates[-44:-1], ret[-43:-1], 2.)}
    data, market = _momentum_setup(frames)
    daily.add_momentum(data, market, tmp_path)
    row = next(r for r in data['momentum_rows'] if r['symbol'] == 'NEWUSDT')
    assert row['status'] == 'unavailable' and row['score'] is None
    seven = {r['symbol']: r for r in data['seven_day']['rows']}['NEWUSDT']
    assert seven['status'] == 'ok' and seven['beta'] == pytest.approx(2.)
    seven_rs = {r['symbol']: r for r in data['seven_day']['momentum_rows']}['NEWUSDT']
    assert seven_rs['status'] == 'ok' and seven_rs['observations'] == 42


@pytest.mark.parametrize('dry_run', [True,False])
def test_run_sends_two_messages_or_none(monkeypatch,tmp_path,dry_run):
    data=dual_report();sent=[]
    scanner=SimpleNamespace(send_telegram_alert=lambda msg: sent.append(msg) or True)
    monkeypatch.setitem(sys.modules,'binance_pmarp_scanner',scanner)
    monkeypatch.setattr(daily,'scan',lambda *args:data)
    monkeypatch.setattr(daily,'add_momentum',lambda *args:data)
    daily.run(tmp_path,tmp_path,dry_run=dry_run)
    assert len(sent)==(0 if dry_run else 2)
    assert '7天' in sent[1] if not dry_run else True
    saved=json.loads(next(tmp_path.glob('crypto_dual_top10_*.json')).read_text())
    assert len(saved['overlap'])==5
    assert saved['seven_day']['observations']==42


def test_run_missing_seven_day_fails(monkeypatch,tmp_path):
    data=report()
    monkeypatch.setitem(sys.modules,'binance_pmarp_scanner',
                        SimpleNamespace(send_telegram_alert=lambda msg: True))
    monkeypatch.setattr(daily,'scan',lambda *args:data)
    monkeypatch.setattr(daily,'add_momentum',lambda *args:data)
    with pytest.raises(RuntimeError,match='7天报告缺失'):
        daily.run(tmp_path,tmp_path)


def test_send_failure_propagates(monkeypatch,tmp_path):
    data=dual_report()
    monkeypatch.setitem(sys.modules,'binance_pmarp_scanner',SimpleNamespace(send_telegram_alert=lambda msg:False))
    monkeypatch.setattr(daily,'scan',lambda *args:data)
    monkeypatch.setattr(daily,'add_momentum',lambda *args:data)
    with pytest.raises(RuntimeError,match='发送失败'):
        daily.run(tmp_path,tmp_path)


def test_second_send_failure_propagates(monkeypatch,tmp_path):
    data=dual_report();calls=[]
    def send(msg):
        calls.append(msg)
        return len(calls)==1
    monkeypatch.setitem(sys.modules,'binance_pmarp_scanner',SimpleNamespace(send_telegram_alert=send))
    monkeypatch.setattr(daily,'scan',lambda *args:data)
    monkeypatch.setattr(daily,'add_momentum',lambda *args:data)
    with pytest.raises(RuntimeError,match='发送失败'):
        daily.run(tmp_path,tmp_path)
    assert len(calls)==2


def test_nonidentical_beta_just_above_one_is_not_rounded_away(tmp_path):
    dates = _momentum_dates()
    ret = np.random.default_rng(7).normal(0, .01, len(dates) - 1)
    data, market = _momentum_setup({
        'BTCUSDT': _series_frame(dates, ret, 1.),
        'ABOVEUSDT': _series_frame(dates, ret, 1.00000001),
    })
    daily.add_momentum(data, market, tmp_path)
    assert 'ABOVEUSDT' in data['seven_day']['beta_top10']
    assert 'BTCUSDT' not in data['seven_day']['beta_top10']
