"""0d 景气动量精选池 acceptance (Boss 2026-11-19): real 2026-11-14 week, 2026-11-18 morning signals, read-only.

Usage: accept.py ROOT WORK EVIDENCE PNG_DIR
WORK holds prosperity.db and market.db (.backup snapshots), prosperity_pool.json (cloud, as_of 2026-11-14),
delivery/out/2026-11-14/momentum-20261114.json (cloud M16 board) and morning_20261118_080133.json (cloud
morning signals, as_of 2026-11-17). Nothing in WORK is written: market.db is a WAL-format file, so it is
opened with immutable=1 (a plain mode=ro open would create -wal/-shm files next to it). EVIDENCE gets
section-0d.txt, morning_report_<as_of>.html and result.json; PNG_DIR gets the images (not committed).
"""
import hashlib, json, re, sqlite3, sys
from datetime import date, datetime, timezone
from pathlib import Path

root, work, evidence, png_dir = map(Path, sys.argv[1:5])
sys.path.insert(0, str(root))
evidence.mkdir(parents=True, exist_ok=True)
db, market_db, pool_path = work / 'prosperity.db', work / 'market.db', work / 'prosperity_pool.json'
delivery = work / 'delivery'
board_path = delivery / 'out' / '2026-11-14' / 'momentum-20261114.json'
morning_json = work / 'morning_20261118_080133.json'
MORNING_RUN = datetime(2026, 11, 18, 0, 1, 33, tzinfo=timezone.utc)      # 08:01:33 SGT, the cloud run
EXPECTED_PICKS = [('ABBV', 5), ('V', 21), ('COST', 23), ('MDT', 27)]
CV = '13e5f9d5183f1b8e'
sha = lambda p: hashlib.sha256(Path(p).read_bytes()).hexdigest()
inputs = (db, market_db, pool_path, board_path, morning_json)
before = {p.name: sha(p) for p in inputs}
listing_before = sorted(str(p.relative_to(work)) for p in work.rglob('*'))
result = {'inputs': {name: digest[:16] for name, digest in before.items()}}

from terminal.prosperity.version import code_version
result['code_version'] = {'value': code_version(), 'expected': CV, 'ok': code_version() == CV}

pool_file = json.loads(pool_path.read_text(encoding='utf-8'))
board_file = json.loads(board_path.read_text(encoding='utf-8'))
pool_syms = {m['symbol'] for m in pool_file['members']}
pool_rank = {m['symbol']: m['rank'] for m in pool_file['members']}

# ---- the morning builder on the snapshots, through the real readers -----------------------------
from src.data import prosperity_morning
import src.data.market_store as msmod
from src.data.market_store import MarketStore
from terminal.selection_compass import scan_selection_compass
from scripts import morning_report as mr

real_pool, real_momentum = prosperity_morning.load_bound_prosperity_pool, prosperity_morning.load_prosperity_momentum_picks
prosperity_morning.load_bound_prosperity_pool = lambda *a, **kw: real_pool(pool_path, db, now=MORNING_RUN)
prosperity_morning.load_prosperity_momentum_picks = lambda pool, *a, **kw: real_momentum(pool, delivery)
store = MarketStore(db_path=market_db, read_only=True)
conn = sqlite3.connect(market_db.resolve().as_uri() + '?mode=ro&immutable=1', uri=True)
conn.execute('PRAGMA query_only=ON')
conn.row_factory = sqlite3.Row
store._local.conn = conn                     # MarketStore would open mode=ro, which writes -wal/-shm here
msmod.get_store = lambda *a, **kw: store
cloud = json.loads(morning_json.read_text(encoding='utf-8'))['market_signals']
ms = dict(cloud)
as_of = ms['as_of']
payload = mr._build_prosperity_compass({}, as_of, scan_selection_compass)
ms['prosperity_compass'] = payload
text_0d = mr.format_section_prosperity_compass(payload)
text_1 = mr.format_section_pmarp_by_signal_and_cap(ms)
text_2 = mr.format_section_layered_volume_anomaly(ms)
(evidence / 'section-0d.txt').write_text(text_0d + '\n', encoding='utf-8')
html_path = mr.compile_morning_html_report(mr.build_html_payload(ms, None, as_of), as_of, out_dir=evidence)
pngs = mr.render_morning_report_images(market_signals=ms, dv_result=None, output_dir=png_dir)

