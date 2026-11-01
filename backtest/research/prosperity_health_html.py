"""M12 health report HTML: one self-contained page per scheme, fixed chapters (north star §4.3).

Reads only the dict built by `prosperity_health.scheme_report` plus run inputs. No external
scripts, styles, fonts or images; charts are inline SVG.
"""
from __future__ import annotations

import html
import math
from typing import Any, Iterable, List, Mapping, Optional, Sequence

from backtest.research.prosperity_health import CHAPTERS, HORIZONS, TIERS

CSS = """
:root {
  --bg: #fdfbf7; --surface: #ffffff; --surface2: #f5f0e8; --border: #e8e0d0;
  --text: #2d2a26; --text-dim: #7a7265; --gold: #d4a012; --red: #dc3545; --green: #198754;
  --blue: #2563eb; --amber: #e8960c;
}
@media (prefers-color-scheme: dark) {
  :root:not([data-theme="light"]) {
    --bg: #1c1a17; --surface: #24211d; --surface2: #2c2823; --border: #3a352e;
    --text: #ece6dc; --text-dim: #a89f91; --gold: #e0b43a; --red: #f06b78; --green: #4cc38a;
    --blue: #7aa2ff; --amber: #f0a83a;
  }
}
:root[data-theme="dark"] {
  --bg: #1c1a17; --surface: #24211d; --surface2: #2c2823; --border: #3a352e;
  --text: #ece6dc; --text-dim: #a89f91; --gold: #e0b43a; --red: #f06b78; --green: #4cc38a;
  --blue: #7aa2ff; --amber: #f0a83a;
}
* { box-sizing: border-box; }
body { margin: 0; background: var(--bg); color: var(--text);
  font: 14px/1.65 -apple-system, BlinkMacSystemFont, "PingFang SC", "Microsoft YaHei", sans-serif; }
main { max-width: 1100px; margin: 0 auto; padding: 24px 16px 64px; }
h1 { font-size: 22px; margin: 0 0 4px; }
h2 { font-size: 17px; margin: 36px 0 10px; padding-bottom: 6px; border-bottom: 2px solid var(--gold); }
h3 { font-size: 14px; margin: 18px 0 6px; color: var(--text-dim); }
.card { background: var(--surface); border: 1px solid var(--border); border-radius: 8px; padding: 14px 16px; }
.notes { margin: 10px 0 0; padding-left: 18px; color: var(--red); }
.kv { display: grid; grid-template-columns: repeat(auto-fit, minmax(240px, 1fr)); gap: 4px 18px; }
.kv div span { color: var(--text-dim); }
.scroll { overflow-x: auto; }
table { border-collapse: collapse; width: 100%; font-variant-numeric: tabular-nums; margin: 6px 0; }
th, td { border-bottom: 1px solid var(--border); padding: 4px 8px; text-align: right; white-space: nowrap; }
th { background: var(--surface2); font-weight: 600; }
td:first-child, th:first-child { text-align: left; }
.dim { color: var(--text-dim); font-size: 12px; }
.pos { color: var(--green); } .neg { color: var(--red); }
details { margin: 8px 0; } summary { cursor: pointer; color: var(--blue); }
svg text { fill: var(--text-dim); font-size: 11px; }
code { font-size: 12px; }
"""

DASH = '—'
STATUS_LABELS = {'complete': '已满期', 'in_progress': '进行中', 'not_started': '未开始'}
BAR_COLORS = ('var(--green)', '#7fbf7f', 'var(--text-dim)', '#e8a0a0', 'var(--red)')


def _e(value: Any) -> str:
    return html.escape(str(value), quote=True)


def _num(value: Optional[float]) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def pct(value: Optional[float], digits: int = 2, signed: bool = True) -> str:
    if not _num(value):
        return DASH
    text = f'{value * 100:{"+" if signed else ""}.{digits}f}%'
    cls = 'pos' if value > 0 else 'neg' if value < 0 else ''
    return f'<span class="{cls}">{text}</span>' if signed and cls else text


def num(value: Optional[float], digits: int = 3) -> str:
    return f'{value:.{digits}f}' if _num(value) else DASH


def pval(value: Optional[float]) -> str:
    return f'{value:.3f}' if _num(value) else 'UNKNOWN'


def table(head: Sequence[str], rows: Iterable[Sequence[str]]) -> str:
    th = ''.join(f'<th>{_e(h)}</th>' for h in head)
    body = ''.join('<tr>' + ''.join(f'<td>{c}</td>' for c in r) + '</tr>' for r in rows)
    return f'<div class="scroll"><table><thead><tr>{th}</tr></thead><tbody>{body}</tbody></table></div>'


