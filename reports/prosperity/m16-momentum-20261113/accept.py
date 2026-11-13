"""M16 acceptance on the real 2026-11-07 board. Read-only on every database.

Usage: accept.py ROOT DATA POOL_JSON WORK EVIDENCE
  ROOT      repo (worktree) root
  DATA      directory holding the live prosperity.db and market.db (opened read-only)
  POOL_JSON prosperity_pool.json copied from the cloud (as_of 2026-11-07)
  WORK      scratch directory (created); EVIDENCE receives result.json and the message texts
"""
import copy, hashlib, json, shutil, subprocess, sys
from datetime import datetime, timezone
from pathlib import Path

root, data, pool_path, work, evidence = map(Path, sys.argv[1:6])
sys.path.insert(0, str(root))
AS_OF, CV = '2026-11-07', '13e5f9d5183f1b8e'
db, market_db = data / 'prosperity.db', data / 'market.db'
work.mkdir(parents=True, exist_ok=True)
evidence.mkdir(parents=True, exist_ok=True)
VERIFY = Path(__file__).with_name('verify.py')
result = {'as_of': AS_OF, 'code_version': CV}

# ---- A1 CLI on the real board -------------------------------------------------------------------
board_json = work / 'momentum.json'
cli = subprocess.run([sys.executable, str(root / 'scripts/momentum_board.py'), '--db', str(db), '--market-db',
                      str(market_db), '--code-version', CV, '--as-of', AS_OF, '--out', str(board_json)],
                     capture_output=True, text=True)
assert cli.returncode == 0, cli.stderr
payload = json.loads(board_json.read_text(encoding='utf-8'))
result['cli'] = {'price_date': payload['price_date'], 'universe': payload['universe']['count'],
                 'json_sha256_16': hashlib.sha256(board_json.read_bytes()).hexdigest()[:16],
                 'boards': {k: {'window': b['window'], 'scored': len(b['scored']),
                                'long_eligible': sum(r['eligible'] for r in b['scored']),
                                'unavailable': b['unavailable'], 'adjustments': b['adjustments']}
                            for k, b in payload['boards'].items()}}


def verify(path):
    done = subprocess.run(['/usr/bin/python3', '-I', str(VERIFY), '--json', str(path), '--market-db', str(market_db),
                           '--db', str(db), '--code-version', CV, '--as-of', AS_OF], capture_output=True, text=True)
    return done.returncode, done.stdout.strip().splitlines()


# ---- A2 independent verifier: clean file, then tampered copies ----------------------------------
rc, lines = verify(board_json)
result['verify'] = {'rc': rc, 'summary': lines[-1], 'notes': [l for l in lines if l.startswith('note:')]}


def tampered(name, change):
    doc = copy.deepcopy(payload)
    change(doc)
    path = work / f'tampered-{name}.json'
    path.write_text(json.dumps(doc), encoding='utf-8')          # default allow_nan=True: NaN case needs it
    rc, lines = verify(path)
    return {'rc': rc, 'summary': lines[-1], 'first': next((l for l in lines if l.startswith('DIFF:')), None)}


b14 = lambda doc: doc['boards']['14w']


def exclude_as_split(doc, count):          # review P2: the verifier must not pass a board that excludes (almost) everyone
    for board in doc['boards'].values():
        moved, board['scored'] = board['scored'][:count], board['scored'][count:]
        board['unavailable'].update({r['symbol']: 'split_suspect' for r in moved})


result['verify_negatives'] = {
    'drop_scored_row': tampered('drop-scored', lambda doc: b14(doc)['scored'].pop(400)),
    'drop_unavailable': tampered('drop-unavailable', lambda doc: b14(doc)['unavailable'].pop('MNST')),
    'score_plus_0.01': tampered('score', lambda doc: b14(doc)['scored'][10].__setitem__('score', b14(doc)['scored'][10]['score'] + 0.01)),
    'score_nan': tampered('nan', lambda doc: b14(doc)['scored'][10].__setitem__('score', float('nan'))),
    'drop_adjustment': tampered('adjustment', lambda doc: b14(doc)['adjustments'].clear()),
    'flip_eligible': tampered('eligible', lambda doc: next(r for r in b14(doc)['scored'] if not r['eligible']).__setitem__('eligible', True)),
    'move_to_unavailable': tampered('move', lambda doc: b14(doc)['unavailable'].__setitem__(b14(doc)['scored'].pop(5)['symbol'], 'price_gap')),
    'empty_boards_all_split_suspect': tampered('empty', lambda doc: exclude_as_split(doc, 10**6)),
    'coverage_below_90pct': tampered('thin', lambda doc: exclude_as_split(doc, 100)),
    'one_split_exclusion_without_evidence': tampered('split', lambda doc: exclude_as_split(doc, 1)),
}

# ---- A3 deliver_week with fake senders: real momentum CLI, stubbed web page ----------------------
from src.data import prosperity_delivery as d