# ---- independent recomputation (plain sqlite + json, no project code) ----------------------------
raw = sqlite3.connect(market_db.resolve().as_uri() + '?mode=ro&immutable=1', uri=True)


def close_on(symbol, day):
    row = raw.execute('SELECT close FROM daily_price WHERE symbol=? AND date=?', (symbol, day)).fetchone()
    return row and row[0]


def ema30(symbol):
    rows = raw.execute('SELECT date, close FROM daily_price WHERE symbol=? AND date<=? ORDER BY date DESC LIMIT ?',
                       (symbol, as_of, 127)).fetchall()[::-1]
    if len(rows) < 127 or rows[-1][0][:10] != as_of:
        return None
    e = rows[0][1]
    for _, c in rows[1:]:
        e = c * 2 / 31 + e * 29 / 31
    return rows[-1][1], e


def cap(symbol):
    r = raw.execute('SELECT date, market_cap FROM historical_market_cap WHERE symbol=? AND date<=? '
                    'ORDER BY date DESC LIMIT 1', (symbol, as_of)).fetchone()
    if not r or r[1] is None or r[1] <= 0:
        return None
    age = (date.fromisoformat(as_of) - date.fromisoformat(r[0][:10])).days
    return r[1] if 0 <= age <= 7 else None


board = board_file['boards']['14w']
top30 = sorted((r for r in board['scored'] if r['eligible']), key=lambda r: r['rank'])[:30]
want_picks = [(r['symbol'], r['rank']) for r in top30 if r['symbol'] in pool_syms]
first_day, last_day = board['window']
momentum = payload.get('momentum') or {}
got_picks = [(p['symbol'], p['momentum_rank']) for p in momentum.get('picks') or []]
table = [line.split(' | ') for line in text_0d.splitlines()[3:] if ' | ' in line]
members = {m['symbol']: m for m in payload.get('members') or []}
mismatch = []
for symbol, rank in want_picks:
    m, ind, row = members.get(symbol), ema30(symbol), next((r for r in table if r[0] == symbol), None)
    ret = close_on(symbol, last_day) / close_on(symbol, first_day) - 1          # 14 weeks = window first → last close
    want_above = None if ind is None else ind[0] > ind[1]
    want_cells = [symbol, str(rank), '{:+.0%}'.format(ret), str(pool_rank[symbol]),
                  '—' if ind is None else '{:.2f}'.format(ind[0]), '—' if ind is None else '{:.2f}'.format(ind[1]),
                  {True: '上方', False: '下方', None: '—'}[want_above], mr._format_market_cap(cap(symbol))]
    pick = next(p for p in momentum['picks'] if p['symbol'] == symbol)
    if m is None or row is None:
        mismatch.append((symbol, 'missing'))
        continue
    if abs(pick['momentum_return'] - ret) > 1e-9 * max(1.0, abs(ret)):
        mismatch.append((symbol, '14w return', pick['momentum_return'], ret))
    if m['above'] is not want_above or (ind is not None and (abs(m['close'] - ind[0]) > 1e-9 * ind[0]
                                                           or abs(m['ema30'] - ind[1]) > 1e-6 * ind[1])):
        mismatch.append((symbol, 'ema30', m['close'], m['ema30'], ind))
    if m['marketCap'] != cap(symbol):
        mismatch.append((symbol, 'market cap', m['marketCap'], cap(symbol)))
    shown = [row[i] for i in (0, 1, 2, 3, 6, 7, 8, 9)]
    if shown != want_cells:
        mismatch.append((symbol, 'text cells', shown, want_cells))
