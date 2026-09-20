"""Read-only Quant adapter and evidence for turnover universes.

Two catalog modes share the transport/cache helpers:

* :meth:`TrendMarket.catalog` reconstructs the historical universe for the
  daily scan (current exchangeInfo + saved catalogs + official archive audit).
  It is the default and is unchanged.
* :meth:`TrendMarket.current_catalog` / :class:`CurrentTrendMarket` serve the
  weekly report, which intentionally replays history over the current
  tradable universe only.  Delisted, SETTLING, PENDING and historical-only
  contracts are excluded by construction; no archive audit and no saved
  historical catalog are consulted.
"""
import hashlib
import json
import logging
import os
from pathlib import Path
import time
import xml.etree.ElementTree as ET

import pandas as pd
import requests

from scripts.crypto_beta_scanner import MarketData

BUCKET = 'https://s3-ap-northeast-1.amazonaws.com/data.binance.vision'
NS = {'s': 'http://s3.amazonaws.com/doc/2006-03-01/'}
logger = logging.getLogger(__name__)


class InactiveSymbolError(RuntimeError):
    """Explicit exchange -1122 response; not proof of no historical trading."""


def eligible_metadata(metadata):
    if not isinstance(metadata, list):
        raise ValueError('合约元数据不是列表')
    records, seen = [], set()
    for meta in metadata:
        if not isinstance(meta, dict) or not isinstance(meta.get('symbol'), str) or not meta['symbol']:
            raise ValueError('合约元数据标识缺失')
        symbol = meta['symbol']
        if symbol in seen:
            raise ValueError('重复合约元数据: '+symbol)
        seen.add(symbol)
        if any(key not in meta or not isinstance(meta[key], str)
               for key in ('underlyingType','quoteAsset','contractType')):
            raise ValueError('合约分类元数据缺失: '+symbol)
        if not (meta.get('underlyingType') == 'COIN' and meta.get('quoteAsset') == 'USDT'
                and meta.get('contractType') == 'PERPETUAL'):
            continue
        start, end = meta.get('onboardDate'), meta.get('deliveryDate')
        if (type(start) is not int or type(end) is not int or start <= 0 or end <= start
                or not isinstance(meta.get('status'), str) or not meta['status']):
            raise ValueError('合约生命周期元数据无效: '+symbol)
        records.append(meta)
    return records


def overlaps(meta, start, end):
    return meta['onboardDate'] < int(end.timestamp()*1000) and meta['deliveryDate'] > int(start.timestamp()*1000)


def _save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name+f'.{os.getpid()}.tmp')
    temp.write_text(json.dumps(value, ensure_ascii=False, allow_nan=False)+'\n')
    os.replace(temp, path)


