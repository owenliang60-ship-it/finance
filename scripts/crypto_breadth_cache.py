"""One validated rolling daily-price file per contract; no per-day full copies."""
import hashlib
import json
import math

import pandas as pd

from scripts.crypto_trend_market import _save, InactiveSymbolError

DAY = 86400000
MAX_BARS = 731


def rows_hash(rows):
    return hashlib.sha256(json.dumps(rows,separators=(',',':'),allow_nan=False).encode()).hexdigest()


def checked_rows(raw, start, end):
    if not isinstance(raw,list):
        raise ValueError('日线缓存/响应不是列表')
    selected = {}
    for row in raw:
        if not isinstance(row,list) or len(row)<8 or type(row[0]) is not int or row[0]%DAY:
            raise ValueError('日线行格式/时间错误')
        timestamp=row[0]
        if not start <= timestamp < end:
            continue
        if timestamp in selected:
            raise ValueError('日线日期重复')
        close=float(row[4])
        if not math.isfinite(close) or close<=0:
            raise ValueError('日线收盘价无效')
        selected[timestamp]=row
    return selected


def daily_rows(market, symbol, start, end, meta):
    start_ms=max(int(start.timestamp()*1000),meta['onboardDate']//DAY*DAY)
    end_ms=min(int(end.timestamp()*1000),meta['deliveryDate']//DAY*DAY)
    if end_ms<=start_ms or (end_ms-start_ms)//DAY>MAX_BARS:
        raise ValueError('宽度价格窗口为空或超过731天')
    path=market.cache_dir/'rolling_1d'/(symbol+'.json')
    prior_end=0
    if path.exists():
        payload=json.loads(path.read_text())
        if payload.get('schema_version')!=1 or payload.get('symbol')!=symbol:
            raise ValueError('日线缓存身份/schema不符: '+symbol)
        raw=payload['rows']
        if rows_hash(raw)!=payload['sha256']:
            raise ValueError('日线缓存hash不符: '+symbol)
        prior_end=payload['end_ms']
        all_rows=checked_rows(raw,payload['start_ms'],prior_end)
        if sorted(all_rows)!=list(range(payload['start_ms'],prior_end,DAY)):
            raise ValueError('持久日线缓存不连续: '+symbol)
        selected=checked_rows(raw,start_ms,end_ms)
    else:
        # One-time adoption of the old audited snapshots; never delete them.
        legacy=sorted((market.cache_dir/'daily').glob(
            f'*/{symbol}_1d_*_breadth_731_ema20_pmarp150.json'))
        raw=json.loads(legacy[-1].read_text()) if legacy else []
        selected=checked_rows(raw,start_ms,end_ms)
    missing=[t for t in range(start_ms,end_ms,DAY) if t not in selected]
    spans=[]
    for timestamp in missing:
        if spans and spans[-1][1]==timestamp:
            spans[-1][1]+=DAY
        else:
            spans.append([timestamp,timestamp+DAY])
    for lower,upper in spans:
        try:
            fresh=market.fetch_daily_raw(symbol,pd.Timestamp(lower,unit='ms',tz='UTC'),
                                         pd.Timestamp(upper,unit='ms',tz='UTC'),
                                         limit=(upper-lower)//DAY)
        except InactiveSymbolError as exc:
            if selected:
                raise ValueError(symbol+': 已有交易历史，补点失败不能视为未开盘') from exc
            raise
        # Only a wholly empty, pending contract can invoke the existing
        # unopened-contract proof. A partial/stale history never does.
        if not fresh and not selected and meta['status']=='PENDING_TRADING':
            return []
        selected.update(checked_rows(fresh,lower,upper))
    expected=list(range(start_ms,end_ms,DAY))
    if sorted(selected)!=expected:
        absent=[str(pd.Timestamp(t,unit='ms',tz='UTC').date()) for t in expected if t not in selected]
        raise ValueError(symbol+': 日线缺失 '+', '.join(absent))
    rows=[selected[t] for t in expected]
    # Commit only complete, valid prices. Failed refresh leaves prior data intact.
    # A historical replay cannot roll a newer production cache backward.
    if end_ms>=prior_end:
        _save(path,dict(schema_version=1,symbol=symbol,start_ms=start_ms,end_ms=end_ms,
                        sha256=rows_hash(rows),rows=rows))
    return rows
