"""Behavioral tests for the weekly crypto report (RVOL / Fisher / weekly trend)."""
import json
import re
import subprocess
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

from scripts import crypto_weekly_report as weekly
from scripts.crypto_weekly_report import (
    aggregate_weeks,
    build_messages,
    build_report,
    fisher_for_symbol,
    resolve_cutoff,
    rvol_for_symbol,
    split_message,
    utc_cutoff,
    week_complete,
    week_key,
    week_starts,
    weekly_fisher,
    weekly_rvol,
    weekly_volume_rankings,
)

CUTOFF = pd.Timestamp('2026-09-21', tz='UTC')          # Monday 00:00 UTC
RANK_START = CUTOFF - pd.Timedelta(days=210)           # 30 complete weeks
RVOL_START = CUTOFF - pd.Timedelta(days=371)           # 53 complete weeks
ROOT = Path(__file__).resolve().parent.parent

# Beijing Monday 08:00 is exactly the UTC Monday boundary.
BEIJING_ENDPOINTS = [
    ('2026-09-20 07:59', '2026-09-14'),
    ('2026-09-21 07:59', '2026-09-14'),
    ('2026-09-21 08:00', '2026-09-21'),
    ('2026-09-21 09:00', '2026-09-21'),
]


def meta(symbol, onboard=1500000000000, delivery=4133404800000, status='TRADING'):
    return dict(symbol=symbol, quoteAsset='USDT', underlyingType='COIN',
                contractType='PERPETUAL', status=status,
                onboardDate=onboard, deliveryDate=delivery)


def daily_frame(base=1000.0, slope=0.0, days=371, high=1.01, low=0.99):
    dates = pd.date_range(end=CUTOFF - pd.Timedelta(days=1), periods=days, freq='D')
    t = np.arange(days)
    close = 100.0 * np.exp(slope * t)
    volume = base * (1.0 + 0.2 * np.sin(t / 7.0))
    return pd.DataFrame({'timestamp': dates, 'open': close, 'high': close * high,
                         'low': close * low, 'close': close, 'volume': volume,
                         'quote_volume': volume})


def native_weekly(weeks=30, shape='up_last'):
    starts = pd.date_range(end=CUTOFF - pd.Timedelta(days=7), periods=weeks, freq='7D')
    t = np.arange(weeks)
    if shape == 'up_last':
        hl2 = 100.0 - 2.0 * t
        hl2[-1] = 300.0
    elif shape == 'down_last':
        hl2 = 100.0 + 2.0 * t
        hl2[-1] = 0.1
    elif shape == 'up':
        hl2 = 100.0 * np.exp(0.02 * t)
    elif shape == 'down':
        hl2 = 100.0 * np.exp(-0.02 * t)
    else:  # calm
        hl2 = np.full(weeks, 100.0)
    return pd.DataFrame({'timestamp': starts, 'open': hl2, 'high': hl2 * 1.02,
                         'low': hl2 * 0.98, 'close': hl2, 'volume': 10.0,
                         'quote_volume': 10.0})


class FakeMarket:
    def __init__(self, records, daily, weekly_frames, active=None):
        self.records = list(records)
        self.daily = daily
        self.weekly_frames = weekly_frames
        self.active = set(active if active is not None else daily)
        self.requests = 0
        self.calls = []

    def catalog(self, as_of):
        return list(self.records), set(self.active), {'method': 'frozen weekly fixture'}

    def has_archive_activity(self, *args):
        return False

    def fetch_daily(self, symbol, start, end, limit=32, required_history=None):
        self.calls.append(('daily', symbol, required_history, limit))
        self.requests += 1
        return self.daily[symbol].copy()

    def fetch(self, symbol, limit, interval='4h', required_history=None):
        self.calls.append(('fetch', symbol, interval, limit, required_history))
        self.requests += 1
        return self.weekly_frames[symbol].copy()


def fixture_market():
    records = [meta(s) for s in ('AUSDT', 'BUSDT', 'CUSDT', 'DUSDT', 'EUSDT', 'FUSDT')]
    daily = {
        'AUSDT': daily_frame(base=3000.0, slope=0.010),
        'BUSDT': daily_frame(base=2000.0, slope=-0.010),
        'CUSDT': daily_frame(base=1000.0, slope=0.0),
        'DUSDT': daily_frame(base=500.0, slope=0.010),
        'EUSDT': daily_frame(base=400.0, slope=0.010),
        'FUSDT': daily_frame(base=300.0, slope=0.010),
    }
    native = {
        'AUSDT': native_weekly(shape='up_last'),
        'BUSDT': native_weekly(shape='down_last'),
        'CUSDT': native_weekly(shape='calm'),
    }
    return FakeMarket(records, daily, native)


# ---------------------------------------------------------------------------
# Week boundary / endpoint
# ---------------------------------------------------------------------------

@pytest.mark.parametrize('beijing, expected', BEIJING_ENDPOINTS)
def test_weekly_endpoint_rolls_at_beijing_0800(beijing, expected):
    now = pd.Timestamp(beijing, tz='Asia/Shanghai').tz_convert('UTC')
    assert utc_cutoff(now) == pd.Timestamp(expected, tz='UTC')


def test_resolve_cutoff_rejects_wrong_weekday_future_and_incomplete():
    now = pd.Timestamp('2026-09-21 01:00', tz='UTC')
    assert resolve_cutoff(as_of='2026-09-20', now=now) == CUTOFF
    with pytest.raises(ValueError, match='周日'):
        resolve_cutoff(as_of='2026-09-19', now=now)
    # The most recent Sunday's week has not closed before Monday 08:00 Beijing.
    with pytest.raises(ValueError, match='完整'):
        resolve_cutoff(as_of='2026-09-20', now=pd.Timestamp('2026-09-20 23:00', tz='UTC'))
    with pytest.raises(ValueError, match='完整'):
        resolve_cutoff(as_of='2026-09-27', now=now)


