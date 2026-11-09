"""M11 景气网页: prosperity.db boards → one static HTML page (latest weekly + every quarter-end board).

Read-only and not in code_version: the page only displays stored rows, it never changes a score.
Plan: docs/plans/2026-11-02-prosperity-m11-web.md. The cloud delivery (M13) calls load_pages and
render_page directly.
"""
from __future__ import annotations

from dataclasses import dataclass
from html import escape
import math
from typing import List, Mapping, Optional, Sequence, Tuple

from src.data.prosperity_pool import (LIQUIDITY_TOP, POOL_GRADES, POOL_SCHEME_ID, board_mismatch,
                                      criteria_payload, validate_prosperity_pool)
from src.data.prosperity_storage import BoardKey, content_hash
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
FRESHNESS_NOTE = '✓ 仅表示该期池文件与本榜匹配，未验证当前新鲜度。'


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
                                       # ('approximate' only on a mixed board; see BoardPage.all_approximate)
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
    all_approximate: bool = False      # every row is approximate PIT: said once in the header (Boss 2026-11-03)


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
    # A replay board is approximate on every row: one header line instead of 70k badges (Boss 2026-11-03).
    # A mixed board keeps the per-row badge, so a single approximate row is never hidden.
    everyone = bool(ordered) and all(stocks[r.symbol].get('pit_basis') == 'approximate' for r in ordered)
    rows = tuple(WebRow(symbol=r.symbol, name=stocks[r.symbol].get('name'), sector=stocks[r.symbol].get('sector'),
                        status=r.status, rank=r.rank, grade=r.grade, score=r.score,
                        observe_reason=r.observe_reason, family_scores=dict(r.family_scores),
                        badges=_badges(r, labels.get(r.symbol, set()),
                                       None if everyone else stocks[r.symbol].get('pit_basis')),
                        pool_rank=pool_ranks.get(r.symbol))
                 for r in ordered)
    counts = {k: sum(r.status == k for r in rows) for k in _STATUS_ORDER}
    counts.update({g: sum(r.status == 'ranked' and r.grade == g for r in rows) for g in ('STRICT', 'FULL', 'BELOW')})
    return BoardPage(scheme_id=board.scheme_id, scheme_hash=board.scheme_hash, code_version=board.code_version,
                     board_type=meta.board_type, mode=meta.mode, as_of=board.as_of,
                     quarter_label=meta.quarter_label, frozen_at=meta.frozen_at,
                     params_bootstrap=bool(board.params_bootstrap), counts=counts, rows=rows, pool_note=pool_note,
                     all_approximate=everyone)


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
    """Per scheme: the latest live weekly board, then every quarter-end board, newest first.

    A rebuilt chain also stores replay weekly boards; none of them is "latest" (review 2026-11-03),
    so before the first live Saturday a scheme has no latest weekly section. Boards are read one at
    a time and dropped once converted. A scheme with no board is skipped.
    """
    pages = []
    for sid in WEB_SCHEMES:
        h = scheme_hash(SCHEMES[sid])
        # list_boards has no mode filter; same header-only query as scripts/prosperity_health.py
        live = store._conn.execute("SELECT MAX(as_of) FROM board WHERE board_type='weekly' AND mode='live' "
                                   'AND scheme_hash=? AND code_version=?', (h, code_version)).fetchone()[0]
        weekly = [BoardKey('weekly', live, h, code_version)] if live else []
        finals = store.list_boards(scheme_hash=h, code_version=code_version, board_type='quarter_final')
        for key in weekly + list(finals[::-1]):
            stored = store.get_board(key)
            if sid == POOL_SCHEME_ID and key.board_type == 'weekly':
                ranks, note = ({}, '未标入池：' + pool_error) if pool_error else pool_binding(pool, stored)
                pages.append(board_page(stored, pool_ranks=ranks, pool_note=note))
            else:
                pages.append(board_page(stored))
    return pages


# ---- HTML ---------------------------------------------------------------------------
NAME_CHARS = 28
LATEST = '最新周榜'
_TYPE_TEXT = {'weekly': '周榜', 'quarter_final': '季末冻结榜'}
_MODE_TEXT = {'live': '实盘', 'replay': '回放'}

