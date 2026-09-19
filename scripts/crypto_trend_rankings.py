"""Descriptive 7/14/30-day trend rankings within persistent daily Top100 pools."""
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
from scripts.crypto_trend_market import TrendMarket, eligible_metadata, overlaps

PERIODS = (7, 14, 30)
WEIGHTS = {'return': .4, 'er': .2, 'r_squared': .2, 'drawdown': .2}


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


def build_pools(daily_rankings, as_of, active_symbols, periods=PERIODS):
    as_of = utc_day(as_of)
    pools = {}
    for days in periods:
        sets = []
        for day in pd.date_range(end=as_of, periods=days, freq='D'):
            rows = daily_rankings.get(str(day.date()))
            if not isinstance(rows, list) or not rows:
                raise ValueError(f'{day.date()}完整排名缺失')
            symbols = [r.get('symbol') for r in rows]
            if (any(not isinstance(s, str) or not s for s in symbols)
                    or len(set(symbols)) != len(symbols)
                    or [r.get('rank') for r in rows] != list(range(1, len(rows)+1))):
                raise ValueError(f'{day.date()}排名重复或无效')
            sets.append(set(symbols))
        pools[f'{days}d'] = sorted(set.intersection(*sets) & set(active_symbols))
    return pools


def rank_period(pool, closes_by_symbol, as_of, days):
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
    for row in valid:
        row['percentiles'] = {}
        for key in WEIGHTS:
            sign = -1 if key == 'drawdown' else 1
            row['percentiles'][key] = 100*sum(sign*r[key] <= sign*row[key] for r in valid)/len(valid)
        row['score'] = sum(row['percentiles'][key]*weight for key, weight in WEIGHTS.items())
    ranked = sorted((row for row in valid if row['eligible']), key=lambda r: (-r['score'], r['symbol']))
    for i, row in enumerate(ranked, 1):
        row['rank'] = i
    return dict(period=f'{days}d', period_days=days, interval='4h', observations=days*6,
                as_of=str(as_of.date()), start_date=str((as_of-pd.Timedelta(days=days-1)).date()),
                pool=sorted(pool), pool_size=len(pool), valid_count=len(valid),
                uptrend_count=len(ranked), rows=rows, ranked=ranked, top10=ranked[:10])


def build_report(market, as_of, top_n=100):
    as_of = utc_day(as_of)
    dates = pd.date_range(end=as_of, periods=30, freq='D')
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
                frame = market.fetch_daily(symbol, dates[0], as_of+pd.Timedelta(days=1))
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
    daily = daily_volume_rankings(frames, metadata, as_of, top_n=top_n, confirmed_unopened=unopened)
    pools = build_pools(daily, as_of, active)
    closes, price_failures = {}, {}
    for symbol in sorted(set().union(*(set(pool) for pool in pools.values()))):
        try:
            frame = daily_frame(market.fetch(symbol, 188, interval='4h'))
            closes[symbol] = frame['close']
        except Exception as exc:
            price_failures[symbol] = str(exc)
    periods = {f'{days}d': rank_period(pools[f'{days}d'], closes, as_of, days) for days in PERIODS}
    for period in periods.values():
        for row in period['rows']:
            if row['symbol'] in price_failures:
                row['reason'] = price_failures[row['symbol']]
    return dict(schema_version=1, as_of=str(as_of.date()), top_n=top_n,
                weights=WEIGHTS.copy(), percentile_method='100 * count(values <= current) / valid_pool_count; drawdown reversed',
                direction_gate='return > 0 and log_price_slope > 0; applied AFTER pool percentiles',
                pool_method='intersection of daily UTC quote-volume TopN over each horizon; currently tradable candidates',
                universe_evidence=evidence, confirmed_unopened=unopened,
                daily_top100=daily, periods=periods, kline_requests=market.requests)


def message(report, days):
    period = report['periods'][f'{days}d']
    lines = [f"*Crypto {days}天趋势榜 | {report['as_of']} UTC*",
             f"窗口内每日USDT成交额前{report['top_n']}的交集 · 4h完整收盘",
             f"池 {period['pool_size']} · 有效 {period['valid_count']} · 上涨 {period['uptrend_count']}",
             '权重：涨幅40% / ER20% / R²20% / 回撤20%',
             '分位在本窗口有效池内计算；上涨需涨幅与回归斜率均为正。', '']
    for row in period['top10']:
        symbol = re.sub(r'([_*`\[])', r'\\\1', row['symbol'].removesuffix('USDT'))
        lines.append(f"{row['rank']}. *{symbol}* | 分 {row['score']:.1f} | 涨 {row['return']:+.1%}"
                     f" | ER {row['er']:.3f} | R² {row['r_squared']:.3f} | 回撤 {row['drawdown']:.1%}")
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
    report = build_report(market, as_of)
    report['generated_at'] = pd.Timestamp.now(tz='UTC').isoformat()
    texts = [(days, message(report, days)) for days in (30,14,7)]
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