def test_week_starts_are_ascending_mondays_ending_before_cutoff():
    weeks = week_starts(CUTOFF, 30)
    assert len(weeks) == 30
    assert weeks[-1] == CUTOFF - pd.Timedelta(days=7)
    assert weeks[0] == CUTOFF - pd.Timedelta(days=210)
    assert all(w.weekday() == 0 for w in weeks)
    assert weeks == sorted(weeks)


# ---------------------------------------------------------------------------
# Weekly aggregation and lifecycle completeness
# ---------------------------------------------------------------------------

def test_week_complete_uses_aligned_lifetime_boundaries():
    ws = CUTOFF - pd.Timedelta(days=14)
    assert week_complete(meta('X', onboard=int(ws.timestamp() * 1000)), ws)
    # Onboarded mid-week: that week is not complete, the next one is.
    midweek = int((ws + pd.Timedelta(days=2)).timestamp() * 1000)
    assert not week_complete(meta('X', onboard=midweek), ws)
    assert week_complete(meta('X', onboard=midweek), ws + pd.Timedelta(days=7))
    # Delivery exactly at the week close keeps the week complete.
    assert week_complete(meta('X', delivery=int((ws + pd.Timedelta(days=7)).timestamp() * 1000)), ws)
    assert not week_complete(meta('X', delivery=int((ws + pd.Timedelta(days=6)).timestamp() * 1000)), ws)


def test_partial_listing_week_counts_observed_days_and_is_incomplete():
    onboard = pd.Timestamp('2026-03-04 12:00', tz='UTC')  # Wednesday
    dates = pd.date_range(start=onboard.normalize(), end=CUTOFF - pd.Timedelta(days=1), freq='D')
    t = np.arange(len(dates))
    frame = pd.DataFrame({'timestamp': dates, 'open': 100.0, 'high': 101.0, 'low': 99.0,
                          'close': 100.0, 'volume': 1.0 + 0.1 * np.sin(t),
                          'quote_volume': 1.0 + 0.1 * np.sin(t)})
    m = meta('AUSDT', onboard=int(onboard.timestamp() * 1000))
    all_weeks = week_starts(CUTOFF, 53)
    entries = aggregate_weeks(frame, m, all_weeks)
    first_week = onboard.normalize() - pd.Timedelta(days=onboard.weekday())
    first = entries[week_key(first_week)]
    assert first['days'] == 5                       # Wed..Sun observed, not the missing Mon/Tue
    assert first['complete'] is False
    assert entries[week_key(first_week + pd.Timedelta(days=7))]['complete'] is True


def test_aggregate_weeks_rejects_missing_duplicate_and_bad_volume():
    frame = daily_frame()
    weeks = week_starts(CUTOFF, 53)
    missing = frame.drop(50).reset_index(drop=True)
    with pytest.raises(ValueError, match='缺失'):
        aggregate_weeks(missing, meta('AUSDT'), weeks)
    duplicate = pd.concat([frame, frame.iloc[[50]]]).reset_index(drop=True)
    with pytest.raises(ValueError):
        aggregate_weeks(duplicate, meta('AUSDT'), weeks)
    negative = frame.copy()
    negative.loc[50, 'quote_volume'] = -1.0
    with pytest.raises(ValueError):
        aggregate_weeks(negative, meta('AUSDT'), weeks)


def test_aggregate_weeks_sums_quote_volume_within_window():
    frame = daily_frame(base=100.0)
    weeks = week_starts(CUTOFF, 53)
    entries = aggregate_weeks(frame, meta('AUSDT'), weeks)
    last = entries[week_key(weeks[-1])]
    expected = frame.iloc[-7:]['quote_volume'].sum()
    assert last['quote_volume'] == pytest.approx(float(expected))
    assert last['complete'] is True


def _utc(stamp):
    stamp = pd.Timestamp(stamp)
    return stamp.tz_localize('UTC') if stamp.tzinfo is None else stamp.tz_convert('UTC')


def _lifetime_frame(dates, onboard, delivery, value=5.0):
    """Daily rows with flat zero-volume REST padding outside the lifetime."""
    start, stop = _utc(onboard), _utc(delivery)
    volume = np.array([value if start <= _utc(day) < stop else 0.0
                       for day in dates], dtype=float)
    return pd.DataFrame({'timestamp': dates, 'open': 100.0, 'high': 101.0, 'low': 99.0,
                         'close': 100.0, 'volume': volume, 'quote_volume': volume})


def test_aggregate_weeks_ignores_flat_rows_before_onboard():
    onboard = pd.Timestamp('2026-03-04', tz='UTC')                 # Wednesday 00:00
    first_week = onboard.normalize() - pd.Timedelta(days=onboard.weekday())
    dates = pd.date_range(start=first_week, end=CUTOFF - pd.Timedelta(days=1), freq='D')
    # Binance pads flat zero-volume bars for the pre-onboard Mon/Tue.
    frame = _lifetime_frame(dates, onboard, pd.Timestamp.max)
    m = meta('AUSDT', onboard=int(onboard.timestamp() * 1000))
    entries = aggregate_weeks(frame, m, week_starts(CUTOFF, 53))
    first = entries[week_key(first_week)]
    assert first['days'] == 5                                     # Wed..Sun, not the 7 REST rows
    assert first['quote_volume'] == pytest.approx(5.0 * 5)
    assert first['complete'] is False


