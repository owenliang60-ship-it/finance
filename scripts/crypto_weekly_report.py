"""Weekly crypto report: last closed Binance week RVOL / Fisher / weekly trend.

Frozen behavior (Boss approved 2026-09-20):

* One report per fully closed Binance native week.  The week boundary is
  Monday 00:00 UTC (08:00 Beijing); the report is derived from the most recent
  Monday 00:00 UTC ``<= now`` so a still-open partial week is never used.
* Universe: current Binance USDT-margined COIN PERPETUAL contracts that are
  ``status == TRADING`` with ``onboard <= report cutoff < delivery``.  Quote
  turnover is in USDT (never base units or rolling ticker volume).
* Delisted, SETTLING, PENDING and historical-only-absent contracts are
  excluded from every weekly turnover ranking, indicator and trend pool.
  This is an intentional current-universe historical replay
  （当前可交易合约回看，已下架排除，非历史全市场快照）, not an original
  all-market point-in-time snapshot.
* The latest complete week's Top100 by USDT quote turnover is the selection
  for RVOL and Fisher, restricted to the current universe.  The 7/14/30 weeks
  of historical weekly Top100 are retained but replayed over that same
  current universe.
* RVOL reuses :func:`src.indicators.rvol.calculate_rvol` with the previous 52
  complete weeks and population std (``ddof=0``).  A zero/undefined std is
  reported as unavailable rather than fabricated as a zero score.  The
  reported Top20 is the approved equal-weight composite of the RVOL rank and
  the full-selection turnover rank; unavailable rows keep no score and never
  backfill from outside the Top100.
* Fisher length 9 uses native Binance weekly high/low and a long warm-up.
  Only a NEW cross on the last completed week is counted; unknown history is
  never classified as a no-cross and the rate denominator is the fixed
  selected pool.
* Weekly trend pools need a strict majority of the 7/14/30 weeks in the
  weekly Top100, then reuse the daily trend metric/score kernel on daily
  closes.  Weekly-only scoring adds a fifth component: total USDT quote
  turnover over the same 7/14/30-week horizon
  (return50 / ER15 / R²15 / drawdown10 / turnover10).  Daily rankings keep
  their original 40/20/20/20 weights and never gain a turnover component.
  No current-week bar and no new RS/Beta metric.

The module is a thin adapter: transport, archive evidence, catalog and ranking
kernels stay in :mod:`scripts.crypto_trend_market` and
:mod:`scripts.crypto_trend_rankings`.
"""

from __future__ import annotations

import argparse
from collections import Counter
import importlib
import json
from pathlib import Path
import re
import sys

import numpy as np
import pandas as pd

from scripts.compare_crypto_relative_momentum import window
from scripts.crypto_beta_scanner import daily_frame
from scripts.crypto_trend_market import (CurrentTrendMarket, eligible_metadata,
                                         overlaps)
from scripts.crypto_trend_metrics import trend_metrics
from scripts.crypto_trend_rankings import (WEIGHTS, atomic_text, build_pools,
    score_rows, V4_WEIGHTS, CURRENT_SCORING_VERSION, LONG_DRAWDOWN_LIMIT,
    EXCLUDED_TRADING_SYMBOLS, direction_eligible, weight_description)
from src.indicators.fisher import DEFAULT_LENGTH, fisher_transform, latest_crossing
from src.indicators.rvol import calculate_rvol

SCHEMA_VERSION = 4
TREND_SCORING_VERSION = CURRENT_SCORING_VERSION
RVOL_SCORING_VERSION = 1             # composite RVOL x turnover ranking generation
RVOL_LOOKBACK = 52
# Approved equal-weight composite: 50% RVOL rank + 50% turnover rank.  The
# turnover leg keeps the literal 100/99 reference from the delivered workbook,
# so production (Top100) is exact and small test selections stay comparable.
RVOL_WEIGHTS = {'rvol': .5, 'turnover': .5}
TURNOVER_RANK_REFERENCE = 100
FISHER_LENGTH = DEFAULT_LENGTH
TREND_WEEKS = (7, 14, 30)
# Weekly trend windows retain daily close sampling; score and long eligibility
# use the same current rule as daily reports/backtests. Turnover is USDT/day.
WEEKLY_WEIGHTS = V4_WEIGHTS
RANK_WEEKS = 30                     # completed weeks used for turnover Top100
DAILY_HISTORY_WEEKS = RVOL_LOOKBACK + 1
DAILY_HISTORY_DAYS = DAILY_HISTORY_WEEKS * 7
RANK_HISTORY_DAYS = RANK_WEEKS * 7
FISHER_WARMUP_LIMIT = 500           # native 1w bars (~9.6y, wider than 400 weeks)
DAILY_FETCH_LIMIT = 499           # covers 371d at Binance weight-2 (not 1500/10)
TELEGRAM_LIMIT = 3900