def ci(stat: Mapping[str, Any]) -> str:
    if _num(stat.get('lo')) and _num(stat.get('hi')):
        return f'[{pct(stat["lo"], signed=False)}, {pct(stat["hi"], signed=False)}]'
    return DASH


def _scale(values: Sequence[float], height: float):
    lo, hi = min(values + [0.0]), max(values + [0.0])
    if hi == lo:
        hi, lo = lo + 1e-6, lo - 1e-6
    pad = (hi - lo) * 0.08
    lo, hi = lo - pad, hi + pad
    return lambda v: height - (v - lo) / (hi - lo) * height


def tier_chart(chapter: Mapping[str, Any]) -> str:
    """Grouped bars: per horizon, Q1..Q5 mean excess with 95% whiskers."""
    w, h, top, left, group_w = 640, 220, 14, 46, 190
    stats = [[chapter[str(hz)]['tiers'][t] for t in range(TIERS)] for hz in HORIZONS]
    values = [v for row in stats for s in row for v in (s.get('mean'), s.get('lo'), s.get('hi')) if _num(v)]
    if not values:
        return '<p class="dim">尚无已满期数据。</p>'
    y = _scale(values, h)
    parts = [f'<line x1="{left}" x2="{w}" y1="{top + y(0):.1f}" y2="{top + y(0):.1f}" stroke="var(--border)"/>']
    for g, row in enumerate(stats):
        x0 = left + 12 + g * group_w
        parts.append(f'<text x="{x0 + 70}" y="{top + h + 16}" text-anchor="middle">{HORIZONS[g]} 季</text>')
        for t, s in enumerate(row):
            if not _num(s.get('mean')):
                continue
            x = x0 + t * 30
            y0, y1 = top + y(0), top + y(s['mean'])
            parts.append(f'<rect x="{x}" y="{min(y0, y1):.1f}" width="22" height="{abs(y1 - y0):.1f}" '
                         f'fill="{BAR_COLORS[t]}"><title>Q{t + 1} {s["mean"] * 100:.2f}%</title></rect>')
            if _num(s.get('lo')) and _num(s.get('hi')):
                cx = x + 11
                parts.append(f'<line x1="{cx}" x2="{cx}" y1="{top + y(s["lo"]):.1f}" y2="{top + y(s["hi"]):.1f}" '
                             f'stroke="var(--text)" stroke-width="1"/>')
    for v in (min(values), 0.0, max(values)):
        parts.append(f'<text x="{left - 6}" y="{top + y(v) + 4:.1f}" text-anchor="end">{v * 100:.1f}%</text>')
    return (f'<svg viewBox="0 0 {w} {h + top + 24}" width="100%" role="img" '
            f'aria-label="五档平均超额">{"".join(parts)}</svg>')


def ic_chart(series: Sequence[Sequence[Any]]) -> str:
    pts = [(a, v) for a, v in series if _num(v)]
    if not pts:
        return '<p class="dim">尚无已满期 IC。</p>'
    w, h, top, left = 640, 160, 10, 40
    y = _scale([v for _, v in pts], h)
    bw = (w - left) / len(pts)
    parts = [f'<line x1="{left}" x2="{w}" y1="{top + y(0):.1f}" y2="{top + y(0):.1f}" stroke="var(--border)"/>']
    for i, (a, v) in enumerate(pts):
        y0, y1 = top + y(0), top + y(v)
        color = 'var(--green)' if v >= 0 else 'var(--red)'
        parts.append(f'<rect x="{left + i * bw + 2:.1f}" y="{min(y0, y1):.1f}" width="{max(bw - 4, 1):.1f}" '
                     f'height="{abs(y1 - y0):.1f}" fill="{color}"><title>{_e(a)} IC {v:.3f}</title></rect>')
    parts.append(f'<text x="{left}" y="{top + h + 16}">{_e(pts[0][0])}</text>')
    parts.append(f'<text x="{w}" y="{top + h + 16}" text-anchor="end">{_e(pts[-1][0])}</text>')
    return (f'<svg viewBox="0 0 {w} {h + top + 24}" width="100%" role="img" '
            f'aria-label="逐期 IC">{"".join(parts)}</svg>')


def _stat_cells(stat: Mapping[str, Any], *, with_bh: bool = False) -> List[str]:
    cells = [str(stat.get('n', 0)), pct(stat.get('mean')), ci(stat), num(stat.get('t'), 2),
             pval(stat.get('p')), str(stat.get('lag', DASH))]
    if with_bh:
        cells.append(pval(stat.get('p_bh')))
    return cells