def test_aggregate_weeks_ignores_flat_rows_after_delivery():
    delivery = pd.Timestamp('2026-09-16 08:00', tz='UTC')         # Wednesday of terminal week
    week_start = pd.Timestamp('2026-09-14', tz='UTC')
    dates = pd.date_range(start=week_start, periods=7, freq='D')
    frame = _lifetime_frame(dates, pd.Timestamp('2000-01-01'), delivery)
    m = meta('AUSDT', delivery=int(delivery.timestamp() * 1000))
    entries = aggregate_weeks(frame, m, week_starts(CUTOFF, 1))
    last = entries[week_key(week_start)]
    assert last['days'] == 3                                      # Mon..Wed, not the 7 REST rows
    assert last['quote_volume'] == pytest.approx(5.0 * 3)


def test_aggregate_weeks_missing_inside_lifetime_still_fails():
    onboard = pd.Timestamp('2026-03-04 12:00', tz='UTC')
    first_week = onboard.normalize() - pd.Timedelta(days=onboard.weekday())
    dates = pd.date_range(start=first_week, end=CUTOFF - pd.Timedelta(days=1), freq='D')
    frame = _lifetime_frame(dates, onboard, pd.Timestamp.max)
    # A real gap inside the lifetime must not be hidden by the padding filter.
    frame = frame[frame['timestamp'] != pd.Timestamp('2026-03-05', tz='UTC')].reset_index(drop=True)
    m = meta('AUSDT', onboard=int(onboard.timestamp() * 1000))
    with pytest.raises(ValueError, match='缺失'):
        aggregate_weeks(frame, m, week_starts(CUTOFF, 53))


# ---------------------------------------------------------------------------
# Weekly turnover rankings
# ---------------------------------------------------------------------------

def test_weekly_rankings_descending_stable_ties_and_full_top_n_required():
    records = [meta(s) for s in ('AUSDT', 'BUSDT', 'CUSDT')]
    weeks = week_starts(CUTOFF, 30)
    weekly_map = {}
    for symbol, value in (('AUSDT', 300.0), ('BUSDT', 300.0), ('CUSDT', 100.0)):
        weekly_map[symbol] = {week_key(ws): dict(quote_volume=value, complete=True, days=7)
                              for ws in weeks}
    rankings = weekly_volume_rankings(weekly_map, records, weeks, top_n=2)
    assert [r['symbol'] for r in rankings[week_key(weeks[-1])]] == ['AUSDT', 'BUSDT']
    with pytest.raises(ValueError, match='不足'):
        weekly_volume_rankings(weekly_map, records, weeks, top_n=4)


def test_build_report_excludes_delisted_and_pending_before_fetch():
    records = [meta('AUSDT'), meta('BUSDT'), meta('CUSDT'),
               meta('GONEUSDT', status='SETTLING',
                    delivery=int((CUTOFF - pd.Timedelta(days=40)).timestamp() * 1000)),
               meta('PENDUSDT', status='PENDING_TRADING')]
    daily = {
        'AUSDT': daily_frame(base=3000.0, slope=0.010),
        'BUSDT': daily_frame(base=2000.0, slope=-0.010),
        'CUSDT': daily_frame(base=1000.0, slope=0.0),
        # A removed competitor with enormous historical turnover must never be
        # fetched and must not crowd the current-only ranking.
        'GONEUSDT': daily_frame(base=1e12, slope=0.0),
        'PENDUSDT': daily_frame(base=1e12, slope=0.0),
    }
    native = {'AUSDT': native_weekly(shape='up_last'),
              'BUSDT': native_weekly(shape='down_last'),
              'CUSDT': native_weekly(shape='calm')}
    market = FakeMarket(records, daily, native, active={'AUSDT', 'BUSDT', 'CUSDT'})
    report = build_report(market, CUTOFF, top_n=3)
    fetched = {call[1] for call in market.calls}
    assert fetched == {'AUSDT', 'BUSDT', 'CUSDT'}
    ranked = {row['symbol'] for rows in report['weekly_top100'].values() for row in rows}
    assert ranked == {'AUSDT', 'BUSDT', 'CUSDT'}
    assert {row['symbol'] for row in report['latest_top100']} == {'AUSDT', 'BUSDT', 'CUSDT'}
    for label in ('30w', '14w', '7w'):
        assert 'GONEUSDT' not in report['trend']['periods'][label]['pool']
        assert 'PENDUSDT' not in report['trend']['periods'][label]['pool']


# ---------------------------------------------------------------------------
# RVOL
# ---------------------------------------------------------------------------

def test_rvol_uses_previous_52_weeks_without_current_leak():
    history = list(np.linspace(100.0, 200.0, 52))
    current = 1000.0
    row = rvol_for_symbol('XUSDT', history + [current])
    expected_std = float(np.std(history, ddof=0))
    expected = (current - float(np.mean(history))) / expected_std
    assert row['status'] == 'ok'
    assert row['sigma'] == pytest.approx(expected)
    assert row['std'] == pytest.approx(expected_std)
    assert row['mean'] == pytest.approx(float(np.mean(history)))
    # If the current week were included the population std would differ.
    assert row['std'] != pytest.approx(float(np.std(history + [current], ddof=0)))


@pytest.mark.parametrize('volumes', [
    [100.0] * 52,          # insufficient complete weeks
    [100.0] * 53,          # zero population std must not fabricate a zero score
    [100.0] * 52 + [float('nan')],
])
def test_rvol_unavailable_cases_have_reasons(volumes):
    row = rvol_for_symbol('XUSDT', volumes)
    assert row['status'] == 'unavailable'
    assert row['sigma'] is None
    assert row['reason']


def test_rvol_coverage_denominator_is_fixed_selected_pool():
    selected = [f'S{i:03}USDT' for i in range(100)]
    volumes = {s: list(np.linspace(1.0, 53.0, 53)) for s in selected}
    volumes['S000USDT'] = []
    result = weekly_rvol(selected, volumes)
    assert result['selected_count'] == 100
    assert result['denominator'] == 100
    assert result['valid_count'] == 99
    assert result['unavailable_count'] == 1
    assert result['coverage'] == pytest.approx(0.99)
    assert result['top20'][0]['rank'] == 1