# The cloud run of 2026-11-18 08:01 scanned the same pool on its own market.db. close / above / rank should
# agree. Reported, not gated: EMA30 differs slightly (the recursive EMA is seeded from the cloud's longer shared
# price frames, here from 127 DB rows) and market caps can differ (this snapshot's cap rows are not the values
# that run read). The gate above is the independent recomputation on this same snapshot.
cloud_rows = {m['symbol']: m for m in (cloud.get('prosperity_compass') or {}).get('members') or []}
both = sorted(s for s in pool_syms if s in members and s in cloud_rows)
cloud_check = {
    'members_compared': len(both),
    'close_above_rank_mismatches': [s for s in both if any(
        members[s].get(k) != cloud_rows[s].get(k) for k in ('close', 'above', 'rank'))],
    'ema30_max_relative_diff': max((abs(members[s]['ema30'] - cloud_rows[s]['ema30']) / members[s]['ema30']
                                    for s in both if members[s].get('ema30') and cloud_rows[s].get('ema30')),
                                   default=0.0),
    'market_cap_differs': [s for s in both if members[s].get('marketCap') != cloud_rows[s].get('marketCap')],
}
head = text_0d.splitlines()[1]
result['section_0d'] = {
    'available': payload['available'], 'reason': payload.get('reason'),
    'momentum_available': momentum.get('available'), 'momentum_price_date': momentum.get('price_date'),
    'picks': got_picks, 'picks_ok': got_picks == want_picks == EXPECTED_PICKS,
    'rows_in_text': [r[0] for r in table], 'subtitle': head,
    'subtitle_counts_ok': head.endswith('| EMA30 {}/{} | 站上EMA30 {} 只'.format(
        sum(members[s]['above'] is not None for s, _ in want_picks), len(want_picks),
        sum(members[s]['above'] is True for s, _ in want_picks))),
    'unscored': momentum.get('unscored'), 'pool_members_scanned': len(members),
    'pool_ema30_ready': payload['coverage'].get('ema30_ready'),
    'independent_mismatches': mismatch, 'cloud_run_cross_check': cloud_check,
    'no_underscore': '_' not in text_0d, 'no_premium': 'Premium' not in text_0d,
}


def segment_counts(text):
    return [int(n) for n in re.findall(r'▶ (?:景气精选池|其他 Extended)（(\d+)）', text)]


def in_pool_segment(text, symbol):
    _, _, rest = text.partition('▶ 景气精选池')
    pool_part, _, other = rest.partition('▶ 其他 Extended')
    hit = re.compile(r'(^|[\s*])' + re.escape(symbol) + r'([\s|*]|$)', re.M)
    return bool(hit.search(pool_part)), bool(hit.search(other))


split = {'pool_symbols': len(pool_syms), 'split_symbols_equal_pool_file': set(payload['pool']['symbols']) == pool_syms}
for key, text in (('pmarp', text_1), ('volume_anomaly', text_2)):
    hits = ms[key]['hits']
    want = [sum(h['symbol'] in pool_syms for h in hits), sum(h['symbol'] not in pool_syms for h in hits)]
    misplaced = [h['symbol'] for h in hits if in_pool_segment(text, h['symbol']) != (
        h['symbol'] in pool_syms, h['symbol'] not in pool_syms)]
    split[key] = {'hits': len(hits), 'text_counts': segment_counts(text), 'independent_counts': want,
                  'misplaced_symbols': misplaced}
html = Path(html_path).read_text(encoding='utf-8')
block = html.split('<h3>0d. 景气动量精选池（试运行）</h3>')[1].split('<h2>')[0].split('<h3>1. ')[0]
result['html'] = {'file': Path(html_path).name, 'bytes': len(html.encode('utf-8')),
                  'picks_in_0d_block': [s for s, _ in EXPECTED_PICKS if '>{}<'.format(s) in block],
                  'other_pool_members_in_0d_block': [s for s in sorted(pool_syms - {s for s, _ in EXPECTED_PICKS})
                                                     if '>{}<'.format(s) in block],
                  'no_premium_comparison': '与 Premium 对比' not in html,
                  'has_group_headings': all(x in html for x in ('景气精选池 · ', '其他 Extended · '))}
split['png'] = [p.name for p in pngs]
result['sections_1_2'] = split
after = {p.name: sha(p) for p in inputs}
result['inputs_unchanged'] = before == after
result['work_listing_unchanged'] = listing_before == sorted(str(p.relative_to(work)) for p in work.rglob('*'))
(evidence / 'result.json').write_text(json.dumps(result, ensure_ascii=False, indent=1) + '\n', encoding='utf-8')
print(json.dumps(result, ensure_ascii=False, indent=1))
