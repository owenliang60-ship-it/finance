"""Daily all-crypto USDT perpetual PMARP breadth and trailing-year percentiles.

Reuses the Quant scanner's PMARP kernel and TrendMarket's historical catalog.
The universe is effective-dated, includes BTC/delisted contracts, and never
uses the momentum Top100 gate. All files are task-owned; no market.db writes.
"""
import argparse
import hashlib
import importlib
import json
from pathlib import Path
import sys

import numpy as np
import pandas as pd

from scripts.crypto_beta_scanner import daily_frame
from scripts.crypto_trend_market import TrendMarket, eligible_metadata, fetch_daily_or_confirm_unopened
from scripts.crypto_trend_rankings import atomic_text, utc_day

LOOKBACK_DAYS = 365
EMA_PERIOD = 20
PMARP_LOOKBACK = 150
# Same kernel as the daily scanner. 365 seed bars before the first reported
# day allow EMA stabilization before the 150-day comparison window.
SEED_DAYS = 365


MANIFEST_PATH = Path(__file__).resolve().parents[1]/'config/crypto_pmarp_breadth_sources.json'
BUNDLED_RETIRED_DIR = MANIFEST_PATH.parents[1]/'reports/crypto-pmarp-breadth-2026-09-28/breadth_cache/retired_prices'


class BreadthMarket(TrendMarket):
    """Task-specific, hash-pinned prices for REST-unavailable retired contracts."""
    def __init__(self, *args, manifest, retired_dir, **kwargs):
        self.retired_dir = Path(retired_dir)
        self.retired_sources = manifest['retired_prices']
        errors = []
        for symbol in sorted(self.retired_sources):
            try:
                self._retired_rows(symbol)
            except (ValueError, OSError) as exc:
                errors.append(symbol+': '+str(exc))
        if errors:
            raise ValueError('退市价格预检查失败: '+'; '.join(errors))
        super().__init__(*args, supplemental_records=manifest['symbols'],
                         archive_retirements=manifest['archive_retirements'], **kwargs)
        for meta in self.supplemental_records:
            if meta.get('onboard_precision') == 'date':
                seed_start = self.as_of-pd.Timedelta(days=LOOKBACK_DAYS+SEED_DAYS)
                if seed_start <= pd.Timestamp(meta['onboardDate'],unit='ms',tz='UTC')+pd.Timedelta(days=1):
                    raise ValueError('日期精度上市证据不足以覆盖该暖机窗口: '+meta['symbol'])

    def catalog(self, as_of):
        records, active, evidence = super().catalog(as_of)
        self.lifetimes = {m['symbol']:m for m in records}
        return records, active, evidence

    def fetch_daily(self, symbol, start, end, **kwargs):
        if symbol not in self.retired_sources:
            from scripts.crypto_breadth_cache import daily_rows
            rows = daily_rows(self,symbol,start,end,self.lifetimes[symbol])
            return self.scanner.klines_to_dataframe(rows) if rows else pd.DataFrame()
        return self.scanner.klines_to_dataframe(self._retired_rows(symbol))

    def _retired_rows(self, symbol):
        raw = (self.retired_dir/(symbol+'.json')).read_bytes()
        if hashlib.sha256(raw).hexdigest() != self.retired_sources[symbol]['sha256']:
            raise ValueError('退市价格hash不符: '+symbol)
        rows = json.loads(raw)
        if not isinstance(rows, list) or not rows:
            raise ValueError('退市价格格式错误: '+symbol)
        return rows


def percentile(current, history):
    values = np.asarray(history, dtype=float)
    if not len(values) or not np.isfinite(values).all() or not np.isfinite(current):
        raise ValueError('分位历史为空或含无效数值')
    return float(100*np.count_nonzero(values <= current)/len(values))