def test_rvol_rejects_negative_weekly_volume():
    volumes = list(np.linspace(100.0, 200.0, 53))
    volumes[10] = -1.0
    row = rvol_for_symbol('XUSDT', volumes)
    assert row['status'] == 'unavailable'
    assert row['sigma'] is None
    assert '负' in row['reason']


# ---------------------------------------------------------------------------
# Fisher
# ---------------------------------------------------------------------------

def test_fisher_last_bar_upcross_and_downcross():
    up = fisher_for_symbol('AUSDT', native_weekly(shape='up_last'), meta('AUSDT'), CUTOFF)
    down = fisher_for_symbol('BUSDT', native_weekly(shape='down_last'), meta('BUSDT'), CUTOFF)
    calm = fisher_for_symbol('CUSDT', native_weekly(shape='calm'), meta('CUSDT'), CUTOFF)
    assert up['status'] == 'ok' and up['crossing'] == 'up'
    assert down['status'] == 'ok' and down['crossing'] == 'down'
    assert calm['status'] == 'ok' and calm['crossing'] is None


def test_fisher_gap_incomplete_latest_week_and_bad_ohlc_unavailable():
    frame = native_weekly(shape='up')
    gapped = frame.drop(index=10).reset_index(drop=True)
    row = fisher_for_symbol('AUSDT', gapped, meta('AUSDT'), CUTOFF)
    assert row['status'] == 'unavailable' and '缺口' in row['reason']

    # Latest completed week missing entirely: the last complete week is older.
    short = frame.iloc[:-1].reset_index(drop=True)
    row = fisher_for_symbol('AUSDT', short, meta('AUSDT'), CUTOFF)
    assert row['status'] == 'unavailable' and '最新完整周' in row['reason']

    bad = frame.copy()
    bad.loc[20, 'low'] = bad.loc[20, 'high'] * 2
    row = fisher_for_symbol('AUSDT', bad, meta('AUSDT'), CUTOFF)
    assert row['status'] == 'unavailable' and 'OHLC' in row['reason']


def test_fisher_incomplete_listing_week_is_excluded_from_warmup():
    frame = native_weekly(shape='up')
    # Onboard midday in the first bar's week: that week is partial and excluded.
    onboard = int((frame['timestamp'].iloc[0] + pd.Timedelta(days=3)).timestamp() * 1000)
    row = fisher_for_symbol('AUSDT', frame, meta('AUSDT', onboard=onboard), CUTOFF)
    assert row['status'] == 'ok'
    assert row['complete_weeks'] == len(frame) - 1


@pytest.mark.parametrize('offset', [
    pd.Timedelta(seconds=1),
    pd.Timedelta(microseconds=1),
    pd.Timedelta(nanoseconds=1),
])
def test_fisher_rejects_non_midnight_subsecond_weekly_timestamp(offset):
    frame = native_weekly(shape='up').copy()
    # Use nanosecond storage so a 1ns offset is representable (not silently lost).
    frame['timestamp'] = pd.DatetimeIndex(frame['timestamp']).as_unit('ns')
    frame.loc[0, 'timestamp'] = frame.loc[0, 'timestamp'] + offset
    row = fisher_for_symbol('AUSDT', frame, meta('AUSDT'), CUTOFF)
    assert row['status'] == 'unavailable'
    assert '00:00' in row['reason']


def test_fisher_rate_denominator_is_fixed_selected_pool(monkeypatch):
    selected = [f'S{i:03}USDT' for i in range(100)]

    def fake(symbol, frame, meta_record, cutoff, length=9):
        if symbol == 'S000USDT':
            return dict(symbol=symbol, status='unavailable', crossing=None, reason='gap')
        crossing = 'up' if int(symbol[1:4]) < 50 else None
        return dict(symbol=symbol, status='ok', crossing=crossing, fisher=0.1, trigger=0.0)

    monkeypatch.setattr(weekly, 'fisher_for_symbol', fake)
    result = weekly_fisher(selected, {}, {}, CUTOFF)
    assert result['denominator'] == 100
    assert result['selected_count'] == 100
    assert result['valid_count'] == 99
    assert result['unknown_count'] == 1
    assert result['up_count'] == 49
    assert result['down_count'] == 0
    assert result['up_rate'] == pytest.approx(49.0)   # not 49/99
    assert result['down_rate'] == pytest.approx(0.0)
    assert 'S000USDT' not in result['up_crossings']
    assert 'S000USDT' not in result['down_crossings']


# ---------------------------------------------------------------------------
# Full report
# ---------------------------------------------------------------------------

def test_full_report_fixture_structure_and_no_current_week():
    market = fixture_market()
    report = build_report(market, CUTOFF, top_n=3)
    assert report['schema_version'] == weekly.SCHEMA_VERSION
    assert report['week_start'] == '2026-09-14'
    assert report['week_end'] == '2026-09-21'
    assert report['beijing_close'].endswith('08:00')
    assert [r['symbol'] for r in report['latest_top100']] == ['AUSDT', 'BUSDT', 'CUSDT']
    assert len(report['weekly_top100']) == 30
    assert report['rvol']['selected_count'] == 3
    assert report['rvol']['valid_count'] == 3
    assert report['fisher']['up_crossings'] == ['AUSDT']
    assert report['fisher']['down_crossings'] == ['BUSDT']
    assert report['fisher']['up_rate'] == pytest.approx(100 / 3)
    assert set(report['trend']['periods']) == {'30w', '14w', '7w'}
    assert report['trend']['periods']['30w']['interval'] == '1d'
    assert report['trend']['periods']['30w']['observations'] == 210
    assert report['trend']['periods']['7w']['observations'] == 49
    # Only the three latest-week Top100 symbols are trend candidates.
    assert set(report['trend']['periods']['30w']['pool']) == {'AUSDT', 'BUSDT', 'CUSDT'}
    assert report['trend']['periods']['30w']['top10'][0]['symbol'] == 'AUSDT'
    json.dumps(report, ensure_ascii=False, allow_nan=False)