_CSS = """
:root{--bg:#fbfaf7;--fg:#1d1d1b;--muted:#6b6860;--line:#e4e1d8;--head:#f1eee6;--card:#ffffff;
--accent:#2f5d8a;--red:#b3261e;--redbg:#fbe9e7;--badge:#ecebe6;--pool:#e6f0e6;color-scheme:light}
@media (prefers-color-scheme: dark){:root:not([data-theme="light"]){--bg:#161615;--fg:#e8e6e1;--muted:#a19d94;
--line:#34332f;--head:#22211f;--card:#1c1c1a;--accent:#8db4dc;--red:#f2b8b5;--redbg:#3b1f1d;--badge:#2b2a27;
--pool:#1f3324;color-scheme:dark}}
:root[data-theme="dark"]{--bg:#161615;--fg:#e8e6e1;--muted:#a19d94;--line:#34332f;--head:#22211f;--card:#1c1c1a;
--accent:#8db4dc;--red:#f2b8b5;--redbg:#3b1f1d;--badge:#2b2a27;--pool:#1f3324;color-scheme:dark}
*{box-sizing:border-box}
body{margin:0;padding:16px;background:var(--bg);color:var(--fg);
font:14px/1.5 -apple-system,BlinkMacSystemFont,"PingFang SC","Helvetica Neue",Arial,sans-serif}
main{max-width:1280px;margin:0 auto}
h1{font-size:22px;margin:0 0 4px}h2{font-size:17px;margin:0 0 4px}
a{color:var(--accent)}.m{color:var(--muted);font-size:13px;margin:2px 0}
.w{overflow-x:auto;margin:8px 0;border:1px solid var(--line);border-radius:6px;background:var(--card)}
table{border-collapse:collapse;width:100%;font-variant-numeric:tabular-nums}
th,td{padding:4px 8px;border-bottom:1px solid var(--line);text-align:left;white-space:nowrap}
th{background:var(--head);font-weight:600;position:sticky;top:0}
section td:nth-child(1),section td:nth-child(n+6):nth-child(-n+10),section td:nth-child(13){text-align:right}
tr.p{background:var(--pool)}
aside{margin:16px 0 0}aside td:nth-child(1),aside td:nth-child(n+6){text-align:right}
section{content-visibility:auto;contain-intrinsic-size:auto 1500px;margin:28px 0 0;padding-top:12px;
border-top:2px solid var(--line)}
td i,td b{display:inline-block;padding:0 6px;margin:1px 2px 1px 0;border-radius:4px;background:var(--badge);
font-size:12px;font-style:normal;font-weight:normal}
td b{background:var(--redbg);color:var(--red)}
.note{font-weight:600}details{margin:8px 0}summary{cursor:pointer;color:var(--accent)}
nav .w td,nav .w th{text-align:center}
"""


def _num(value, digits):
    return '—' if value is None else f'{value:.{digits}f}'


def _score(value) -> str:
    """Rounded down to 0.1, so a shown score never crosses a grade line: 64.99 shows 64.9, not 65.0 (review)."""
    return '—' if value is None else f'{math.floor(value * 10) / 10:.1f}'


def _row_label(page: BoardPage) -> str:
    return LATEST if page.board_type == 'weekly' else (page.quarter_label or page.as_of)


def _badge(code: str) -> str:
    tag = 'b' if code in RED_BADGES else 'i'          # short tags: 72k rows × ~2 badges (size budget A2)
    return f'<{tag}>{escape(BADGE_TEXT.get(code, code))}</{tag}>'


def _name_cell(r: WebRow) -> str:
    name = r.name or '—'
    short = name if len(name) <= NAME_CHARS else name[:NAME_CHARS] + '…'
    return f'<td title="{escape(name)}">{escape(short)}' if short != name else f'<td>{escape(short)}'