def run(cmd, **kw):
    if cmd[1].endswith('prosperity_web.py'):                     # the 10 MB page is not under test here
        Path(cmd[cmd.index('--out') + 1]).write_bytes(b'<html>stub</html>')
        return subprocess.CompletedProcess(cmd, 0, '', '')
    assert cmd[1].endswith('momentum_board.py'), cmd
    return subprocess.run(cmd, **kw)


def deliver(folder, now, run=run):
    msgs, docs = [], []
    out = d.deliver_week(run=run, as_of=AS_OF, code_version=CV, db=db, market_db=market_db, pool_path=pool_path,
                         delivery_dir=work / folder, now=lambda: now,
                         send_document=lambda path, **kw: docs.append(Path(path).name) or True,
                         send_message=lambda text, **kw: msgs.append(text) or True)
    return out, msgs


sha = lambda p: hashlib.sha256(Path(p).read_bytes()).hexdigest()


def listed(text):
    """Symbols on the one '*14周*' line, in order. Exactly one such line, or the check itself fails
    (code review: an all() over zero matching lines would pass vacuously)."""
    lines = [l for l in text.splitlines() if l.startswith('*14周*')]
    assert len(lines) == 1, f'expected one *14周* line, found {len(lines)}'
    return [tok.split(' #')[0] for tok in lines[0].split(': ', 1)[1].split(' · ') if ' #' in tok]


before = {'prosperity': sha(db), 'market': sha(market_db)}
out, msgs = deliver('delivery', datetime(2026, 11, 7, 12, 0, tzinfo=timezone.utc))
momentum_msg = next(m for m in msgs if m.startswith('*动量榜'))
combo = [m for m in msgs if m.startswith('*景气动量精选')]
pool = json.loads(pool_path.read_text(encoding='utf-8'))
pool_syms = [m['symbol'] for m in pool['members']]
expected = {k: [r['symbol'] for r in sorted((r for r in b['scored'] if r['eligible']), key=lambda r: r['rank'])
                if r['symbol'] in set(pool_syms)] for k, b in payload['boards'].items()}
result['delivery'] = {
    'status': out.status, 'sent': list(out.sent), 'error': out.error,
    'momentum_chars': len(momentum_msg), 'combo_messages': len(combo), 'combo_chars': [len(m) for m in combo],
    'pool_members': len(pool_syms), 'combo_14w': expected['14w'],
    'combo_lists_exactly_the_eligible_pool_members': listed(combo[0]) == expected['14w'],
    'rerun_status': deliver('delivery', datetime(2026, 11, 7, 12, 30, tzinfo=timezone.utc))[0].status}
(evidence / 'momentum-text.txt').write_text(momentum_msg + '\n', encoding='utf-8')
(evidence / 'combo-text.txt').write_text('\n\n'.join(combo) + '\n', encoding='utf-8')

# ---- A4 negatives on the real data ---------------------------------------------------------------
stale, stale_msgs = deliver('delivery-stale-pool', datetime(2026, 11, 20, 12, 0, tzinfo=timezone.utc))
fail_run = lambda cmd, **kw: (subprocess.CompletedProcess(cmd, 6, '', 'boom') if cmd[1].endswith('momentum_board.py')
                              else run(cmd, **kw))
failed, _ = deliver('delivery-cli-fail', datetime(2026, 11, 7, 12, 0, tzinfo=timezone.utc), run=fail_run)
ledger = d.read_ledger(work / 'delivery-cli-fail', AS_OF)['items']
retried, _ = deliver('delivery-cli-fail', datetime(2026, 11, 7, 12, 40, tzinfo=timezone.utc))
result['delivery_negatives'] = {
    'stale_pool': {'sent': list(stale.sent), 'board_has_no_star': '★' not in stale_msgs[-2],
                   'combo': stale_msgs[-1]},
    'cli_failure': {'status': failed.status, 'error': failed.error, 'sent': list(failed.sent),
                    'ledger_items': sorted(ledger), 'retry_sent': list(retried.sent)}}

# ---- A5 the 100-member cap -----------------------------------------------------------------------
cap = copy.deepcopy(payload)
ranks = {k: {r['symbol']: r['rank'] for r in b['scored'] if r['eligible']} for k, b in cap['boards'].items()}
cap_pool = sorted(ranks['14w'], key=lambda s: -ranks['14w'][s])[:100]   # 100 real names, widest '#n' tokens
cap_text = d.combo_text(cap, cap_pool, None)
result['cap_100'] = {'pool': len(cap_pool), 'chars': len(cap_text), 'within_limit': len(cap_text) <= 4096,
                     'every_name_listed_once': sorted(listed(cap_text)) == sorted(d.md_escape(s) for s in cap_pool)}
(evidence / 'combo-cap-100.txt').write_text(cap_text + '\n', encoding='utf-8')

result['inputs_unchanged'] = before == {'prosperity': sha(db), 'market': sha(market_db)}
(evidence / 'result.json').write_text(json.dumps(result, ensure_ascii=False, indent=1) + '\n', encoding='utf-8')
print(json.dumps(result, ensure_ascii=False, indent=1))
shutil.rmtree(work, ignore_errors=True)