# ---------------------------------------------------------------------------
# Week boundaries
# ---------------------------------------------------------------------------

def _as_utc(value):
    stamp = pd.Timestamp(value)
    return stamp.tz_localize('UTC') if stamp.tzinfo is None else stamp.tz_convert('UTC')


def utc_cutoff(now=None):
    """Exclusive end (Monday 00:00 UTC) of the most recent fully closed week."""
    now = _as_utc(pd.Timestamp.now(tz='UTC') if now is None else now)
    return now.normalize() - pd.Timedelta(days=now.weekday())


def resolve_cutoff(as_of=None, now=None):
    """Return the week cutoff, honouring an explicit ``--as-of`` Sunday."""
    now = _as_utc(pd.Timestamp.now(tz='UTC') if now is None else now)
    if as_of is None:
        return utc_cutoff(now)
    day = _as_utc(as_of).normalize()
    if day.weekday() != 6:
        raise ValueError('as-of必须为周日(UTC)，表示最近完整周的最后一天')
    cutoff = day + pd.Timedelta(days=1)
    if cutoff > now:
        raise ValueError('指定周的周一00:00 UTC尚未到达，周报不完整')
    return cutoff


def week_starts(cutoff, count):
    """Ascending Monday 00:00 UTC starts, newest at ``cutoff - 7d``."""
    if type(count) is not int or count <= 0:
        raise ValueError('周数必须为正整数')
    end = _as_utc(cutoff) - pd.Timedelta(days=7)
    return list(pd.date_range(end=end, periods=count, freq='7D'))


def week_key(week_start):
    return str(_as_utc(week_start).date())


def week_complete(meta_record, week_start):
    """A week is complete only when the whole Mon..Sun span is within lifetime."""
    start = _as_utc(week_start)
    end = start + pd.Timedelta(days=7)
    return (int(meta_record['onboardDate']) <= int(start.timestamp() * 1000)
            and int(meta_record['deliveryDate']) >= int(end.timestamp() * 1000))


def aggregate_weeks(frame, meta_record, weeks):
    """Aggregate strict complete UTC days into weekly bars.

    Partial listing/delisting weeks keep their observed lifetime-overlapping
    days but require every such day to be present.  Missing, duplicate,
    non-daily or invalid turnover stops the caller: missing history is never
    converted into a zero.
    """
    symbol = meta_record['symbol']
    if frame is None or getattr(frame, 'empty', True):
        raise ValueError(f'{symbol} 日线数据为空')
    daily = daily_frame(frame)
    if daily.empty:
        raise ValueError(f'{symbol} 日线数据为空')
    if daily.index.duplicated().any():
        raise ValueError(f'{symbol} 日线时间重复')
    if 'quote_volume' not in daily.columns:
        raise ValueError(f'{symbol} 日线成交额缺失')
    result = {}
    for week_start in weeks:
        start = _as_utc(week_start)
        end = start + pd.Timedelta(days=7)
        expected = [start + pd.Timedelta(days=offset) for offset in range(7)]
        expected = [day for day in expected
                    if overlaps(meta_record, day, day + pd.Timedelta(days=1))]
        if not expected:
            continue
        rows = daily.loc[(daily.index >= start) & (daily.index < end)]
        # Binance REST can pad flat zero-volume rows wholly before onboarding
        # or after delivery (e.g. LOOM/MKR).  They are outside this contract's
        # life, so drop them before the completeness check: a day *within* the
        # lifetime that is missing still fails the equality below, while a
        # partially overlapping listing/delivery day is kept.
        in_life = np.asarray(
            [overlaps(meta_record, day, day + pd.Timedelta(days=1))
             for day in rows.index], dtype=bool)
        rows = rows.loc[in_life]
        if len(rows) != len(expected) or not rows.index.equals(pd.DatetimeIndex(expected)):
            raise ValueError(f'{symbol} {start.date()} 周内日线缺失/重复/非日线')
        values = pd.to_numeric(rows['quote_volume'], errors='coerce').to_numpy(dtype=float)
        if not np.isfinite(values).all() or (values < 0).any():
            raise ValueError(f'{symbol} {start.date()} 成交额无效')
        result[week_key(start)] = dict(
            week_start=str(start.date()), week_end=str(end.date()),
            quote_volume=float(values.sum()), complete=week_complete(meta_record, start),
            days=len(expected))
    return result


# ---------------------------------------------------------------------------
# RVOL
# ---------------------------------------------------------------------------