def _header(report: Mapping[str, Any], inputs: Mapping[str, Any]) -> str:
    h = report['header']
    bh = h.get('bh', {})
    rc = h['return_coverage']
    items = [
        ('方案', f'{_e(h["scheme_id"])} · <code>{_e(h["scheme_hash"])}</code>'),
        ('code_version', f'<code>{_e(h["code_version"])}</code>'),
        ('季末冻结榜', f'{h["periods"]} 期（{_e(" 至 ".join(h["as_of_range"] or [DASH]))}）'),
        ('已满期数 1/2/3 季', ' / '.join(str(h['complete'][str(x)]) for x in HORIZONS)),
        ('进行中 1/2/3 季', ' / '.join(str(h['in_progress'][str(x)]) for x in HORIZONS)),
        ('approximate 占比（可排名行）', pct(h['approximate_share'], 1, signed=False)),
        ('收益覆盖率（可排名，1/2/3 季）',
         ' / '.join(pct(rc[str(x)]['ranked'][0] / rc[str(x)]['ranked'][1], 1, signed=False)
                    if rc[str(x)]['ranked'][1] else DASH for x in HORIZONS)),
        ('收益覆盖率（池，1/2/3 季）',
         ' / '.join(pct(rc[str(x)]['universe'][0] / rc[str(x)]['universe'][1], 1, signed=False)
                    if rc[str(x)]['universe'][1] else DASH for x in HORIZONS)),
        ('价格最后日期', _e(h['price_last_date'])),
        ('BH 检验族', f'{bh.get("family", DASH)} 项，其中 {bh.get("estimated", DASH)} 项有 p 值；未估计项按 p = 1 计入'),
        ('prosperity.db', f'<code>{_e(inputs.get("prosperity_db_sha256", DASH))[:16]}</code>'),
        ('market.db', f'<code>{_e(inputs.get("market_db_sha256", DASH))[:16]}</code>'),
        ('生成时间', _e(inputs.get('generated_at', DASH))),
    ]
    kv = ''.join(f'<div><span>{k}：</span>{v}</div>' for k, v in items)
    notes = ''.join(f'<li>{_e(n)}</li>' for n in h['notes'])
    return f'<div class="card"><div class="kv">{kv}</div><ul class="notes">{notes}</ul></div>'


def _ch_tiers(ch: Mapping[str, Any]) -> str:
    out = [tier_chart(ch), '<p class="dim">柱 = 各档相对池均值的平均超额；须线 = 95% 区间（Newey-West，lag 按实际重叠期数）；'
           '期数 &lt; 5 不给区间。</p>']
    for hz in HORIZONS:
        c = ch[str(hz)]
        rows = [[f'Q{t + 1}', *_stat_cells(c['tiers'][t])] for t in range(TIERS)]
        rows.append(['Q1 − Q5', *_stat_cells(c['spread'], with_bh=True)])
        rows.append(['本方案 Top 40', *_stat_cells(c['top'])])
        out.append(f'<h3>{hz} 季 · 五档单调：{"是" if c["monotonic"] else "否"}</h3>')
        out.append(table(['组', '期数', '平均超额', '95% 区间', 't', 'p', 'lag', 'p_bh'],
                         [r + ([''] if len(r) == 7 else []) for r in rows]))
        detail = [[_e(p['as_of']), STATUS_LABELS.get(p['status'], _e(p['status'])), _e(p['start'] or DASH), _e(p['end'] or DASH),
                   pct(p['baseline']), *[pct(x) for x in p['tier_excess']], pct(p['spread']), num(p['ic']),
                   f'{p["coverage"].get("ranked", [0, 0])[0]}/{p["coverage"].get("ranked", [0, 0])[1]}'
                   if p['coverage'] else DASH] for p in c['periods']]
        out.append('<details><summary>逐期明细</summary>' + table(
            ['as_of', '状态', '起点', '终点', '池均值', 'Q1', 'Q2', 'Q3', 'Q4', 'Q5', 'Q1−Q5', 'IC', '有效/可排名'],
            detail) + '</details>')
    return ''.join(out)


