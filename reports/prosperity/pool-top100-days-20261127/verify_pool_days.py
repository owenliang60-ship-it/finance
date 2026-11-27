#!/usr/bin/env python3
"""Independent zero-dependency verifier for the 90d >=31 top-100 pool, M11 web page, and M16 U70 subset.

Standard library only (`python3 -I`); imports nothing from this repository.
1. Recomputes the 90-day SPY (`close > 0`) window, per-day top-100 counts, `daily_min`, and expected
   pool membership + `top100_days` directly from `market.db` and `prosperity.db` via independent SQL.
2. Checks `prosperity_pool.json` header, criteria, source hash, universe, coverage, window, and members
   (rejecting NaN/Infinity, booleans, floats on integer fields).
3. Parses the static HTML page with `html.parser` and verifies every section, row, cell, badge,
   F1 weekly pool column (`✓` and `xx/90`), and top `<aside id="pool">` table.
4. When `--momentum` is given, verifies the 70-day window equals the last 70 days of the 90-day window,
   recomputes every member's 70-day top-100 count, and checks `U70 (>=36/70) ⊆ pool_liquid (>=31/90)`.

Prints `checked N rows, M pool days, L pool list rows, U u70 symbols; differences: K`; exit 1 when K > 0.
"""
from __future__ import annotations

import argparse
from datetime import date
import hashlib
from html.parser import HTMLParser
import json
import math
import sqlite3
import sys

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
POOL_DAYS = 90
POOL_MIN_TOP_DAYS = 31
POOL_TOP = 100
POOL_MAX_LAG = 7
POOL_MAX_SPAN = 160
COVERAGE_FLOOR = 0.90
MOMENTUM_DAYS = 70
MOMENTUM_MIN_TOP_DAYS = 36


def _no_nan(token: str):
    raise ValueError(f'non-finite JSON constant: {token}')


def _strict_int(v) -> bool:
    return type(v) is int


def _finite_num(v) -> bool:
    return type(v) in (int, float) and math.isfinite(float(v))


class Page(HTMLParser):
    """Sections → rows → (cells, badges), plus <aside id="pool"> and table headers."""

    def __init__(self):
        super().__init__()
        self.sections, self.cur, self.body, self.head_row = {}, None, False, False
        self.badge, self.head, self.pool = None, [], None

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == 'section':
            self.cur = self.sections.setdefault(a['id'], {'rows': [], 'header': [], 'ths': []})
        elif tag == 'aside' and a.get('id') == 'pool':
            self.pool = self.cur = {'rows': [], 'header': [], 'ths': []}
        elif self.cur is None:
            return
        elif tag == 'thead':
            self.head_row = True
        elif tag == 'th' and self.head_row:
            self.cur['ths'].append('')
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
        if tag == 'thead':
            self.head_row = False
        elif tag == 'tbody':
            self.body = False
        elif tag in ('i', 'b'):
            self.badge = None
        elif tag in ('section', 'aside'):
            self.cur = None

    def handle_data(self, data):
        if self.cur is None:
            if not self.sections:
                self.head.append(data)
            return
        if self.head_row and self.cur['ths']:
            self.cur['ths'][-1] += data
            return
        if not self.body:
            self.cur['header'].append(data)
            return
        rows = self.cur['rows']
        if rows and rows[-1]['cells']:
            rows[-1]['cells'][-1] += data
            if self.badge is not None:
                self.badge[1] += data


def parse_html(path: str):
    p = Page()
    with open(path, encoding='utf-8') as f:
        p.feed(f.read())
    p.close()
    for s in list(p.sections.values()) + ([p.pool] if p.pool else []):
        for r in s['rows']:
            r['cells'] = [c.strip() for c in r['cells']]
            r['badges'] = [(t, x.strip()) for t, x in r['badges']]
        s['ths'] = [t.strip() for t in s['ths']]
        s['header'] = ''.join(s['header'])
    return {'__head__': {'header': ''.join(p.head)}, '__pool__': p.pool, **p.sections}


