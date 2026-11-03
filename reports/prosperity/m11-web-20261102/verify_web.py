#!/usr/bin/env python3
"""Independent check of the M11 web page against prosperity.db (plan §6, A3/A4).

Standard library only; imports nothing from this repository. Rebuilds every expected row straight
from the board / score / stock_period / stock_input tables and its own pool binding, parses the
HTML with html.parser, and compares section by section, row by row, cell by cell.
content_hash is not recomputed here (it hashes M8's canonical JSON; rewriting it would copy the
implementation); the unit tests cover that branch.

Usage: verify_web.py --db SNAPSHOT --html PAGE --code-version V [--pool POOL.json]
Prints `checked N rows, M pool ranks; differences: K`; exit 1 when K > 0.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import sqlite3
import sys
from html.parser import HTMLParser

SCHEMES = ('F1', 'F1-rank', 'F1-exfin', 'F0')
FAMILIES = ('growth', 'accel', 'quality', 'expectation')
SHOWN = ('eps_turnaround', 'eps_ttm_turnaround', 'semiannual_period')
TEXT = {
    'pe_redflag': 'PE红旗', 'ntm_loss': '预期亏损', 'ntm_gate_unknown': 'NTM待验',
    'forward_stale': '预期过期', 'gm_slope_missing': '无毛利斜率', 'industry_unknown': '行业未知',
    'net_margin_down': '降级·净利率降', 'new_listing_gate': '降级·新上市', 'ntm_not_above_ttm': '降级·NTM≤TTM',
    'eps_turnaround': '扭亏', 'eps_ttm_turnaround': 'TTM扭亏', 'semiannual_period': '半年报',
    'approximate': '近似PIT',
}
REASON = {
    'identity_unverified': '身份未核实', 'low_coverage': '因子覆盖不足', 'insufficient_quarters': '季数不足',
    'no_scorable_factor': '无可打分因子', 'universe_excluded': '方案排除',
}
RED = {'pe_redflag', 'ntm_loss', 'net_margin_down', 'new_listing_gate', 'ntm_not_above_ttm'}
POOL_CRITERIA_TOP = 100


# ---- page ---------------------------------------------------------------------------
class Page(HTMLParser):
    """Sections → rows → (cells, badges). </td> and </tr> may be omitted in the page."""

    def __init__(self):
        super().__init__()
        self.sections, self.cur, self.body, self.badge = {}, None, False, None

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == 'section':
            self.cur = self.sections.setdefault(a['id'], {'rows': [], 'header': []})
        elif self.cur is None:
            return
        elif tag == 'tbody':
            self.body = True
        elif self.body and tag == 'tr':
            self.cur['rows'].append({'cls': a.get('class'), 'cells': [], 'badges': []})
        elif self.body and tag == 'td':
            self.cur['rows'][-1]['cells'].append('')
        elif self.body and tag in ('i', 'b'):
            self.badge = [tag, '']
            self.cur['rows'][-1]['badges'].append(self.badge)

    def handle_endtag(self, tag):
        if tag == 'tbody':
            self.body = False
        elif tag in ('i', 'b'):
            self.badge = None
        elif tag == 'section':
            self.cur = None

    def handle_data(self, data):
        if self.cur is None:
            return
        if not self.body:
            self.cur['header'].append(data)
            return
        rows = self.cur['rows']
        if rows and rows[-1]['cells']:
            rows[-1]['cells'][-1] += data
            if self.badge is not None:
                self.badge[1] += data


def parse(path):
    p = Page()
    with open(path, encoding='utf-8') as f:
        p.feed(f.read())
    p.close()
    for s in p.sections.values():
        for r in s['rows']:
            r['cells'] = [c.strip() for c in r['cells']]
            r['badges'] = [(t, x.strip()) for t, x in r['badges']]
        s['header'] = ''.join(s['header'])
    return p.sections


# ---- expectations -------------------------------------------------------------------
def boards(conn, version):
    out = []
    for sid in SCHEMES:
        q = ('SELECT board_id, board_type, as_of, mode, params_bootstrap, members_json FROM board '
             'WHERE scheme_id=? AND code_version=? AND board_type=? AND mode LIKE ? ORDER BY as_of DESC')
        weekly = conn.execute(q, (sid, version, 'weekly', 'live')).fetchall()[:1]      # latest = live only
        finals = conn.execute(q, (sid, version, 'quarter_final', '%')).fetchall()
        for b in weekly + finals:
            out.append({'scheme': sid, 'id': b[0], 'type': b[1], 'as_of': b[2], 'mode': b[3],
                        'boot': b[4], 'members': json.loads(b[5]),
                        'section': f'{sid}-{"w" if b[1] == "weekly" else "q"}-{b[2]}'})
    return out


def expected_rows(conn, board_id):
    rows = conn.execute(
        'SELECT s.symbol, s.status, s.observe_reason, s.score, s.grade, s.rank, s.family_scores_json, '
        's.demotions_json, s.badges_json, sp.name, sp.sector, sp.pit_basis, si.factor_meta_json '
        'FROM score s JOIN board b ON b.board_id=s.board_id '
        'JOIN stock_period sp ON sp.board_id=s.board_id AND sp.symbol=s.symbol '
        'JOIN stock_input si ON si.input_set_id=b.input_set_id AND si.symbol=s.symbol '
        'WHERE s.board_id=?', (board_id,)).fetchall()
    order = {'ranked': 0, 'observe': 1, 'excluded': 2}
    out = []
    for (sym, status, reason, score, grade, rank, fam, dem, badges, name, sector, pit, meta) in sorted(
            rows, key=lambda r: (order[r[1]], r[5] or 0, r[0])):
        labels = set(json.loads(meta)['labels'])
        codes = [b for b in json.loads(badges) if b != 'params_bootstrap'] + json.loads(dem)
        codes += [x for x in SHOWN if x in labels] + (['approximate'] if pit == 'approximate' else [])
        codes = list(dict.fromkeys(codes))
        fam = json.loads(fam)
        out.append({
            'symbol': sym, 'status': status, 'grade': grade, 'rank': rank, 'name': name,
            'cells': ['—' if rank is None else str(rank), sym, None, sector or '—',
                      grade if status == 'ranked' else REASON.get(reason or '', reason or '—'),
                      '—' if score is None else f'{math.floor(score * 10) / 10:.1f}',   # rounded down
                      *('—' if fam.get(k) is None else f'{fam[k]:.0f}' for k in FAMILIES),
                      ''.join(TEXT.get(c, c) for c in codes)],
            'badges': [('b' if c in RED else 'i', TEXT.get(c, c)) for c in codes],
        })
    return out


def bind_pool(conn, pool, board, rows):
    """Own binding: (symbol → dollar-volume rank, reason or None). Plan §9.2 steps 3–4 and §15."""
    if pool is None:
        return {}, 'no pool'
    try:
        if pool['as_of'] != board['as_of'] or board['mode'] != 'live':
            return {}, 'as_of/board'
        version = conn.execute('SELECT code_version FROM board WHERE board_id=?', (board['id'],)).fetchone()[0]
        if pool['source']['code_version'] != version:
            return {}, 'code_version'
        ranked = [r for r in rows if r['status'] == 'ranked']
        cut = -(-len(ranked) // 5)
        eligible = sum(r['rank'] <= cut and r['grade'] in ('STRICT', 'FULL') for r in ranked)
        symbols = sorted(set(board['members']))
        digest = hashlib.sha256('\n'.join(symbols).encode('utf-8')).hexdigest()
        cov = pool['coverage']
        if (pool['universe']['count'] != len(symbols) or pool['universe']['symbols_sha256'] != digest
                or cov['ranked']['covered'] != len(ranked) or cov['tier_cut'] != cut or cov['eligible'] != eligible):
            return {}, 'counts'
        by = {r['symbol']: r for r in rows}
        for m in pool['members']:
            r = by.get(m['symbol'])
            if (r is None or r['status'] != 'ranked' or r['grade'] not in ('STRICT', 'FULL')
                    or m['rank'] != r['rank'] or m['grade'] != r['grade'] or m['rank'] > cut
                    or not 1 <= m['dollar_volume_rank'] <= POOL_CRITERIA_TOP):
                return {}, 'member ' + str(m['symbol'])
        return {m['symbol']: m['dollar_volume_rank'] for m in pool['members']}, None
    except (KeyError, TypeError):
        return {}, 'malformed'


# ---- compare ------------------------------------------------------------------------
def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument('--db', required=True)
    ap.add_argument('--html', required=True)
    ap.add_argument('--pool')
    ap.add_argument('--code-version', required=True, help='the version the page was generated for')
    a = ap.parse_args(argv)
    pool = None
    if a.pool:
        try:
            with open(a.pool, encoding='utf-8') as f:
                pool = json.load(f)
        except (OSError, ValueError):
            pool = None
    conn = sqlite3.connect(f'file:{a.db}?mode=ro&immutable=1', uri=True)
    page = parse(a.html)
    diffs, checked, pool_checked = [], 0, 0

    def diff(msg):
        diffs.append(msg)
        if len(diffs) <= 20:
            print('DIFF', msg)

    expected_boards = boards(conn, a.code_version)
    if [b['section'] for b in expected_boards] != list(page):
        diff(f'sections: expected {len(expected_boards)}, page has {len(page)} (or order differs)')
    for b in expected_boards:
        sec = page.get(b['section'])
        if sec is None:
            diff(f'{b["section"]}: missing')
            continue
        rows = expected_rows(conn, b['id'])
        pooled_board = b['scheme'] == 'F1' and b['type'] == 'weekly'
        marks, why = bind_pool(conn, pool, b, rows) if pooled_board else ({}, None)
        if bool(b['boot']) != ('参数自举' in sec['header']):
            diff(f'{b["section"]}: params_bootstrap header')
        if pooled_board:
            note_ok = (f'入池 {len(marks)} 只' in sec['header']) if marks else ('未标入池' in sec['header'])
            if not note_ok:
                diff(f'{b["section"]}: pool note (expected {"marked" if marks else "unmarked: " + str(why)})')
        got = sec['rows']
        if len(got) != len(rows):
            diff(f'{b["section"]}: {len(got)} rows on page, {len(rows)} in db')
        for e, g in zip(rows, got):
            checked += 1
            cells = g['cells']
            want = list(e['cells'])
            name = e['name'] or '—'
            want[2] = name if len(name) <= 28 else name[:28] + '…'
            if marks:                                      # pooled table: two extra columns on every row
                rank = marks.get(e['symbol'])
                want += ['✓' if rank else '', '' if rank is None else str(rank)]
                pool_checked += rank is not None
                if (g['cls'] == 'p') != (rank is not None):
                    diff(f'{b["section"]} {e["symbol"]}: row highlight')
            if cells != want:
                bad = [i for i, (x, y) in enumerate(zip(cells, want)) if x != y] or ['length']
                diff(f'{b["section"]} {e["symbol"]}: cells {bad} page={[cells[i] for i in bad if i != "length"] or cells} '
                     f'db={[want[i] for i in bad if i != "length"] or want}')
            if g['badges'] != e['badges']:
                diff(f'{b["section"]} {e["symbol"]}: badges page={g["badges"]} db={e["badges"]}')
        if marks and sum(r['cls'] == 'p' for r in got) != len(marks):
            diff(f'{b["section"]}: {sum(r["cls"] == "p" for r in got)} marked rows, pool has {len(marks)}')
    print(f'checked {checked} rows, {pool_checked} pool ranks; differences: {len(diffs)}')
    return 1 if diffs else 0


if __name__ == '__main__':
    sys.exit(main())