def rvol_for_symbol(symbol, volumes):
    """Current complete week vs the previous 52 complete weeks.

    ``calculate_rvol`` is reused for the valid path; a zero or undefined
    population std is reported as unavailable instead of its historical 0.0
    fallback.
    """
    row = dict(symbol=symbol, status='unavailable', sigma=None, reason=None,
               quote_volume=None, mean=None, std=None)
    try:
        series = pd.Series([float(value) for value in volumes], dtype=float)
    except (TypeError, ValueError) as exc:
        row['reason'] = f'成交额序列无效: {exc}'
        return row
    if len(series) < RVOL_LOOKBACK + 1:
        row['reason'] = f'完整周不足{RVOL_LOOKBACK + 1}（{len(series)}）'
        return row
    recent = series.iloc[-(RVOL_LOOKBACK + 1):]
    if not np.isfinite(recent.to_numpy()).all():
        row['reason'] = '成交额序列含无效值'
        return row
    if (recent.to_numpy(dtype=float) < 0).any():
        row['reason'] = '成交额序列含负数'
        return row
    std = float(recent.iloc[:-1].std(ddof=0))
    if not np.isfinite(std) or std <= 0:
        row['reason'] = '前52周总体标准差为零或无效'
        return row
    value = calculate_rvol(recent, RVOL_LOOKBACK)
    if value is None or not np.isfinite(value):
        row['reason'] = 'RVOL无法计算'
        return row
    row.update(status='ok', sigma=float(value), std=std,
               mean=float(recent.iloc[:-1].mean()),
               quote_volume=float(recent.iloc[-1]))
    return row


def weekly_rvol(selected, volumes_by_symbol, errors=None):
    """RVOL rows plus the approved composite Top20.

    ``selected`` is already ordered by latest-week quote turnover
    (``-quote_volume, symbol``), so enumerating it supplies ``turnover_rank``
    for every selected symbol, including RVOL-unavailable members.  The
    turnover leg therefore never collapses to a compressed valid-only subset.

    The composite is the user-approved
    ``50*(N-r)/(N-1) + 50*(100-v)/99`` where ``N`` is the valid-RVOL count,
    ``r`` the descending RVOL rank (symbol ascending on ties, matching the
    existing ordinal policy) and ``v`` the full-selection turnover rank.  The
    literal 100 turnover reference is kept from the delivered workbook.
    Ordering uses the equivalent integer numerator
    ``(N-r)*99 + (100-v)*(N-1)`` so mathematically equal scores never flip on
    floating-point noise.  ``N == 1`` gives the sole valid row an RVOL
    component of 100; ``N == 0`` yields an empty ranking.  Unavailable rows
    keep ``score=None`` and never backfill from outside the Top100.
    """
    errors = errors or {}
    rows = []
    for symbol in selected:
        if symbol in errors:
            rows.append(dict(symbol=symbol, status='unavailable', sigma=None,
                             reason=str(errors[symbol])))
        else:
            rows.append(rvol_for_symbol(symbol, volumes_by_symbol.get(symbol, [])))
    for position, row in enumerate(rows, 1):
        row['turnover_rank'] = position
        row['rvol_rank'] = None
        row['rvol_score'] = None
        row['turnover_score'] = None
        row['score'] = None
        row['rank'] = None
    valid = [row for row in rows if row['status'] == 'ok']
    ordered = sorted(valid, key=lambda row: (-row['sigma'], row['symbol']))
    for rvol_rank, row in enumerate(ordered, 1):
        row['rvol_rank'] = rvol_rank
        row['turnover_score'] = (100.0 * (TURNOVER_RANK_REFERENCE - row['turnover_rank'])
                                 / 99.0)
    count = len(valid)
    if count > 1:
        for row in valid:
            rvol_rank = row['rvol_rank']
            row['rvol_score'] = 100.0 * (count - rvol_rank) / (count - 1)
            row['score'] = (50.0 * (count - rvol_rank) / (count - 1)
                            + 50.0 * (TURNOVER_RANK_REFERENCE - row['turnover_rank']) / 99.0)
        ordered = sorted(valid, key=lambda row: (
            -((count - row['rvol_rank']) * 99
              + (TURNOVER_RANK_REFERENCE - row['turnover_rank']) * (count - 1)),
            row['rvol_rank'], row['turnover_rank'], row['symbol']))
    elif count == 1:
        row = valid[0]
        row['rvol_score'] = 100.0
        row['score'] = (50.0
                        + 50.0 * (TURNOVER_RANK_REFERENCE - row['turnover_rank']) / 99.0)
        ordered = valid
    else:
        ordered = []
    for rank, row in enumerate(ordered, 1):
        row['rank'] = rank
    denominator = len(selected)
    return dict(lookback_weeks=RVOL_LOOKBACK, selected_count=denominator,
                denominator=denominator, valid_count=count,
                unavailable_count=len(rows) - count,
                coverage=(count / denominator) if denominator else None,
                scoring_version=RVOL_SCORING_VERSION,
                weights=dict(RVOL_WEIGHTS),
                turnover_reference=TURNOVER_RANK_REFERENCE,
                rank_method=('composite 50*(N-r)/(N-1)+50*(100-v)/99; ordered by '
                             'descending integer numerator (N-r)*99+(100-v)*(N-1), '
                             'then r, v, symbol'),
                rows=rows, top20=ordered[:20])