def load_boards(conn, version: str):
    out = []
    for sid in SCHEMES:
        q = ('SELECT board_id, board_type, as_of, mode, params_bootstrap, members_json FROM board '
             'WHERE scheme_id=? AND code_version=? AND board_type=? AND mode LIKE ? ORDER BY as_of DESC')
        weekly = conn.execute(q, (sid, version, 'weekly', 'live')).fetchall()[:1]
        finals = conn.execute(q, (sid, version, 'quarter_final', '%')).fetchall()
        for b in weekly + finals:
            out.append({'scheme': sid, 'id': b[0], 'type': b[1], 'as_of': b[2], 'mode': b[3],
                        'boot': b[4], 'members': json.loads(b[5]),
                        'section': f'{sid}-{"w" if b[1] == "weekly" else "q"}-{b[2]}'})
    return out


def expected_rows(conn, board_id: int):
    rows = conn.execute(
        'SELECT s.symbol, s.status, s.observe_reason, s.score, s.grade, s.rank, s.family_scores_json, '
        's.demotions_json, s.badges_json, sp.name, sp.sector, sp.pit_basis, si.factor_meta_json '
        'FROM score s JOIN board b ON b.board_id=s.board_id '
        'JOIN stock_period sp ON sp.board_id=s.board_id AND sp.symbol=s.symbol '
        'JOIN stock_input si ON si.input_set_id=b.input_set_id AND si.symbol=s.symbol '
        'WHERE s.board_id=?', (board_id,)).fetchall()
    order = {'ranked': 0, 'observe': 1, 'excluded': 2}
    everyone = bool(rows) and all(r[11] == 'approximate' for r in rows)
    out = []
    for (sym, status, reason, score, grade, rank, fam, dem, badges, name, sector, pit, meta) in sorted(
            rows, key=lambda r: (order[r[1]], r[5] or 0, r[0])):
        labels = set(json.loads(meta)['labels'])
        codes = [b for b in json.loads(badges) if b != 'params_bootstrap'] + json.loads(dem)
        codes += [x for x in SHOWN if x in labels]
        codes += ['approximate'] if pit == 'approximate' and not everyone else []
        codes = list(dict.fromkeys(codes))
        fam = json.loads(fam)
        out.append({
            'symbol': sym, 'status': status, 'grade': grade, 'rank': rank, 'name': name,
            'score': score, 'sector': sector,
            'cells': ['—' if rank is None else str(rank), sym, None, sector or '—',
                      grade if status == 'ranked' else REASON.get(reason or '', reason or '—'),
                      '—' if score is None else f'{math.floor(score * 10) / 10:.1f}',
                      *('—' if fam.get(k) is None else f'{fam[k]:.0f}' for k in FAMILIES),
                      ''.join(TEXT.get(c, c) for c in codes)],
            'badges': [('b' if c in RED else 'i', TEXT.get(c, c)) for c in codes],
        })
    return out, everyone


def expected_criteria(conn):
    f1 = conn.execute("SELECT scheme_hash FROM scheme WHERE scheme_id='F1'").fetchall()
    if len(f1) != 1:
        raise SystemExit('expected exactly one F1 scheme row')
    return {
        'scheme_id': 'F1', 'scheme_hash': f1[0][0], 'grades': ['STRICT', 'FULL'], 'tier': {'tiers': 5},
        'liquidity': {'metric': 'top_dollar_volume_days', 'calendar': 'SPY',
                      'trading_days': POOL_DAYS, 'min_top_days': POOL_MIN_TOP_DAYS, 'top': POOL_TOP},
    }