class TrendMarket(MarketData):
    """All writes are task-owned evidence/cache; shared Quant cache is read-only.

    ``history_days`` widens the removed-contract archive audit for callers with
    a longer horizon (the weekly report needs 210 days).  ``catalog_dirs`` adds
    read-only saved-catalog locations (e.g. the daily trend cache) so the
    weekly task never writes into the daily artifact directory.  Defaults keep
    the daily caller byte-for-byte identical.
    """

    def __init__(self, scanner, cache_dir, as_of, history_days=30, catalog_dirs=()):
        super().__init__(scanner)
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.as_of = pd.Timestamp(as_of)
        self.end = self.as_of+pd.Timedelta(days=1)
        self.history_days = history_days
        self.catalog_dirs = [Path(directory) for directory in catalog_dirs]
        self.archive_requests = 0
        self.pending_symbols = set()

    def _json(self, endpoint, params=None):
        time.sleep(1)
        self.requests += 1
        value = self.scanner.api_request_with_retry(self.scanner.CONFIG['base_url']+endpoint, params=params)
        if value is None:
            raise RuntimeError('交易所API失败: '+endpoint)
        return value

    def archive_keys(self, *, prefix, delimiter='', marker=''):
        seen = set()
        while True:
            params = dict(prefix=prefix, marker=marker, **{'max-keys':1000})
            if delimiter:
                params['delimiter'] = delimiter
            key = hashlib.sha256(json.dumps(params, sort_keys=True).encode()).hexdigest()
            path = self.cache_dir/'archive'/str(self.as_of.date())/(key+'.xml')
            if path.exists():
                raw = path.read_text()
            else:
                time.sleep(1)
                for attempt in range(1, 4):
                    self.archive_requests += 1
                    try:
                        response = requests.get(BUCKET, params=params, timeout=30)
                        response.raise_for_status()
                        raw = response.text
                        break
                    except (requests.ConnectionError, requests.Timeout, requests.HTTPError) as exc:
                        if (isinstance(exc, requests.HTTPError)
                                and (exc.response is None or exc.response.status_code
                                     not in (429, 500, 502, 503, 504))):
                            raise
                        logger.warning('Binance归档请求失败 %s/3 prefix=%s marker=%s: %s',
                                       attempt, prefix, marker, exc)
                        if attempt == 3:
                            raise RuntimeError(
                                f'Binance归档请求3次失败: prefix={prefix} marker={marker}'
                            ) from exc
                        time.sleep(attempt * 2)
            root = ET.fromstring(raw)
            truncated = root.findtext('s:IsTruncated', namespaces=NS)
            if truncated not in ('true','false'):
                raise ValueError('归档列表缺少分页状态')
            next_marker = root.findtext('s:NextMarker', namespaces=NS)
            if truncated == 'true' and (not next_marker or next_marker in seen or next_marker == marker):
                raise ValueError('归档分页游标缺失或重复')
            if not path.exists():
                path.parent.mkdir(parents=True, exist_ok=True)
                temp = path.with_name(path.name+f'.{os.getpid()}.tmp')
                temp.write_text(raw)
                os.replace(temp, path)
            for node in root.findall('s:CommonPrefixes/s:Prefix', NS)+root.findall('s:Contents/s:Key', NS):
                if node.text:
                    yield node.text
            if truncated == 'false':
                break
            seen.add(next_marker)
            marker = next_marker

    def archive_symbols(self):
        symbols = set()
        for frequency in ('daily','monthly'):
            prefix = f'data/futures/um/{frequency}/klines/'
            for key in self.archive_keys(prefix=prefix, delimiter='/'):
                if key.startswith(prefix):
                    symbol = key[len(prefix):].rstrip('/')
                    if '/' not in symbol and symbol.endswith('USDT'):
                        symbols.add(symbol)
        if not symbols:
            raise ValueError('归档合约目录为空，不能确认历史覆盖')
        return symbols

    def has_archive_activity(self, symbol, start, end):
        prefix = f'data/futures/um/daily/klines/{symbol}/1d/'
        marker = prefix+f'{symbol}-1d-{start.date()}'
        for key in self.archive_keys(prefix=prefix, marker=marker):
            if key.endswith('.zip'):
                day = key[-14:-4]
                if str(start.date()) <= day < str(end.date()):
                    return True
        return False

    def catalog(self, as_of):
        start = as_of-pd.Timedelta(days=self.history_days-1)
        current = self._json('/fapi/v1/exchangeInfo')
        if not isinstance(current, dict) or not current.get('symbols'):
            raise ValueError('交易所合约元数据为空')
        eligible_metadata(current['symbols'])
        combined = {}
        snapshots = []
        paths = {path for directory in [self.cache_dir, *self.catalog_dirs]
                 for path in Path(directory).glob('catalog_*.json')}
        for path in sorted(paths, key=lambda p: (p.name, str(p))):
            if path.stem.removeprefix('catalog_') > str(as_of.date()):
                continue
            saved = json.loads(path.read_text())
            eligible_metadata(saved['symbols'])
            combined.update({m['symbol']:m for m in saved['symbols']})
            snapshots.append(path.name)
        combined.update({m['symbol']:m for m in current['symbols']})
        records = eligible_metadata(list(combined.values()))
        unknown = sorted(self.archive_symbols()-set(combined))
        unresolved = [s for s in unknown if self.has_archive_activity(s, start, self.end)]
        if unresolved:
            raise ValueError('期内归档合约元数据缺失: '+', '.join(unresolved))
        current_records = eligible_metadata(current['symbols'])
        self.pending_symbols = {m['symbol'] for m in current_records if m['status'] == 'PENDING_TRADING'}
        at = int(self.end.timestamp()*1000)
        active = {m['symbol'] for m in current_records
                  if m['status'] == 'TRADING' and m['onboardDate'] <= at < m['deliveryDate']}
        if not active:
            raise ValueError('当前可交易合约为空')
        path = self.cache_dir/f'catalog_{as_of.date()}.json'
        _save(path, dict(symbols=list(combined.values()),
                         retrieved_at=pd.Timestamp.now(tz='UTC').isoformat()))
        evidence = dict(method='effective-dated reconstruction; current exchangeInfo + saved catalogs + official archive audit',
                        limitation='Not original point-in-time exchangeInfo vintages; archived prices/metadata may be revised.',
                        prior_catalogs=snapshots, metadata_count=len(records),
                        unknown_archive_symbols=unknown, unknown_active_symbols=unresolved)
        return records, active, evidence

    def current_catalog(self, as_of):
        """Current-tradable-only catalog for the weekly current-universe replay.

        Fetches ``exchangeInfo`` exactly once, validates the eligible metadata,
        persists the weekly task's own snapshot and returns only contracts
        that are TRADING with ``onboard <= cutoff < delivery``.  Delisted,
        SETTLING, PENDING and historical-only-absent contracts are excluded by
        construction, so the official archive audit and saved historical
        catalogs are deliberately never consulted.  Daily callers keep using
        :meth:`catalog`, whose defaults are byte-for-byte unchanged.
        """
        current = self._json('/fapi/v1/exchangeInfo')
        if not isinstance(current, dict) or not current.get('symbols'):
            raise ValueError('交易所合约元数据为空')
        records = eligible_metadata(current['symbols'])
        at = int(self.end.timestamp()*1000)
        self.pending_symbols = {m['symbol'] for m in records
                                if m['status'] == 'PENDING_TRADING'}
        active = {m['symbol'] for m in records
                  if m['status'] == 'TRADING' and m['onboardDate'] <= at < m['deliveryDate']}
        if not active:
            raise ValueError('当前可交易合约为空')
        kept = [m for m in records if m['symbol'] in active]
        excluded = [m for m in records if m['symbol'] not in active]
        path = self.cache_dir/f'catalog_{as_of.date()}.json'
        _save(path, dict(symbols=list(current['symbols']),
                         retrieved_at=pd.Timestamp.now(tz='UTC').isoformat()))
        evidence = dict(
            method='current-universe replay; one exchangeInfo snapshot of Binance COIN USDT PERPETUAL contracts',
            universe='current TRADING only (status==TRADING, onboard <= report cutoff < delivery)',
            limitation=('Delisted/SETTLING/PENDING/historical-only contracts are excluded by design; '
                        'historical weeks are replayed over the current tradable universe, '
                        'not an original all-market point-in-time snapshot.'),
            metadata_count=len(kept),
            active_count=len(active),
            excluded_count=len(excluded),
            excluded_symbols=[m['symbol'] for m in excluded],
        )
        return kept, active, evidence

    def daily_cache_path(self, symbol, start, end, required_history):
        """Cache key identifies end (as_of dir), interval and required history."""
        return (self.cache_dir/'daily'/str(self.as_of.date())
                / f'{symbol}_1d_{pd.Timestamp(start).date()}_{pd.Timestamp(end).date()}'
                  f'_{required_history}.json')

    def fetch_daily(self, symbol, start, end, limit=32, required_history=None):
        params = dict(symbol=symbol, interval='1d', startTime=int(start.timestamp()*1000),
                      endTime=int(end.timestamp()*1000)-1, limit=limit)
        raw = None
        path = None
        if required_history is not None:
            path = self.daily_cache_path(symbol, start, end, required_history)
            if path.exists():
                try:
                    raw = json.loads(path.read_text())
                except (ValueError, OSError) as exc:
                    raise ValueError('日线缓存无效: '+symbol) from exc
                if not isinstance(raw, list):
                    raise ValueError('日线缓存无效: '+symbol)
        if raw is None:
            try:
                raw = self._json('/fapi/v1/klines', params)
            except RuntimeError:
                if symbol in self.pending_symbols:
                    # Quant's retry helper discards HTTP error bodies. Inspect this
                    # one known boundary without treating a transport failure as [].
                    time.sleep(1)
                    self.requests += 1
                    response = requests.get(self.scanner.CONFIG['base_url']+'/fapi/v1/klines',
                                            params=params, timeout=30)
                    if response.status_code == 400 and response.json().get('code') == -1122:
                        raise InactiveSymbolError(f'{symbol}: exchange -1122 Invalid symbol status')
                raise
            if not isinstance(raw, list):
                raise ValueError('日线响应格式错误: '+symbol)
            if path is not None:
                _save(path, raw)
        return self.scanner.klines_to_dataframe(raw) if raw else pd.DataFrame()

    def fetch(self, symbol, limit, interval='4h', required_history=None):
        # Fixed endpoint prevents reruns later today from displacing closed bars.
        # A longer required history gets its own key so a smaller prior cache
        # can never masquerade as sufficient history.
        suffix = f'_{required_history}' if required_history is not None else ''
        path = self.cache_dir/'prices'/str(self.as_of.date())/f'{symbol}_{interval}{suffix}.json'
        if path.exists():
            raw = json.loads(path.read_text())
        else:
            raw = self._json('/fapi/v1/klines', dict(symbol=symbol, interval=interval, limit=limit,
                                                   endTime=int(self.end.timestamp()*1000)-1))
            if not isinstance(raw, list) or not raw:
                raise ValueError('价格数据缺失: '+symbol)
            _save(path, raw)
        if not isinstance(raw, list) or not raw:
            raise ValueError('价格缓存无效: '+symbol)
        return self.scanner.klines_to_dataframe(raw)


class CurrentTrendMarket(TrendMarket):
    """Weekly adapter: current-tradable universe only.

    ``catalog`` is redirected to :meth:`TrendMarket.current_catalog`, so the
    weekly report never runs the archive audit and never reads saved
    historical catalogs.  Everything else (JSON transport, cache keys,
    pending-symbol handling) is inherited unchanged.
    """

    def catalog(self, as_of):
        return self.current_catalog(as_of)
