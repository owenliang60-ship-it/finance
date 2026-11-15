"""USD-M REST boundary with atomic shared rate state. No retries.

Writes are off by default. Each environment has its own switch; neither unlocks the other.

Successful JSON is an acknowledgement, never proof of a fill. See exchange-api.md.
"""
from dataclasses import dataclass, field
import hashlib
import hmac
import json
import math
import os
import stat
import threading
import time
from urllib.parse import urlencode

import requests


class Endpoints:
    TIME = '/fapi/v1/time'
    EXCHANGE_INFO = '/fapi/v1/exchangeInfo'
    KLINES = '/fapi/v1/klines'
    TICKER_24HR = '/fapi/v1/ticker/24hr'
    TICKER_PRICE = '/fapi/v1/ticker/price'
    MARK_PRICE = '/fapi/v1/premiumIndex'
    ORDER = '/fapi/v1/order'
    OPEN_ORDERS = '/fapi/v1/openOrders'
    ALGO_ORDER = '/fapi/v1/algoOrder'
    OPEN_ALGO_ORDERS = '/fapi/v1/openAlgoOrders'
    POSITION_RISK = '/fapi/v3/positionRisk'
    POSITION_RISK_V2 = '/fapi/v2/positionRisk'
    ACCOUNT = '/fapi/v3/account'
    POSITION_MODE = '/fapi/v1/positionSide/dual'
    MULTI_ASSETS_MODE = '/fapi/v1/multiAssetsMargin'
    SYMBOL_CONFIG = '/fapi/v1/symbolConfig'
    LEVERAGE = '/fapi/v1/leverage'
    MARGIN_TYPE = '/fapi/v1/marginType'
    USER_TRADES = '/fapi/v1/userTrades'
    INCOME = '/fapi/v1/income'


PUBLIC = {Endpoints.TIME, Endpoints.EXCHANGE_INFO, Endpoints.KLINES,
          Endpoints.TICKER_24HR, Endpoints.TICKER_PRICE, Endpoints.MARK_PRICE}
READABLE = PUBLIC | {Endpoints.ORDER, Endpoints.OPEN_ORDERS, Endpoints.ALGO_ORDER,
                     Endpoints.OPEN_ALGO_ORDERS, Endpoints.POSITION_RISK, Endpoints.POSITION_RISK_V2,
                     Endpoints.ACCOUNT, Endpoints.POSITION_MODE, Endpoints.MULTI_ASSETS_MODE,
                     Endpoints.SYMBOL_CONFIG, Endpoints.USER_TRADES, Endpoints.INCOME}
WRITABLE = {'POST': {Endpoints.ORDER, Endpoints.ALGO_ORDER, Endpoints.LEVERAGE, Endpoints.MARGIN_TYPE},
            'DELETE': {Endpoints.ORDER, Endpoints.ALGO_ORDER}}
BASE_URLS = {'demo': 'https://demo-fapi.binance.com', 'prod': 'https://fapi.binance.com'}


@dataclass(frozen=True, repr=False)
class Credentials:
    api_key: str
    api_secret: str

    def __post_init__(self):
        if not all(isinstance(v, str) and v.strip() for v in (self.api_key, self.api_secret)):
            raise ValueError('credentials require nonempty api_key and api_secret')

    def __repr__(self):
        return 'Credentials(<redacted>)'


