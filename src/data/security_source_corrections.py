"""Evidence-bound read views; never rewrite the physical vendor identifiers."""
import hashlib
import json
import re
from datetime import date, datetime, timezone
from pathlib import Path


def load_security_source_corrections(config_dir, evidence_root=None):
    path = Path(config_dir) / 'security_source_corrections.json'
    if not path.exists():
        return []
    records = json.loads(path.read_text())
    if not isinstance(records, list):
        raise ValueError('security corrections must be a list')
    ids = set()
    for record in records:
        if not isinstance(record, dict):
            raise ValueError('invalid security correction record')
        required = ('id', 'basket', 'raw_symbol', 'raw_name', 'raw_cusip')
        if any(not isinstance(record.get(k), str) or not record[k] for k in required):
            raise ValueError('security correction requires exact source fields')
        if (record['id'] in ids or record.get('source_kind') != 'live'
                or 'raw_isin' not in record
                or record['raw_isin'] is not None and not isinstance(record['raw_isin'], str)
                or record.get('action') not in ('classify_cvr', 'correct_cusip')):
            raise ValueError('invalid security correction scope/action')
        ids.add(record['id'])
        try:
            start, end = date.fromisoformat(record['valid_from']), date.fromisoformat(record['valid_to'])
            date.fromisoformat(record['reviewed_at'])
            anchors = record['required_snapshot_dates']
            if not isinstance(anchors, list) or not anchors or len(set(anchors)) != len(anchors):
                raise ValueError('missing or duplicate snapshot anchors')
            if start > end or any(not start <= date.fromisoformat(day) <= end for day in anchors):
                raise ValueError('invalid snapshot anchor range')
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError('invalid correction dates/anchors') from exc
        if record['action'] == 'correct_cusip':
            if (not re.fullmatch(r'[A-Z0-9]{9}', str(record.get('effective_cusip', '')))
                    or record['effective_cusip'] in ('000000000', record['raw_cusip'])
                    or not re.fullmatch(r'[A-Z]{2}[A-Z0-9]{9}[0-9]', str(record['raw_isin']))):
                raise ValueError('CUSIP correction requires reviewed replacement and exact ISIN')
        elif 'effective_cusip' in record:
            raise ValueError('classification must not also rewrite identity')
        evidence = record.get('evidence')
        if not isinstance(evidence, list) or not evidence:
            raise ValueError('correction requires frozen evidence')
        for item in evidence:
            if (not isinstance(item, dict) or not str(item.get('url', '')).startswith('https://')
                    or not re.fullmatch(r'[a-f0-9]{64}', str(item.get('sha256', '')))
                    or not isinstance(item.get('path'), str)):
                raise ValueError('invalid correction evidence')
            root = Path(evidence_root) if evidence_root is not None else Path(__file__).resolve().parents[2]
            source = root / item['path']
            if not source.is_file() or hashlib.sha256(source.read_bytes()).hexdigest() != item['sha256']:
                raise ValueError('correction evidence missing or hash mismatch')
    return records


def match_security_source_correction(row, records):
    """Match trusted physical fields, rejecting inconsistent normalized copies."""
    if not records or row.get('source_kind') != 'live':
        return None
    raw = row.get('raw_payload_json')
    if isinstance(raw, str):
        raw = json.loads(raw)
    if not isinstance(raw, dict):
        return None  # Ordinary issuer validation owns missing physical evidence.
    cusip = raw.get('securityCusip') or raw.get('cusip')
    if raw.get('securityCusip') and raw.get('cusip') and raw['securityCusip'] != raw['cusip']:
        raise ValueError('raw CUSIP conflict')
    day = row.get('holding_date', row.get('snapshot_date', ''))
    basket = row.get('basket_symbol', row.get('basket'))
    matched = [r for r in records if r['basket'] == basket
               and r['source_kind'] == row.get('source_kind')
               and r['valid_from'] <= day <= r['valid_to']
               and r['raw_symbol'] == raw.get('asset')
               and r['raw_name'] == raw.get('name')
               and r['raw_cusip'] == cusip and r['raw_isin'] == raw.get('isin')]
    if len(matched) > 1:
        raise ValueError('multiple matching security corrections')
    if not matched:
        return None
    for field, original in (('raw_symbol', raw.get('asset')), ('name', raw.get('name')),
                            ('cusip', cusip), ('isin', raw.get('isin')),
                            ('issuer_lei', raw.get('lei')), ('asset_category', raw.get('assetCat')),
                            ('weight_pct', raw.get('weightPercentage')),
                            ('market_value', raw.get('marketValue'))):
        if row.get(field) != original:
            raise ValueError('correction disagrees with raw source: ' + field)
    return matched[0]