def test_report_ignores_current_partial_week_bar():
    market = fixture_market()
    for symbol, frame in market.daily.items():
        extra = frame.iloc[[-1]].copy()
        extra['timestamp'] = CUTOFF
        extra['quote_volume'] = 1e12
        market.daily[symbol] = pd.concat([frame, extra]).reset_index(drop=True)
    report = build_report(market, CUTOFF, top_n=3)
    assert [r['symbol'] for r in report['latest_top100']] == ['AUSDT', 'BUSDT', 'CUSDT']
    assert report['weekly_top100'][week_key(CUTOFF - pd.Timedelta(days=7))][0]['quote_volume'] < 1e11


def test_report_universe_missing_stops_before_indicator_fetch():
    market = fixture_market()
    # Row 200 is inside the 30-week ranking window (rows 161..370).
    market.daily['AUSDT'] = market.daily['AUSDT'].drop(200).reset_index(drop=True)
    with pytest.raises(ValueError, match='成交额覆盖'):
        build_report(market, CUTOFF, top_n=3)
    assert all(call[0] != 'fetch' for call in market.calls)


def test_report_current_contract_api_failure_still_stops():
    market = fixture_market()

    def boom(symbol, *args, **kwargs):
        raise RuntimeError('network down')

    market.fetch_daily = boom
    with pytest.raises(ValueError, match='成交额覆盖'):
        build_report(market, CUTOFF, top_n=3)


def test_report_current_contract_empty_history_still_stops():
    market = fixture_market()
    market.daily['AUSDT'] = pd.DataFrame()
    with pytest.raises(ValueError, match='成交额覆盖'):
        build_report(market, CUTOFF, top_n=3)


def test_report_and_messages_state_current_universe_replay():
    report = build_report(fixture_market(), CUTOFF, top_n=3)
    assert '当前可交易' in report['pool_method']
    assert '已下架' in report['pool_method']
    assert '非历史全市场快照' in report['pool_method']
    joined = '\n'.join(build_messages(report))
    assert '当前可交易合约回看' in joined
    assert '已下架' in joined
    assert '非历史全市场快照' in joined


def test_daily_fetch_limit_is_weight_two_and_covers_history():
    market = fixture_market()
    build_report(market, CUTOFF, top_n=3)
    daily_calls = [call for call in market.calls if call[0] == 'daily']
    assert daily_calls
    assert weekly.DAILY_FETCH_LIMIT == 499                 # Binance weight 2, covers 371d
    assert all(call[3] == 499 for call in daily_calls)
    assert all(call[2] == 371 for call in daily_calls)


# ---------------------------------------------------------------------------
# Messages / delivery
# ---------------------------------------------------------------------------

def test_messages_cover_all_sections_and_escape_symbols():
    report = build_report(fixture_market(), CUTOFF, top_n=3)
    messages = build_messages(report)
    assert len(messages) == 5
    joined = '\n'.join(messages)
    assert 'RVOL' in messages[0] and '2026-09-14' in messages[0]
    assert 'Fisher9' in messages[1] and '固定分母3' in messages[1]
    assert '30周趋势榜' in messages[2]
    assert '14周趋势榜' in messages[3]
    assert '7周趋势榜' in messages[4]
    assert 'AUSDT' not in joined  # USDT suffix stripped


def test_fisher_message_shows_percent_and_trigger_definitions():
    report = build_report(fixture_market(), CUTOFF, top_n=3)
    report['fisher'].update(valid_count=99, unknown_count=1, denominator=100,
                            up_count=12, down_count=3, up_rate=12.0, down_rate=3.0)
    message = build_messages(report)[1]
    assert '12个（12.0%）' in message          # count / percent, not 12.0/100
    assert '3个（3.0%）' in message
    assert '12.0/100' not in message and '3.0/100' not in message   # not the old ratio form
    assert '上穿Trigger' in message and '下穿Trigger' in message
    assert '突破' not in message


def test_fisher_message_all_unknown_is_explicit_not_flat_no_cross():
    report = build_report(fixture_market(), CUTOFF, top_n=3)
    report['fisher'].update(valid_count=0, unknown_count=3,
                            up_count=0, down_count=0, up_rate=0.0, down_rate=0.0)
    message = build_messages(report)[1]
    assert '未知 3' in message
    assert '未知历史不视为无交叉' in message


def test_split_message_keeps_chunks_under_limit():
    text = '\n'.join(f'line-{i:04d}-' + 'x' * 80 for i in range(120))
    chunks = split_message(text, limit=1000)
    assert all(len(c) <= 1000 for c in chunks)
    assert ''.join(chunks).replace('\n', '') == text.replace('\n', '')


def test_split_message_breaks_over_long_symbol_line_safely():
    symbols = '、'.join(f'COIN{i:03d}' for i in range(400))
    text = '*Crypto 周报 · Fisher9*\n' + symbols
    chunks = split_message(text, limit=200)
    assert all(len(chunk) <= 200 for chunk in chunks)
    assert ''.join(chunks).replace('\n', '') == text.replace('\n', '')
    assert chunks[0].startswith('*Crypto 周报 · Fisher9*')      # Markdown header intact

    # A line with no delimiter still splits on Unicode character boundaries.
    hard = '中文货币' * 400
    pieces = split_message(hard, limit=64)
    assert all(len(piece) <= 64 for piece in pieces)
    assert ''.join(pieces) == hard
    assert all(len(piece) <= 64 for piece in split_message('中' * 1000, limit=64))