# ---------------------------------------------------------------------------
# Fisher
# ---------------------------------------------------------------------------

def fisher_for_symbol(symbol, frame, meta_record, cutoff, length=FISHER_LENGTH):
    """Fisher9 state and last-week crossing for one native weekly series."""
    cutoff = _as_utc(cutoff)
    row = dict(symbol=symbol, status='unavailable', crossing=None, reason=None,
               fisher=None, trigger=None, complete_weeks=0)
    if frame is None or getattr(frame, 'empty', True):
        row['reason'] = '缺少周线历史'
        return row
    weekly = daily_frame(frame)
    if weekly.empty or not {'high', 'low'}.issubset(weekly.columns):
        row['reason'] = '周线OHLC缺失'
        return row
    weekly = weekly.loc[weekly.index < cutoff]
    if weekly.empty:
        row['reason'] = '缺少截止前的周线'
        return row
    if weekly.index.duplicated().any():
        row['reason'] = '周线时间重复'
        return row
    if not all(stamp.weekday() == 0 and stamp == stamp.normalize()
               for stamp in weekly.index):
        row['reason'] = '周线时间戳非UTC周一00:00'
        return row
    highs = pd.to_numeric(weekly['high'], errors='coerce').to_numpy(dtype=float)
    lows = pd.to_numeric(weekly['low'], errors='coerce').to_numpy(dtype=float)
    if (not np.isfinite(highs).all() or not np.isfinite(lows).all()
            or (lows <= 0).any() or (highs < lows).any()):
        row['reason'] = '周线OHLC无效'
        return row
    starts = list(weekly.index)
    for previous, current in zip(starts, starts[1:]):
        if current - previous != pd.Timedelta(days=7):
            row['reason'] = f'周线缺口: {previous.date()}→{current.date()}'
            return row
    keep = [index for index, start in enumerate(starts) if week_complete(meta_record, start)]
    if not keep:
        row['reason'] = '没有完整周'
        return row
    for before, after in zip(keep, keep[1:]):
        if after != before + 1:
            row['reason'] = '完整周之间存在缺口'
            return row
    latest = cutoff - pd.Timedelta(days=7)
    if starts[keep[-1]] != latest:
        row['reason'] = f'缺少最新完整周（{latest.date()}）'
        return row
    row['complete_weeks'] = len(keep)
    if len(keep) < length:
        row['reason'] = f'完整周不足{length}'
        return row
    try:
        transformed = fisher_transform(highs[keep], lows[keep], length=length)
    except ValueError as exc:
        row['reason'] = f'周线OHLC无效: {exc}'
        return row
    fishers = transformed['fisher']
    if fishers.size < 3 or not np.isfinite(fishers[-3:]).all():
        row['reason'] = '有效Fisher历史不足3周'
        return row
    row.update(status='ok', fisher=float(fishers[-1]),
               trigger=float(transformed['trigger'][-1]),
               crossing=latest_crossing(fishers))
    return row


def weekly_fisher(selected, frames_by_symbol, records_by_symbol, cutoff, length=FISHER_LENGTH):
    rows = []
    for symbol in selected:
        try:
            rows.append(fisher_for_symbol(symbol, frames_by_symbol.get(symbol),
                                          records_by_symbol.get(symbol, {}), cutoff, length))
        except Exception as exc:  # pragma: no cover - defensive per-symbol isolation
            rows.append(dict(symbol=symbol, status='unavailable', crossing=None, reason=str(exc)))
    valid = [row for row in rows if row['status'] == 'ok']
    up = [row['symbol'] for row in valid if row['crossing'] == 'up']
    down = [row['symbol'] for row in valid if row['crossing'] == 'down']
    denominator = len(selected)
    return dict(length=length, selected_count=denominator, denominator=denominator,
                valid_count=len(valid), unknown_count=denominator - len(valid),
                up_count=len(up), down_count=len(down),
                up_rate=(100.0 * len(up) / denominator) if denominator else None,
                down_rate=(100.0 * len(down) / denominator) if denominator else None,
                up_crossings=up, down_crossings=down, rows=rows)


# ---------------------------------------------------------------------------
# Weekly trend
# ---------------------------------------------------------------------------

