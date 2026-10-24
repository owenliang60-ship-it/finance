"""Independent A9: stdlib + SQL only, with no prosperity-engine imports."""
import argparse
import calendar
from datetime import date, timedelta
import hashlib
import json
from pathlib import Path
import sqlite3


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--db', type=Path, required=True)
    p.add_argument('--market-db', type=Path, required=True)
    p.add_argument('--version', required=True)
    p.add_argument('--out', type=Path, required=True)
    a = p.parse_args()
    records = {}
    for path in Path(str(a.db)+'.state').joinpath('candidates').glob('*.json'):
        env = json.loads(path.read_text())
        body = env['payload']
        assert hashlib.sha256(json.dumps(body, sort_keys=True, separators=(',', ':'), ensure_ascii=False,
                                        allow_nan=False).encode()).hexdigest() == env['sha256']
        if body['identity']['code_version'] == a.version:
            key = (body['identity']['quarter_label'], body['identity']['as_of'])
            assert key not in records
            records[key] = body
    conn = sqlite3.connect(a.db.resolve().as_uri()+'?mode=ro', uri=True)
    market = sqlite3.connect(a.market_db.resolve().as_uri()+'?mode=ro', uri=True)
    quarters = conn.execute("SELECT quarter_label,as_of,input_set_id FROM board WHERE board_type='quarter_final' "
        "AND scheme_id='F1' AND code_version=? ORDER BY as_of", (a.version,)).fetchall()
    expected_labels = [f'{y}Q{q}' for y in range(2021, 2027) for q in range(1,5)
                       if (2021,2) <= (y,q) <= (2026,2)]
    assert [r[0] for r in quarters] == expected_labels
    verified, sample = [], []
    for label, actual, inputs in quarters:
        year, month = int(label[:4]), int(label[-1])*3
        end = date(year, month, calendar.monthrange(year, month)[1])
        day = end+timedelta(days=60)
        while day.weekday() != 5:
            day += timedelta(days=1)
        chosen = None
        while chosen is None:
            row = records[(label, day.isoformat())]
            codes = [r[0] for r in row['evidence']]
            assert len(codes) == len(set(codes)) and codes and row['health']['status'] == 'pass'
            ratio = sum(bool(bucket and bucket >= label) for _,bucket in row['evidence'])/len(codes)
            assert ratio == row['coverage']
            decision = 'forced' if (day-end).days >= 80 else 'coverage' if ratio >= .95 else None
            assert decision == row['decision']
            if decision:
                chosen = day.isoformat()
                assert chosen == actual
                assert conn.execute("SELECT COUNT(*) FROM board WHERE board_type='quarter_final' AND as_of=? "
                                    "AND code_version=?", (chosen,a.version)).fetchone()[0] == 4
                verified.append({'quarter': label, 'as_of': chosen, 'coverage': ratio, 'decision': decision})
            else:
                day += timedelta(days=7)
        if label in ('2021Q2','2024Q2','2026Q2'):
            targets = sorted(dict(records[(label, actual)]['evidence']))[:20]
            for symbol in targets:
                raw = conn.execute('SELECT input_json FROM stock_input WHERE input_set_id=? AND symbol=?',
                                   (inputs,symbol)).fetchone()[0]
                stored = json.loads(raw)
                fiscal = stored['current_fiscal']
                source = market.execute('SELECT date FROM income_quarterly WHERE symbol=? AND date=?',
                                        (symbol,fiscal)).fetchone()
                assert source is not None, (label,symbol,fiscal)
                shifted = date.fromisoformat(source[0][:10])-timedelta(days=7)
                bucket = f'{shifted.year}Q{(shifted.month-1)//3+1}'
                assert bucket == stored['quarter_bucket'] == dict(records[(label,actual)]['evidence'])[symbol]
                sample.append({'quarter':label,'symbol':symbol,'source_fiscal':fiscal,'quarter_bucket':bucket})
    conn.close()
    market.close()
    assert len(sample) == 60
    a.out.write_text(json.dumps({'status':'PASS','quarters':verified,'source_bucket_sample':sample,
        'scope':'Candidate denominator evidence is given; dates/ratios/first trigger independently recomputed. '
                '60 fiscal dates checked against source SQL; does not reimplement all M4 eligibility.'}, indent=2))
    print(f'PASS: {len(verified)} quarters; 60 source SQL fiscal-date/bucket checks')


if __name__ == '__main__':
    main()