def _cells(r: WebRow, *, pooled: bool) -> str:
    """One row on one line. </td> and </tr> are optional in HTML5 and omitted (A2 size budget);
    alignment comes from column position in the CSS, not per-cell classes."""
    name_cell = _name_cell(r)
    grade = r.grade if r.status == 'ranked' else REASON_TEXT.get(r.observe_reason or '', r.observe_reason or '—')
    parts = [f'<td>{"—" if r.rank is None else r.rank}', f'<td>{escape(r.symbol)}', name_cell,
             f'<td>{escape(r.sector or "—")}', f'<td>{escape(grade)}', f'<td>{_score(r.score)}']
    parts += [f'<td>{_num(r.family_scores.get(k), 0)}' for k, _ in FAMILIES]
    parts.append('<td>' + ''.join(_badge(b) for b in r.badges))
    if pooled:
        marked = r.pool_rank is not None
        parts += [f'<td>{"✓" if marked else ""}', f'<td>{r.pool_rank if marked else ""}']
    cls = ' class="p"' if pooled and r.pool_rank is not None else ''
    return f'<tr{cls}>' + ''.join(parts)


def _table(rows: Sequence[WebRow], *, pooled: bool) -> str:
    heads = ['排名', '代码', '名称', '行业', '评级', '分数', *(label for _, label in FAMILIES), '徽章']
    if pooled:
        heads += ['入池', '成交额排名']
    head = ''.join(f'<th>{h}</th>' for h in heads)
    body = '\n'.join(_cells(r, pooled=pooled) for r in rows)
    return f'<div class="w"><table><thead><tr>{head}</tr></thead><tbody>\n{body}\n</tbody></table></div>'


def _section(page: BoardPage, peers: Sequence[BoardPage]) -> str:
    c = page.counts
    pooled = any(r.pool_rank is not None for r in page.rows)    # pool_binding marks rows only when bound
    title = f'{escape(page.scheme_id)} · {_TYPE_TEXT.get(page.board_type, page.board_type)} · {page.as_of}'
    meta = [f'季度 {escape(page.quarter_label)}' if page.quarter_label else None,
            f'冻结于 {escape(page.frozen_at)}' if page.frozen_at else None,
            _MODE_TEXT.get(page.mode, page.mode),
            f'可排名 {c["ranked"]}（STRICT {c["STRICT"]} / FULL {c["FULL"]} / BELOW {c["BELOW"]}）',
            f'观察 {c["observe"]}', f'排除 {c["excluded"]}',
            '参数自举' if page.params_bootstrap else None,
            '本榜全部为近似 PIT' if page.all_approximate else None]
    links = ' · '.join(f'<a href="#{section_id(p)}">{escape(p.scheme_id)}</a>' for p in peers)
    out = [f'<section id="{section_id(page)}">', f'<h2>{title}</h2>',
           '<p class="m">' + ' · '.join(m for m in meta if m) + '</p>']
    if page.pool_note:
        out.append(f'<p class="note">{escape(page.pool_note)}</p>')
    if pooled:
        out.append(f'<p class="m">{FRESHNESS_NOTE}</p>')
    out.append(f'<p class="m">同期其他方案：{links or "无"} · <a href="#toc">回目录</a></p>')
    top, rest = [], []
    for r in page.rows:
        (top if r.status == 'ranked' and r.grade in ('STRICT', 'FULL') else rest).append(r)
    out.append(_table(top, pooled=pooled) if top else '<p class="m">本榜无 STRICT/FULL 行。</p>')
    if rest:
        out.append(f'<details><summary>其余 {len(rest)} 行（BELOW {c["BELOW"]} / 观察 {c["observe"]} / '
                   f'排除 {c["excluded"]}）</summary>{_table(rest, pooled=pooled)}</details>')
    out.append('</section>')
    return '\n'.join(out)


