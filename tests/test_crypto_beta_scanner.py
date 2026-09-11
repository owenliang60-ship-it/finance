import importlib
import json
import sys
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

from scripts.crypto_beta_scanner import daily_frame, day_volume, closes_31, measure, scan, messages, run

AS_OF = pd.Timestamp('2026-09-06', tz='UTC')
RETURNS = np.array([.01, -.02, .035, -.015, .005] * 6)


def frame(beta=1, volume=1):
    closes = 100 * np.cumprod(np.r_[1, 1 + beta * RETURNS])
    return pd.DataFrame({'timestamp': pd.date_range(end=AS_OF, periods=31),
                         'close': closes, 'quote_volume': volume})


@pytest.mark.parametrize('beta', [1, 2, -1, 0])
def test_known_beta(beta):
    closes = closes_31(daily_frame(frame(beta)), AS_OF)
    bench = closes_31(daily_frame(frame()), AS_OF)
    result, corr = measure(closes, bench)
    assert result == pytest.approx(beta)
    if beta:
        assert corr == pytest.approx(np.sign(beta))
    else:
        assert corr is None


@pytest.mark.parametrize('mutation', ['missing', 'duplicate', 'stale', 'zero', 'nan', 'short'])
def test_reject_invalid_history(mutation):
    data = frame()
    if mutation == 'missing':
        data = data.drop(10)
    elif mutation == 'duplicate':
        data = pd.concat([data, data.iloc[[10]]])
    elif mutation == 'stale':
        data['timestamp'] -= pd.Timedelta(days=1)
    elif mutation == 'zero':
        data.loc[10, 'close'] = 0
    elif mutation == 'nan':
        data.loc[10, 'close'] = np.nan
    else:
        data = data.tail(10)
    with pytest.raises(ValueError):
        closes_31(daily_frame(data), AS_OF)


def test_open_candle_not_used_and_reverse_order_ok():
    data = frame()
    future = data.iloc[[-1]].copy()
    future['timestamp'] += pd.Timedelta(days=1)
    future['close'] = 99999
    actual = closes_31(daily_frame(pd.concat([data, future]).iloc[::-1]), AS_OF)
    expected = closes_31(daily_frame(data), AS_OF)
    pd.testing.assert_series_equal(actual, expected)


class FakeMarket:
    def __init__(self):
        self.data = {'BTCUSDT': frame(1, 500), 'ALTUSDT': frame(2, 1000),
                     'LOWUSDT': frame(-1, 10)}
        self.calls = []

    def symbols(self):
        return list(self.data)

    def cached(self, symbol):
        return self.data[symbol]

    def fetch(self, symbol, limit):
        self.calls.append((symbol, limit))
        return self.data[symbol]


def test_rank_then_compute_and_reuse_cache():
    market = FakeMarket()
    report = scan(market, AS_OF, top_n=2)
    assert [r['symbol'] for r in report['rows']] == ['ALTUSDT', 'BTCUSDT']
    assert report['valid_count'] == 2
    assert market.calls == []
    assert all(r['observations'] == 30 for r in report['rows'])


def test_short_history_stays_in_top_does_not_replace():
    market = FakeMarket()
    market.data['ALTUSDT'] = market.data['ALTUSDT'].tail(10)
    report = scan(market, AS_OF, 2)
    assert report['valid_count'] == 1
    assert report['rows'][-1]['symbol'] == 'ALTUSDT'
    assert report['rows'][-1]['beta'] is None
    assert market.calls == [('ALTUSDT', 32)]


def test_missing_volume_blocks_ranking_instead_of_fallback():
    market = FakeMarket()
    market.data['LOWUSDT'] = market.data['LOWUSDT'].iloc[:-1]
    with pytest.raises(RuntimeError, match='覆盖不完整'):
        scan(market, AS_OF, 2)


def test_bad_benchmark_blocks_entire_scan():
    market = FakeMarket()
    market.data['BTCUSDT']['close'] = 100
    with pytest.raises(ValueError, match='方差'):
        scan(market, AS_OF, 2)


def test_future_listing_excluded_from_prior_day():
    market = FakeMarket()
    market.data['NEWUSDT'] = frame().tail(1)
    market.data['NEWUSDT']['timestamp'] += pd.Timedelta(days=1)
    report = scan(market, AS_OF, 2)
    assert report['ranked_coverage'] == 3


def test_message_pages_under_limit_and_json_no_nan():
    report = scan(FakeMarket(), AS_OF, 2)
    report['rows'] = [report['rows'][0]] * 50
    chunks = messages(report)
    assert len(chunks) == 3
    assert all(len(c) < 4096 for c in chunks)
    json.dumps(report, allow_nan=False)


def test_run_dry_run_never_sends(monkeypatch, tmp_path):
    report = scan(FakeMarket(), AS_OF, 2)
    sent = []
    fake = SimpleNamespace(send_telegram_alert=lambda msg: sent.append(msg))
    monkeypatch.setitem(sys.modules, 'binance_pmarp_scanner', fake)
    monkeypatch.setattr('scripts.crypto_beta_scanner.scan', lambda *a: report)
    run(tmp_path, tmp_path, dry_run=True)
    assert not sent
    assert json.loads(next(tmp_path.glob('*.json')).read_text())['valid_count'] == 2


def test_send_failure_propagates(monkeypatch, tmp_path):
    report = scan(FakeMarket(), AS_OF, 2)
    monkeypatch.setitem(sys.modules, 'binance_pmarp_scanner',
                        SimpleNamespace(send_telegram_alert=lambda msg: False))
    monkeypatch.setattr('scripts.crypto_beta_scanner.scan', lambda *a: report)
    with pytest.raises(RuntimeError, match='发送失败'):
        run(tmp_path, tmp_path)


def test_telegram_legacy_markdown_symbol_is_escaped():
    report = scan(FakeMarket(), AS_OF, 2)
    report['rows'][0]['symbol'] = 'A_BCUSDT'
    assert 'A\\_BC' in messages(report)[0]


def test_daily_entry_failure_is_visible(monkeypatch):
    from scripts.quant import daily_scan_all as daily
    assert daily.SCANNERS[-1] == ('Crypto 双榜', 'binance_beta_scanner')
    def fail():
        raise RuntimeError('beta unavailable')
    monkeypatch.setitem(sys.modules, 'fake_beta_failure', SimpleNamespace(main=fail))
    assert daily.run_scanner(4, 4, 'Beta', 'fake_beta_failure') is False


def test_messages_only_strictly_above_one():
    report = scan(FakeMarket(), AS_OF, 2)
    template = report['rows'][0]
    report['rows'] = [dict(template, symbol=symbol, beta=beta, status=status)
                      for symbol, beta, status in [
                          ('HIGHUSDT', 1.01, 'ok'), ('EDGEUSDT', 1.0, 'ok'),
                          ('LOWUSDT', 0.99, 'ok'), ('NEGUSDT', -2, 'ok'),
                          ('MISSINGUSDT', None, 'unavailable')]]
    text = '\n'.join(messages(report))
    assert 'HIGH |' in text
    for name in ('EDGE', 'LOW', 'NEG', 'MISSING'):
        assert name + ' |' not in text
    assert '符合 1 个' in text
    assert len(report['rows']) == 5  # Preserve the underlying audit data.


def test_messages_no_matches_is_explicit():
    report = scan(FakeMarket(), AS_OF, 2)
    report['rows'] = [r for r in report['rows'] if r['symbol'] == 'BTCUSDT']
    text = '\n'.join(messages(report))
    assert '今日无 beta > 1 的币种' in text
    assert 'BTC |' not in text
