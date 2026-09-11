"""BTC 30-day beta for the prior UTC day's top-50 USDT perpetuals."""
import argparse
import importlib
import json
import math
import os
import re
from pathlib import Path
import sys
import time

import numpy as np
import pandas as pd

from src.indicators.beta import compute_beta


def daily_frame(frame):
    if frame is None or frame.empty or 'timestamp' not in frame:
        return pd.DataFrame()
    frame = frame.copy()
    frame.index = pd.to_datetime(frame['timestamp'], utc=True, errors='raise')
    return frame.sort_index()


def day_volume(frame, as_of):
    if frame.empty or 'quote_volume' not in frame:
        raise ValueError('成交额缺失')
    rows = frame.loc[frame.index == as_of, 'quote_volume']
    if len(rows) != 1:
        raise ValueError('成交额日期缺失或重复')
    value = float(rows.iloc[0])
    if not math.isfinite(value) or value < 0:
        raise ValueError('成交额无效')
    return value


def closes_31(frame, as_of):
    dates = pd.date_range(end=as_of, periods=31, freq='D')
    if frame.empty or 'close' not in frame:
        raise ValueError('缺少连续31日收盘价')
    window = frame.loc[(frame.index >= dates[0]) & (frame.index <= as_of)]
    if len(window) != 31 or not window.index.equals(dates):
        raise ValueError('历史不足31日、缺日或日期重复')
    closes = pd.to_numeric(window['close'], errors='coerce')
    if not np.isfinite(closes).all() or (closes <= 0).any():
        raise ValueError('收盘价无效')
    return closes


def measure(closes, benchmark):
    beta = compute_beta(closes, benchmark, window=30, min_obs=30)
    if beta is None:
        raise ValueError('BTC方差不足或收益率无效')
    own_returns = closes.pct_change().dropna()
    btc_returns = benchmark.pct_change().dropna()
    correlation = (float(own_returns.corr(btc_returns))
                   if own_returns.std() > 0 else None)
    if correlation is not None and not math.isfinite(correlation):
        correlation = None
    return float(beta), correlation


class MarketData:
    """Reuse Quant's API, conversion and cache reader; never mutate shared cache."""
    def __init__(self, scanner):
        self.scanner = scanner
        self.requests = 0

    def symbols(self):
        symbols = self.scanner.get_all_usdt_futures()
        if not symbols or len(symbols) != len(set(symbols)):
            raise RuntimeError('活跃合约列表不可用或重复')
        return sorted(symbols)

    def cached(self, symbol):
        return self.scanner.load_cached_data(symbol)

    def fetch(self, symbol, limit, interval='1d'):
        time.sleep(1)
        self.requests += 1
        klines = self.scanner.fetch_klines(symbol, interval=interval, limit=limit)
        if not klines:
            raise RuntimeError('K线API不可用')
        return self.scanner.klines_to_dataframe(klines)