def period_quote_volume(frame, symbol, cutoff, weeks):
    """Total USDT quote turnover over the ranking's matching weeks*7-day window.

    The window is ``[cutoff - 7*weeks days, cutoff)``: the same horizon as the
    price interval, but it deliberately excludes the initial price baseline bar
    at ``cutoff - 7*weeks - 1 day`` and the still-open current week at/after
    ``cutoff``.  Every expected UTC daily bar must be present exactly once with
    a finite, non-negative ``quote_volume``; a missing/duplicate/stale/NaN or
    negative bar is never converted into a zero fallback (numeric zero is a
    valid observed turnover).
    """
    if type(weeks) is not int or weeks <= 0:
        raise ValueError('周数必须为正整数')
    cutoff = _as_utc(cutoff)
    start = cutoff - pd.Timedelta(days=7 * weeks)
    dates = pd.date_range(start=start, end=cutoff - pd.Timedelta(days=1), freq='D')
    if len(dates) != 7 * weeks:
        raise ValueError(f'{symbol} {weeks}周窗口无效')
    if frame is None or getattr(frame, 'empty', True):
        raise ValueError(f'{symbol} 日线数据为空')
    daily = daily_frame(frame)
    if daily.empty:
        raise ValueError(f'{symbol} 日线数据为空')
    if daily.index.duplicated().any():
        raise ValueError(f'{symbol} 日线时间重复')
    if 'quote_volume' not in daily.columns:
        raise ValueError(f'{symbol} 日线成交额缺失')
    rows = daily.loc[(daily.index >= start) & (daily.index < cutoff)]
    if len(rows) != len(dates) or not rows.index.equals(dates):
        raise ValueError(f'{symbol} {weeks}周日线缺失/重复/非日线')
    values = pd.to_numeric(rows['quote_volume'], errors='coerce').to_numpy(dtype=float)
    if not np.isfinite(values).all() or (values < 0).any():
        raise ValueError(f'{symbol} {weeks}周成交额无效')
    return float(values.sum())


def rank_weeks(pool, closes_by_symbol, frames_by_symbol, cutoff, weeks):
    """Rank a weekly trend pool on daily closes and window USDT turnover.

    ``frames_by_symbol`` is the required turnover evidence: the same raw daily
    frames already fetched for the weekly build (no new API calls).  A symbol
    whose price or strict turnover window is unusable is explicitly
    unavailable and never scored on fabricated or old-weight evidence.
    """
    cutoff = _as_utc(cutoff)
    pool = sorted(set(pool) - EXCLUDED_TRADING_SYMBOLS)
    as_of = cutoff - pd.Timedelta(days=1)
    rows = []
    for symbol in sorted(pool):
        row = dict(symbol=symbol, status='unavailable', eligible=False)
        try:
            sample = window(closes_by_symbol[symbol], cutoff, '1d', count=weeks * 7)
            metrics = trend_metrics(sample)
            turnover = period_quote_volume(frames_by_symbol[symbol], symbol, cutoff, weeks)
            row.update(metrics, quote_volume=turnover / (weeks * 7), window_quote_volume=turnover, status='ok',
                       eligible=metrics['return'] > 0 and metrics['slope'] > 0)
        except (KeyError, ValueError, TypeError, OverflowError) as exc:
            row['reason'] = str(exc)
        rows.append(row)
    valid = [row for row in rows if row['status'] == 'ok']
    if pool and not valid:
        raise ValueError(f'{weeks}周非空池全部价格或成交额不可用，停止发布')
    score_rows(rows, CURRENT_SCORING_VERSION, {r['symbol']: r.get('quote_volume') for r in rows})
    for row in valid:
        row['eligible'] = direction_eligible(row)
    ranked = sorted((row for row in valid if row['eligible']),
                    key=lambda row: (-row['score'], row['symbol']))
    for rank, row in enumerate(ranked, 1):
        row['rank'] = rank
    start = as_of - pd.Timedelta(days=weeks * 7)
    return dict(period=f'{weeks}w', period_weeks=weeks, interval='1d',
                observations=weeks * 7, as_of=str(as_of.date()),
                start_date=str(start.date()), pool=sorted(pool), pool_size=len(pool),
                valid_count=len(valid), uptrend_count=len(ranked),
                weights=WEEKLY_WEIGHTS.copy(), scoring_version=CURRENT_SCORING_VERSION,
                long_drawdown_limit=LONG_DRAWDOWN_LIMIT, quote_volume_unit='USDT/day',
                rows=rows, ranked=ranked, top10=ranked[:10])


# ---------------------------------------------------------------------------
# Report assembly
# ---------------------------------------------------------------------------

