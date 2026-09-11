"""Daily top-volume crypto: beta Top10, 4h relative momentum Top10 and overlap."""
import argparse
import importlib
import json
import math
import os
from pathlib import Path
import re
import sys

import pandas as pd

from scripts.crypto_beta_scanner import MarketData, daily_frame, scan
from scripts.compare_crypto_relative_momentum import window, score


def add_momentum(report, market, cache_dir):
    end = pd.Timestamp(report['as_of'], tz='UTC') + pd.Timedelta(days=1)
    cache_dir = Path(cache_dir) / report['as_of']
    cache_dir.mkdir(parents=True, exist_ok=True)

    def history(symbol):
        path = cache_dir / f'{symbol}_4h.csv'
        if path.exists():
            frame = pd.read_csv(path, parse_dates=['timestamp'])
        else:
            # Up to 6 current-day bars may be present; keep 181 closed prices
            # ending at the fixed prior UTC day even when rerun later today.
            frame = market.fetch(symbol, 188, interval='4h')
            temp = path.with_name(path.name + f'.{os.getpid()}.tmp')
            frame.to_csv(temp, index=False)
            os.replace(temp, path)
        data = daily_frame(frame)
        return window(data['close'], end, '4h')

    benchmark = history('BTCUSDT')
    if benchmark.pct_change().iloc[1:].var() < 1e-12:
        raise ValueError('4h BTC基准方差不足')
    rows = []
    for item in report['rows']:
        symbol = item['symbol']
        if symbol == 'BTCUSDT':
            continue
        row = dict(symbol=symbol, score=None, rs=None, status='unavailable')
        try:
            result = score(history(symbol), benchmark)
            row.update(result, status='ok', observations=180)
        except Exception as exc:
            row['reason'] = str(exc)
        rows.append(row)
    report['momentum_rows'] = rows
    report['momentum_valid_count'] = sum(r['status'] == 'ok' for r in rows)
    report['momentum_target_count'] = len(rows)
    return report


def top_lists(report):
    beta = sorted((r for r in report['rows']
                   if r['status'] == 'ok' and r['beta'] is not None
                   and math.isfinite(r['beta']) and r['beta'] > 1),
                  key=lambda r: (-r['beta'], r['symbol']))[:10]
    rs = sorted((r for r in report['momentum_rows']
                 if r['status'] == 'ok' and r['score'] is not None
                 and math.isfinite(r['score'])),
                key=lambda r: (-r['score'], r['symbol']))[:10]
    return beta, rs


def symbol_label(symbol):
    return re.sub(r'([_*`\[])', r'\\\1', symbol.removesuffix('USDT'))


def message(report):
    beta, rs = top_lists(report)
    beta_ranks = {r['symbol']: i for i, r in enumerate(beta, 1)}
    rs_ranks = {r['symbol']: i for i, r in enumerate(rs, 1)}
    shared = set(beta_ranks) & set(rs_ranks)
    lines = [f"*Crypto 双榜 | {report['as_of']} UTC*",
             f"昨日USDT成交额前{report['selected_count']} · 同一30天窗口", '',
             f"*⭐ 双榜同时入选：{len(shared)} 个*（重点关注名单）"]
    if shared:
        for row in beta:
            symbol = row['symbol']
            if symbol in shared:
                lines.append(f"⭐ *{symbol_label(symbol)}* — Beta第{beta_ranks[symbol]} / RS第{rs_ranks[symbol]}")
    else:
        lines.append('今日两个前十榜无共同币种。')
    lines += ['', f"*① Beta前十 · β > 1*（实际{len(beta)}个）",
              f"日线30收益率 · 有效{report['valid_count']}/{report['selected_count']}"]
    for i, row in enumerate(beta, 1):
        star = '⭐ ' if row['symbol'] in shared else ''
        lines.append(f"{i}. {star}{symbol_label(row['symbol'])} | β {row['beta']:.2f}")
    if not beta:
        lines.append('今日无 beta > 1 的有效币种。')
    lines += ['', f"*② RS 4h前十*（实际{len(rs)}个）",
              f"180个4h收益率 · 有效{report['momentum_valid_count']}/{report['momentum_target_count']}",
              '评分=平均超额收益/超额波动；相对涨幅以BTC计价']
    for i, row in enumerate(rs, 1):
        star = '⭐ ' if row['symbol'] in shared else ''
        lines.append(f"{i}. {star}{symbol_label(row['symbol'])} | 分 {row['score']:+.3f} | 相对 {row['rs']:+.1%}")
    if not rs:
        lines.append('暂无有效4h RS结果，不能判断双榜交集。')
    missing = report['momentum_target_count'] - report['momentum_valid_count']
    if missing or report['valid_count'] < report['selected_count']:
        lines += ['', '⚠️ 部分历史不足或数据不可用，排名及交集仅基于有效数据。']
    lines += ['', '⭐ = 两榜同时入选；历史联动与相对表现，不代表未来收益。']
    text = '\n'.join(lines)
    if len(text) > 4000:
        raise ValueError('双榜消息超过长度限制')
    return text


def run(scanner_dir, output_dir, dry_run=False):
    sys.path.insert(0, str(scanner_dir))
    scanner = importlib.import_module('binance_pmarp_scanner')
    market = MarketData(scanner)
    as_of = pd.Timestamp.now(tz='UTC').normalize() - pd.Timedelta(days=1)
    report = scan(market, as_of)
    output_dir = Path(output_dir)
    add_momentum(report, market, output_dir / '4h_cache')
    if report['momentum_valid_count'] == 0:
        raise RuntimeError('4h RS全部不可用，停止发送双榜')
    report['generated_at'] = pd.Timestamp.now(tz='UTC').isoformat()
    report['kline_requests'] = market.requests
    beta, rs = top_lists(report)
    report['beta_top10'] = [r['symbol'] for r in beta]
    report['rs_top10'] = [r['symbol'] for r in rs]
    report['overlap'] = [r['symbol'] for r in beta if r['symbol'] in report['rs_top10']]
    text = message(report)
    target = output_dir / f"crypto_dual_top10_{report['as_of']}.json"
    temp = target.with_name(target.name + f'.{os.getpid()}.tmp')
    temp.write_text(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False)+'\n')
    os.replace(temp, target)
    target.with_suffix('.md').write_text(text+'\n')
    print(text, flush=True)
    if not dry_run and not scanner.send_telegram_alert(text):
        raise RuntimeError('双榜发送失败')
    print(f'Artifact: {target}', flush=True)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--scanner-dir', required=True, type=Path)
    parser.add_argument('--output-dir', required=True, type=Path)
    parser.add_argument('--dry-run', action='store_true')
    args = parser.parse_args()
    run(args.scanner_dir, args.output_dir, args.dry_run)


if __name__ == '__main__':
    main()
