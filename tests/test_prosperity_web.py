"""M11 web page: board pages, pool binding, static HTML renderer."""
import copy
from datetime import date, timedelta

import pytest

from src.data.prosperity_pool import LiquidityResult, build_pool, validate_prosperity_pool
from src.data.prosperity_store import ProsperityStore
from src.data.prosperity_web import board_page, load_pages, pool_binding, section_id
from terminal.prosperity.schemes import SCHEMES, scheme_hash
from tests.prosperity_fixtures import storage_batch, weekdays


@pytest.fixture()
def store(tmp_path):
    db = tmp_path / 'prosperity.db'
    with ProsperityStore(db, read_only=False) as s:
        s.save_batch(storage_batch(freeze=True, quarter_label='2026Q2'))
        s.save_batch(storage_batch(as_of='2026-10-03', freeze=True, quarter_label='2026Q3'))
    with ProsperityStore(db) as s:
        yield s


def test_pages_cover_latest_weekly_and_every_quarter_final(store):
    pages = load_pages(store, code_version='test-v1')
    assert [(p.scheme_id, p.board_type, p.as_of) for p in pages[:3]] == [
        ('F1', 'weekly', '2026-10-03'), ('F1', 'quarter_final', '2026-10-03'), ('F1', 'quarter_final', '2026-09-26')]
    assert len(pages) == 4 * 3
    assert len({section_id(p) for p in pages}) == len(pages)


def test_every_row_kept_once_ranked_first_by_rank(store):
    for p in load_pages(store, code_version='test-v1'):
        ranked = [r for r in p.rows if r.status == 'ranked']
        assert [r.rank for r in ranked] == sorted(r.rank for r in ranked)
        assert p.rows[:len(ranked)] == tuple(ranked)
        assert len({r.symbol for r in p.rows}) == len(p.rows) == 12
        assert p.counts['ranked'] + p.counts['observe'] + p.counts['excluded'] == 12


def test_board_badge_moves_to_header_and_labels_follow_rules(store):
    p = load_pages(store, code_version='test-v1')[0]
    assert p.params_bootstrap is True
    assert all('params_bootstrap' not in r.badges for r in p.rows)
    assert all(b not in ('listing_date_unknown', 'eps_split_retrospective', 'identity_unverified')
               for r in p.rows for b in r.badges)
    obs = [r for r in p.rows if r.status == 'observe']
    assert {r.observe_reason for r in obs} >= {'insufficient_quarters', 'identity_unverified'}


def test_row_view_matches_the_stored_board(store):
    key = store.list_boards(scheme_hash=F1, code_version='test-v1', board_type='weekly')[-1]
    stored = store.get_board(key)
    page = board_page(stored)
    rows = {r.symbol: r for r in stored.board.rows}
    names = {p['symbol']: p['name'] for p in stored.stocks}
    for w in page.rows:
        r = rows[w.symbol]
        assert (w.status, w.rank, w.grade, w.score, w.observe_reason) == (
            r.status, r.rank, r.grade, r.score, r.observe_reason)
        assert dict(w.family_scores) == dict(r.family_scores) and w.name == names[w.symbol]
        assert w.pool_rank is None
    assert page.pool_note is None
    assert page.counts['STRICT'] + page.counts['FULL'] + page.counts['BELOW'] == page.counts['ranked']


def test_badges_order_demotions_labels_and_approximate_pit(store):
    from dataclasses import replace
    key = store.list_boards(scheme_hash=F1, code_version='test-v1', board_type='weekly')[-1]
    stored = store.get_board(key)
    target = next(r for r in stored.board.rows if r.status == 'ranked')
    rows = tuple(replace(r, badges=('params_bootstrap', 'pe_redflag'), demotions=('net_margin_down', 'pe_redflag'))
                 if r.symbol == target.symbol else r for r in stored.board.rows)
    factors = tuple(replace(f, labels=('listing_date_unknown', 'semiannual_period', 'eps_turnaround'))
                    if f.symbol == target.symbol else f for f in stored.factors)
    stocks = tuple({**p, 'pit_basis': 'approximate'} if p['symbol'] == target.symbol else p for p in stored.stocks)
    edited = replace(stored, board=replace(stored.board, rows=rows), factors=factors, stocks=stocks)
    w = next(r for r in board_page(edited).rows if r.symbol == target.symbol)
    assert w.badges == ('pe_redflag', 'net_margin_down', 'eps_turnaround', 'semiannual_period', 'approximate')