def weekly_volume_rankings(weekly_map, records, weeks, top_n=100):
    if type(top_n) is not int or top_n <= 0:
        raise ValueError('排名数量必须为正整数')
    rankings = {}
    for week_start in weeks:
        key = week_key(week_start)
        candidates = []
        for meta_record in records:
            entry = weekly_map.get(meta_record['symbol'], {}).get(key)
            if entry is None:
                continue
            candidates.append((meta_record['symbol'], entry['quote_volume']))
        if len(candidates) < top_n:
            raise ValueError(f'{_as_utc(week_start).date()}有效成交额候选不足{top_n}')
        ordered = sorted(candidates, key=lambda row: (-row[1], row[0]))[:top_n]
        rankings[key] = [dict(symbol=symbol, rank=rank, quote_volume=value)
                         for rank, (symbol, value) in enumerate(ordered, 1)]
    return rankings


def build_report(market, cutoff, top_n=100):
    cutoff = _as_utc(cutoff)
    if cutoff.weekday() != 0 or cutoff != cutoff.normalize():
        raise ValueError('cutoff必须为UTC周一00:00')
    as_of = cutoff - pd.Timedelta(days=1)
    rank_week_list = week_starts(cutoff, RANK_WEEKS)
    all_week_list = week_starts(cutoff, DAILY_HISTORY_WEEKS)
    report_start = cutoff - pd.Timedelta(days=RANK_HISTORY_DAYS)
    fetch_start = cutoff - pd.Timedelta(days=DAILY_HISTORY_DAYS)

    metadata, active, evidence = market.catalog(as_of)
    # Current-universe replay: exclude delisted/SETTLING/PENDING/historical-only
    # contracts BEFORE any history fetch so an injected fixture catalog cannot
    # leak a removed competitor into the rankings, indicators or trend pools.
    records = [record for record in eligible_metadata(metadata)
               if record['symbol'] in active]
    by_symbol = {record['symbol']: record for record in records}

    frames, unopened, failures = {}, [], []
    for meta_record in records:
        if not overlaps(meta_record, report_start, cutoff):
            continue
        symbol = meta_record['symbol']
        try:
            frame = market.fetch_daily(symbol, fetch_start, cutoff, limit=DAILY_FETCH_LIMIT,
                                       required_history=DAILY_HISTORY_DAYS)
            if frame is None or frame.empty:
                raise ValueError(f'{symbol} 日线数据为空')
            frames[symbol] = frame
        except Exception as exc:
            # A currently-eligible contract with a real gap or API failure must
            # still stop publication; excluding delisted is never a licence to
            # skip a valid current contract.
            failures.append(dict(symbol=symbol, reason=str(exc)))
    if failures:
        raise ValueError('历史成交额覆盖不完整，停止发布: '
                         + json.dumps(failures, ensure_ascii=False))

    weekly_map = {}
    try:
        for symbol, frame in frames.items():
            weekly_map[symbol] = aggregate_weeks(frame, by_symbol[symbol], rank_week_list)
    except ValueError as exc:
        raise ValueError('历史成交额覆盖不完整，停止发布: ' + str(exc)) from exc
    rankings = weekly_volume_rankings(weekly_map, records, rank_week_list, top_n=top_n)
    latest_key = week_key(rank_week_list[-1])
    selected = [row['symbol'] for row in rankings[latest_key]]

    volumes_by_symbol, rvol_errors = {}, {}
    for symbol in selected:
        try:
            entries = aggregate_weeks(frames[symbol], by_symbol[symbol], all_week_list)
            volumes_by_symbol[symbol] = [
                entries[key]['quote_volume']
                for week_start in all_week_list
                for key in [week_key(week_start)]
                if key in entries and entries[key]['complete']
            ]
        except Exception as exc:
            volumes_by_symbol[symbol] = []
            rvol_errors[symbol] = str(exc)
    rvol = weekly_rvol(selected, volumes_by_symbol, rvol_errors)

    native_frames, fisher_errors = {}, {}
    for symbol in selected:
        try:
            native_frames[symbol] = market.fetch(symbol, FISHER_WARMUP_LIMIT, interval='1w',
                                                 required_history=FISHER_WARMUP_LIMIT)
        except Exception as exc:
            fisher_errors[symbol] = str(exc)
    fisher = weekly_fisher(selected, native_frames, by_symbol, cutoff)
    for row in fisher['rows']:
        if row['symbol'] in fisher_errors and row['status'] != 'ok':
            row['reason'] = fisher_errors[row['symbol']]

    pools = build_pools(rankings, cutoff - pd.Timedelta(days=7), active,
                        periods=TREND_WEEKS, freq='7D', label_suffix='w')
    union = set().union(*(set(pool) for pool in pools.values())) if pools else set()
    closes = {}
    for symbol in sorted(union):
        try:
            closes[symbol] = daily_frame(frames[symbol])['close']
        except Exception:
            closes[symbol] = pd.Series(dtype=float)
    periods = {}
    for weeks in (30, 14, 7):
        label = f'{weeks}w'
        period = rank_weeks(pools[label], closes, frames, cutoff, weeks)
        counts = Counter(row['symbol']
                         for week_start in all_week_list[-weeks:]
                         for row in rankings[week_key(week_start)])
        period['min_top100_weeks'] = weeks // 2 + 1
        period['top100_week_counts'] = {symbol: counts[symbol] for symbol in period['pool']}
        periods[label] = period

    return dict(schema_version=SCHEMA_VERSION, scoring_version=TREND_SCORING_VERSION,
                rvol_scoring_version=RVOL_SCORING_VERSION,
                report_type='crypto_weekly',
                week_start=str(rank_week_list[-1].date()), week_end=str(cutoff.date()),
                cutoff_utc=cutoff.isoformat(),
                beijing_close=cutoff.tz_convert('Asia/Shanghai').isoformat(),
                top_n=top_n, weights=WEEKLY_WEIGHTS.copy(),
                rvol_lookback_weeks=RVOL_LOOKBACK, fisher_length=FISHER_LENGTH,
                pool_method=('当前可交易合约回看：仅当前 TRADING COIN USDT 永续合约'
                             '（onboard <= 截止 < delivery）参与 7/14/30 周历史周成交额前100与趋势池；'
                             '已下架/SETTLING/PENDING/历史合约排除，非历史全市场快照。'),
                universe_evidence=evidence, confirmed_unopened=unopened,
                weekly_top100=rankings, latest_top100=rankings[latest_key],
                rvol=rvol, fisher=fisher, trend={'periods': periods},
                kline_requests=market.requests)


