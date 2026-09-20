"""Trend rankings for coins in daily turnover Top100 on a strict majority of days."""
from collections import Counter
import importlib
import json
import math
import os
from pathlib import Path
import re
import sys

import pandas as pd

from scripts.compare_crypto_relative_momentum import window
from scripts.crypto_beta_scanner import daily_frame
from scripts.crypto_trend_metrics import trend_metrics
from scripts.crypto_trend_market import TrendMarket, InactiveSymbolError, eligible_metadata, overlaps

PERIODS = (7, 14, 30)
DAILY_PERIODS = (10, 14)
WEIGHTS = {'return': .4, 'er': .2, 'r_squared': .2, 'drawdown': .2}
STRENGTH_WEIGHTS = {'absolute_return': .5, 'er': .15, 'r_squared': .15,
                    'drawdown': .1, 'quote_volume': .1}


def score_rows(rows, version='v1', quote_turnover=None):
    """Shared research/report score; percentiles precede direction filtering."""
    if version not in ('v1','v2'):
        raise ValueError('unknown scoring version')
    valid=[r for r in rows if r['status']=='ok']
    weights=WEIGHTS if version=='v1' else STRENGTH_WEIGHTS
    if version=='v2':
        for row in valid:
            value=(quote_turnover or {}).get(row['symbol'])
            if value is None or not math.isfinite(float(value)) or float(value)<0:
                raise ValueError('missing/invalid turnover '+row['symbol'])
            row['absolute_return']=abs(row['return'])
            row['quote_volume']=float(value)
    score_valid_rows(valid,weights)
    return rows


def utc_day(value):
    day = pd.Timestamp(value)
    day = day.tz_localize('UTC') if day.tzinfo is None else day.tz_convert('UTC')
    if day != day.normalize():
        raise ValueError('as_of必须为UTC整日')
    return day


def volume_history(frame, meta, dates):
    """Validate ALL relevant dates; never convert a missing observation to zero."""
    frame = daily_frame(frame)
    result = {}
    for day in dates:
        if not overlaps(meta, day, day+pd.Timedelta(days=1)):
            continue
        if frame.empty or 'quote_volume' not in frame:
            raise ValueError(f"{meta['symbol']} {day.date()}成交额缺失")
        rows = frame.loc[(frame.index >= day) & (frame.index < day+pd.Timedelta(days=1))]
        if len(rows) != 1 or rows.index[0] != day:
            raise ValueError(f"{meta['symbol']} {day.date()}成交额日期缺失/重复/非日线")
        value = float(rows['quote_volume'].iloc[0])
        if not math.isfinite(value) or value < 0:
            raise ValueError(f"{meta['symbol']} {day.date()}成交额无效")
        result[str(day.date())] = value
    return result


def daily_volume_rankings(frames, metadata, as_of, days=30, top_n=100,
                          confirmed_unopened=()):
    if type(top_n) is not int or top_n <= 0 or type(days) is not int or days <= 0:
        raise ValueError('排名数量及天数必须为正整数')
    dates = pd.date_range(end=utc_day(as_of), periods=days, freq='D')
    volumes = {str(day.date()): [] for day in dates}
    records = eligible_metadata(metadata)
    for meta in records:
        symbol = meta['symbol']
        if symbol in confirmed_unopened:
            if meta['status'] != 'PENDING_TRADING':
                raise ValueError('仅明确未开盘的PENDING合约可豁免')
            continue
        for date, value in volume_history(frames.get(symbol), meta, dates).items():
            volumes[date].append((symbol, value))
    rankings = {}
    for date, rows in volumes.items():
        if len(rows) < top_n:
            raise ValueError(f'{date}有效成交额候选不足{top_n}')
        ordered = sorted(rows, key=lambda row: (-row[1], row[0]))[:top_n]
        rankings[date] = [dict(symbol=symbol, rank=i, quote_volume=value)
                          for i, (symbol, value) in enumerate(ordered, 1)]
    return rankings


def score_valid_rows(valid, weights=None):
    """Attach percentiles and an integer weighted score to valid rows.

    Percentiles use ``100 * count(values <= current) / valid_count`` over ALL
    measured pool members (the caller applies the direction gate afterwards).
    Drawdown is scored reversed so a smaller drawdown is better.  The integer
    point numerator preserves exact ties; separately summing rounded
    percentile floats can invent 1e-14 gaps.  Shared by the daily 4h and
    weekly 1d ranking kernels.

    ``weights`` defaults to the daily :data:`WEIGHTS` (40/20/20/20) so every
    existing daily caller keeps its exact behavior; the weekly kernel passes
    its own approved return/ER/R²/drawdown/turnover weights explicitly.
    """
    weights = WEIGHTS if weights is None else weights
    for row in valid:
        row['percentiles'] = {}
        counts = {}
        for key in weights:
            sign = -1 if key == 'drawdown' else 1
            counts[key] = sum(sign*r[key] <= sign*row[key] for r in valid)
            row['percentiles'][key] = 100*counts[key]/len(valid)
        row['score'] = sum(counts[key]*(weight*100) for key, weight in weights.items())/len(valid)
    return valid


