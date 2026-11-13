"""One-off: eval json → standalone HTML comparing single momentum windows. Usage: make_sweep_html.py EVAL.json OUT.html PICK(e.g. 60d)"""
import json, math, sys
r = json.load(open(sys.argv[1])); dst, pick = sys.argv[2], sys.argv[3]
S = r['stats']; wins = [f'{L}d' for L in r['windows']]; base = '7周∩14周'
names = wins + [base]
lab = lambda n: (f"{int(n[:-1]) // 5}周" if n.endswith('d') else '7∩14')
pct = lambda v: f'{v:+.1%}'

def bars(metric, title, sub):
    W, H, L, R, T, B = 1100, 250, 52, 16, 14, 40
    vals = [S[p][n][metric] for p in ('开发段', '验证段') for n in names]
    lo, hi = min(0, min(vals)), max(0, max(vals)); span = (hi - lo) or 1
    lo, hi = lo - span * 0.05, hi + span * 0.08
    Y = lambda v: T + (hi - v) * (H - T - B) / (hi - lo)
    step = (W - L - R) / (len(names) + 0.6); bw = step * 0.36
    out = ''
    ticks = [lo + i * (hi - lo) / 5 for i in range(6)]
    tk = sorted({round(t * 20) / 20 for t in ticks} | {0.0})
    out += ''.join(f"<line x1={L} x2={W - R} y1={Y(t):.1f} y2={Y(t):.1f} class={'z' if t == 0 else 'g'} /><text x={L - 8} y={Y(t) + 4:.1f} class=ax text-anchor=end>{t:+.0%}</text>" for t in tk if lo <= t <= hi)
    for i, n in enumerate(names):
        x0 = L + i * step + (step * 0.6 if n == base else 0) + step * 0.12
        hl = n == pick
        for j, (p, cls) in enumerate((('开发段', 'b1'), ('验证段', 'b2'))):
            v = S[p][n][metric]; y = Y(max(v, 0)); h = abs(Y(v) - Y(0))
            tip = f"{lab(n)}（{n}）｜{p}｜{title}：{v:+.1%}"
            out += f"<rect x={x0 + j * (bw + 2):.1f} y={y:.1f} width={bw:.1f} height={max(h, 1):.1f} rx=3 class={cls} data-tip='{tip}' />"
        out += f"<text x={x0 + bw + 1:.1f} y={H - 22} class='ax{' hl' if hl else ''}' text-anchor=middle>{lab(n)}</text><text x={x0 + bw + 1:.1f} y={H - 8} class=ax2 text-anchor=middle>{n if n != base else '原方案'}</text>"
        if hl: out += f"<rect x={x0 - 5:.1f} y={T} width={2 * bw + 12:.1f} height={H - T - B + 2} class=hlbox />"
    return f"<h2>{title}</h2><div class=m>{sub}</div><div class=legend><span><i style='background:var(--c1)'></i>开发段（{S['开发段']['SPY']['weeks']} 周，2024-07 之前）</span><span><i style='background:var(--c2)'></i>验证段（{S['验证段']['SPY']['weeks']} 周，2024-07 起）</span><span>框出的是建议窗口</span></div><svg viewBox='0 0 {W} {H}'>{out}</svg>"