def recompute_market_liquidity(mconn, members_list, as_of: str):
    """Independent SQL: BETWEEN range read filtered in Python over SPY close>0 calendar."""
    members = sorted(set(members_list))
    spy = [r[0] for r in mconn.execute(
        "SELECT date FROM daily_price WHERE symbol='SPY' AND close > 0 AND date <= ? ORDER BY date", (as_of,))]
    if len(spy) < POOL_DAYS:
        raise ValueError(f'SPY calendar too short: {len(spy)}')
    win90 = spy[-POOL_DAYS:]
    lag = (date.fromisoformat(as_of) - date.fromisoformat(win90[-1])).days
    span = (date.fromisoformat(as_of) - date.fromisoformat(win90[0])).days
    if lag > POOL_MAX_LAG or span >= POOL_MAX_SPAN:
        raise ValueError(f'window lag={lag} span={span}')
    win_set, mem_set = set(win90), set(members)
    per_day = {d: {} for d in win90}
    for sym, d, close, vol in mconn.execute(
            'SELECT symbol, date, close, volume FROM daily_price WHERE date BETWEEN ? AND ?',
            (win90[0], win90[-1])):
        if d in win_set and sym in mem_set and close is not None and vol is not None:
            c, v = float(close), float(vol)
            if math.isfinite(c) and math.isfinite(v) and c > 0 and v >= 0:
                dv = c * v
                if math.isfinite(dv) and dv >= 0:
                    per_day[d][sym] = dv
    counts90 = {s: 0 for s in members}
    counts70 = {s: 0 for s in members}
    win70_set = set(win90[-MOMENTUM_DAYS:])
    for d in win90:
        top_syms = [s for _, s in sorted((-dv, s) for s, dv in per_day[d].items())[:POOL_TOP]]
        for s in top_syms:
            counts90[s] += 1
            if d in win70_set:
                counts70[s] += 1
    daily_min = min(len(per_day[d]) for d in win90)
    return win90, counts90, counts70, daily_min