# ---------------------------------------------------------------------------
# Messages and delivery
# ---------------------------------------------------------------------------

def symbol_label(symbol):
    return re.sub(r'([_*`\[])', r'\\\1', str(symbol).removesuffix('USDT'))


def format_amount(value):
    value = float(value)
    if abs(value) >= 1e9:
        return f'{value / 1e9:.2f}B'
    if abs(value) >= 1e6:
        return f'{value / 1e6:.2f}M'
    if abs(value) >= 1e3:
        return f'{value / 1e3:.2f}K'
    return f'{value:.0f}'


def _window_label(report):
    return (f"{report['week_start']} → {report['week_end']} UTC · "
            f"北京 {report['beijing_close'][:10]} {report['beijing_close'][11:16]} 收盘")


def build_messages(report):
    label = _window_label(report)
    rvol = report['rvol']
    fisher = report['fisher']
    messages = []

    lines = [f'*Crypto 周报 · 综合分Top20 | {label}*',
             f"最新完整周 USDT 成交额前{report['top_n']} · "
             f"综合分 = RVOL排名50% + 成交额排名50%",
             f"RVOL = 当前周 vs 前{RVOL_LOOKBACK}完整周（总体标准差）· "
             "成交额排名取全榜位次（不可用成员占位，不补位）",
             '当前可交易合约回看 · 已下架排除 · 非历史全市场快照',
             f"有效 {rvol['valid_count']}/{rvol['denominator']}"]
    for row in rvol['top20']:
        lines.append(f"{row['rank']}. *{symbol_label(row['symbol'])}*"
                     f" | 综合分 {row['score']:.2f}"
                     f" | RVOL {row['sigma']:+.2f}σ"
                     f" | 成交额 {format_amount(row['quote_volume'])} USDT"
                     f" | 成交额排名{row['turnover_rank']}")
    if not rvol['top20']:
        lines.append('无可用 RVOL 综合分。')
    if rvol['unavailable_count']:
        lines.append(f"⚠️ 不可用 {rvol['unavailable_count']} 个（完整周不足/标准差为零），不补位。")
    messages.append('\n'.join(lines))

    up_labels = '、'.join(symbol_label(symbol) for symbol in fisher['up_crossings']) or '无'
    down_labels = '、'.join(symbol_label(symbol) for symbol in fisher['down_crossings']) or '无'
    lines = [f'*Crypto 周报 · Fisher9 | {label}*',
             f"最新完整周新交叉 · 固定分母{fisher['denominator']}",
             f"有效 {fisher['valid_count']}/{fisher['denominator']} · 未知 {fisher['unknown_count']}",
             f"⬆️ 上穿 {fisher['up_count']}个（{fisher['up_rate']:.1f}%）",
             up_labels,
             f"⬇️ 下穿 {fisher['down_count']}个（{fisher['down_rate']:.1f}%）",
             down_labels,
             '上穿Trigger：上周Fisher ≤ 上周Trigger，且本周Fisher > 本周Trigger',
             '下穿Trigger：上周Fisher ≥ 上周Trigger，且本周Fisher < 本周Trigger',
             '当前可交易合约回看 · 已下架排除 · 非历史全市场快照',
             '未知历史不视为无交叉；仅统计最新完整周的新交叉。']
    messages.append('\n'.join(lines))

    for weeks in (30, 14, 7):
        period = report['trend']['periods'][f'{weeks}w']
        lines = [f'*Crypto {weeks}周趋势榜 | {label}*',
                 f"近{weeks}个完整周至少{period['min_top100_weeks']}/{weeks}周进入周成交额前100 · 日线完整收盘",
                 f"池 {period['pool_size']} · 有效 {period['valid_count']} · 上涨 {period['uptrend_count']}",
                 weight_description(),
                 '成交额分=100×V/(V+10亿USDT)，V为窗口日均额；先有效池分位计分，多头需涨幅与斜率均正、回撤≤30%。', '']
        for row in period['top10']:
            lines.append(f"{row['rank']}. *{symbol_label(row['symbol'])}* | 分 {row['score']:.1f}"
                         f" | 涨 {row['return']:+.1%} | ER {row['er']:.3f}"
                         f" | R² {row['r_squared']:.3f} | 回撤 {row['drawdown']:.1%}"
                         f" | 日均额 {format_amount(row['quote_volume'])}")
        if not period['top10']:
            lines.append('本窗口无符合条件的上涨趋势币种，不补位。')
        if period['valid_count'] < period['pool_size']:
            lines.append('⚠️ 部分成员价格或成交额历史不可用，评分基于有效成员，不补位。')
        lines += ['', '回撤=距窗口最高收盘价；分数仅代表本池相对位置。',
                  '当前可交易合约回看，已下架排除，非历史全市场快照。',
                  '描述历史趋势，初始权重未经收益预测验证。']
        messages.append('\n'.join(lines))
    return messages