def nav_chart():
    series = [(pick, f'单窗口 {lab(pick)}', 'var(--c1)', 2.5, ''), (base, '原方案 7∩14', 'var(--c2)', 2, ''), ('池全体', '景气精选池全体', 'var(--c3)', 1.8, ''),
              ('SPY', 'SPY', 'var(--t1)', 1.5, '5 4'), ('QQQ', 'QQQ', 'var(--t2)', 1.5, '2 3')]
    dates = r['dates']; vals = {}
    for k, *_ in series:
        v, acc = [1.0], 1.0
        for x in r['returns'][k]: acc *= 1 + x; v.append(acc)
        vals[k] = v
    W, H, L, R, T, B = 1100, 420, 52, 170, 16, 30
    lo = min(min(v) for v in vals.values()); hi = max(max(v) for v in vals.values())
    ticks = [t for t in (0.5, 0.75, 1, 1.5, 2, 3, 4, 6, 8, 12, 16, 24, 32, 48, 64, 96) if lo * 0.9 <= t <= hi * 1.1]
    lo, hi = min(lo, ticks[0]), max(hi, ticks[-1]); n = len(dates)
    X = lambda i: L + i * (W - L - R) / n
    Y = lambda v: T + (math.log(hi) - math.log(v)) * (H - T - B) / (math.log(hi) - math.log(lo))
    shade = ''
    for p in S:
        if p.startswith('下跌段'):
            a, b = p.split(' ')[1].split('→'); shade += f"<rect x={X(dates.index(a)):.1f} y={T} width={X(dates.index(b) + 1) - X(dates.index(a)):.1f} height={H - T - B} class=sh />"
    grid = ''.join(f"<line x1={L} x2={W - R} y1={Y(t):.1f} y2={Y(t):.1f} class=g /><text x={L - 8} y={Y(t) + 4:.1f} class=ax text-anchor=end>{(t - 1) * 100:+.0f}%</text>" for t in ticks)
    xl, seen = '', set()
    for i, d in enumerate(dates):
        key = d[:4] + ('a' if d[5:7] <= '06' else 'b')
        if key not in seen: seen.add(key); xl += f"<text x={X(i):.1f} y={H - 8} class=ax text-anchor=middle>{d[:7]}</text>"
    lines, ends = '', []
    for k, name, color, w, dash in series:
        pts = ' '.join(f"{X(i):.1f},{Y(v):.1f}" for i, v in enumerate(vals[k]))
        lines += f"<polyline points='{pts}' fill=none stroke='{color}' stroke-width={w} stroke-linejoin=round" + (f" stroke-dasharray='{dash}'" if dash else '') + " />"
        ends.append([Y(vals[k][-1]), name, vals[k][-1], color])
    ends.sort()
    for i in range(1, len(ends)): ends[i][0] = max(ends[i][0], ends[i - 1][0] + 16)
    labels = ''.join(f"<circle cx={W - R + 8} cy={y:.1f} r=4 fill='{c}' /><text x={W - R + 18} y={y + 4:.1f} class=lb>{nm} {(v - 1) * 100:+.0f}%</text>" for y, nm, v, c in ends)
    leg = ''.join(f"<span><i class=ln style='border-color:{c};{'border-top-style:dashed;border-top-width:2px;' if d else ''}'></i>{nm}</span>" for _, nm, c, _, d in series)
    return (f"<h2>累计收益（对数坐标）</h2><div class=legend>{leg}<span style='background:var(--s2);padding:0 6px'>灰底 = SPY 从高点跌到低点（≥10%）</span></div>"
            f"<svg viewBox='0 0 {W} {H}'>{shade}{grid}{xl}{lines}{labels}</svg>")

cols = [('ann', '年化'), ('mdd', '最大回撤'), ('beta', 'β'), ('alpha_ann', 'alpha 年化'), ('ex_pool_ann', '超池 年化'), ('won_spy', '赢 SPY'), ('names', '只数'), ('turnover', '周换手')]
def cell(n, p, k):
    v = S[p][n][k]
    return f"{v:.2f}" if k == 'beta' else f"{v:.1f}" if k == 'names' else f"{v:.0%}" if k in ('won_spy', 'turnover') else pct(v)
rows = ''
for n in names + ['池全体', '池内其余', 'SPY', 'QQQ']:
    cls = ' class=pick' if n == pick else ''
    rows += (f"<tr{cls}><td>{lab(n) if n in names else n}<span class=m> {n if n.endswith('d') else ''}</span></td>" + ''.join(f"<td class=n>{cell(n, '全程', k)}</td>" for k, _ in cols)
             + f"<td class='n sep'>{pct(S['开发段'][n]['alpha_ann'])}</td><td class=n>{pct(S['验证段'][n]['alpha_ann'])}</td><td class='n sep'>{pct(S['开发段'][n]['ex_pool_ann'])}</td><td class=n>{pct(S['验证段'][n]['ex_pool_ann'])}</td></tr>")