def symbol_history(frame, meta, start, end, calculator):
    """Validate every expected bar before calculating; gaps cannot lower N."""
    first = max(start, pd.Timestamp(meta['onboardDate'], unit='ms', tz='UTC').normalize())
    stop = min(end, pd.Timestamp(meta['deliveryDate'], unit='ms', tz='UTC').normalize())
    expected = pd.date_range(first, stop-pd.Timedelta(days=1), freq='D')
    frame = daily_frame(frame)
    if frame.empty or 'close' not in frame:
        raise ValueError(meta['symbol']+': 日线数据缺失')
    frame = frame.loc[(frame.index >= first) & (frame.index < stop)]
    if not frame.index.equals(expected):
        raise ValueError(meta['symbol']+': 日线缺失、重复或时间错位')
    closes = pd.to_numeric(frame['close'], errors='coerce')
    if not np.isfinite(closes).all() or (closes <= 0).any():
        raise ValueError(meta['symbol']+': 收盘价无效')
    scores = calculator(closes.reset_index(drop=True), ema_period=EMA_PERIOD,
                        lookback=PMARP_LOOKBACK)
    if len(scores) != len(closes):
        raise ValueError(meta['symbol']+': PMARP输出长度错误')
    scores = pd.Series(np.asarray(scores, dtype=float), index=frame.index)
    # Only first 150 actual listing bars are legitimately unavailable.
    mature = scores.iloc[PMARP_LOOKBACK:]
    if not np.isfinite(mature).all() or not mature.between(0, 100).all():
        raise ValueError(meta['symbol']+': PMARP计算异常')
    scores.iloc[:PMARP_LOOKBACK] = np.nan
    return scores


def build_report(market, as_of):
    as_of = utc_day(as_of)
    end = as_of+pd.Timedelta(days=1)
    dates = pd.date_range(end=as_of, periods=LOOKBACK_DAYS+1, freq='D')
    start = dates[0]-pd.Timedelta(days=SEED_DAYS)
    metadata, _active, evidence = market.catalog(as_of)
    records = [m for m in eligible_metadata(metadata)
               if max(dates[0]+pd.Timedelta(days=1),
                      pd.Timestamp(m['onboardDate'],unit='ms',tz='UTC').normalize()+pd.Timedelta(days=1))
               <= min(end,pd.Timestamp(m['deliveryDate'],unit='ms',tz='UTC'))]
    if not records:
        raise ValueError('历史Crypto合约池为空')
    rows = {day:dict(date=str(day.date()), eligible_count=0, warmup_count=0,
                    valid_count=0, strong_count=0, weak_count=0)
            for day in dates}
    constituents = {}
    unopened = []
    failures = []
    for meta in sorted(records, key=lambda m:m['symbol']):
        symbol = meta['symbol']
        try:
            frame = fetch_daily_or_confirm_unopened(
                market,meta,start,end,limit=1000,
                required_history='breadth_731_ema20_pmarp150')
            if frame is None:
                unopened.append(symbol)
                continue
            scores = symbol_history(frame, meta, start, end, market.scanner.calculate_pmarp)
        except Exception as exc:
            failures.append(dict(symbol=symbol,reason=str(exc)))
            continue
        today = None
        for day, row in rows.items():
            if not (meta['onboardDate'] < int((day+pd.Timedelta(days=1)).timestamp()*1000)
                    <= meta['deliveryDate']):
                continue
            row['eligible_count'] += 1
            value = scores.loc[day]
            if pd.isna(value):
                row['warmup_count'] += 1
            else:
                row['valid_count'] += 1
                row['strong_count'] += int(value >= 98)
                row['weak_count'] += int(value <= 2)
                if day == as_of:
                    today = float(value)
        constituents[symbol] = dict(onboardDate=meta['onboardDate'],
                                    deliveryDate=meta['deliveryDate'],
                                    pmarp_as_of=today)
    if failures:
        raise ValueError('日线/PMARP检查失败: '+json.dumps(failures,ensure_ascii=False))
    for row in rows.values():
        if not row['valid_count']:
            raise ValueError(row['date']+': PMARP有效分母为零')
        row['strong_pct'] = 100*row['strong_count']/row['valid_count']
        row['weak_pct'] = 100*row['weak_count']/row['valid_count']
    history = list(rows.values())
    today = history[-1]
    result = dict(schema_version=1, status='ok', as_of=str(as_of.date()),
                  data_cutoff_utc=end.isoformat(), lookback_days=LOOKBACK_DAYS,
                  comparison_start=history[0]['date'], comparison_end=history[-2]['date'],
                  percentile_method='100 * count(previous 365 daily breadth values <= today) / 365; excludes today; ties included',
                  parameters=dict(ema_period=EMA_PERIOD, pmarp_lookback=PMARP_LOOKBACK,
                                  seed_days=SEED_DAYS, price_start=str(start.date()),
                                  strong_threshold=98, weak_threshold=2),
                  universe='historical daily Crypto USDT perpetuals; BTC included; no turnover filter',
                  universe_evidence=evidence, constituents=constituents,
                  confirmed_unopened=unopened,
                  history=history, current=today,
                  kline_requests=market.requests, archive_requests=market.archive_requests)
    for side in ('strong','weak'):
        result[side+'_percentile'] = percentile(today[side+'_pct'],
                                               [r[side+'_pct'] for r in history[:-1]])
    return result