def test_run_dry_run_writes_all_artifacts_and_never_sends(monkeypatch, tmp_path):
    report = build_report(fixture_market(), CUTOFF, top_n=3)
    sent = []

    def send(text):
        # Artifacts must already exist before the first send.
        assert list(tmp_path.glob('crypto_weekly_*.json'))
        assert list(tmp_path.glob('crypto_weekly_*.md'))
        sent.append(text)
        return True

    monkeypatch.setattr(weekly, 'build_report', lambda market, cutoff, top_n=100: report)
    scanner = SimpleNamespace(send_telegram_alert=send)
    weekly.run(tmp_path, tmp_path, dry_run=True, now=CUTOFF, scanner=scanner)
    assert sent == []
    assert list(tmp_path.glob('crypto_weekly_2026-09-21.json'))
    assert len(list(tmp_path.glob('crypto_weekly_*.md'))) == 5


def test_run_send_failure_raises_after_artifacts(monkeypatch, tmp_path):
    report = build_report(fixture_market(), CUTOFF, top_n=3)
    monkeypatch.setattr(weekly, 'build_report', lambda market, cutoff, top_n=100: report)
    scanner = SimpleNamespace(send_telegram_alert=lambda text: False)
    with pytest.raises(RuntimeError, match='发送失败'):
        weekly.run(tmp_path, tmp_path, now=CUTOFF, scanner=scanner)
    assert list(tmp_path.glob('crypto_weekly_2026-09-21.json'))


# ---------------------------------------------------------------------------
# Weekly cache keys (TrendMarket generalization)
# ---------------------------------------------------------------------------

def test_weekly_daily_cache_key_separates_required_history(tmp_path):
    from scripts.crypto_trend_market import TrendMarket

    class Scanner:
        CONFIG = {'base_url': 'https://fixture.invalid'}

        def __init__(self):
            self.calls = []

        def api_request_with_retry(self, url, params=None):
            self.calls.append(params)
            return [[int(params['startTime']), '1', '2', '0.5', '1.5', '1', 0, '10']]

        def klines_to_dataframe(self, raw):
            return pd.DataFrame({'timestamp': pd.to_datetime([r[0] for r in raw], unit='ms', utc=True),
                                 'close': [float(r[4]) for r in raw],
                                 'quote_volume': [float(r[7]) for r in raw]})

    scanner = Scanner()
    market = TrendMarket(scanner, tmp_path, CUTOFF - pd.Timedelta(days=1), history_days=210)
    start, end = RVOL_START, CUTOFF
    market.fetch_daily('AUSDT', start, end, limit=1500, required_history=371)
    assert len(scanner.calls) == 1
    # Identical request is served from the task-owned cache.
    market.fetch_daily('AUSDT', start, end, limit=1500, required_history=371)
    assert len(scanner.calls) == 1
    # A smaller prior horizon has a different key and cannot masquerade as sufficient.
    market.fetch_daily('AUSDT', CUTOFF - pd.Timedelta(days=30), end, limit=1500, required_history=30)
    assert len(scanner.calls) == 2
    path = market.daily_cache_path('AUSDT', start, end, 371)
    assert path.exists()
    path.write_text('{')
    with pytest.raises(ValueError, match='缓存'):
        market.fetch_daily('AUSDT', start, end, limit=1500, required_history=371)


def test_weekly_native_cache_key_separates_required_history(tmp_path):
    from scripts.crypto_trend_market import TrendMarket

    class Scanner:
        CONFIG = {'base_url': 'https://fixture.invalid'}

        def __init__(self):
            self.calls = []

        def api_request_with_retry(self, url, params=None):
            self.calls.append(params)
            return [[0, '1', '2', '0.5', '1.5', '1', 0, '10']]

        def klines_to_dataframe(self, raw):
            return pd.DataFrame({'timestamp': pd.to_datetime([r[0] for r in raw], unit='ms', utc=True),
                                 'close': [float(r[4]) for r in raw]})

    scanner = Scanner()
    market = TrendMarket(scanner, tmp_path, CUTOFF - pd.Timedelta(days=1))
    market.fetch('AUSDT', 500, interval='1w', required_history=500)
    market.fetch('AUSDT', 500, interval='1w', required_history=500)
    assert len(scanner.calls) == 1
    market.fetch('AUSDT', 100, interval='1w', required_history=100)
    assert len(scanner.calls) == 2


# ---------------------------------------------------------------------------
# Quant shim / wrapper
# ---------------------------------------------------------------------------

def test_weekly_wrapper_shares_daily_lock_and_has_own_log():
    script_path = ROOT / 'scripts' / 'quant' / 'run_weekly_scan.sh'
    script = script_path.read_text()
    assert '/tmp/quant-cron-locks/quant_daily_scan.lock' in script
    assert 'flock -w' in script                       # finite wait, never a silent skip
    assert 'set -a' in script and '.env' in script
    assert 'quant_weekly_scan.log' in script          # own log
    assert 'send_alert' in script                     # own error alert
    result = subprocess.run(['bash', '-n', str(script_path)], capture_output=True)
    assert result.returncode == 0, result.stderr.decode()


def test_weekly_shim_points_at_versioned_finance_module():
    source = (ROOT / 'scripts' / 'quant' / 'weekly_scan_all.py').read_text()
    assert 'crypto_weekly_report' in source
    assert 'Finance' in source


# ---------------------------------------------------------------------------
# Weekly turnover-weighted trend score (return50/ER15/R²15/DD10/turnover10)
# ---------------------------------------------------------------------------