F1 = scheme_hash(SCHEMES['F1'])


def fake_liquidity(stored):
    """Window = 30 weekdays ending the day before as_of; members by symbol, dollar volume descending."""
    as_of = stored.board.as_of
    window = weekdays((date.fromisoformat(as_of) - timedelta(days=1)).isoformat(), 30)
    symbols = sorted(stored.meta.members)
    return LiquidityResult(as_of, tuple(window), {s: 1e9 - i * 1e6 for i, s in enumerate(symbols)},
                           {s: i + 1 for i, s in enumerate(symbols)})


@pytest.fixture()
def pool_store(tmp_path):
    # count=20: 18/20 ranked = 0.90 clears the M10 coverage floor; count=12 gives 10/12 and is rejected (review P2-3)
    db = tmp_path / 'pool.db'
    with ProsperityStore(db, read_only=False) as s:
        s.save_batch(storage_batch(count=20))
        s.save_batch(storage_batch(as_of='2026-10-03', count=20))
    with ProsperityStore(db) as s:
        yield s


def boards(s):
    old, new = (s.get_board(k) for k in s.list_boards(scheme_hash=F1, code_version='test-v1', board_type='weekly'))
    return old, new


def pool_for(stored):
    return build_pool(stored, fake_liquidity(stored), generated_at=stored.board.as_of + 'T09:00:00Z')


def test_pool_marks_only_when_bound(pool_store):
    old, stored = boards(pool_store)
    pool = pool_for(stored)
    assert len(pool['members']) == 4                                    # first tier = top 4 of 18 ranked
    ranks, note = pool_binding(pool, stored)
    assert ranks == {m['symbol']: m['dollar_volume_rank'] for m in pool['members']} and note.startswith('入池')
    # a pool that is valid on its own but belongs to another week
    assert pool_binding(pool_for(old), stored) == ({}, '未标入池：池文件与本榜不符（as_of）')
    bad = copy.deepcopy(pool); bad['source']['content_hash'] = '0' * 64
    assert pool_binding(bad, stored) == ({}, '未标入池：池文件与本榜不符（content_hash）')
    assert pool_binding(None, stored) == ({}, '未标入池：未提供池文件')
    assert pool_binding({'schema_version': 9}, stored)[1].startswith('未标入池：池文件校验失败')
    # only as_of edited: the trading window no longer fits, so the validator rejects it first
    bad = copy.deepcopy(pool); bad['as_of'] = '2026-09-26'
    assert pool_binding(bad, stored)[1].startswith('未标入池：池文件校验失败')


def test_pool_binding_rejects_a_board_that_is_not_the_f1_live_weekly(pool_store):
    from dataclasses import replace
    _, stored = boards(pool_store)
    pool = pool_for(stored)
    replay = replace(stored, meta=replace(stored.meta, mode='replay'))
    assert pool_binding(pool, replay) == ({}, '未标入池：池文件与本榜不符（board）')


def test_pool_members_must_match_the_board(pool_store):
    _, stored = boards(pool_store)
    pool = pool_for(stored)
    rows = {r.symbol: r for r in stored.board.rows}
    outsider = next(s for s, r in sorted(rows.items())
                    if r.symbol not in {m['symbol'] for m in pool['members']}
                    and (r.status != 'ranked' or r.grade == 'BELOW'))
    bad = copy.deepcopy(pool); bad['members'][0]['symbol'] = outsider   # source hash untouched
    validate_prosperity_pool(bad, check_freshness=False)                # the M10 validator alone lets it through
    assert pool_binding(bad, stored) == ({}, f'未标入池：池成员与本榜不符（{outsider}）')
    bad = copy.deepcopy(pool); m = bad['members'][0]
    m['grade'] = 'FULL' if m['grade'] == 'STRICT' else 'STRICT'
    assert pool_binding(bad, stored) == ({}, f"未标入池：池成员与本榜不符（{m['symbol']}）")