def _ch_ic(ch: Mapping[str, Any]) -> str:
    rows = [[f'{hz} 季', str(ch[str(hz)].get('n', 0)), num(ch[str(hz)].get('mean')), num(ch[str(hz)].get('sd')),
             num(ch[str(hz)].get('icir'), 2), f'[{num(ch[str(hz)].get("lo"))}, {num(ch[str(hz)].get("hi"))}]'
             if _num(ch[str(hz)].get('lo')) else DASH, pval(ch[str(hz)].get('p')), str(ch[str(hz)].get('lag', DASH)),
             pval(ch[str(hz)].get('p_bh'))] for hz in HORIZONS]
    return (ic_chart(ch['3']['series']) + '<p class="dim">上图：3 季逐期 Spearman IC（分数排名 vs 收益）。'
            'ICIR = IC 均值 / 样本标准差，是每期值，未年化（持有期重叠）。</p>'
            + table(['持有期', '期数', 'IC 均值', '标准差', 'ICIR', '95% 区间', 'p', 'lag', 'p_bh'], rows))


CONTROL_ROWS = (('top', '本方案 Top 40'), ('momentum', '价格动量 Top 40（12-1）'),
                ('premium', 'Premium 池（approximate）'), ('universe', '扩展池等权'),
                ('top_minus_momentum', 'Top 40 − 动量'), ('top_minus_premium', 'Top 40 − Premium'))


def _ch_controls(ch: Mapping[str, Any]) -> str:
    out = ['<p class="dim">每个持有期只用共同有效期（三组都有值的期）。Premium 用 selection_compass 纯函数在'
           '点时财报上重算，4 季 CAGR 是当前值，标 approximate。p 值为描述性，不进 BH 检验族。</p>']
    for hz in HORIZONS:
        k = str(hz)
        rng = ch['common_range'][k]
        out.append(f'<h3>{hz} 季 · 共同有效期 {ch["common_periods"][k]} 期'
                   f'{"（" + _e(" 至 ".join(rng)) + "）" if rng else ""}</h3>')
        rows = []
        for key, label in CONTROL_ROWS:
            g = ch['horizons'][k][key]
            if key == 'universe':
                rows.append([label, str(ch['common_periods'][k]), pct(g['mean_return']), pct(g['excess']),
                             DASH, DASH, DASH, DASH])
                continue
            ex = g['excess']
            rows.append([label, str(ex.get('n', 0)), pct(g.get('mean_return')), pct(ex.get('mean')), ci(ex),
                         num(ex.get('t'), 2), pval(ex.get('p')), str(ex.get('lag', DASH))])
        out.append(table(['组', '期数', '平均收益', '平均超额', '95% 区间', 't', 'p', 'lag'], rows))
    prem = [[_e(p['as_of']), str(p['members']), pct(p['fundamental_ready'], 1, signed=False),
             pct(p['beta_ready'], 1, signed=False)] for p in ch.get('premium_periods', ())]
    out.append('<details><summary>Premium 逐期只数与覆盖率</summary><p class="dim">基本面可评估率低的期'
               '（早期历史季度不足 5 个）Premium 只数很少，该期对照不稳。</p>'
               + table(['as_of', 'Premium 只数', '基本面可评估', 'beta 可用'], prem) + '</details>')
    return ''.join(out)


def _ch_overlap(ch: Mapping[str, Any]) -> str:
    rows = [[_e(p['as_of']), str(p['overlap']), str(p['top_n']), str(p['momentum_n'])] for p in ch['periods']]
    return (f'<p>平均重叠 {num(ch["mean_overlap"], 1)} 只，占 Top 40 的 {pct(ch["mean_share"], 1, signed=False)}。'
            '动量要 252 个交易日价格，早期几期没有。</p>'
            + table(['as_of', '重叠只数', '本方案 Top', '动量 Top'], rows))


def _ch_concentration(ch: Mapping[str, Any]) -> str:
    rows = [[_e(p['as_of']), _e(p['top_sector'] or DASH), pct(p['max_share'], 1, signed=False), num(p['hhi'])]
            for p in ch['periods']]
    return (f'<p>Top 40 最大行业平均占比 {pct(ch["mean_max_share"], 1, signed=False)}，'
            f'平均 HHI {num(ch["mean_hhi"])}。</p>' + table(['as_of', '最大行业', '占比', 'HHI'], rows))


def _ch_turnover(ch: Mapping[str, Any]) -> str:
    rows = [[_e(p['as_of']), pct(p['turnover'], 1, signed=False)] for p in ch['quarterly']]
    wk = ch['weekly']
    weekly = (f'周榜换手：live 周榜共 {wk["n"]} 张，不足 2 张，暂不计算。' if 'series' not in wk else
              f'周榜换手：{wk["n"]} 张，平均 {pct(wk["mean"], 1, signed=False)}。')
    return (f'<p>相邻季末榜 Top 40 平均换手 {pct(ch["quarterly_mean"], 1, signed=False)}。{_e(weekly)}</p>'
            + table(['as_of', '较上一期换手'], rows))