APPROVED_WEEKLY_WEIGHTS = {'return': .5, 'er': .15, 'r_squared': .15,
                           'drawdown': .1, 'quote_volume': .1}


def closes_series(slope, days=50, start=None):
    end = CUTOFF - pd.Timedelta(days=1) if start is None else start
    dates = pd.date_range(end=end, periods=days, freq='D')
    values = 100.0 * np.exp(slope * np.arange(days))
    return pd.Series(values, index=dates)


def volume_frame(volume=1.0, days=260):
    dates = pd.date_range(end=CUTOFF - pd.Timedelta(days=1), periods=days, freq='D')
    return pd.DataFrame({'timestamp': dates, 'close': 100.0, 'quote_volume': volume})


def test_weekly_weights_are_approved_scheme_and_sum_to_one():
    assert weekly.WEEKLY_WEIGHTS == APPROVED_WEEKLY_WEIGHTS
    assert sum(weekly.WEEKLY_WEIGHTS.values()) == pytest.approx(1.0)
    assert set(weekly.WEEKLY_WEIGHTS) == {'return', 'er', 'r_squared',
                                          'drawdown', 'quote_volume'}


def test_period_quote_volume_is_full_horizon_excluding_baseline_and_current_week():
    for weeks in (7, 14, 30):
        days = weeks * 7
        dates = pd.date_range(end=CUTOFF - pd.Timedelta(days=1), periods=days + 1, freq='D')
        frame = pd.DataFrame({'timestamp': dates, 'close': 100.0, 'quote_volume': 1.0})
        baseline = CUTOFF - pd.Timedelta(days=days + 1)
        assert frame['timestamp'].iloc[0] == baseline
        frame.loc[frame['timestamp'] == baseline, 'quote_volume'] = 1e9   # price baseline
        frame.loc[frame['timestamp'] == CUTOFF - pd.Timedelta(days=1), 'quote_volume'] = 1.0
        current = pd.DataFrame({'timestamp': [CUTOFF], 'close': [100.0], 'quote_volume': [1e9]})
        frame = pd.concat([frame, current]).reset_index(drop=True)
        assert weekly.period_quote_volume(frame, 'AUSDT', CUTOFF, weeks) == pytest.approx(float(days))


def test_period_quote_volume_uses_usdt_quote_volume_not_base_volume():
    dates = pd.date_range(end=CUTOFF - pd.Timedelta(days=1), periods=49, freq='D')
    frame = pd.DataFrame({'timestamp': dates, 'close': 100.0, 'volume': 1e12,
                          'quote_volume': 3.0})
    assert weekly.period_quote_volume(frame, 'AUSDT', CUTOFF, 7) == pytest.approx(49 * 3.0)
    with pytest.raises(ValueError, match='成交额'):
        weekly.period_quote_volume(frame.drop(columns=['quote_volume']), 'AUSDT', CUTOFF, 7)


def test_rank_weeks_bigger_quote_volume_raises_only_the_ten_percent_component():
    closes = {'AUSDT': closes_series(.01), 'BUSDT': closes_series(.01)}
    frames = {'AUSDT': volume_frame(2.0), 'BUSDT': volume_frame(1.0)}
    report = weekly.rank_weeks(['AUSDT', 'BUSDT'], closes, frames, CUTOFF, 7)
    by = {row['symbol']: row for row in report['rows']}
    assert by['AUSDT']['percentiles']['quote_volume'] == pytest.approx(100.0)
    assert by['BUSDT']['percentiles']['quote_volume'] == pytest.approx(50.0)
    for key in ('return', 'er', 'r_squared', 'drawdown'):
        assert by['AUSDT']['percentiles'][key] == by['BUSDT']['percentiles'][key]
    assert by['AUSDT']['score'] - by['BUSDT']['score'] == pytest.approx(5.0)
    assert [row['symbol'] for row in report['ranked']] == ['AUSDT', 'BUSDT']


def test_rank_weeks_hand_scored_different_profiles_tie_exactly_at_75(monkeypatch):
    metrics = {
        'AUSDT': {'return': .02, 'er': .1, 'r_squared': .1, 'drawdown': .2, 'slope': .01},
        'BUSDT': {'return': .01, 'er': .2, 'r_squared': .2, 'drawdown': .1, 'slope': .01},
    }
    monkeypatch.setattr(weekly, 'trend_metrics', lambda sample: metrics[sample.name])
    closes = {symbol: closes_series(.01).rename(symbol) for symbol in metrics}
    frames = {'AUSDT': volume_frame(1.0), 'BUSDT': volume_frame(2.0)}
    report = weekly.rank_weeks(['AUSDT', 'BUSDT'], closes, frames, CUTOFF, 7)
    by = {row['symbol']: row for row in report['rows']}
    assert by['AUSDT']['score'] == pytest.approx(75.0)
    assert by['BUSDT']['score'] == pytest.approx(75.0)
    assert by['AUSDT']['eligible'] is True and by['BUSDT']['eligible'] is True
    assert [row['symbol'] for row in report['ranked']] == ['AUSDT', 'BUSDT']


def test_rank_weeks_identical_evidence_ties_stable_by_symbol():
    closes = {'BUSDT': closes_series(.01), 'AUSDT': closes_series(.01)}
    frames = {'AUSDT': volume_frame(1.0), 'BUSDT': volume_frame(1.0)}
    report = weekly.rank_weeks(['BUSDT', 'AUSDT'], closes, frames, CUTOFF, 7)
    by = {row['symbol']: row for row in report['rows']}
    assert by['AUSDT']['score'] == by['BUSDT']['score']
    for symbol in ('AUSDT', 'BUSDT'):
        for key in weekly.WEEKLY_WEIGHTS:
            assert by[symbol]['percentiles'][key] == 100.0
    assert [row['symbol'] for row in report['ranked']] == ['AUSDT', 'BUSDT']