def test_pool_counts_must_match_the_board(pool_store):
    # §15: the file alone cannot prove its ranked count; board_mismatch recomputes it from the board
    _, stored = boards(pool_store)
    pool = pool_for(stored)
    bad = copy.deepcopy(pool); stat = bad['coverage']['ranked']
    stat.update(covered=stat['total'], ratio=1.0)        # 18 → 20; ceil(20/5) is still 4, so the file stays consistent
    validate_prosperity_pool(bad, check_freshness=False)
    assert pool_binding(bad, stored) == ({}, '未标入池：池文件与本榜不符（ranked）')


def test_pool_note_only_on_f1_latest_weekly(pool_store):
    _, stored = boards(pool_store)
    pool = pool_for(stored)
    pages = load_pages(pool_store, code_version='test-v1', pool=pool)
    noted = [p for p in pages if p.pool_note]
    assert [(p.scheme_id, p.board_type) for p in noted] == [('F1', 'weekly')]
    assert noted[0].pool_note.startswith('入池')
    marked = {r.symbol: r.pool_rank for r in noted[0].rows if r.pool_rank is not None}
    assert marked == {m['symbol']: m['dollar_volume_rank'] for m in pool['members']}
    assert all(r.pool_rank is None for p in pages[1:] for r in p.rows)


def test_pool_error_is_written_on_the_f1_board(pool_store):
    pages = load_pages(pool_store, code_version='test-v1', pool_error='池文件无法读取')
    assert pages[0].pool_note == '未标入池：池文件无法读取'
    assert all(r.pool_rank is None for r in pages[0].rows)


# ---- Task 2: HTML renderer ----------------------------------------------------------
import re
from dataclasses import replace
from html.parser import HTMLParser

from src.data.prosperity_web import BADGE_TEXT, REASON_TEXT, render_page


@pytest.fixture()
def pages(store):
    return load_pages(store, code_version='test-v1')


@pytest.fixture()
def pages_with_pool(pool_store):
    _, stored = boards(pool_store)
    return load_pages(pool_store, code_version='test-v1', pool=pool_for(stored))


class BodyRows(HTMLParser):
    """Body rows of the board tables. </td> and </tr> are omitted in the page, so a new <td>/<tr> starts a cell/row."""
    def __init__(self):
        super().__init__()
        self.rows, self.body = [], False

    def handle_starttag(self, tag, attrs):
        if tag == 'tbody':
            self.body = True
        elif self.body and tag == 'tr':
            self.rows.append({'class': dict(attrs).get('class'), 'cells': [], 'badges': []})
        elif self.body and tag == 'td':
            self.rows[-1]['cells'].append('')
        elif self.body and tag in ('i', 'b'):
            self.rows[-1]['badges'].append([tag, ''])

    def handle_endtag(self, tag):
        if tag == 'tbody':
            self.body = False

    def handle_data(self, data):
        if self.body and self.rows and self.rows[-1]['cells']:
            self.rows[-1]['cells'][-1] += data
            if self.rows[-1]['badges'] and len(self.rows[-1]['cells']) == 11:
                self.rows[-1]['badges'][-1][1] += data


def body_rows(html):
    parser = BodyRows()
    parser.feed(html)
    parser.close()
    for r in parser.rows:                    # the newline after a row lands in its last cell; browsers collapse it
        r['cells'] = [c.strip() for c in r['cells']]
        r['badges'] = [[t, text.strip()] for t, text in r['badges']]
    return parser.rows


def row_of(html, symbol):
    return next(r for r in body_rows(html) if r['cells'][1] == symbol)


def test_page_is_static_and_self_contained(pages):
    html = render_page(pages, generated_at='2026-11-02T00:00:00Z', source={'db_sha256': 'a' * 64, 'code_version': 'test-v1'})
    assert '<script' not in html.lower()
    assert not re.search(r'(src|href)\s*=\s*["\']?(https?:)?//', html)
    ids = re.findall(r'<section id="([^"]+)"', html)
    assert ids == [section_id(p) for p in pages]
    targets = re.findall(r'href="#([^"]+)"', html)
    assert set(ids) <= set(targets)                                 # every board reachable from the page
    for target in targets:
        assert target in ids or target == 'toc'
    assert 'id="toc"' in html and 'a' * 16 in html and 'test-v1' in html


def test_strict_full_open_others_folded(pages):
    html = render_page(pages, generated_at='x', source={})
    first = html.split('<section id=')[1]
    main, folded = first.split('<details>', 1)
    grades = [r['cells'][4] for r in body_rows(main)]
    assert grades and all(g in ('STRICT', 'FULL') for g in grades)
    assert '其余' in folded and REASON_TEXT['insufficient_quarters'] in folded