def scan(market, as_of, top_n=50):
    symbols = market.symbols()
    frames, volumes, failures = {}, {}, []
    for symbol in symbols:
        try:
            try:
                frame = daily_frame(market.cached(symbol))
                volume = day_volume(frame, as_of)
            except (ValueError, TypeError, KeyError, OverflowError):
                frame = daily_frame(market.fetch(symbol, 2))
                # A contract launched today has no prior-day trading to rank.
                if not frame.empty and frame.index.min() > as_of:
                    continue
                volume = day_volume(frame, as_of)
            frames[symbol], volumes[symbol] = frame, volume
        except Exception as exc:
            failures.append({'symbol': symbol, 'reason': str(exc)})
    if failures:
        raise RuntimeError('昨日成交额覆盖不完整，停止发布Top50: ' +
                           json.dumps(failures, ensure_ascii=False))
    ranked = sorted(volumes, key=lambda s: (-volumes[s], s))[:top_n]
    if len(ranked) != top_n:
        raise RuntimeError(f'昨日可排名合约不足{top_n}个')

    def history(symbol):
        frame = frames.get(symbol, pd.DataFrame())
        try:
            return closes_31(frame, as_of)
        except (ValueError, TypeError, KeyError, OverflowError):
            return closes_31(daily_frame(market.fetch(symbol, 32)), as_of)

    benchmark = history('BTCUSDT')
    measure(benchmark, benchmark)  # Fail the whole scan if BTC is unusable.
    rows = []
    for rank, symbol in enumerate(ranked, 1):
        row = dict(symbol=symbol, volume_rank=rank, quote_volume=volumes[symbol],
                   beta=None, correlation=None, observations=0, status='unavailable')
        try:
            closes = benchmark if symbol == 'BTCUSDT' else history(symbol)
            beta, correlation = measure(closes, benchmark)
            row.update(beta=beta, correlation=correlation, observations=30, status='ok')
        except Exception as exc:
            row['reason'] = str(exc)
        rows.append(row)
    rows.sort(key=lambda row: (row['beta'] is None,
                              -row['beta'] if row['beta'] is not None else 0,
                              row['symbol']))
    return dict(as_of=as_of.date().isoformat(), benchmark='BTCUSDT', window=30,
                universe_size=len(symbols), ranked_coverage=len(volumes),
                selected_count=top_n, valid_count=sum(r['status'] == 'ok' for r in rows),
                rows=rows)


def messages(report):
    rows = [row for row in report['rows']
            if row['status'] == 'ok' and row['beta'] is not None and row['beta'] > 1]
    header = (f"BTC Beta 30D | 截至 {report['as_of']} UTC\n"
              f"昨日USDT成交额前{report['selected_count']} | 仅β > 1 | 按beta降序\n"
              f"有效 {report['valid_count']}/{report['selected_count']} | 符合 {len(rows)} 个 | 30个日收益率\n"
              "币种 | β30D | BTC相关性 | 昨日成交额\n")
    if not rows:
        return [header + '今日无 beta > 1 的币种。']
    chunks, lines = [], []
    for row in rows:
        symbol = row['symbol'].removesuffix('USDT')
        symbol = re.sub(r'([_*`\[])', r'\\\1', symbol)
        amount = f"{row['quote_volume'] / 1e6:,.1f}M"
        corr = f"{row['correlation']:.2f}" if row['correlation'] is not None else 'N/A'
        line = f"{symbol} | {row['beta']:.2f} | {corr} | {amount} USDT"
        lines.append(line)
    # Fixed small pages keep each payload below Telegram's 4096-char limit.
    for start in range(0, len(lines), 20):
        chunks.append(header + '\n'.join(lines[start:start+20]) +
                      '\nβ为历史联动敏感度，不代表未来涨跌。')
    return chunks


def run(scanner_dir, output_dir, dry_run=False):
    sys.path.insert(0, str(scanner_dir))
    scanner = importlib.import_module('binance_pmarp_scanner')
    market = MarketData(scanner)
    as_of = pd.Timestamp.now(tz='UTC').normalize() - pd.Timedelta(days=1)
    report = scan(market, as_of)
    report['generated_at'] = pd.Timestamp.now(tz='UTC').isoformat()
    report['kline_requests'] = market.requests
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    target = output_dir / f"btc_beta_30d_{report['as_of']}.json"
    temp = target.with_name(target.name + f'.{os.getpid()}.tmp')
    temp.write_text(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + '\n')
    os.replace(temp, target)
    for message in messages(report):
        print(message, flush=True)
        if not dry_run and not scanner.send_telegram_alert(message):
            raise RuntimeError('Beta榜单发送失败')
    print(f"Artifact: {target}; Kline requests: {market.requests}", flush=True)
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--scanner-dir', required=True, type=Path)
    parser.add_argument('--output-dir', required=True, type=Path)
    parser.add_argument('--dry-run', action='store_true')
    args = parser.parse_args(argv)
    run(args.scanner_dir, args.output_dir, args.dry_run)


if __name__ == '__main__':
    main()
