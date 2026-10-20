"""Own Binance transport around the established TrendMarket market semantics."""
import hashlib
import json
from urllib.parse import urlsplit

import numpy as np
import pandas as pd

from scripts.crypto_trend_market import TrendMarket, InactiveSymbolError
from .binance import BASE_URLS, Endpoints, ExchangeError


def digest(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':'),allow_nan=False,default=str).encode()).hexdigest()


def klines_to_frame(rows):
    if not isinstance(rows,list):
        raise ValueError('invalid kline response')
    if not rows:
        return pd.DataFrame()
    if any(not isinstance(r,list) or len(r)<8 for r in rows):
        raise ValueError('incomplete kline row')
    frame=pd.DataFrame({'timestamp':pd.to_datetime([r[0] for r in rows],unit='ms',utc=True),
                        **{name:[float(r[i]) for r in rows] for name,i in
                           [('open',1),('high',2),('low',3),('close',4),('volume',5),('quote_volume',7)]}})
    if (not np.isfinite(frame.drop(columns='timestamp').to_numpy()).all()
            or frame.timestamp.duplicated().any() or not frame.timestamp.is_monotonic_increasing
            or (frame[['open','high','low','close']]<=0).any().any()
            or (frame[['volume','quote_volume']]<0).any().any()):
        raise ValueError('invalid kline values or clock')
    return frame


class PublicScanner:
    """Scanner-shaped transport only; does not read Quant's mutable cache."""
    def __init__(self,client):
        self.client=client
        self.CONFIG={'base_url':BASE_URLS[client.env]}

    def api_request_with_retry(self,url,params=None):
        # Legacy interface name; intentionally no retries of failed requests.
        parts=urlsplit(url)
        if parts.scheme+'://'+parts.netloc != self.CONFIG['base_url'] or parts.query or parts.fragment:
            raise ValueError('unexpected API origin')
        return self.client.request('GET',parts.path,params=params)

    @staticmethod
    def load_cached_data(symbol):
        return pd.DataFrame()

    klines_to_dataframe=staticmethod(klines_to_frame)


class PublicMarket(TrendMarket):
    def __init__(self,client,cache_dir,as_of,**kwargs):
        super().__init__(PublicScanner(client),cache_dir,as_of,**kwargs)
        self.input_hashes={}

    def catalog(self,as_of):
        result=super().catalog(as_of)
        records,active,evidence=result
        self.input_hashes['catalog']=digest(dict(records=records,active=sorted(active),evidence=evidence))
        return result

    def fetch_daily_raw(self,symbol,start,end,limit=1000):
        # Replace legacy direct-requests error probe: every fapi call uses our limiter.
        try:
            raw=self._json(Endpoints.KLINES,dict(symbol=symbol,interval='1d',
                           startTime=int(start.timestamp()*1000),endTime=int(end.timestamp()*1000)-1,limit=limit))
        except ExchangeError as exc:
            if exc.code == -1122 and symbol in self.pending_symbols:
                raise InactiveSymbolError('exchange reports inactive pending symbol') from None
            raise
        if not isinstance(raw,list):
            raise ValueError('daily response is not a list')
        return raw

    def fetch_daily(self,symbol,start,end,limit=32,required_history=None):
        frame=super().fetch_daily(symbol,start,end,limit,required_history or 14)
        self.input_hashes['daily:'+symbol]=hashlib.sha256(frame.to_json(date_format='iso',double_precision=15).encode()).hexdigest()
        return frame

    def fetch(self,symbol,limit,interval='4h',required_history=None):
        frame=super().fetch(symbol,limit,interval,required_history)
        self.input_hashes[interval+':'+symbol]=hashlib.sha256(frame.to_json(date_format='iso',double_precision=15).encode()).hexdigest()
        return frame


def fetch_weekly(client,anchor):
    anchor=pd.Timestamp(anchor)
    if anchor.tzinfo is None:
        raise ValueError('anchor must have timezone')
    cutoff=anchor.normalize()
    raw=client.request('GET',Endpoints.KLINES,params=dict(symbol='BTCUSDT',interval='1w',limit=1500,
                                                        endTime=int(cutoff.timestamp()*1000)-1))
    frame=klines_to_frame(raw)
    if frame.empty:
        raise ValueError('missing weekly candles')
    return frame.set_index('timestamp')[['high','low']]