eps = [p for p in S if p.startswith('下跌段')]
erows = ''.join(f"<tr{' class=pick' if n == pick else ''}><td>{lab(n) if n in names else n}</td>" + ''.join(f"<td class=n>{pct(S[p][n]['cum']) if S[p][n] else '—'}</td>" for p in eps) + '</tr>' for n in names + ['池全体', 'SPY', 'QQQ'])
ehead = ''.join(f"<th class=n>{p.split(' ')[1]}<br><span class=m>{S[p]['SPY']['weeks'] if S[p]['SPY'] else ''} 周</span></th>" for p in eps)
doc = f"""<!doctype html><html lang=zh><head><meta charset=utf-8><meta name=viewport content="width=device-width,initial-scale=1"><title>动量窗口回测</title><style>
:root{{color-scheme:light;--bg:#fcfcfb;--s2:#f1f0ec;--line:#dddcd5;--t1:#0b0b0b;--t2:#8a8983;--tx:#52514e;--c1:#2a78d6;--c2:#eb6834;--c3:#1baf7a}}
@media (prefers-color-scheme:dark){{:root{{color-scheme:dark;--bg:#1a1a19;--s2:#262624;--line:#3a3a37;--t1:#f0efec;--t2:#8f8e86;--tx:#c3c2b7;--c1:#3987e5;--c2:#d95926;--c3:#199e70}}}}
body{{margin:0;background:var(--bg);color:var(--t1);font:14px/1.55 -apple-system,"PingFang SC",sans-serif}}main{{max-width:1140px;margin:0 auto;padding:24px 16px 60px}}
h1{{font-size:22px;margin:0 0 4px}}h2{{font-size:18px;margin:30px 0 4px}}.m{{color:var(--tx);font-size:12px;font-weight:400}}.warn{{border-left:3px solid var(--c2);padding:6px 10px;background:var(--s2)}}
svg{{width:100%;height:auto;display:block}}.g{{stroke:var(--line);stroke-width:1}}.z{{stroke:var(--tx);stroke-width:1}}.ax{{fill:var(--tx);font-size:11px}}.ax.hl{{fill:var(--t1);font-weight:600}}.ax2{{fill:var(--t2);font-size:10px}}.lb{{fill:var(--t1);font-size:12px}}
.b1{{fill:var(--c1)}}.b2{{fill:var(--c2)}}.hlbox{{fill:none;stroke:var(--t1);stroke-width:1;stroke-dasharray:3 3;rx:4}}.sh{{fill:var(--tx);opacity:.12}}
.legend{{display:flex;gap:16px;flex-wrap:wrap;font-size:12px;color:var(--tx);margin:6px 0}}.legend i{{display:inline-block;width:12px;height:12px;border-radius:3px;margin-right:5px;vertical-align:-2px}}.legend i.ln{{width:18px;height:0;border-top:3px solid;border-radius:0;vertical-align:3px}}
.scroll{{overflow-x:auto}}table{{border-collapse:collapse;font-size:13px;margin-top:6px}}th{{text-align:left;font-weight:500;color:var(--tx);font-size:12px;border-bottom:1px solid var(--line);padding:5px 12px 5px 0;vertical-align:bottom}}td{{border-bottom:1px solid var(--line);padding:5px 12px 5px 0}}.n{{text-align:right;font-variant-numeric:tabular-nums}}th.n{{text-align:right}}.sep{{border-left:1px solid var(--line);padding-left:12px}}tr.pick td{{background:var(--s2);font-weight:500}}
#tip{{position:absolute;pointer-events:none;background:var(--t1);color:var(--bg);padding:6px 9px;border-radius:6px;font-size:12px;display:none;white-space:pre;z-index:9}}
</style></head><body><main>
<h1>动量榜只留一个窗口：该留哪个</h1>
<div class=m>景气精选池不变，V4 算法不变，入选 = 池内通过上涨资格的全部。只改动量窗口长度，共 13 个候选。每周一收盘等权换仓，没有股票入选就空仓。{r['first']} 至 {r['last']}，{r['weeks']} 周。价格收益，不含分红，不扣成本。</div>
<p class=warn>回放结果，近似点时；越早的年份越粗。开发段用来选窗口，验证段只用来检验，不参与选择。</p>
{bars('alpha_ann', '选股 alpha（年化）', '扣掉"跟着 SPY 涨跌"那部分之后剩下的收益。越高越好；两段都高才算数。')}
{bars('ex_pool_ann', '比景气池全体多赚（年化）', '同样是景气池里的票，加了动量筛选后每年多赚多少。单独衡量动量这一刀。')}
{nav_chart()}
<h2>全部候选</h2><div class=scroll><table><tr><th>窗口</th>{''.join(f'<th class=n>{t}</th>' for _, t in cols)}<th class='n sep'>alpha<br>开发段</th><th class=n>alpha<br>验证段</th><th class='n sep'>超池<br>开发段</th><th class=n>超池<br>验证段</th></tr>{rows}</table></div>
<h2>大盘下跌段里的累计收益</h2><div class=m>下跌段由数据自己找出：SPY 周线从高点到低点跌 ≥10%。</div><div class=scroll><table><tr><th>窗口</th>{ehead}</tr>{erows}</table></div>
</main><div id=tip></div><script>
const tip=document.getElementById('tip');document.addEventListener('mousemove',e=>{{const t=e.target.closest('[data-tip]');if(!t){{tip.style.display='none';return}}
tip.textContent=t.dataset.tip.split('｜').join('\\n');tip.style.display='block';tip.style.left=Math.min(e.pageX+12,document.documentElement.scrollWidth-tip.offsetWidth-8)+'px';tip.style.top=(e.pageY+14)+'px'}});
</script></body></html>"""
open(dst, 'w', encoding='utf-8').write(doc); print('bytes', len(doc.encode()))
