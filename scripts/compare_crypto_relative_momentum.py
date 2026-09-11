"""Descriptive same-horizon comparison of 4h and daily BTC-relative momentum."""
import argparse
import importlib
import json
from pathlib import Path
import sys
import time

import numpy as np
import pandas as pd

from backtest.metrics import _relative_metrics


def close_series(klines):
    dates = pd.to_datetime([r[0] for r in klines], unit='ms', utc=True)
    return pd.Series([float(r[4]) for r in klines], index=dates).sort_index()


def window(closes, end, interval):
    count, freq = (180, '4h') if interval == '4h' else (30, '1D')
    # end is exclusive UTC midnight. Candle timestamps denote opening times.
    delta = pd.Timedelta(hours=4) if interval == '4h' else pd.Timedelta(days=1)
    dates = pd.date_range(end=end-delta, periods=count+1, freq=freq)
    sample = closes.loc[(closes.index >= dates[0]) & (closes.index <= dates[-1])]
    if not sample.index.equals(dates):
        raise ValueError('历史不足或时间缺失/重复')
    if not np.isfinite(sample).all() or (sample <= 0).any():
        raise ValueError('无效价格')
    return sample


def score(coin, btc):
    if not coin.index.equals(btc.index):
        raise ValueError('基准时间不一致')
    active = coin.pct_change().iloc[1:] - btc.pct_change().iloc[1:]
    sigma = float(active.std(ddof=1))
    if not np.isfinite(active).all() or sigma <= 1e-10 or not np.isfinite(sigma):
        raise ValueError('超额波动为零或无效')
    # Reuse the existing IR implementation without annualization. Inputs are
    # strictly aligned above, so its legacy length truncation is never exercised.
    ir = _relative_metrics(coin.pct_change().iloc[1:].to_numpy(),
                           list(btc.items()), days_per_year=1)[2]
    return {'score': float(ir),
            'rs': float((coin.iloc[-1]/coin.iloc[0])/(btc.iloc[-1]/btc.iloc[0])-1),
            'return': float(coin.iloc[-1]/coin.iloc[0]-1)}


def ranks(rows, key):
    series = pd.Series({r['symbol']: r[key] for r in rows}, dtype=float)
    return series.rank(ascending=False, method='average')


def overlap(a, b, n=10):
    aa = set(a.sort_values().iloc[:n].index)
    bb = set(b.sort_values().iloc[:n].index)
    return len(aa & bb)


def analyze(raw, source):
    symbols = [r['symbol'] for r in source['rows'] if r['symbol'] != 'BTCUSDT']
    closes = {symbol: {interval: close_series(data) for interval, data in entries.items()}
              for symbol, entries in raw.items()}
    end = pd.Timestamp(source['as_of'], tz='UTC') + pd.Timedelta(days=1)
    checks = 0
    for symbol, entries in closes.items():
        if set(entries) != {'4h', '1d'}:
            continue
        intraday = entries['4h']
        last_of_day = intraday[intraday.index.hour == 20].copy()
        last_of_day.index = last_of_day.index.normalize()
        daily = entries['1d']
        common = daily.index.intersection(last_of_day.index)
        common = common[common < end]
        if len(common):
            if not np.allclose(daily.loc[common], last_of_day.loc[common], rtol=1e-8, atol=1e-10):
                raise ValueError('跨频率收盘价不一致: '+symbol)
            checks += len(common)
    snapshots, misses = [], []
    for days_back in range(6, -1, -1):
        cutoff = end - pd.Timedelta(days=days_back)
        benchmark = {interval: window(closes['BTCUSDT'][interval], cutoff, interval)
                     for interval in ('4h', '1d')}
        rows = []
        for symbol in symbols:
            try:
                four = score(window(closes[symbol]['4h'], cutoff, '4h'), benchmark['4h'])
                daily = score(window(closes[symbol]['1d'], cutoff, '1d'), benchmark['1d'])
                if abs(four['rs']-daily['rs']) > 1e-8:
                    raise ValueError('30日相对收益跨频率不一致')
                rows.append(dict(symbol=symbol, score_4h=four['score'], score_1d=daily['score'],
                                 rs=four['rs'], return_30d=four['return']))
            except (KeyError, ValueError) as exc:
                if days_back == 0:
                    misses.append(dict(symbol=symbol, reason=str(exc)))
        snapshots.append(dict(as_of=(cutoff-pd.Timedelta(days=1)).date().isoformat(),rows=rows))
    # Hold the eligible comparison set fixed for every stability observation.
    stable = set.intersection(*(set(r['symbol'] for r in snap['rows']) for snap in snapshots))
    if len(stable) < 10:
        raise ValueError('共同完整历史样本不足10个')
    transitions = []
    for before, after in zip(snapshots, snapshots[1:]):
        result = dict(as_of=after['as_of'])
        for interval in ('4h', '1d'):
            a = ranks([r for r in before['rows'] if r['symbol'] in stable], 'score_'+interval)
            b = ranks([r for r in after['rows'] if r['symbol'] in stable], 'score_'+interval)
            result['spearman_'+interval] = float(a.corr(b))
            result['top10_retained_'+interval] = overlap(a,b)
        transitions.append(result)
    latest = snapshots[-1]['rows']
    r4, rd = ranks(latest,'score_4h'), ranks(latest,'score_1d')
    betas = {r['symbol']:r['beta'] for r in source['rows']}
    for row in latest:
        row.update(rank_4h=float(r4[row['symbol']]), rank_1d=float(rd[row['symbol']]),
                   beta=betas[row['symbol']])
    latest.sort(key=lambda r:r['rank_4h'])
    return dict(as_of=source['as_of'], close_checks=checks, eligible=len(latest),
                stable_universe_count=len(stable), cross_frequency_spearman=float(r4.corr(rd)),
                cross_frequency_top10_overlap=overlap(r4,rd), latest=latest, unavailable=misses,
                transitions=transitions, snapshots=snapshots)


