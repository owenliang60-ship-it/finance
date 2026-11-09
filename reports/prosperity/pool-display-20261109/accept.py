"""Pool display acceptance (Boss 2026-11-09): real 11-07 snapshot, read-only.

Usage: accept.py ROOT WORK EVIDENCE
WORK holds prosperity.db (self-contained snapshot), prosperity_pool.json, premium_pool.json and the
cloud morning JSON of 2026-11-07, plus market.db (sqlite .backup snapshot). All read-only.
"""
import hashlib, json, re, sqlite3, subprocess, sys
from datetime import datetime, timezone
from pathlib import Path

root, work, evidence = map(Path, sys.argv[1:4])
sys.path.insert(0, str(root))
evidence.mkdir(parents=True, exist_ok=True)
db, pool_path = work / 'prosperity.db', work / 'prosperity_pool.json'
premium_path, morning_json = work / 'premium_pool.json', work / 'morning_20261107_080107.json'
market_db = work / 'market.db'
CV = '13e5f9d5183f1b8e'
sha = lambda p: hashlib.sha256(Path(p).read_bytes()).hexdigest()
before = {'prosperity': sha(db), 'market': sha(market_db)}
result = {'inputs': {'prosperity_sha256_16': before['prosperity'][:16], 'market_sha256_16': before['market'][:16]}}
pool_file = json.loads(pool_path.read_text(encoding='utf-8'))
ranked = sorted(pool_file['members'], key=lambda m: (m['rank'], m['symbol']))
pool_syms = {m['symbol'] for m in ranked}

# ---- A1 web page ------------------------------------------------------------------------------
page = work / 'web.html'
subprocess.run([sys.executable, str(root / 'scripts/prosperity_web.py'), '--db', str(db), '--pool', str(pool_path),
                '--code-version', CV, '--out', str(page)], check=True, capture_output=True)
verify = subprocess.run(['/usr/bin/python3', '-I', str(root / 'reports/prosperity/m11-web-20261102/verify_web.py'),
                         '--db', str(db), '--html', str(page), '--code-version', CV, '--pool', str(pool_path)],
                        capture_output=True, text=True)
aside = page.read_text(encoding='utf-8').split('<aside id="pool">')[1].split('</aside>')[0]
listed = re.findall(r'<tr><td>(\d+)<td>([^<]+)<td', aside)
result['web'] = {'bytes': page.stat().st_size, 'verify': verify.stdout.strip().splitlines()[-1], 'verify_rc': verify.returncode,
                 'list_rows': len(listed),
                 'list_matches_pool_rank_order': [s for _, s in listed] == [m['symbol'] for m in ranked]}
(evidence / 'web-pool-list.html').write_text('<aside id="pool">' + aside + '</aside>\n', encoding='utf-8')

# ---- A2 weekly text through deliver_week with fake senders -------------------------------------
from src.data import prosperity_delivery as d
msgs, docs = [], []

def reuse_page(cmd, **kw):          # the web CLI already ran in A1; copy its page instead of a second 8-minute run
    import shutil
    assert cmd[1].endswith('prosperity_web.py'), cmd
    shutil.copyfile(page, cmd[cmd.index('--out') + 1])
    return subprocess.CompletedProcess(cmd, 0, '', '')

out = d.deliver_week(run=reuse_page,as_of='2026-11-07', code_version=CV, db=db, market_db=market_db, pool_path=pool_path,
                     delivery_dir=work / 'delivery', now=lambda: datetime(2026, 11, 7, 12, 0, tzinfo=timezone.utc),
                     send_document=lambda path, **kw: docs.append((Path(path).name, kw.get('caption'))) or True,
                     send_message=lambda text, **kw: msgs.append(text) or True)
last = msgs[0].splitlines()[-1]
result['weekly_text'] = {'status': out.status, 'sent': list(out.sent), 'caption': docs[0][1],
                         'last_line_ok': last == '当前（{}，按景气排名）: {}'.format(
                             len(ranked), ', '.join(d.md_escape(m['symbol']) for m in ranked))}
(evidence / 'weekly-text.txt').write_text(msgs[0] + '\n', encoding='utf-8')

# ---- B + C morning report on the saved 11-07 signals --------------------------------------------
from src.data import prosperity_morning
import src.data.market_store as msmod
from src.data.market_store import MarketStore
from terminal.selection_compass import scan_selection_compass
from scripts import morning_report as mr

real_loader = prosperity_morning.load_bound_prosperity_pool
prosperity_morning.load_bound_prosperity_pool = lambda *a, **kw: real_loader(
    pool_path, db, now=datetime(2026, 11, 10, 0, 0, tzinfo=timezone.utc))