def verify_pool_and_bind(conn, mconn, pool, board, rows, criteria, diff):
    """Validate pool against both DBs and return ({symbol: top100_days}, unbound_reason_or_None)."""
    if pool is None:
        return {}, 'no pool'
    try:
        if pool.get('schema_version') != 1 or pool.get('name') != '景气精选池':
            diff('pool: schema_version or name invalid')
            return {}, 'schema'
        if pool.get('as_of') != board['as_of'] or board['mode'] != 'live':
            diff(f'pool: as_of {pool.get("as_of")!r} != board {board["as_of"]!r} or mode={board["mode"]!r}')
            return {}, 'as_of/board'
        if pool.get('criteria') != criteria:
            diff('pool: criteria mismatch')
            return {}, 'criteria'
        version, stored_hash = conn.execute('SELECT code_version, content_hash FROM board WHERE board_id=?',
                                            (board['id'],)).fetchone()
        if pool['source']['code_version'] != version or pool['source']['content_hash'] != stored_hash:
            diff('pool: source version or content_hash mismatch')
            return {}, 'source'
        ranked = [r for r in rows if r['status'] == 'ranked']
        cut = -(-len(ranked) // 5)
        eligible_rows = [r for r in ranked if r['rank'] <= cut and r['grade'] in ('STRICT', 'FULL')]
        symbols = sorted(set(board['members']))
        total_n = len(symbols)
        digest = hashlib.sha256('\n'.join(symbols).encode('utf-8')).hexdigest()
        cov = pool['coverage']
        win90, counts90, _, daily_min = recompute_market_liquidity(mconn, symbols, board['as_of'])
        rk_stat, lq_stat = cov['ranked'], cov['liquidity_daily']
        if (not _strict_int(pool['universe']['count']) or pool['universe']['count'] != total_n
                or pool['universe']['symbols_sha256'] != digest
                or not _strict_int(rk_stat['covered']) or rk_stat['covered'] != len(ranked)
                or not _strict_int(rk_stat['total']) or rk_stat['total'] != total_n
                or not _finite_num(rk_stat['ratio']) or not math.isclose(rk_stat['ratio'], len(ranked) / total_n, abs_tol=1e-12)
                or len(ranked) / total_n < COVERAGE_FLOOR
                or not _strict_int(lq_stat['covered']) or lq_stat['covered'] != daily_min
                or not _strict_int(lq_stat['total']) or lq_stat['total'] != total_n
                or not _finite_num(lq_stat['ratio']) or not math.isclose(lq_stat['ratio'], daily_min / total_n, abs_tol=1e-12)
                or daily_min / total_n < COVERAGE_FLOOR
                or not _strict_int(cov['tier_cut']) or cov['tier_cut'] != cut
                or not _strict_int(cov['eligible']) or cov['eligible'] != len(eligible_rows)
                or not _strict_int(cov['selected'])
                or cov['trading_window'] != [win90[0], win90[-1]]):
            diff('pool: universe/coverage/window mismatch vs recomputed DB values')
            return {}, 'counts'
        expected_members = [(r['symbol'], r['rank'], r['grade'], counts90[r['symbol']])
                            for r in sorted(eligible_rows, key=lambda x: x['rank'])
                            if counts90[r['symbol']] >= POOL_MIN_TOP_DAYS]
        member_keys = {'symbol', 'name', 'sector', 'industry', 'rank', 'grade', 'score',
                       'family_scores', 'badges', 'top100_days'}
        got_members = []
        for m in pool['members']:
            if (not isinstance(m, dict) or set(m) != member_keys
                    or not _strict_int(m.get('rank')) or not _strict_int(m.get('top100_days'))
                    or not (POOL_MIN_TOP_DAYS <= m['top100_days'] <= POOL_DAYS)
                    or not _finite_num(m.get('score'))):
                diff(f'pool: malformed member {m!r}')
                return {}, 'member_schema'
            got_members.append((m['symbol'], m['rank'], m['grade'], m['top100_days']))
        if cov['selected'] != len(got_members) or got_members != expected_members:
            diff(f'pool: members mismatch got={len(got_members)} expected={len(expected_members)}')
            return {}, 'members'
        return {sym: days for sym, _, _, days in got_members}, None
    except Exception as exc:
        diff(f'pool: exception during verification: {exc}')
        return {}, 'malformed'


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--db', required=True)
    ap.add_argument('--market-db', required=True)
    ap.add_argument('--html', required=True)
    ap.add_argument('--pool', required=True)
    ap.add_argument('--code-version', required=True)
    ap.add_argument('--momentum')
    a = ap.parse_args(argv)

    diffs, checked, pool_checked, u70_count = [], 0, 0, 0

    def diff(msg: str):
        diffs.append(msg)
        if len(diffs) <= 20:
            print('DIFF', msg)

    try:
        with open(a.pool, encoding='utf-8') as f:
            pool = json.load(f, parse_constant=_no_nan)
    except Exception as exc:
        diff(f'pool file unreadable/non-finite: {exc}')
        pool = None

    conn = sqlite3.connect(f'file:{a.db}?mode=ro', uri=True)
    mconn = sqlite3.connect(f'file:{a.market_db}?mode=ro', uri=True)
    try:
        page = parse_html(a.html)
        expected_boards = load_boards(conn, a.code_version)
        criteria = expected_criteria(conn)
        page_head = page.pop('__head__', {'header': ''})['header']
        pool_list = page.pop('__pool__', None)
        pool_listed = 0
        if '成交额排名' in page_head or any('成交额排名' in ' '.join(s.get('ths', ())) for s in page.values()):
            diff('html: legacy column header 成交额排名 still present')
        if not any(b['scheme'] == 'F1' and b['type'] == 'weekly' for b in expected_boards):
            if '未标入池：库中无 F1 live 周榜' not in page_head:
                diff('page header: missing no-live-weekly notice')
        if [b['section'] for b in expected_boards] != list(page):
            diff(f'sections: expected {len(expected_boards)}, page has {len(page)}')
        for b in expected_boards:
            sec = page.get(b['section'])
            if sec is None:
                diff(f'{b["section"]}: missing')
                continue
            rows, everyone = expected_rows(conn, b['id'])
            pooled_board = b['scheme'] == 'F1' and b['type'] == 'weekly'
            marks, why = verify_pool_and_bind(conn, mconn, pool, b, rows, criteria, diff) if pooled_board else ({}, None)
            if bool(b['boot']) != ('参数自举' in sec['header']):
                diff(f'{b["section"]}: params_bootstrap header')
            if everyone != ('本榜全部为近似 PIT' in sec['header']):
                diff(f'{b["section"]}: all-approximate header')
            if bool(marks) != ('未验证当前新鲜度' in sec['header']):
                diff(f'{b["section"]}: freshness note')
            if pooled_board:
                note_ok = (f'入池 {len(marks)} 只' in sec['header']) if why is None else ('未标入池' in sec['header'])
                if not note_ok:
                    diff(f'{b["section"]}: pool header note mismatch (why={why})')
                if marks and sec['ths'][-2:] != ['入池', '前100天数']:
                    diff(f'{b["section"]}: expected trailing headers 入池/前100天数, got {sec["ths"][-2:]}')
            got = sec['rows']
            if len(got) != len(rows):
                diff(f'{b["section"]}: {len(got)} rows on page, {len(rows)} in db')
            for e, g in zip(rows, got):
                checked += 1
                cells = g['cells']
                want = list(e['cells'])
                name = e['name'] or '—'
                want[2] = name if len(name) <= 28 else name[:28] + '…'
                if marks:
                    days = marks.get(e['symbol'])
                    want += ['✓' if days is not None else '', '' if days is None else f'{days}/{POOL_DAYS}']
                    pool_checked += days is not None
                    if (g['cls'] == 'p') != (days is not None):
                        diff(f'{b["section"]} {e["symbol"]}: row highlight')
                if cells != want:
                    diff(f'{b["section"]} {e["symbol"]}: cells got={cells} want={want}')
                if g['badges'] != e['badges']:
                    diff(f'{b["section"]} {e["symbol"]}: badges got={g["badges"]} want={e["badges"]}')
            if pooled_board:
                if pool_list is None:
                    diff('pool list: missing although F1 live weekly board exists')
                else:
                    if marks and pool_list['ths'] != ['排名', '代码', '名称', '行业', '评级', '分数', '前100天数']:
                        diff(f'pool list: headers got={pool_list["ths"]}')
                    want_list = []
                    for e in rows:
                        if e['symbol'] in marks:
                            name = e['name'] or '—'
                            want_list.append([e['cells'][0], e['symbol'],
                                              name if len(name) <= 28 else name[:28] + '…',
                                              e['cells'][3], e['cells'][4], e['cells'][5],
                                              f'{marks[e["symbol"]]}/{POOL_DAYS}'])
                    got_list = [r['cells'] for r in pool_list['rows']]
                    pool_listed = len(got_list)
                    if got_list != want_list:
                        diff(f'pool list: rows mismatch got={len(got_list)} want={len(want_list)}')
                    if f'精选池 · {b["as_of"]}' not in pool_list['header']:
                        diff('pool list: title date')
                    if not (f'入池 {len(marks)} 只' in pool_list['header'] if why is None else '未标入池' in pool_list['header']):
                        diff('pool list: header note')

            if pooled_board and a.momentum:
                try:
                    with open(a.momentum, encoding='utf-8') as mf:
                        mp = json.load(mf, parse_constant=_no_nan)
                    win90, counts90, counts70, _ = recompute_market_liquidity(mconn, b['members'], b['as_of'])
                    mb = mp['boards']['14w']
                    if mp['as_of'] != b['as_of'] or mp['price_date'] != win90[-1] or mb['days'] != MOMENTUM_DAYS:
                        diff('momentum: header/date/days mismatch')
                    if mb['window'] != [win90[-MOMENTUM_DAYS - 1], win90[-1]]:
                        diff(f'momentum: window {mb["window"]} != {[win90[-MOMENTUM_DAYS - 1], win90[-1]]}')
                    got70 = {}
                    for r in mb['scored']:
                        if not _strict_int(r.get('top100_days')):
                            diff(f'momentum: non-int top100_days on {r.get("symbol")}')
                        got70[r['symbol']] = r['top100_days']
                    for sym, cnt in mb['unavailable_top100_days'].items():
                        if not _strict_int(cnt):
                            diff(f'momentum: non-int unavailable count on {sym}')
                        got70[sym] = cnt
                    if got70 != counts70:
                        bad_syms = [s for s in set(got70) | set(counts70) if got70.get(s) != counts70.get(s)]
                        diff(f'momentum: 70d top100_days mismatch on {bad_syms[:10]}')
                    u70_syms = {s for s, c in got70.items() if c >= MOMENTUM_MIN_TOP_DAYS}
                    pool_liq_syms = {s for s, c in counts90.items() if c >= POOL_MIN_TOP_DAYS}
                    u70_count = len(u70_syms)
                    if not (u70_syms <= pool_liq_syms):
                        diff(f'momentum: U70 not subset of pool liquidity: {sorted(u70_syms - pool_liq_syms)}')
                except Exception as exc:
                    diff(f'momentum verification failed: {exc}')
    finally:
        conn.close()
        mconn.close()

    print(f'checked {checked} rows, {pool_checked} pool days, {pool_listed} pool list rows, '
          f'{u70_count} u70 symbols; differences: {len(diffs)}')
    return 1 if diffs else 0


if __name__ == '__main__':
    sys.exit(main())