@pytest.mark.parametrize('problem', ['missing', 'duplicate', 'nan', 'negative', 'stale'])
def test_rank_weeks_bad_volume_evidence_is_explicitly_unavailable(problem):
    frame = volume_frame(1.0)
    target = 250                       # inside the strict 7-week window (labels 211..259)
    if problem == 'missing':
        frame = frame.drop(target).reset_index(drop=True)
    elif problem == 'duplicate':
        frame = pd.concat([frame, frame.loc[[target]]])
    elif problem == 'nan':
        frame.loc[target, 'quote_volume'] = float('nan')
    elif problem == 'negative':
        frame.loc[target, 'quote_volume'] = -1.0
    elif problem == 'stale':
        frame['timestamp'] = frame['timestamp'] - pd.Timedelta(days=1)
    closes = {'AUSDT': closes_series(.01), 'BUSDT': closes_series(.01)}
    frames = {'AUSDT': frame, 'BUSDT': volume_frame(1.0)}
    report = weekly.rank_weeks(['AUSDT', 'BUSDT'], closes, frames, CUTOFF, 7)
    by = {row['symbol']: row for row in report['rows']}
    assert by['AUSDT']['status'] == 'unavailable'
    assert by['AUSDT']['reason']
    assert 'percentiles' not in by['AUSDT']
    assert by['BUSDT']['status'] == 'ok'
    assert report['valid_count'] == 1


def test_rank_weeks_all_invalid_nonempty_pool_fails_closed():
    frame = volume_frame(1.0)
    frame.loc[250, 'quote_volume'] = float('nan')
    with pytest.raises(ValueError, match='不可用'):
        weekly.rank_weeks(['AUSDT'], {'AUSDT': closes_series(.01)},
                          {'AUSDT': frame}, CUTOFF, 7)


def test_rank_weeks_volume_percentile_denominator_includes_valid_downtrends():
    closes = {'UPUSDT': closes_series(.01), 'FLATUSDT': closes_series(0.0),
              'DOWNUSDT': closes_series(-.01)}
    frames = {'UPUSDT': volume_frame(2.0), 'FLATUSDT': volume_frame(1.0),
              'DOWNUSDT': volume_frame(3.0)}
    report = weekly.rank_weeks(['UPUSDT', 'FLATUSDT', 'DOWNUSDT'], closes, frames, CUTOFF, 7)
    by = {row['symbol']: row for row in report['rows']}
    assert report['valid_count'] == 3 and report['uptrend_count'] == 1
    assert by['FLATUSDT']['percentiles']['quote_volume'] == pytest.approx(100 / 3)
    assert by['DOWNUSDT']['percentiles']['quote_volume'] == pytest.approx(100.0)
    assert set(by['DOWNUSDT']['percentiles']) == {'return', 'er', 'r_squared',
                                                  'drawdown', 'quote_volume'}
    assert [row['symbol'] for row in report['top10']] == ['UPUSDT']


def test_rank_weeks_signed_return_and_direction_gate_unchanged():
    closes = {'UPUSDT': closes_series(.01), 'DOWNUSDT': closes_series(-.01)}
    frames = {'UPUSDT': volume_frame(1.0), 'DOWNUSDT': volume_frame(1.0)}
    report = weekly.rank_weeks(['UPUSDT', 'DOWNUSDT'], closes, frames, CUTOFF, 7)
    by = {row['symbol']: row for row in report['rows']}
    assert by['UPUSDT']['return'] > 0 and by['DOWNUSDT']['return'] < 0
    assert by['UPUSDT']['eligible'] is True and by['DOWNUSDT']['eligible'] is False
    assert [row['symbol'] for row in report['ranked']] == ['UPUSDT']
    assert report['valid_count'] == 2 and report['uptrend_count'] == 1
    assert by['DOWNUSDT']['percentiles']['return'] < by['UPUSDT']['percentiles']['return']


def test_report_uses_existing_daily_frames_for_turnover_without_extra_requests():
    market = fixture_market()
    report = build_report(market, CUTOFF, top_n=3)
    assert report['schema_version'] == 2
    assert report['weights'] == weekly.WEEKLY_WEIGHTS
    assert sum(report['weights'].values()) == pytest.approx(1.0)
    assert len([call for call in market.calls if call[0] == 'daily']) == 6
    native = [call for call in market.calls if call[0] == 'fetch']
    assert len(native) == 3                       # Fisher9 only; no turnover fetches
    assert all(call[2] == '1w' for call in native)
    for label in ('7w', '14w', '30w'):
        for row in report['trend']['periods'][label]['rows']:
            if row['status'] == 'ok':
                assert row['quote_volume'] >= 0.0
                assert 'quote_volume' in row['percentiles']
    json.dumps(report, ensure_ascii=False, allow_nan=False)


def test_weekly_trend_messages_show_new_weights_and_window_turnover():
    report = build_report(fixture_market(), CUTOFF, top_n=3)
    messages = build_messages(report)
    trend_messages = messages[2:5]
    assert len(trend_messages) == 3
    for message in trend_messages:
        assert '涨幅50%' in message and 'ER15%' in message and 'R²15%' in message
        assert '回撤10%' in message and '成交额10%' in message
        assert '各自窗口累计USDT成交额' in message
    joined = '\n'.join(trend_messages)
    assert re.search(r'额 [\d.]+[KMB]?', joined)
    assert weekly.format_amount(1.5e6) == '1.50M'
    assert weekly.format_amount(2.5e9) == '2.50B'
    json.dumps(report, ensure_ascii=False, allow_nan=False)