def markdown(result):
    lines=['# 相对BTC动量：180×4h vs 30×日线', '',
           f"截至 {result['as_of']} UTC；固定当日成交额前50，排除BTC自身；共同有效 {result['eligible']} 个。", '',
           '评分＝同期简单超额收益均值/样本标准差；不年化，跨频率只比较排名。', '',
           f"- 最新跨频率排名Spearman：{result['cross_frequency_spearman']:.3f}",
           f"- 两频率Top10重合：{result['cross_frequency_top10_overlap']}/10",
           f"- 跨频率日末收盘价对拍：{result['close_checks']} 个观测通过",
           f"- 7个日端点的固定完整样本：{result['stable_universe_count']} 个", '',
           '|日期|4h相邻日排名相关|日线相邻日排名相关|4h Top10留存|日线 Top10留存|',
           '|---|---:|---:|---:|---:|']
    for r in result['transitions']:
        lines.append(f"|{r['as_of']}|{r['spearman_4h']:.3f}|{r['spearman_1d']:.3f}|{r['top10_retained_4h']}/10|{r['top10_retained_1d']}/10|")
    lines+=['', '完整排名（beta>1标记沿用原日线快照）：','',
            '|币种|4h排名|日线排名|30D相对BTC涨幅|自身30D涨幅|原beta>1|',
            '|---|---:|---:|---:|---:|---|']
    for r in result['latest']:
        yes='是' if r['beta'] is not None and r['beta']>1 else '否'
        lines.append(f"|{r['symbol']}|{r['rank_4h']:.0f}|{r['rank_1d']:.0f}|{r['rs']:.2%}|{r['return_30d']:.2%}|{yes}|")
    lines+=['','缺失：'+', '.join(r['symbol'] for r in result['unavailable']), '',
            '边界：固定今日池回看，不是PIT历史选股；重叠30天窗口天然相关。仅6次排名变动，不能据此证明4h更优或具有预测alpha；稳定也可能只是反应慢。现货与永续不可混用。']
    return '\n'.join(lines)+'\n'


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-report',type=Path,required=True)
    parser.add_argument('--scanner-dir',type=Path,required=True)
    parser.add_argument('--output-dir',type=Path,required=True)
    args=parser.parse_args()
    args.output_dir.mkdir(parents=True,exist_ok=True)
    source=json.loads(args.source_report.read_text())
    symbols=sorted({r['symbol'] for r in source['rows']}|{'BTCUSDT'})
    sys.path.insert(0,str(args.scanner_dir))
    api=importlib.import_module('binance_pmarp_scanner')
    raw={}
    for i,symbol in enumerate(symbols,1):
        raw[symbol]={}
        for interval,limit in [('4h',224),('1d',39)]:
            path=args.output_dir/f'{symbol}_{interval}.json'
            if path.exists():
                data=json.loads(path.read_text())
            else:
                time.sleep(1)
                data=api.fetch_klines(symbol,interval=interval,limit=limit)
                if not data:
                    raise RuntimeError('行情请求失败: '+symbol+' '+interval)
                path.write_text(json.dumps(data,ensure_ascii=False))
            raw[symbol][interval]=data
        print(f'{i}/{len(symbols)} {symbol}',flush=True)
    result=analyze(raw,source)
    (args.output_dir/'comparison.json').write_text(json.dumps(result,ensure_ascii=False,indent=2,allow_nan=False)+'\n')
    (args.output_dir/'comparison.md').write_text(markdown(result))
    print(json.dumps({k:v for k,v in result.items() if k not in ('snapshots','latest')},ensure_ascii=False),flush=True)


if __name__=='__main__':
    main()