# Prefer these delimiters when one line must be broken so Markdown and the
# symbol list stay intact; the delimiter stays with the left piece.
_SAFE_BREAKS = ('、', '，', ',', '；', ';', ' ', '\t')


def _split_long_line(line, limit):
    """Break one over-long line into pieces of at most ``limit`` characters.

    Splits after a safe delimiter when possible, otherwise at a Unicode
    character boundary, so the reconstructed text is identical.
    """
    pieces, remaining = [], line
    while len(remaining) > limit:
        window = remaining[:limit]
        cut = max((window.rfind(delim) for delim in _SAFE_BREAKS), default=-1)
        cut = cut + 1 if cut > 0 else limit
        pieces.append(remaining[:cut])
        remaining = remaining[cut:]
    if remaining:
        pieces.append(remaining)
    return pieces


def split_message(text, limit=TELEGRAM_LIMIT):
    """Split a Telegram message on line boundaries, never exceeding ``limit``."""
    if type(limit) is not int or limit <= 0:
        raise ValueError('消息长度上限必须为正整数')
    if len(text) <= limit:
        return [text]
    chunks, current = [], ''
    for line in text.split('\n'):
        pieces = _split_long_line(line, limit) if len(line) > limit else [line]
        for piece in pieces:
            candidate = piece if not current else current + '\n' + piece
            if len(candidate) > limit and current:
                chunks.append(current)
                current = piece
            else:
                current = candidate
    if current:
        chunks.append(current)
    return chunks


def run(scanner_dir, output_dir, dry_run=False, as_of=None, now=None, scanner=None):
    cutoff = resolve_cutoff(as_of=as_of, now=now)
    if scanner is None:
        sys.path.insert(0, str(scanner_dir))
        scanner = importlib.import_module('binance_pmarp_scanner')
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    # Current-universe replay: the weekly path neither reads daily catalogs nor
    # audits the removed-contract archive.
    market = CurrentTrendMarket(scanner, output_dir / 'weekly_cache',
                                cutoff - pd.Timedelta(days=1))
    report = build_report(market, cutoff)
    report['generated_at'] = pd.Timestamp.now(tz='UTC').isoformat()
    chunks = []
    for message in build_messages(report):
        chunks.extend(split_message(message))
    target = output_dir / f"crypto_weekly_{report['week_end']}.json"
    atomic_text(target, json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + '\n')
    for index, text in enumerate(chunks, 1):
        atomic_text(output_dir / f"crypto_weekly_{report['week_end']}_{index}.md", text + '\n')
    for text in chunks:
        print(text, flush=True)
    if not dry_run:
        for text in chunks:
            if not scanner.send_telegram_alert(text):
                raise RuntimeError('周报发送失败')
    print(f'Artifact: {target}', flush=True)
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--scanner-dir', required=True, type=Path)
    parser.add_argument('--output-dir', required=True, type=Path)
    parser.add_argument('--dry-run', action='store_true')
    parser.add_argument('--as-of', default=None,
                        help='最近完整周的周日 (YYYY-MM-DD, UTC)')
    args = parser.parse_args(argv)
    run(args.scanner_dir, args.output_dir, args.dry_run, as_of=args.as_of)


if __name__ == '__main__':
    main()