def build_pools(daily_rankings, as_of, active_symbols, periods=PERIODS, freq='D',
                label_suffix='d'):
    as_of = utc_day(as_of)
    pools = {}
    for days in periods:
        counts = Counter()
        for day in pd.date_range(end=as_of, periods=days, freq=freq):
            rows = daily_rankings.get(str(day.date()))
            if not isinstance(rows, list) or not rows:
                raise ValueError(f'{day.date()}完整排名缺失')
            symbols = [r.get('symbol') for r in rows]
            if (any(not isinstance(s, str) or not s for s in symbols)
                    or len(set(symbols)) != len(symbols)
                    or [r.get('rank') for r in rows] != list(range(1, len(rows)+1))):
                raise ValueError(f'{day.date()}排名重复或无效')
            counts.update(symbols)
        pools[f'{days}{label_suffix}'] = sorted(
            symbol for symbol, count in counts.items()
            if count >= days//2+1 and symbol in active_symbols)
    return pools


def rank_period(pool, closes_by_symbol, as_of, days, scoring_version='v1', quote_turnover=None):
    as_of = utc_day(as_of)
    end = as_of+pd.Timedelta(days=1)
    rows = []
    for symbol in sorted(pool):
        row = dict(symbol=symbol, status='unavailable', eligible=False)
        try:
            sample = window(closes_by_symbol[symbol], end, '4h', count=days*6)
            metrics = trend_metrics(sample)
            row.update(metrics, status='ok', eligible=metrics['return'] > 0 and metrics['slope'] > 0)
        except (KeyError, ValueError, TypeError, OverflowError) as exc:
            row['reason'] = str(exc)
        rows.append(row)
    valid = [row for row in rows if row['status'] == 'ok']
    if pool and not valid:
        raise ValueError(f'{days}天非空池全部价格不可用，停止发布')
    score_rows(rows, scoring_version, quote_turnover)
    ranked = sorted((row for row in valid if row['eligible']), key=lambda r: (-r['score'], r['symbol']))
    for i, row in enumerate(ranked, 1):
        row['rank'] = i
    return dict(period=f'{days}d', period_days=days, interval='4h', observations=days*6,
                as_of=str(as_of.date()), start_date=str((as_of-pd.Timedelta(days=days-1)).date()),
                pool=sorted(pool), pool_size=len(pool), valid_count=len(valid),
                uptrend_count=len(ranked), rows=rows, ranked=ranked, top10=ranked[:10])


def build_report(market, as_of, top_n=100, scoring_version='v1', periods=PERIODS):
    as_of = utc_day(as_of)
    windows = tuple(periods)
    if not windows or len(set(windows)) != len(windows) or any(type(h) is not int or h <= 0 for h in windows):
        raise ValueError('报告周期须为不重复的正整数')
    history_days = max(windows)
    dates = pd.date_range(end=as_of, periods=history_days, freq='D')
    metadata, active, evidence = market.catalog(as_of)
    metadata = eligible_metadata(metadata)
    frames, unopened, failures = {}, [], []
    for meta in metadata:
        if not overlaps(meta, dates[0], as_of+pd.Timedelta(days=1)):
            continue
        symbol = meta['symbol']
        try:
            try:
                frame = market.cached(symbol)
                volume_history(frame, meta, dates)
            except (ValueError, TypeError, KeyError, OSError, OverflowError):
                try:
                    frame = market.fetch_daily(symbol, dates[0], as_of+pd.Timedelta(days=1))
                except InactiveSymbolError:
                    if (meta['status'] == 'PENDING_TRADING'
                            and not market.has_archive_activity(symbol, dates[0], as_of+pd.Timedelta(days=1))):
                        unopened.append(symbol)
                        continue
                    raise
                if (frame.empty and meta['status'] == 'PENDING_TRADING'
                        and not market.has_archive_activity(symbol, dates[0], as_of+pd.Timedelta(days=1))):
                    unopened.append(symbol)
                    continue
                volume_history(frame, meta, dates)
            frames[symbol] = frame
        except Exception as exc:
            failures.append(dict(symbol=symbol, reason=str(exc)))
    if failures:
        raise ValueError('历史成交额覆盖不完整，停止发布: '+json.dumps(failures, ensure_ascii=False))
    daily = daily_volume_rankings(frames, metadata, as_of, days=history_days, top_n=top_n, confirmed_unopened=unopened)
    pools = build_pools(daily, as_of, active, periods=windows)
    closes, price_failures = {}, {}
    for symbol in sorted(set().union(*(set(pool) for pool in pools.values()))):
        try:
            # Retain the existing daily-cache history contract for legacy callers.
            frame = daily_frame(market.fetch(symbol, 188, interval='4h'))
            closes[symbol] = frame['close']
        except Exception as exc:
            price_failures[symbol] = str(exc)
    periods={}
    for days in windows:
        quotes=None
        if scoring_version=='v2':
            dates=pd.date_range(end=as_of,periods=days,freq='D')
            quotes={s:float(daily_frame(frames[s]).reindex(dates)['quote_volume'].mean())
                    for s in pools[f'{days}d']}
            # Complete volume history was checked before pool construction.
            # A price-valid coin needs every day of its own score window.
            for s in quotes:
                if daily_frame(frames[s]).reindex(dates)['quote_volume'].isna().any():
                    quotes[s]=None
        periods[f'{days}d']=rank_period(pools[f'{days}d'],closes,as_of,days,scoring_version,quotes)
    for period in periods.values():
        days = period['period_days']
        counts = Counter(row['symbol']
                         for date in pd.date_range(end=as_of, periods=days, freq='D')
                         for row in daily[str(date.date())])
        period['min_top100_days'] = days//2+1
        period['top100_day_counts'] = {symbol: counts[symbol] for symbol in period['pool']}
        for row in period['rows']:
            if row['symbol'] in price_failures:
                row['reason'] = price_failures[row['symbol']]
    return dict(schema_version=3 if scoring_version=='v2' else 2, scoring_version=scoring_version,
                as_of=str(as_of.date()), top_n=top_n,
                weights=(STRENGTH_WEIGHTS if scoring_version=='v2' else WEIGHTS).copy(), percentile_method='100 * count(values <= current) / valid_pool_count; drawdown reversed',
                direction_gate='return > 0 and log_price_slope > 0; applied AFTER pool percentiles',
                pool_method='daily UTC quote-volume TopN on strictly more than half of ALL horizon days; currently tradable candidates',
                universe_evidence=evidence, confirmed_unopened=unopened,
                daily_top100=daily, periods=periods, kline_requests=market.requests)


def message(report, days):
    period = report['periods'][f'{days}d']
    strength=report.get('scoring_version')=='v2'
    lines = [f"*Crypto {days}天趋势榜 | {report['as_of']} UTC*",
             f"窗口内至少{period['min_top100_days']}/{days}天USDT成交额前{report['top_n']} · 4h完整收盘",
             f"池 {period['pool_size']} · 有效 {period['valid_count']} · 上涨 {period['uptrend_count']}",
             ('权重：绝对涨跌幅50% / ER15% / R²15% / 回撤10% / 成交额10%' if strength
              else '权重：涨幅40% / ER20% / R²20% / 回撤20%'),
             '分位在本窗口有效池内计算；上涨需涨幅与回归斜率均为正。', '']
    if strength:
        cutoff = (utc_day(report['as_of'])+pd.Timedelta(days=1)).tz_convert('Asia/Shanghai')
        lines.insert(1, f'数据截至 {cutoff:%Y-%m-%d %H:%M} 北京时间')
    for row in period['top10']:
        symbol = re.sub(r'([_*`\[])', r'\\\1', row['symbol'].removesuffix('USDT'))
        lines.append(f"{row['rank']}. *{symbol}* | 分 {row['score']:.1f} | 涨 {row['return']:+.1%}"
                     f" | ER {row['er']:.3f} | R² {row['r_squared']:.3f} | 回撤 {row['drawdown']:.1%}"
                     +(f" | 日均额 {row['quote_volume']/1e6:.1f}M USDT" if strength else ''))
    if not period['top10']:
        lines.append('本窗口无符合条件的上涨趋势币种，不补位。')
    if period['valid_count'] < period['pool_size']:
        lines.append('⚠️ 部分成员价格历史不可用，评分基于有效成员，不补位。')
    lines += ['', '回撤=距窗口最高收盘价；分数仅代表本池相对位置。',
              '历史资格按现有上市/下架证据重建，非原始逐日快照。',
              '描述历史趋势，初始权重未经收益预测验证。']
    text = '\n'.join(lines)
    if len(text) >= 4000:
        raise ValueError('趋势榜消息超过长度限制')
    return text


def atomic_text(path, content):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name+f'.{os.getpid()}.tmp')
    temp.write_text(content)
    os.replace(temp, path)