def _ch_failures(cases: Sequence[Mapping[str, Any]]) -> str:
    if not cases:
        return '<p class="dim">没有已满 3 季的 STRICT 样本。</p>'
    out = ['<p class="dim">之后 3 季收益最差的 STRICT 股票，附当时的分数拆解。</p>']
    for c in cases:
        fam = '，'.join(f'{_e(k)} {num(v, 2)}' for k, v in c['family_scores'].items())
        rows = [[_e(f), num(x.get('raw')), num(x.get('z'), 2), num(x.get('weight')), num(x.get('contribution'))]
                for f, x in c['breakdown'].items()]
        out.append(f'<details><summary>{_e(c["as_of"])} · {_e(c["symbol"])}（{_e(c["sector"] or DASH)}）'
                   f'收益 {pct(c["return"])}，超额 {pct(c["excess"])}，排名 {c["rank"]}，分数 {num(c["score"], 1)}'
                   f'</summary><p class="dim">各族小分：{fam or DASH}；徽章：{_e("、".join(c["badges"]) or DASH)}</p>'
                   + table(['因子', '原始值', 'z', '权重', '贡献'], rows) + '</details>')
    return ''.join(out)


def _ch_short(items: Sequence[Mapping[str, Any]]) -> str:
    if not items:
        return '<p>本方案没有预期族因子。</p>'
    rows = [[_e(i['factor']), str(i['periods']), pct(i['latest_coverage'], 1, signed=False), _e(i['verdict'])]
            for i in items]
    return ('<p class="dim">预期族（surprise、revision）历史太短，只报期数，不给显著性结论。</p>'
            + table(['因子', '有 z 的冻结期数', '最新一期覆盖率', '结论'], rows))


def _appendix(app: Mapping[str, Any]) -> str:
    unknown = [[_e(u['symbol']), _e(u['as_of']), str(u['horizon']), _e(u['reason']), _e(u['last_price_date'] or DASH)]
               for u in app['unknown']]
    nop = [[_e(x['symbol']), str(x['periods'])] for x in app['no_price']]
    large = [[_e(x['symbol']), _e(x['date']), num(x['ratio'])] for x in app['large_moves']]
    splits = [[_e(x['symbol']), _e(x['fmp_date']), num(x['factor'], 4), _e(x['state'])] for x in app['splits']]
    adj = [[_e(x['symbol']), _e(x['date']), num(x['factor'], 4)] for x in app['adjustments']]
    return ''.join([
        '<h2>附录</h2>',
        f'<details><summary>A. 收益未知（可排名行，{len(unknown)} 条）</summary>'
        + table(['代码', 'as_of', '持有期', '原因', '最后价格日'], unknown) + '</details>',
        f'<details><summary>B. 无任何价格的成员（{len(nop)} 只，幸存者偏差说明）</summary>'
        + table(['代码', '出现期数'], nop) + '</details>',
        f'<details><summary>C. 大幅单日变动（收益照算，{len(large)} 处）</summary>'
        + table(['代码', '日期', '比值'], large) + '</details>',
        f'<details><summary>D. 拆股记录处理结果（{len(splits)} 条）</summary>'
        + table(['代码', 'FMP 日期', '比例', '状态'], splits)
        + '<p class="dim">adjusted = 已按实际跳变日复权；already_adjusted = 价格已复权；'
          'unresolved = 无法判定，跨过它的收益记未知。</p>'
        + table(['代码', '复权跳变日', '比例'], adj) + '</details>',
    ])


def render(report: Mapping[str, Any], *, scheme_id: str, inputs: Mapping[str, Any]) -> str:
    ch = report['chapters']
    bodies = (_ch_tiers(ch[CHAPTERS[0]]), _ch_ic(ch[CHAPTERS[1]]), _ch_controls(ch[CHAPTERS[2]]),
              _ch_overlap(ch[CHAPTERS[3]]), _ch_concentration(ch[CHAPTERS[4]]), _ch_turnover(ch[CHAPTERS[5]]),
              _ch_failures(ch[CHAPTERS[6]]), _ch_short(ch[CHAPTERS[7]]))
    sections = ''.join(f'<section><h2>{k}. {_e(t)}</h2>{b}</section>' for k, (t, b) in enumerate(zip(CHAPTERS, bodies), 1))
    return ('<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width, initial-scale=1">'
            f'<title>景气体检 · {_e(scheme_id)}</title><style>{CSS}</style></head><body><main>'
            f'<h1>景气体检 · {_e(scheme_id)}</h1>{_header(report, inputs)}{sections}{_appendix(report["appendix"])}'
            '</main></body></html>')