def _pool_list(page: Optional[BoardPage]) -> str:
    """The bound pool above the table of contents, in rank order (Boss 2026-11-09). Rows come from the
    F1 latest weekly page, so the list is marked exactly when that board is; otherwise only its note."""
    if page is None:
        return ''
    members = [r for r in page.rows if r.pool_rank is not None]
    out = ['<aside id="pool">', f'<h2>精选池 · {page.as_of}</h2>',
           f'<p class="note">{escape(page.pool_note or "")}</p>']
    if members:
        out.append(f'<p class="m">按景气排名 · 完整周榜见 <a href="#{section_id(page)}">F1 最新周榜</a></p>')
        head = ''.join(f'<th>{h}</th>' for h in ('排名', '代码', '名称', '行业', '评级', '分数', '成交额排名'))
        body = '\n'.join(f'<tr><td>{r.rank}<td>{escape(r.symbol)}{_name_cell(r)}<td>{escape(r.sector or "—")}'
                         f'<td>{escape(r.grade or "—")}<td>{_score(r.score)}<td>{r.pool_rank}' for r in members)
        out.append(f'<div class="w"><table><thead><tr>{head}</tr></thead><tbody>\n{body}\n</tbody></table></div>')
    out.append('</aside>')
    return '\n'.join(out) + '\n'


def render_page(pages: Sequence[BoardPage], *, generated_at: str, source: Mapping[str, str],
                pool_error: Optional[str] = None) -> str:
    """One self-contained static page: no script, no external resource, anchors only.

    Without an F1 live weekly board the pool check never ran; the page header says so (Codex P2),
    together with any pool-file error, instead of leaving only a 无榜 cell in the table of contents.
    """
    by_cell = {(_row_label(p), p.scheme_id): p for p in pages}
    labels = [LATEST] + sorted({_row_label(p) for p in pages} - {LATEST}, reverse=True)
    hashes = {p.scheme_id: p.scheme_hash for p in reversed(pages)}
    db = source.get('db_sha256') or ''
    pool = source.get('pool_sha256') or ''
    strict, full = SCHEMES[POOL_SCHEME_ID].grade_strict, SCHEMES[POOL_SCHEME_ID].grade_full
    head = ''.join(f'<th>{escape(s)}</th>' for s in WEB_SCHEMES)
    toc_rows = []
    for label in labels:
        cells = []
        for sid in WEB_SCHEMES:
            p = by_cell.get((label, sid))
            cells.append(f'<td><a href="#{section_id(p)}">{p.as_of}</a></td>' if p else '<td class="m">无榜</td>')
        toc_rows.append(f'<tr><th>{escape(label)}</th>{"".join(cells)}</tr>')
    scheme_line = ' · '.join(f'{escape(s)} {escape(hashes.get(s, "无榜"))}' for s in WEB_SCHEMES)
    pool_board = next((p for p in pages if p.scheme_id == POOL_SCHEME_ID and p.board_type == 'weekly'), None)
    no_pool_board = '' if pool_board else (
        '<p class="note">未标入池：库中无 F1 live 周榜' + (f'（池文件：{escape(pool_error)}）' if pool_error else '')
        + '</p>\n')
    sections = '\n'.join(_section(p, [by_cell[(_row_label(p), s)] for s in WEB_SCHEMES
                                      if s != p.scheme_id and (_row_label(p), s) in by_cell]) for p in pages)
    return f"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>景气榜单</title><style>{_CSS}</style></head>
<body><main>
<h1>景气榜单</h1>
<p class="m">生成于 {escape(generated_at)} · 库 SHA256 {escape(db[:16] or '—')} · code_version {escape(source.get('code_version') or '—')} · 池文件 {escape(pool[:16] or '无')}</p>
<p class="m">方案 hash：{scheme_line}</p>
<p class="m">评级：分数 ≥ {strict:g} 为 STRICT，≥ {full:g} 为 FULL，其余 BELOW；任一闸门不过降为 BELOW。分数 = 0–100 基础分 + 毛利斜率加减分，向下取一位小数。每张榜只展开 STRICT/FULL，BELOW、观察、排除行收在折叠块里。本页无脚本，用目录链接切换方案和期数。</p>
{no_pool_board}{_pool_list(pool_board)}<nav id="toc"><h2>目录</h2><div class="w"><table><thead><tr><th>期数</th>{head}</tr></thead><tbody>
{chr(10).join(toc_rows)}
</tbody></table></div></nav>
{sections}
</main></body></html>
"""
