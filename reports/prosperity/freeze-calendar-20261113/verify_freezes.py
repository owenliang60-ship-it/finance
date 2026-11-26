"""Independent check of the new freeze calendar: stdlib + SQL only, no prosperity-engine imports.

Adapted from reports/prosperity/m9-scheduler-20261024/verify_freezes.py (old 60/95%/80 rule).
Rule checked here (Boss 2026-11-13): each fiscal quarter freezes on the last Saturday of the
next calendar quarter; coverage is recorded, never a gate; decision is 'calendar'.

For every quarter from --first to --last, under --version:
  - exactly one candidate record (<db>.state/candidates/*.json), checksum valid, health pass;
  - its as_of equals the recomputed freeze date, decision == 'calendar', hashes non-empty;
  - coverage recomputed from its evidence equals the stored value;
  - replay records cut off at the next midnight UTC; the date has 4 quarter_final boards with
    that quarter label and frozen_at == the record's cutoff;
  - the F1 quarter_final chain in that range is exactly these quarters in order.
Sample quarters also check stored current_fiscal against market.db income_quarterly and the
quarter bucket (fiscal date - 7 days). Any failure prints FAIL and exits 1 without writing --out.
"""
import argparse
import calendar
from datetime import date, timedelta
import hashlib
import json
from pathlib import Path
import sqlite3
import sys

SCHEMES = 4


class Failure(Exception):
    pass


def check(ok, message):
    if not ok:
        raise Failure(message)


def labels(first, last):
    y, q = int(first[:4]), int(first[-1])
    while f'{y:04d}Q{q}' <= last:
        yield f'{y:04d}Q{q}'
        y, q = (y + 1, 1) if q == 4 else (y, q + 1)


def planned(label):
    """Last Saturday of the calendar quarter after `label`."""
    y, q = int(label[:4]), int(label[-1])
    y, q = (y + 1, 1) if q == 4 else (y, q + 1)
    month = q * 3
    end = date(y, month, calendar.monthrange(y, month)[1])
    return end - timedelta(days=(end.weekday() - 5) % 7)


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False)


def read_records(db, version):
    records = {}
    for path in sorted(Path(str(db) + '.state').joinpath('candidates').glob('*.json')):
        env = json.loads(path.read_text())
        check(isinstance(env, dict) and set(env) == {'payload', 'sha256'}, f'envelope_shape {path.name}')
        check(hashlib.sha256(canonical(env['payload']).encode()).hexdigest() == env['sha256'],
              f'checksum {path.name}')
        body = env['payload']
        if body['identity']['code_version'] != version:
            continue
        label = body['identity']['quarter_label']
        check(label not in records, f'duplicate_record {label}')
        records[label] = body
    return records


def verify(a):
    records = read_records(a.db, a.version)
    expected = list(labels(a.first, a.last))
    for label, body in records.items():           # every record of this version, inside the range or not
        check(body['identity']['as_of'] == planned(label).isoformat(),
              f'as_of {label}: {body["identity"]["as_of"]} != {planned(label)}')
    conn = sqlite3.connect(a.db.resolve().as_uri() + '?mode=ro', uri=True)
    market = sqlite3.connect(a.market_db.resolve().as_uri() + '?mode=ro', uri=True)
    try:
        chain = conn.execute("SELECT quarter_label, as_of, input_set_id FROM board WHERE board_type='quarter_final' "
                             "AND scheme_id='F1' AND code_version=? ORDER BY as_of", (a.version,)).fetchall()
        chain = [r for r in chain if r[0] in set(expected)]
        check([r[0] for r in chain] == expected, f'f1_chain {[r[0] for r in chain]} != {expected}')
        verified, sample = [], []
        for label, actual, inputs in chain:
            day = planned(label)
            check(label in records, f'missing_record {label}')
            body = records[label]
            check(actual == day.isoformat(), f'board_as_of {label}: {actual} != {day}')
            check(body['decision'] == 'calendar', f'decision {label}: {body["decision"]!r}')
            check(body['health']['status'] == 'pass' and bool(body['hashes']), f'health_or_hashes {label}')
            codes = [r[0] for r in body['evidence']]
            check(codes and len(codes) == len(set(codes)), f'evidence {label}')
            ratio = sum(bool(bucket and bucket >= label) for _, bucket in body['evidence']) / len(codes)
            check(ratio == body['coverage'], f'coverage {label}: {body["coverage"]} != {ratio}')
            if body['meta']['mode'] == 'replay':
                check(body['cutoff'] == (day + timedelta(days=1)).isoformat() + 'T00:00:00Z', f'cutoff {label}')
            finals = conn.execute("SELECT quarter_label, frozen_at FROM board WHERE board_type='quarter_final' "
                                  "AND as_of=? AND code_version=?", (actual, a.version)).fetchall()
            check(len(finals) == SCHEMES and all(r == (label, body['cutoff']) for r in finals),
                  f'quarter_final_boards {label}: {finals}')
            verified.append({'quarter': label, 'as_of': actual, 'coverage': ratio, 'decision': body['decision'],
                             'mode': body['meta']['mode']})
            if label not in a.sample_quarters:
                continue
            buckets = dict(body['evidence'])
            for symbol in sorted(s for s, b in buckets.items() if b)[:a.sample_size]:
                stored = json.loads(conn.execute('SELECT input_json FROM stock_input WHERE input_set_id=? AND symbol=?',
                                                 (inputs, symbol)).fetchone()[0])
                fiscal = stored['current_fiscal']
                source = market.execute('SELECT date FROM income_quarterly WHERE symbol=? AND date=?',
                                        (symbol, fiscal)).fetchone()
                check(source is not None, f'source_fiscal {label} {symbol} {fiscal}')
                shifted = date.fromisoformat(source[0][:10]) - timedelta(days=7)
                bucket = f'{shifted.year}Q{(shifted.month - 1) // 3 + 1}'
                check(bucket == stored['quarter_bucket'] == buckets[symbol], f'bucket {label} {symbol}')
                sample.append({'quarter': label, 'symbol': symbol, 'source_fiscal': fiscal, 'quarter_bucket': bucket})
    finally:
        conn.close()
        market.close()
    return verified, sample


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--db', type=Path, required=True)
    p.add_argument('--market-db', type=Path, required=True)
    p.add_argument('--version', required=True)
    p.add_argument('--out', type=Path, required=True)
    p.add_argument('--first', default='2021Q2')
    p.add_argument('--last', default='2026Q2')
    p.add_argument('--sample-quarters', default='2021Q2,2024Q2,2026Q2', type=lambda v: set(v.split(',')))
    p.add_argument('--sample-size', default=20, type=int)
    a = p.parse_args()
    try:
        verified, sample = verify(a)
    except (Failure, KeyError, TypeError, ValueError, sqlite3.Error) as exc:
        print(f'FAIL: {type(exc).__name__}: {exc}')
        sys.exit(1)
    a.out.write_text(json.dumps({'status': 'PASS', 'quarters': verified, 'source_bucket_sample': sample,
        'scope': 'Freeze dates recomputed from the calendar; candidate coverage recomputed from its evidence; '
                 'sampled fiscal dates checked against source SQL. Candidate denominators are taken as given; '
                 'does not reimplement M4 eligibility.'}, indent=2))
    print(f'PASS: {len(verified)} quarters; {len(sample)} source SQL fiscal-date/bucket checks')


if __name__ == '__main__':
    main()
