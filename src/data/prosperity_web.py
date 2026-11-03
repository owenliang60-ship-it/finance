"""M11 景气网页: prosperity.db boards → one static HTML page (latest weekly + every quarter-end board).

Read-only and not in code_version: the page only displays stored rows, it never changes a score.
Plan: docs/plans/2026-11-02-prosperity-m11-web.md. The cloud delivery (M13) calls load_pages and
render_page directly.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import List, Mapping, Optional, Tuple

from src.data.prosperity_pool import (LIQUIDITY_TOP, POOL_GRADES, POOL_SCHEME_ID, board_mismatch,
                                      criteria_payload, validate_prosperity_pool)
from src.data.prosperity_storage import content_hash
from terminal.prosperity.schemes import SCHEMES, scheme_hash

WEB_SCHEMES = ('F1', 'F1-rank', 'F1-exfin', 'F0')   # table-of-contents column order, same as M12 DEFAULT_SCHEMES
FAMILIES = (('growth', '成长'), ('accel', '加速'), ('quality', '质量'), ('expectation', '预期'))
SHOWN_LABELS = ('eps_turnaround', 'eps_ttm_turnaround', 'semiannual_period')
BOARD_BADGES = ('params_bootstrap',)                # the same on every row: shown once in the board header
BADGE_TEXT = {
    'pe_redflag': 'PE红旗', 'ntm_loss': '预期亏损', 'ntm_gate_unknown': 'NTM待验',
    'forward_stale': '预期过期', 'gm_slope_missing': '无毛利斜率', 'industry_unknown': '行业未知',
    'net_margin_down': '降级·净利率降', 'new_listing_gate': '降级·新上市', 'ntm_not_above_ttm': '降级·NTM≤TTM',
    'eps_turnaround': '扭亏', 'eps_ttm_turnaround': 'TTM扭亏', 'semiannual_period': '半年报',
    'approximate': '近似PIT',
}
REASON_TEXT = {
    'identity_unverified': '身份未核实', 'low_coverage': '因子覆盖不足', 'insufficient_quarters': '季数不足',
    'no_scorable_factor': '无可打分因子', 'universe_excluded': '方案排除',
}
RED_BADGES = {'pe_redflag', 'ntm_loss', 'net_margin_down', 'new_listing_gate', 'ntm_not_above_ttm'}
_STATUS_ORDER = {'ranked': 0, 'observe': 1, 'excluded': 2}


@dataclass(frozen=True)
class WebRow:
    symbol: str
    name: Optional[str]
    sector: Optional[str]
    status: str                        # ranked / observe / excluded
    rank: Optional[int]
    grade: Optional[str]               # STRICT / FULL / BELOW; None unless ranked
    score: Optional[float]
    observe_reason: Optional[str]
    family_scores: Mapping[str, Optional[float]]
    badges: Tuple[str, ...]            # kernel badges (minus BOARD_BADGES) → demotions → SHOWN_LABELS → 'approximate'
    pool_rank: Optional[int]           # dollar-volume rank when the row is in the bound pool


@dataclass(frozen=True)
class BoardPage:
    scheme_id: str
    scheme_hash: str
    code_version: str
    board_type: str                    # weekly / quarter_final
    mode: str                          # live / replay
    as_of: str
    quarter_label: Optional[str]
    frozen_at: Optional[str]
    params_bootstrap: bool
    counts: Mapping[str, int]          # ranked / observe / excluded / STRICT / FULL / BELOW
    rows: Tuple[WebRow, ...]           # ranked by rank; then observe, then excluded, each by symbol
    pool_note: Optional[str]           # set only on the F1 latest weekly board


def section_id(page: BoardPage) -> str:
    return f'{page.scheme_id}-{"w" if page.board_type == "weekly" else "q"}-{page.as_of}'


def _badges(row, labels, pit_basis) -> Tuple[str, ...]:
    codes = [b for b in row.badges if b not in BOARD_BADGES]
    codes += row.demotions
    codes += [label for label in SHOWN_LABELS if label in labels]
    if pit_basis == 'approximate':
        codes.append('approximate')
    return tuple(dict.fromkeys(codes))


def board_page(stored, *, pool_ranks: Optional[Mapping[str, int]] = None,
               pool_note: Optional[str] = None) -> BoardPage:
    board, meta = stored.board, stored.meta
    pool_ranks = pool_ranks or {}
    stocks = {p['symbol']: p for p in stored.stocks}
    labels = {f.symbol: set(f.labels) for f in stored.factors}
    ordered = sorted(board.rows, key=lambda r: (_STATUS_ORDER[r.status], r.rank or 0, r.symbol))
    rows = tuple(WebRow(symbol=r.symbol, name=stocks[r.symbol].get('name'), sector=stocks[r.symbol].get('sector'),
                        status=r.status, rank=r.rank, grade=r.grade, score=r.score,
                        observe_reason=r.observe_reason, family_scores=dict(r.family_scores),
                        badges=_badges(r, labels.get(r.symbol, set()), stocks[r.symbol].get('pit_basis')),
                        pool_rank=pool_ranks.get(r.symbol))
                 for r in ordered)
    counts = {k: sum(r.status == k for r in rows) for k in _STATUS_ORDER}
    counts.update({g: sum(r.status == 'ranked' and r.grade == g for r in rows) for g in ('STRICT', 'FULL', 'BELOW')})
    return BoardPage(scheme_id=board.scheme_id, scheme_hash=board.scheme_hash, code_version=board.code_version,
                     board_type=meta.board_type, mode=meta.mode, as_of=board.as_of,
                     quarter_label=meta.quarter_label, frozen_at=meta.frozen_at,
                     params_bootstrap=bool(board.params_bootstrap), counts=counts, rows=rows, pool_note=pool_note)


def pool_binding(pool: Optional[dict], stored) -> Tuple[Mapping[str, int], str]:
    """(symbol → dollar-volume rank, header note). Marks only a pool bound to this exact board.

    Board level: F1 live weekly, as_of, criteria, code_version, content_hash. Counts: board_mismatch
    recomputes ranked / tier cut / eligible from the board (plan §15). Members: content_hash does not
    cover them, so each one must be a ranked STRICT/FULL row with the same rank and grade (review P2-2).
    """
    if pool is None:
        return {}, '未标入池：未提供池文件'
    try:
        validate_prosperity_pool(pool, check_freshness=False)   # a local snapshot may be older than 8 days
    except (ValueError, RuntimeError, TypeError, KeyError, OverflowError) as exc:
        return {}, f'未标入池：池文件校验失败（{type(exc).__name__}）'
    board, meta = stored.board, stored.meta
    checks = (
        ('board', board.scheme_id == POOL_SCHEME_ID and board.scheme_hash == scheme_hash(SCHEMES[POOL_SCHEME_ID])
         and meta.board_type == 'weekly' and meta.mode == 'live'),
        ('as_of', pool['as_of'] == board.as_of),
        ('criteria', pool['criteria'] == criteria_payload()),
        ('code_version', pool['source']['code_version'] == board.code_version),
        ('content_hash', pool['source']['content_hash'] == content_hash(stored)),
    )
    field = next((name for name, ok in checks if not ok), None) or board_mismatch(pool, stored)
    if field is not None:
        return {}, f'未标入池：池文件与本榜不符（{field}）'
    rows = {r.symbol: r for r in board.rows}
    for m in pool['members']:
        r = rows.get(m['symbol'])
        if (r is None or r.status != 'ranked' or r.grade not in POOL_GRADES
                or m['rank'] != r.rank or m['grade'] != r.grade):
            return {}, f'未标入池：池成员与本榜不符（{m["symbol"]}）'
    members = pool['members']
    return ({m['symbol']: m['dollar_volume_rank'] for m in members},
            f'入池 {len(members)} 只（第一档 ∩ STRICT/FULL ∩ 成交额前 {LIQUIDITY_TOP}，as_of {board.as_of}）')


def load_pages(store, *, code_version: str, pool: Optional[dict] = None,
               pool_error: Optional[str] = None) -> List[BoardPage]:
    """Per scheme: the latest weekly board, then every quarter-end board, newest first.

    Boards are read one at a time and dropped once converted. A scheme with no board is skipped.
    """
    pages = []
    for sid in WEB_SCHEMES:
        h = scheme_hash(SCHEMES[sid])
        weekly = store.list_boards(scheme_hash=h, code_version=code_version, board_type='weekly')
        finals = store.list_boards(scheme_hash=h, code_version=code_version, board_type='quarter_final')
        for key in weekly[-1:] + finals[::-1]:
            stored = store.get_board(key)
            if sid == POOL_SCHEME_ID and key.board_type == 'weekly':
                ranks, note = ({}, '未标入池：' + pool_error) if pool_error else pool_binding(pool, stored)
                pages.append(board_page(stored, pool_ranks=ranks, pool_note=note))
            else:
                pages.append(board_page(stored))
    return pages
