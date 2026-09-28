#!/usr/bin/env python3
"""One-off stdlib-only breadth audit. No producer/scanner imports or writes.

Usage: python3 independent_verify.py PATH/crypto_pmarp_breadth_2026-09-27.json
Redirect stdout to an audit JSON if desired. Nonzero exit means audit failure.
"""
import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import sys
import xml.etree.ElementTree as ET

DAY = 86400000
UTC = timezone.utc


def timestamp(day):
    return int(datetime.fromisoformat(day).replace(tzinfo=UTC).timestamp() * 1000)


def date_string(ms):
    return datetime.fromtimestamp(ms / 1000, UTC).date().isoformat()


def independent_scores(closes):
    # Explicit scalar EMA recurrence; count comparisons in preceding 150 bars.
    ema = closes[0]
    ratios = []
    scores = []
    alpha = 2.0 / 21.0
    for index, close in enumerate(closes):
        if index:
            ema = (1.0 - alpha) * ema + alpha * close
        ratio = close / ema
        score = None
        if index >= 150:
            count = sum(value <= ratio for value in ratios[index-150:index])
            score = count / 150.0 * 100.0
        ratios.append(ratio)
        scores.append(score)
    return scores


def verify(report_path, cache_root, manifest_path):
    report = json.loads(report_path.read_text())
    assert report['status'] == 'ok', 'Producer report is not available'
    assert report['lookback_days'] == 365
    params = report['parameters']
    assert (params['ema_period'], params['pmarp_lookback'], params['seed_days']) == (20, 150, 365)
    assert (params['strong_threshold'], params['weak_threshold']) == (98, 2)
    end = timestamp(report['as_of']) + DAY
    first_day = end - 366 * DAY
    seed_start = first_day - 365 * DAY
    assert params['price_start'] == date_string(seed_start)
    manifest = json.loads(manifest_path.read_text())
    rows = {day: Counter(eligible_count=0, warmup_count=0, valid_count=0,
                         strong_count=0, weak_count=0)
            for day in range(first_day, end, DAY)}
    errors = []
    hashes = {}
    total_bars = 0
    known = {}
    # Independently derive union eligibility from saved exchange snapshots plus
    # official supplemental lifecycles, without calling catalog implementation.
    catalogs = sorted(cache_root.glob('catalog_*.json'))
    assert catalogs, 'No catalog evidence available'
    for catalog in catalogs:
        if catalog.stem[len('catalog_'):] <= report['as_of']:
            for meta in json.loads(catalog.read_text())['symbols']:
                known[meta['symbol']] = meta
    for meta in manifest['symbols']:
        known.setdefault(meta['symbol'], meta)
    expected_symbols = {symbol for symbol, meta in known.items()
                        if meta.get('underlyingType') == 'COIN'
                        and meta.get('quoteAsset') == 'USDT'
                        and meta.get('contractType') == 'PERPETUAL'
                        and meta['onboardDate'] < end and meta['deliveryDate'] > first_day}
    unopened = report.get('confirmed_unopened', [])
    assert isinstance(unopened, list) and len(unopened) == len(set(unopened)), 'Invalid unopened list'
    unopened_evidence = {}
    namespace = {'s': 'http://s3.amazonaws.com/doc/2006-03-01/'}
    archive_pages = {}
    for path in (cache_root/'archive'/report['as_of']).glob('*.xml'):
        raw = path.read_bytes()
        root = ET.fromstring(raw)
        prefix = root.findtext('s:Prefix', namespaces=namespace)
        marker = root.findtext('s:Marker', default='', namespaces=namespace)
        archive_pages[(prefix, marker)] = (root, path, hashlib.sha256(raw).hexdigest())
    for symbol in unopened:
        assert symbol in expected_symbols, symbol+' unopened contract not in candidate universe'
        assert known[symbol]['status'] == 'PENDING_TRADING', symbol+' unopened is not pending'
        prefix = f'data/futures/um/daily/klines/{symbol}/1d/'
        marker = prefix+symbol+'-1d-'+date_string(seed_start)
        visited = set()
        evidence = []
        while True:
            assert marker not in visited, symbol+' archive pagination loop'
            visited.add(marker)
            assert (prefix, marker) in archive_pages, symbol+' missing exact seed-window archive evidence'
            root, path, digest = archive_pages[(prefix, marker)]
            evidence.append({'file': path.name, 'sha256': digest})
            for node in root.findall('s:Contents/s:Key', namespace):
                key = node.text or ''
                if key.endswith('.zip'):
                    day = key[-14:-4]
                    assert not date_string(seed_start) <= day < date_string(end), symbol+' unopened has archive activity'
            truncated = root.findtext('s:IsTruncated', namespaces=namespace)
            assert truncated in ('true', 'false'), symbol+' archive pagination state missing'
            if truncated == 'false':
                break
            following = root.findtext('s:NextMarker', namespaces=namespace)
            assert following and following != marker, symbol+' missing archive continuation marker'
            marker = following
        unopened_evidence[symbol] = evidence
    expected_symbols -= set(unopened)
    actual_symbols = set(report['constituents'])
    if expected_symbols != actual_symbols:
        errors.append({'membership_missing': sorted(expected_symbols-actual_symbols),
                       'membership_extra': sorted(actual_symbols-expected_symbols)})
    assert 'BTCUSDT' in actual_symbols
    for symbol, meta in sorted(report['constituents'].items()):
        if symbol in known:
            for field in ('onboardDate', 'deliveryDate'):
                if meta[field] != known[symbol][field]:
                    errors.append({'symbol': symbol, 'metadata_mismatch': field})
        if symbol in manifest['retired_prices']:
            path = cache_root / 'retired_prices' / (symbol + '.json')
        else:
            name = (f'{symbol}_1d_{date_string(seed_start)}_{date_string(end)}'
                    '_breadth_731_ema20_pmarp150.json')
            path = cache_root / 'daily' / report['as_of'] / name
        raw = path.read_bytes()
        hashes[symbol] = hashlib.sha256(raw).hexdigest()
        if symbol in manifest['retired_prices']:
            assert hashes[symbol] == manifest['retired_prices'][symbol]['sha256'], symbol+' hash mismatch'
        bars = json.loads(raw)
        assert isinstance(bars, list), symbol+' invalid bar container'
        begin = max(seed_start, meta['onboardDate'] // DAY * DAY)
        stop = min(end, meta['deliveryDate'] // DAY * DAY)
        selected = [bar for bar in bars if begin <= int(bar[0]) < stop]
        selected.sort(key=lambda bar: int(bar[0]))
        assert [int(bar[0]) for bar in selected] == list(range(begin, stop, DAY)), symbol+' continuity mismatch'
        closes = [float(bar[4]) for bar in selected]
        assert closes and all(math.isfinite(value) and value > 0 for value in closes), symbol+' invalid close'
        total_bars += len(closes)
        scores = independent_scores(closes)
        by_day = {int(bar[0]): score for bar, score in zip(selected, scores)}
        today_score = None
        for day, counters in rows.items():
            if not meta['onboardDate'] < day + DAY <= meta['deliveryDate']:
                continue
            counters['eligible_count'] += 1
            score = by_day[day]
            if score is None:
                counters['warmup_count'] += 1
                continue
            counters['valid_count'] += 1
            counters['strong_count'] += int(score >= 98)
            counters['weak_count'] += int(score <= 2)
            if day == end - DAY:
                today_score = score
        if today_score != meta['pmarp_as_of']:
            errors.append({'symbol': symbol, 'today_score_expected': today_score,
                           'today_score_producer': meta['pmarp_as_of']})
    expected_history = []
    for day, counters in rows.items():
        row = dict(date=date_string(day), **counters)
        assert row['valid_count'] > 0
        for side in ('strong', 'weak'):
            row[side+'_pct'] = 100 * row[side+'_count'] / row['valid_count']
        expected_history.append(row)
    assert len(report['history']) == 366
    for expected, actual in zip(expected_history, report['history']):
        if expected != actual:
            errors.append({'date': expected['date'], 'expected': expected, 'producer': actual})
    if report['current'] != expected_history[-1]:
        errors.append({'current_mismatch': True})
    percentiles = {}
    for side in ('strong', 'weak'):
        today = expected_history[-1][side+'_pct']
        le = sum(row[side+'_pct'] <= today for row in expected_history[:-1])
        value = 100 * le / 365
        percentiles[side] = {'count_le': le, 'percentile': value}
        if value != report[side+'_percentile']:
            errors.append({'percentile_mismatch': side, 'expected': value,
                           'producer': report[side+'_percentile']})
    assert report['comparison_start'] == date_string(first_day)
    assert report['comparison_end'] == date_string(end-2*DAY)
    return {'status': 'PASS' if not errors else 'FAIL',
            'method': 'Independent scalar EMA20 recurrence and previous-150 comparison counts; stdlib only',
            'report_sha256': hashlib.sha256(report_path.read_bytes()).hexdigest(),
            'symbols': len(actual_symbols), 'raw_bars_used': total_bars,
            'daily_rows_verified': 366, 'historical_days': 365,
            'confirmed_unopened_archive_evidence': unopened_evidence,
            'current': expected_history[-1], 'percentiles': percentiles,
            'error_count': len(errors), 'errors': errors[:30], 'input_sha256': hashes}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('report', type=Path)
    parser.add_argument('--cache-root', type=Path)
    parser.add_argument('--manifest', type=Path,
                        default=Path(__file__).resolve().parents[2]/'config/crypto_pmarp_breadth_sources.json')
    args = parser.parse_args()
    try:
        result = verify(args.report, args.cache_root or args.report.parent/'breadth_cache', args.manifest)
    except Exception as exc:
        result = {'status': 'FAIL', 'error': type(exc).__name__ + ': ' + str(exc)}
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result['status'] == 'PASS' else 1


if __name__ == '__main__':
    sys.exit(main())
