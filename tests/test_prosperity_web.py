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