def run(scanner_dir, output_dir, dry_run=False):
    sys.path.insert(0, str(scanner_dir))
    scanner = importlib.import_module('binance_pmarp_scanner')
    as_of = pd.Timestamp.now(tz='UTC').normalize()-pd.Timedelta(days=1)
    output_dir = Path(output_dir)
    market = TrendMarket(scanner, output_dir/'trend_cache', as_of)
    report = build_report(market, as_of, scoring_version='v2', periods=DAILY_PERIODS)
    report['generated_at'] = pd.Timestamp.now(tz='UTC').isoformat()
    report['published_period_days'] = list(DAILY_PERIODS)
    report['data_cutoff_utc'] = (utc_day(report['as_of'])+pd.Timedelta(days=1)).isoformat()
    texts = [(days, message(report, days)) for days in DAILY_PERIODS]
    target = output_dir/f"crypto_trend_top10_{report['as_of']}.json"
    atomic_text(target, json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False)+'\n')
    for days, text in texts:
        atomic_text(output_dir/f"crypto_trend_top10_{days}d_{report['as_of']}.md", text+'\n')
    for days, text in texts:
        print(text, flush=True)
        if not dry_run and not scanner.send_telegram_alert(text):
            raise RuntimeError(f'{days}天趋势榜发送失败')
    print(f'Artifact: {target}', flush=True)
    return report