def apply_security_source_corrections(rows, records):
    """Copy a physical snapshot collection into its audited calculation view."""
    out, matches = [], {}
    snapshots = {(r.get('basket_symbol', r.get('basket')),
                  r.get('holding_date', r.get('snapshot_date')), r.get('source_kind')) for r in rows}
    for row in rows:
        if row.get('filter_reason') == 'reviewed_cvr':
            raise ValueError('physical source cannot supply a reviewed correction marker')
        result = dict(row)
        result.pop('effective_security', None)
        result.pop('correction_id', None)
        correction = match_security_source_correction(row, records)
        if correction:
            result['correction_id'] = correction['id']
            marker = (correction['id'], row.get('holding_date', row.get('snapshot_date')))
            matches[marker] = matches.get(marker, 0) + 1
            if correction['action'] == 'classify_cvr':
                result.update(included=0, symbol=None, covered_by=None, filter_reason='reviewed_cvr',
                              effective_security={'asset_category': 'DE'})
            else:
                result['effective_security'] = {'cusip': correction['effective_cusip']}
        out.append(result)
    for correction in records:
        for day in correction['required_snapshot_dates']:
            if ((correction['basket'], day, correction['source_kind']) in snapshots
                    and matches.get((correction['id'], day), 0) != 1):
                raise ValueError('reviewed snapshot correction row missing or duplicated: ' + correction['id'])
    return out


def apply_pit_security_corrections(conn, basket, snapshot_date, holdings, records):
    """PIT's legacy table lacks IDs: require an exact same-day physical witness."""
    scoped = [r for r in records if r['action'] == 'classify_cvr'
              and r['basket'] == basket and snapshot_date in r['required_snapshot_dates']]
    if not scoped:
        return holdings
    sources = [dict(r) for r in conn.execute(
        "SELECT * FROM fmp_fund_disclosure_holdings "
        "WHERE basket_symbol=? AND holding_date=? AND source_kind='live'",
        [basket, snapshot_date])]
    view = apply_security_source_corrections(sources, scoped)
    out = [dict(r) for r in holdings]
    for record in scoped:
        witnesses = [r for r in view if r.get('correction_id') == record['id']]
        if len(witnesses) != 1:
            raise ValueError('PIT correction requires one same-day physical source witness')
        witness = witnesses[0]
        fetched = datetime.fromisoformat(witness['fetched_at'].replace('Z', '+00:00'))
        if (fetched.tzinfo is None or fetched.astimezone(timezone.utc).date().isoformat() != snapshot_date
                or witness['composition_available_date'] > snapshot_date):
            raise ValueError('PIT correction physical source not available at snapshot')
        raw = json.loads(witness['raw_payload_json']) if isinstance(witness['raw_payload_json'], str) else witness['raw_payload_json']
        expected = dict(raw_asset=raw['asset'], name=raw['name'],
                        weight_pct=raw['weightPercentage'], market_value=raw['marketValue'],
                        updated_at=raw['updatedAt'])
        matches = [r for r in out if all(r.get(k) == v for k, v in expected.items())]
        if len(matches) != 1:
            raise ValueError('PIT correction source fields missing, different or duplicated')
        matches[0].update(included=0, symbol=None, covered_by=None,
                          filter_reason='reviewed_cvr', correction_id=record['id'])
    return out