def message(report):
    current = report['current']
    lines = [f"*Crypto 日线PMARP市场宽度 | {report['as_of']} UTC*",
             '历史逐日Crypto USDT永续池 · 含BTC · 全池等权',
             f"有效 {current['valid_count']}/{current['eligible_count']} · 新币预热 {current['warmup_count']}"]
    for side, label in [('strong','极强 ≥98'),('weak','极弱 ≤2')]:
        lines.append(f"{label}：{current[side+'_count']}/{current['valid_count']} = "
                     f"{current[side+'_pct']:.2f}% | 一年分位 P{report[side+'_percentile']:.1f}")
    lines += [f"对比 {report['comparison_start']} 至 {report['comparison_end']}，此前365日，不含当天。",
              '分位=历史宽度≤今日的天数占比；并列计入（连续为0也可能P100）。',
              'EMA20 / PMARP150 · 先看今日占比；分位仅表示历史排名，并列会抬高排名。']
    return '\n'.join(lines)


def run(scanner_dir, output_dir, dry_run=False, *, scanner=None, as_of=None, retired_dir=None):
    if scanner is None:
        sys.path.insert(0,str(scanner_dir))
        scanner = importlib.import_module('binance_pmarp_scanner')
    as_of = utc_day(as_of if as_of is not None else
                   pd.Timestamp.now(tz='UTC').normalize()-pd.Timedelta(days=1))
    if as_of >= pd.Timestamp.now(tz='UTC').normalize():
        raise ValueError('as_of必须是已收盘UTC日线')
    output_dir = Path(output_dir)
    error = None
    try:
        if (scanner.CONFIG['ema_period'], scanner.CONFIG['lookback']) != (EMA_PERIOD, PMARP_LOOKBACK):
            raise ValueError('Crypto扫描器PMARP参数已变更，需明确更新宽度口径')
        manifest = json.loads(MANIFEST_PATH.read_text())
        market = BreadthMarket(scanner, output_dir/'breadth_cache', as_of,
                             manifest=manifest,
                             retired_dir=retired_dir if retired_dir is not None else BUNDLED_RETIRED_DIR,
                             history_days=LOOKBACK_DAYS+1,
                             catalog_dirs=[output_dir/'trend_cache'])
        report = build_report(market, as_of)
        text = message(report)
    except Exception as exc:
        error = exc
        report = dict(schema_version=1,status='unavailable',as_of=str(as_of.date()),reason=str(exc))
        text = (f"Crypto 日线PMARP市场宽度 | {as_of.date()} UTC\n"
                '⚠️ 不可用：历史币池或日线完整性检查未通过。\n'
                '不发布宽度/一年分位；请检查任务错误记录。')
    report['generated_at'] = pd.Timestamp.now(tz='UTC').isoformat()
    path = output_dir/f"crypto_pmarp_breadth_{as_of.date()}.json"
    atomic_text(path,json.dumps(report,ensure_ascii=False,indent=2,allow_nan=False)+'\n')
    atomic_text(path.with_suffix('.md'),text+'\n')
    print(text,flush=True)
    print(f'Artifact: {path}',flush=True)
    from scripts.crypto_report_delivery import send_once
    send_once(scanner,text,output_dir,f"{as_of.date()}-breadth-{report['status']}",dry_run=dry_run)
    if error is not None:
        raise RuntimeError('市场宽度不可用: '+str(error)) from error
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--scanner-dir',type=Path,required=True)
    parser.add_argument('--output-dir',type=Path,required=True)
    parser.add_argument('--as-of',help='Last closed UTC daily bar (YYYY-MM-DD)')
    parser.add_argument('--dry-run',action='store_true')
    parser.add_argument('--retired-dir',type=Path)
    args = parser.parse_args()
    run(args.scanner_dir,args.output_dir,args.dry_run,as_of=args.as_of,retired_dir=args.retired_dir)


if __name__ == '__main__':
    main()