def test_numbers_and_missing_families(pages):
    html = render_page(pages, generated_at='x', source={})
    f0 = next(p for p in pages if p.scheme_id == 'F0')
    r = next(r for r in f0.rows if r.status == 'ranked')
    section = html.split(f'<section id="{section_id(f0)}"')[1].split('</section>')[0]
    from src.data.prosperity_web import _score
    cells = row_of(section, r.symbol)['cells']
    assert cells[5] == _score(r.score) and cells[6] == f"{r.family_scores['growth']:.0f}"
    assert cells[9] == '—'                                          # F0 has no expectation family


def test_badges_in_chinese_unknown_codes_kept(pages):
    row = replace(pages[0].rows[0], badges=('pe_redflag', 'brand_new_code'))
    page = replace(pages[0], rows=(row,) + pages[0].rows[1:])
    badges = row_of(render_page([page], generated_at='x', source={}), row.symbol)['badges']
    assert badges == [['b', BADGE_TEXT['pe_redflag']], ['i', 'brand_new_code']]   # <b> = red flag


def test_text_is_escaped(pages):
    row = replace(pages[0].rows[0], name='A<b>&Co')
    html = render_page([replace(pages[0], rows=(row,))], generated_at='x', source={})
    assert 'A&lt;b&gt;&amp;Co' in html and 'A<b>&Co' not in html
    assert row_of(html, row.symbol)['cells'][2] == 'A<b>&Co'


def test_pool_columns_only_on_noted_board(pages_with_pool):
    html = render_page(pages_with_pool, generated_at='x', source={})
    sections = html.split('<section id=')[1:]
    assert '成交额排名' in sections[0] and sections[0].count('✓') == 4
    assert all('成交额排名' not in s and '✓' not in s for s in sections[1:])
    marked = {r.symbol: r.pool_rank for r in pages_with_pool[0].rows if r.pool_rank}
    for symbol, rank in marked.items():
        r = row_of(sections[0], symbol)
        assert r['cells'][-2:] == ['✓', str(rank)] and r['class'] == 'p'
    unmarked = [r for r in body_rows(sections[0]) if r['cells'][1] not in marked]
    assert unmarked and all(r['cells'][-2:] == ['', ''] and r['class'] is None for r in unmarked)


def test_toc_marks_a_scheme_without_boards(pages):
    html = render_page([p for p in pages if p.scheme_id == 'F1'], generated_at='x', source={})
    toc = html.split('id="toc"')[1].split('</nav>')[0]
    assert toc.count('无榜') == 3 * 3                               # three schemes × three rows


def test_dark_mode_tokens_present(pages):
    html = render_page(pages, generated_at='x', source={})
    assert 'prefers-color-scheme: dark' in html and ':root[data-theme="dark"]' in html


# ---- review fixes (2026-11-03) -------------------------------------------------------
def test_latest_weekly_is_the_live_board_not_a_replay(tmp_path):
    # a rebuilt chain writes replay weekly boards; before the first live Saturday none of them is "latest"
    from dataclasses import replace as dc_replace
    db = tmp_path / 'replay.db'
    with ProsperityStore(db, read_only=False) as s:
        live = storage_batch(freeze=True, quarter_label='2026Q2')
        s.save_batch(live)
        replay = tuple(dc_replace(b, meta=dc_replace(b.meta, mode='replay', source_evidence={
            **b.meta.source_evidence, 'observed_at': None}))
            for b in storage_batch(as_of='2026-10-03', freeze=True, quarter_label='2026Q3'))
        s.save_batch(replay)
    with ProsperityStore(db) as s:
        pages = load_pages(s, code_version='test-v1')
    weekly = [p for p in pages if p.board_type == 'weekly']
    assert [(p.as_of, p.mode) for p in weekly] == [('2026-09-26', 'live')] * 4


def test_displayed_score_never_crosses_a_grade_line():
    from src.data.prosperity_web import _score
    assert _score(64.9897) == '64.9' and _score(49.9999) == '49.9' and _score(65.0) == '65.0'
    assert _score(97.45) == '97.4' and _score(-0.05) == '-0.1' and _score(None) == '—'