store = MarketStore(db_path=market_db, read_only=True)
msmod.get_store = lambda: store
premium = {**json.loads(premium_path.read_text(encoding='utf-8')), 'available': True}   # skip freshness
ms = json.loads(morning_json.read_text(encoding='utf-8'))['market_signals']
as_of = ms['as_of']
payload = mr._build_prosperity_compass(premium, {}, as_of, scan_selection_compass)
ms['prosperity_compass'] = payload
text_0d = mr.format_section_prosperity_compass(payload)
text_1 = mr.format_section_pmarp_by_signal_and_cap(ms)
text_2 = mr.format_section_layered_volume_anomaly(ms)
(evidence / 'section-0d.txt').write_text(text_0d + '\n', encoding='utf-8')
(evidence / 'section-1-2.txt').write_text(text_1 + '\n\n' + text_2 + '\n', encoding='utf-8')
html_path = mr.compile_morning_html_report(mr.build_html_payload(ms, None, as_of), as_of, out_dir=evidence)
pngs = mr.render_morning_report_images(market_signals=ms, dv_result=None, output_dir=work / 'png')

# independent EMA30 (pure python, same 127-row window) and 7-day market cap for every member
conn = sqlite3.connect(f'file:{market_db}?mode=ro', uri=True)
def ema30(symbol):
    rows = conn.execute('SELECT date, close FROM daily_price WHERE symbol=? ORDER BY date DESC LIMIT ?',
                        (symbol, mr.SELECTION_COMPASS_PRICE_ROWS)).fetchall()[::-1]
    if len(rows) < mr.SELECTION_COMPASS_PRICE_ROWS or rows[-1][0][:10] != as_of:
        return None
    e = rows[0][1]
    for _, c in rows[1:]:
        e = c * 2 / 31 + e * 29 / 31
    return rows[-1][1], e
def cap(symbol):
    r = conn.execute('SELECT date, market_cap FROM historical_market_cap WHERE symbol=? AND date<=? '
                     'ORDER BY date DESC LIMIT 1', (symbol, as_of)).fetchone()
    if not r or r[1] is None or r[1] <= 0:
        return None
    age = (datetime.fromisoformat(as_of) - datetime.fromisoformat(r[0][:10])).days
    return r[1] if 0 <= age <= 7 else None
rows = {m['symbol']: m for m in payload['members']}
mismatch = []
for m in ranked:
    got, ind = rows.get(m['symbol']), ema30(m['symbol'])
    want_above = None if ind is None else ind[0] > ind[1]
    if got is None or got['above'] is not want_above or got['rank'] != m['rank'] \
            or got['dollar_volume_rank'] != m['dollar_volume_rank']:
        mismatch.append((m['symbol'], 'ema/rank', got and got['above'], want_above))
    elif ind is not None and abs(got['ema30'] - ind[1]) > 1e-6 * ind[1]:
        mismatch.append((m['symbol'], 'ema30 value', got['ema30'], ind[1]))
    elif got['above'] is not True and (got['marketCap'] or None) != cap(m['symbol']):
        mismatch.append((m['symbol'], 'market cap', got['marketCap'], cap(m['symbol'])))
table = [l for l in text_0d.splitlines()[3:] if ' | ' in l and not l.startswith('与 Premium')]
positions = [l.split(' | ')[7] for l in table]
result['section_0d'] = {'available': payload['available'], 'rows': len(table), 'members': len(ranked),
                        'hits': len(payload['hits']), 'above_rows': positions.count('上方'),
                        'below_rows': positions.count('下方'), 'not_ready_rows': positions.count('—'),
                        'above_first': positions == sorted(positions, key=['上方', '下方', '—'].index),
                        'independent_mismatches': mismatch}

def segment_counts(text):
    return [int(n) for n in re.findall(r'▶ (?:景气精选池|其他 Extended)（(\d+)）', text)]
def in_pool_segment(text, symbol):
    head, _, rest = text.partition('▶ 景气精选池')
    pool_part, _, other = rest.partition('▶ 其他 Extended')
    hit = re.compile(r'(^|[\s*])' + re.escape(symbol) + r'([\s|*]|$)', re.M)
    return bool(hit.search(pool_part)), bool(hit.search(other))
split = {}
for key, text in (('pmarp', text_1), ('volume_anomaly', text_2)):
    hits = ms[key]['hits']
    want = [sum(h['symbol'] in pool_syms for h in hits), sum(h['symbol'] not in pool_syms for h in hits)]
    misplaced = [h['symbol'] for h in hits if in_pool_segment(text, h['symbol']) != (
        h['symbol'] in pool_syms, h['symbol'] not in pool_syms)]
    split[key] = {'hits': len(hits), 'text_counts': segment_counts(text), 'independent_counts': want,
                  'misplaced_symbols': misplaced}
html = Path(html_path).read_text(encoding='utf-8')
split['html_has_group_headings'] = all(x in html for x in ('景气精选池 · ', '其他 Extended · ', '<h3>景气精选池</h3>'))
split['png'] = [p.name for p in pngs]
result['sections_1_2'] = split
after = {'prosperity': sha(db), 'market': sha(market_db)}
result['inputs_unchanged'] = before == after
(evidence / 'result.json').write_text(json.dumps(result, ensure_ascii=False, indent=1) + '\n', encoding='utf-8')
print(json.dumps(result, ensure_ascii=False, indent=1))
