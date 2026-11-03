"""M11 景气网页: prosperity.db boards → one static HTML page (latest weekly + every quarter-end board).

Read-only and not in code_version: the page only displays stored rows, it never changes a score.
Plan: docs/plans/2026-11-02-prosperity-m11-web.md. The cloud delivery (M13) calls load_pages and
render_page directly.
"""
from __future__ import annotations

from dataclasses import dataclass
from html import escape
from typing import List, Mapping, Optional, Sequence, Tuple

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
td.n{text-align:right}tr.p{background:var(--pool)}
section{content-visibility:auto;contain-intrinsic-size:auto 1500px;margin:28px 0 0;padding-top:12px;
border-top:2px solid var(--line)}
.b{display:inline-block;padding:0 6px;margin:1px 2px 1px 0;border-radius:4px;background:var(--badge);font-size:12px}
.b.r{background:var(--redbg);color:var(--red)}
.note{font-weight:600}details{margin:8px 0}summary{cursor:pointer;color:var(--accent)}
nav .w td,nav .w th{text-align:center}
"""


def _num(value, digits):
    return '—' if value is None else f'{value:.{digits}f}'


def _row_label(page: BoardPage) -> str:
    return LATEST if page.board_type == 'weekly' else (page.quarter_label or page.as_of)


def _badge(code: str) -> str:
    cls = 'b r' if code in RED_BADGES else 'b'
    return f'<span class="{cls}">{escape(BADGE_TEXT.get(code, code))}</span>'


def _cells(r: WebRow, *, pooled: bool) -> str:
    name = r.name or '—'
    short = name if len(name) <= NAME_CHARS else name[:NAME_CHARS] + '…'
    name_cell = f'<td title="{escape(name)}">{escape(short)}</td>' if short != name else f'<td>{escape(short)}</td>'
    grade = r.grade if r.status == 'ranked' else REASON_TEXT.get(r.observe_reason or '', r.observe_reason or '—')
    parts = [f'<td class="n">{"—" if r.rank is None else r.rank}</td>', f'<td>{escape(r.symbol)}</td>', name_cell,
             f'<td>{escape(r.sector or "—")}</td>', f'<td class="g">{escape(grade)}</td>',
             f'<td class="n">{_num(r.score, 1)}</td>']
    parts += [f'<td class="n">{_num(r.family_scores.get(k), 0)}</td>' for k, _ in FAMILIES]
    parts.append('<td>' + ''.join(_badge(b) for b in r.badges) + '</td>')
    if pooled:
        marked = r.pool_rank is not None
        parts += [f'<td>{"✓" if marked else ""}</td>', f'<td class="n">{r.pool_rank if marked else ""}</td>']
    cls = ' class="p"' if pooled and r.pool_rank is not None else ''
    return f'<tr{cls}>' + ''.join(parts) + '</tr>'


def _table(rows: Sequence[WebRow], *, pooled: bool) -> str:
    heads = ['排名', '代码', '名称', '行业', '评级', '分数', *(label for _, label in FAMILIES), '徽章']
    if pooled:
        heads += ['入池', '成交额排名']
    head = ''.join(f'<th>{h}</th>' for h in heads)
    body = '\n'.join(_cells(r, pooled=pooled) for r in rows)
    return f'<div class="w"><table><thead><tr>{head}</tr></thead><tbody>\n{body}\n</tbody></table></div>'


def _section(page: BoardPage, peers: Sequence[BoardPage]) -> str:
    c = page.counts
    pooled = page.pool_note is not None and page.pool_note.startswith('入池')
    title = f'{escape(page.scheme_id)} · {_TYPE_TEXT.get(page.board_type, page.board_type)} · {page.as_of}'
    meta = [f'季度 {escape(page.quarter_label)}' if page.quarter_label else None,
            f'冻结于 {escape(page.frozen_at)}' if page.frozen_at else None,
            _MODE_TEXT.get(page.mode, page.mode),
            f'可排名 {c["ranked"]}（STRICT {c["STRICT"]} / FULL {c["FULL"]} / BELOW {c["BELOW"]}）',
            f'观察 {c["observe"]}', f'排除 {c["excluded"]}',
            '参数自举' if page.params_bootstrap else None]
    links = ' · '.join(f'<a href="#{section_id(p)}">{escape(p.scheme_id)}</a>' for p in peers)
    out = [f'<section id="{section_id(page)}">', f'<h2>{title}</h2>',
           '<p class="m">' + ' · '.join(m for m in meta if m) + '</p>']
    if page.pool_note:
        out.append(f'<p class="note">{escape(page.pool_note)}</p>')
    out.append(f'<p class="m">同期其他方案：{links or "无"} · <a href="#toc">回目录</a></p>')
    top = [r for r in page.rows if r.status == 'ranked' and r.grade in ('STRICT', 'FULL')]
    rest = [r for r in page.rows if r not in top]
    out.append(_table(top, pooled=pooled) if top else '<p class="m">本榜无 STRICT/FULL 行。</p>')
    if rest:
        out.append(f'<details><summary>其余 {len(rest)} 行（BELOW {c["BELOW"]} / 观察 {c["observe"]} / '
                   f'排除 {c["excluded"]}）</summary>{_table(rest, pooled=pooled)}</details>')
    out.append('</section>')
    return '\n'.join(out)


def render_page(pages: Sequence[BoardPage], *, generated_at: str, source: Mapping[str, str]) -> str:
    """One self-contained static page: no script, no external resource, anchors only."""
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
<p class="m">评级：分数 ≥ {strict:g} 为 STRICT，≥ {full:g} 为 FULL，其余 BELOW；任一闸门不过降为 BELOW。分数 = 0–100 基础分 + 毛利斜率加减分。每张榜只展开 STRICT/FULL，BELOW、观察、排除行收在折叠块里。本页无脚本，用目录链接切换方案和期数。</p>
<nav id="toc"><h2>目录</h2><div class="w"><table><thead><tr><th>期数</th>{head}</tr></thead><tbody>
{chr(10).join(toc_rows)}
</tbody></table></div></nav>
{sections}
</main></body></html>
"""