def load_credentials(path):
    """Read exactly a regular, caller-owned 0600 JSON file; reject symlinks."""
    fd = os.open(os.fspath(path), os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(fd, 'r') as handle:
        info = os.fstat(handle.fileno())
        if not stat.S_ISREG(info.st_mode) or stat.S_IMODE(info.st_mode) != 0o600 or info.st_uid != os.getuid():
            raise ValueError('credential file must be owned by caller and have mode 0600')
        try:
            values = json.load(handle)
            return Credentials(values['api_key'], values['api_secret'])
        except (ValueError, TypeError, KeyError):
            raise ValueError('invalid credential file; require api_key and api_secret') from None


def sign_query(query, secret):
    key = secret.encode() if isinstance(secret, str) else secret
    return hmac.new(key, query.encode(), hashlib.sha256).hexdigest()


class ExchangeError(RuntimeError):
    """Safe metadata only. Raw response/error text may contain credential material."""
    def __init__(self, outcome, reason, *, code=None, http_status=None, retry_after=None):
        self.outcome = outcome
        self.code = code
        self.http_status = http_status
        self.retry_after = retry_after
        super().__init__(f'{outcome}: {reason} (HTTP={http_status}, code={code})')


@dataclass
class RateLimitState:
    """Share across all clients in one process. Owner persists pause across jobs."""
    limit: int = None
    used_weight: int = 0
    blocked_until: float = 0.0
    new_orders_paused: bool = False
    minute: int = None
    lock: object = field(default_factory=threading.RLock, repr=False)

    def reserve(self, now, cost, protective, bootstrap=False):
        if now < self.blocked_until:
            raise ExchangeError('NOT_SENT', 'rate cooldown', retry_after=self.blocked_until - now)
        current_minute = int(now // 60)
        if self.minute != current_minute:
            self.used_weight = 0
            self.minute = current_minute
        if self.limit is None:
            if not bootstrap:
                raise ExchangeError('NOT_SENT', 'request weight limit unknown; fetch exchangeInfo')
        elif not protective and (self.used_weight >= self.limit * .75 or self.used_weight + cost > self.limit * .75):
            raise ExchangeError('NOT_SENT', 'request weight soft limit', retry_after=60 - now % 60)
        self.used_weight += cost

    def observe(self, headers, now):
        current_minute = int(now // 60)
        if self.minute != current_minute:
            self.used_weight = 0
            self.minute = current_minute
        headers = {k.lower(): v for k, v in headers.items()}
        raw = headers.get('x-mbx-used-weight-1m')
        if raw is not None:
            try:
                measured = int(raw)
                if measured >= 0:
                    self.used_weight = max(self.used_weight, measured)
            except (ValueError, TypeError):
                pass  # Keep our conservative reservation; never reset to zero.

    def configure(self, payload):
        try:
            rows = [x for x in payload['rateLimits'] if x['rateLimitType'] == 'REQUEST_WEIGHT'
                    and x['interval'] == 'MINUTE' and x['intervalNum'] == 1]
            limit = int(rows[0]['limit'])
            if len(rows) != 1 or limit <= 0:
                raise ValueError
        except (KeyError, TypeError, ValueError, IndexError):
            self.limit = None
            raise ExchangeError('UNKNOWN', 'invalid exchangeInfo request weight limit') from None
        self.limit = limit


_SHARED_LIMITERS = {env: RateLimitState() for env in BASE_URLS}


def _weight(path, params):
    # Reserve conservative bounds where symbol-dependent costs differ.
    if path == Endpoints.KLINES:
        n = int(params.get('limit', 500))
        if n < 1 or n > 1500:
            raise ValueError('kline limit must be 1..1500')
        return 1 if n < 100 else 2 if n < 500 else 5 if n <= 1000 else 10
    if path == Endpoints.TICKER_PRICE:
        return 1 if params.get('symbol') else 2
    if path == Endpoints.TICKER_24HR:
        return 1 if params.get('symbol') else 40
    if path == Endpoints.MARK_PRICE:
        return 1 if params.get('symbol') else 10
    if path in {Endpoints.TIME, Endpoints.EXCHANGE_INFO}:
        return 1
    return 40  # Private routes reserve a deliberately conservative upper bound.


class BinanceClient:
    def __init__(self, env='demo', credentials=None, transport=None, limiter=None,
                 clock=time.time, allow_demo_writes=False, allow_prod_writes=False):
        if env not in BASE_URLS:
            raise ValueError('env must be demo or prod')
        self.env = env
        self.credentials = credentials
        self.transport = transport if transport is not None else requests.Session()
        self.limiter = limiter if limiter is not None else _SHARED_LIMITERS[env]
        self.clock = clock
        self.allow_demo_writes = allow_demo_writes
        self.allow_prod_writes = allow_prod_writes
        self.clock_offset_ms = None
        self.clock_synced_at = None

    def sync_clock(self):
        before = self.clock()
        payload = self.request('GET', Endpoints.TIME, protective=True)
        after = self.clock()
        try:
            server = int(payload['serverTime'])
            if server <= 0 or after < before or after - before > 1:
                raise ValueError
        except (TypeError, KeyError, ValueError):
            self.clock_offset_ms = None
            raise ExchangeError('UNKNOWN', 'invalid or slow clock sample') from None
        self.clock_offset_ms = server - (before + after) * 500
        self.clock_synced_at = after
        return self.clock_offset_ms

    def request(self, method, path, params=None, signed=False, protective=False):
        method = method.upper()
        mutation = method != 'GET'
        writable = self.allow_demo_writes if self.env == 'demo' else self.allow_prod_writes
        if mutation and not writable:
            raise ExchangeError('NOT_SENT', 'writes disabled')
        if (method == 'GET' and path not in READABLE) or (mutation and path not in WRITABLE.get(method, set())):
            raise ValueError('unsupported endpoint or method')
        if path not in PUBLIC and not signed:
            raise ExchangeError('NOT_SENT', 'private endpoint requires signing')
        values = dict(params or {})
        if {'timestamp', 'recvWindow', 'signature'} & values.keys():
            raise ValueError('signing fields are managed by the client')
        with self.limiter.lock:
            now = self.clock()
            if mutation and not protective and self.limiter.new_orders_paused:
                raise ExchangeError('NOT_SENT', 'new orders paused after IP ban')
            headers = {}
            if signed:
                if self.credentials is None:
                    raise ExchangeError('NOT_SENT', 'credentials missing')
                age = None if self.clock_synced_at is None else now - self.clock_synced_at
                if self.clock_offset_ms is None or age is None or not 0 <= age <= 60:
                    raise ExchangeError('NOT_SENT', 'clock sync missing or stale')
                if mutation and not protective and abs(self.clock_offset_ms) > 1000:
                    raise ExchangeError('NOT_SENT', 'clock skew exceeds 1000 ms')
                values.update(timestamp=int(now * 1000 + self.clock_offset_ms), recvWindow=5000)
                headers['X-MBX-APIKEY'] = self.credentials.api_key
            query = urlencode({key: str(value).lower() if isinstance(value, bool) else value
                               for key, value in values.items()})
            if signed:
                query += '&signature=' + sign_query(query, self.credentials.api_secret)
            self.limiter.reserve(now, _weight(path, values), protective,
                                 bootstrap=path in {Endpoints.TIME, Endpoints.EXCHANGE_INFO})
        kwargs = {'headers': headers, 'timeout': (5, 10), 'allow_redirects': False}
        if mutation:
            headers['Content-Type'] = 'application/x-www-form-urlencoded'
            kwargs['data'] = query
        else:
            kwargs['params'] = query
        try:
            response = self.transport.request(method, BASE_URLS[self.env] + path, **kwargs)
        except requests.RequestException:
            raise ExchangeError('UNKNOWN', 'transport failure; query before retry') from None
        with self.limiter.lock:
            self.limiter.observe(response.headers, self.clock())
            status = response.status_code
            retry_after = None
            if status in (429, 418):
                lower = {k.lower(): v for k, v in response.headers.items()}
                try:
                    retry_after = float(lower['retry-after'])
                    if not math.isfinite(retry_after) or retry_after <= 0:
                        raise ValueError
                except (KeyError, ValueError, TypeError):
                    retry_after = 60.0 if status == 429 else math.inf
                self.limiter.blocked_until = max(self.limiter.blocked_until, self.clock() + retry_after)
                if status == 418:
                    self.limiter.new_orders_paused = True
            try:
                payload = response.json()
            except ValueError:
                raise ExchangeError('UNKNOWN', 'invalid JSON response', http_status=status, retry_after=retry_after) from None
            code = payload.get('code') if isinstance(payload, dict) else None
            code = code if isinstance(code, int) and not isinstance(code, bool) else None
            if status >= 500 or status == 408 or code in (-1001, -1006, -1007):
                raise ExchangeError('UNKNOWN', 'execution result uncertain; query before retry', code=code, http_status=status)
            if status >= 400 or (code is not None and code < 0):
                raise ExchangeError('REJECTED', 'exchange rejected request', code=code, http_status=status, retry_after=retry_after)
            if not 200 <= status < 300 or not isinstance(payload, (dict, list)):
                raise ExchangeError('UNKNOWN', 'unexpected response', http_status=status)
            if path == Endpoints.EXCHANGE_INFO:
                self.limiter.configure(payload)
            return payload
