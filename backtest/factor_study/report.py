"""
因子研究报告 — 文本 + HTML + CSV 导出

文本: IC 汇总表 + 事件研究排行榜
HTML: IC 衰减曲线 + 分位数柱状图 + 事件统计表 (Chart.js)
CSV: 完整结果导出到 data/factor_study/
"""

import logging
from datetime import datetime
from pathlib import Path
from typing import List, Optional

import pandas as pd

from backtest.factor_study.runner import FactorStudyResults

# ══════════════════════════════════════════════════════════
# 多重检验校正
# ══════════════════════════════════════════════════════════


def _apply_bh_fdr(p_values: List[float]) -> List[float]:
    """Benjamini-Hochberg FDR 校正 (无外部依赖).

    给定 N 个原始 p-value，返回 FDR 调整后的 p-value。
    排序后从大到小: p_adj[i] = min(p_adj[i+1], p[i] * N / rank)
    """
    n = len(p_values)
    if n == 0:
        return []

    # 按 p-value 升序排列，保留原始索引
    indexed = sorted(enumerate(p_values), key=lambda x: x[1])
    adjusted = [0.0] * n

    # 从大到小调整
    prev = 1.0
    for rank_idx in range(n - 1, -1, -1):
        orig_idx, p = indexed[rank_idx]
        rank = rank_idx + 1
        adj = min(prev, p * n / rank)
        adj = min(adj, 1.0)
        adjusted[orig_idx] = adj
        prev = adj

    return adjusted

logger = logging.getLogger(__name__)

_PROJECT_ROOT = Path(__file__).parent.parent.parent
_OUTPUT_DIR = _PROJECT_ROOT / "data" / "factor_study"


# ══════════════════════════════════════════════════════════
# 文本报告
# ══════════════════════════════════════════════════════════

def print_results(results: FactorStudyResults):
    """打印单个因子的研究结果到 stdout"""
    print(f"\n{'='*70}")
    print(f"  因子研究: {results.factor_name}")
    print(f"{'='*70}")
    print(f"  市场: {results.config.market}")
    print(f"  计算频率: {results.config.computation_freq}")
    print(f"  计算日数: {results.n_computation_dates}")
    print(f"  股票数: {results.n_symbols}")
    print(f"  耗时: {results.elapsed_seconds:.1f}s")

    bench = results.config.benchmark_symbol

    # IS 日期范围
    if results.is_dates:
        print(f"\n  In-Sample: {results.is_dates[0]} ~ {results.is_dates[-1]}"
              f" ({len(results.is_dates)} 日)")
    if results.oos_skipped:
        print(f"  OOS skipped: 数据不足 ({len(results.oos_dates)} < "
              f"{results.config.min_oos_dates})")
    elif results.oos_dates:
        print(f"  Out-of-Sample: {results.oos_dates[0]} ~ {results.oos_dates[-1]}"
              f" ({len(results.oos_dates)} 日)")

    # ── In-Sample IC ──
    if results.ic_results:
        ic_label = f"IC 分析 — IS (Excess vs {bench})" if bench else "IC 分析 — IS"
        print(f"\n{'─'*70}")
        print(f"  Track 1: {ic_label}")
        print(f"{'─'*70}")
        _print_ic_table(results.ic_results)

    # ── OOS IC ──
    if results.oos_ic_results:
        ic_label = f"IC 分析 — OOS (Excess vs {bench})" if bench else "IC 分析 — OOS"
        print(f"\n{'─'*70}")
        print(f"  Track 1: {ic_label}")
        print(f"{'─'*70}")
        _print_ic_table(results.oos_ic_results)

    # ── In-Sample Events ──
    _print_event_section(results.event_results, "IS")

    # ── OOS Events ──
    if results.oos_event_results:
        _print_event_section(results.oos_event_results, "OOS")

    print(f"{'='*70}\n")


def _print_ic_table(ic_results):
    """打印 IC 汇总表"""
    print(f"  {'Horizon':>8} {'Mean IC':>10} {'Std IC':>10} "
          f"{'IC_IR':>8} {'Hit%':>8} {'Q5-Q1':>10}")
    print(f"  {'─'*8} {'─'*10} {'─'*10} {'─'*8} {'─'*8} {'─'*10}")
    for ic in ic_results:
        print(f"  {ic.horizon:>8d} {ic.mean_ic:>10.4f} {ic.std_ic:>10.4f} "
              f"{ic.ic_ir:>8.2f} {ic.ic_hit_rate:>7.1%} "
              f"{ic.top_bottom_spread:>10.4f}")


def _print_event_section(event_results, label):
    """打印事件研究 section"""
    if not event_results:
        return

    print(f"\n{'─'*70}")
    print(f"  Track 2: 事件研究 — {label} (Top 10, BH-FDR corrected)")
    print(f"{'─'*70}")

    p_values = [ev.p_value for ev in event_results]
    p_fdr_values = _apply_bh_fdr(p_values)

    indexed_events = list(zip(event_results, p_fdr_values))
    indexed_events.sort(key=lambda x: abs(x[0].t_stat), reverse=True)
    top10 = indexed_events[:10]

    print(f"  {'Signal':<30} {'H':>4} {'N':>6} {'Neff':>6} {'Mean':>8} "
          f"{'Hit%':>7} {'t-stat':>8} {'p-val':>8} {'p-FDR':>8}")
    print(f"  {'─'*30} {'─'*4} {'─'*6} {'─'*6} {'─'*8} "
          f"{'─'*7} {'─'*8} {'─'*8} {'─'*8}")

    for ev, p_fdr in top10:
        sig = "**" if p_fdr < 0.05 else "  "
        print(f"  {ev.signal_label:<30} {ev.horizon:>4d} {ev.n_events:>6d} "
              f"{ev.n_effective:>6d} {ev.mean_return:>8.4f} {ev.hit_rate:>6.1%} "
              f"{ev.t_stat:>8.2f} {ev.p_value:>7.4f} {p_fdr:>7.4f} {sig}")


# ══════════════════════════════════════════════════════════
# CSV 导出
# ══════════════════════════════════════════════════════════

def export_csv(results: FactorStudyResults) -> Path:
    """导出完整结果到 CSV"""
    _OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    date_str = datetime.now().strftime("%Y%m%d")
    name = results.factor_name

    # IC results
    if results.ic_results:
        ic_rows = []
        for ic in results.ic_results:
            row = {
                "factor": ic.factor_name,
                "horizon": ic.horizon,
                "mean_ic": ic.mean_ic,
                "std_ic": ic.std_ic,
                "ic_ir": ic.ic_ir,
                "ic_hit_rate": ic.ic_hit_rate,
                "top_bottom_spread": ic.top_bottom_spread,
            }
            for q, ret in ic.quantile_returns.items():
                row[f"Q{q}_return"] = ret
            ic_rows.append(row)

        ic_df = pd.DataFrame(ic_rows)
        ic_path = _OUTPUT_DIR / f"ic_{name}_{date_str}.csv"
        ic_df.to_csv(ic_path, index=False)
        logger.info(f"IC 结果已导出: {ic_path}")

    # Event results
    if results.event_results:
        # BH-FDR 校正
        p_values = [ev.p_value for ev in results.event_results]
        p_fdr_values = _apply_bh_fdr(p_values)

        ev_rows = []
        for ev, p_fdr in zip(results.event_results, p_fdr_values):
            ev_rows.append({
                "factor": ev.factor_name,
                "signal": ev.signal_label,
                "horizon": ev.horizon,
                "n_events": ev.n_events,
                "n_effective": ev.n_effective,
                "mean_return": ev.mean_return,
                "median_return": ev.median_return,
                "hit_rate": ev.hit_rate,
                "t_stat": ev.t_stat,
                "p_value": ev.p_value,
                "p_fdr": p_fdr,
            })

        ev_df = pd.DataFrame(ev_rows)
        ev_path = _OUTPUT_DIR / f"events_{name}_{date_str}.csv"
        ev_df.to_csv(ev_path, index=False)
        logger.info(f"事件研究结果已导出: {ev_path}")

    # OOS IC results
    if results.oos_ic_results:
        oos_ic_rows = []
        for ic in results.oos_ic_results:
            row = {
                "factor": ic.factor_name,
                "split": "OOS",
                "horizon": ic.horizon,
                "mean_ic": ic.mean_ic,
                "std_ic": ic.std_ic,
                "ic_ir": ic.ic_ir,
                "ic_hit_rate": ic.ic_hit_rate,
                "top_bottom_spread": ic.top_bottom_spread,
            }
            for q, ret in ic.quantile_returns.items():
                row[f"Q{q}_return"] = ret
            oos_ic_rows.append(row)

        oos_ic_df = pd.DataFrame(oos_ic_rows)
        oos_ic_path = _OUTPUT_DIR / f"ic_oos_{name}_{date_str}.csv"
        oos_ic_df.to_csv(oos_ic_path, index=False)
        logger.info(f"OOS IC 结果已导出: {oos_ic_path}")

    # OOS Event results
    if results.oos_event_results:
        p_values = [ev.p_value for ev in results.oos_event_results]
        p_fdr_values = _apply_bh_fdr(p_values)

        oos_ev_rows = []
        for ev, p_fdr in zip(results.oos_event_results, p_fdr_values):
            oos_ev_rows.append({
                "factor": ev.factor_name,
                "split": "OOS",
                "signal": ev.signal_label,
                "horizon": ev.horizon,
                "n_events": ev.n_events,
                "n_effective": ev.n_effective,
                "mean_return": ev.mean_return,
                "median_return": ev.median_return,
                "hit_rate": ev.hit_rate,
                "t_stat": ev.t_stat,
                "p_value": ev.p_value,
                "p_fdr": p_fdr,
            })

        oos_ev_df = pd.DataFrame(oos_ev_rows)
        oos_ev_path = _OUTPUT_DIR / f"events_oos_{name}_{date_str}.csv"
        oos_ev_df.to_csv(oos_ev_path, index=False)
        logger.info(f"OOS 事件研究结果已导出: {oos_ev_path}")

    return _OUTPUT_DIR


# ══════════════════════════════════════════════════════════
# HTML 报告
# ══════════════════════════════════════════════════════════

def generate_html_report(
    all_results: List[FactorStudyResults],
) -> str:
    """生成 HTML 报告"""
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    factor_names = [r.factor_name for r in all_results]
    title = ", ".join(factor_names)

    # IS 结果
    ic_table_html = _build_ic_table(all_results)
    decay_chart_js = _build_decay_chart(all_results)
    quantile_chart_js = _build_quantile_chart(all_results)
    event_table_html = _build_event_table(all_results)

    # OOS 结果
    oos_section = _build_oos_section(all_results)

    # IS/OOS 日期范围信息
    split_info = _build_split_info(all_results)

    html = f"""<!DOCTYPE html>
<html lang="zh">
<head>
<meta charset="UTF-8">
<title>因子研究报告 — {title}</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
<style>
    body {{ font-family: -apple-system, sans-serif; max-width: 1400px; margin: auto; padding: 20px; background: #1a1a2e; color: #e0e0e0; }}
    h1 {{ color: #ffd700; }} h2 {{ color: #4fc3f7; margin-top: 30px; }} h3 {{ color: #ff9800; }}
    table {{ border-collapse: collapse; width: 100%; margin: 15px 0; }}
    th, td {{ border: 1px solid #333; padding: 6px 10px; text-align: right; font-size: 13px; }}
    th {{ background: #2a2a4a; color: #ffd700; }}
    tr:nth-child(even) {{ background: #1e1e3a; }}
    .sig {{ color: #4caf50; font-weight: bold; }}
    .config {{ background: #2a2a4a; padding: 15px; border-radius: 8px; margin: 15px 0; }}
    .chart-row {{ display: grid; grid-template-columns: 1fr 1fr; gap: 20px; }}
    canvas {{ background: #1e1e3a; border-radius: 8px; margin: 10px 0; }}
    .oos-warn {{ background: #4a3000; padding: 10px; border-radius: 6px; border-left: 4px solid #ff9800; margin: 15px 0; }}
</style>
</head>
<body>
<h1>因子研究报告</h1>
<p>生成时间: {now} | 因子: {title}</p>

{_build_config_section(all_results)}
{split_info}

<h2>In-Sample 结果</h2>

<h3>Track 1: IC 分析</h3>
{ic_table_html}

<div class="chart-row">
    <div>
        <h3>IC 衰减曲线</h3>
        <canvas id="decayChart" width="600" height="350"></canvas>
    </div>
    <div>
        <h3>分位数收益</h3>
        <canvas id="quantileChart" width="600" height="350"></canvas>
    </div>
</div>

<h3>Track 2: 事件研究 — IS (显著信号)</h3>
{event_table_html}

{oos_section}

<script>
{decay_chart_js}
{quantile_chart_js}
</script>
</body>
</html>"""

    return html


def save_html_report(html: str, factor_names: List[str]) -> Path:
    """保存 HTML 报告"""
    _OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    date_str = datetime.now().strftime("%Y%m%d")
    name = "_".join(factor_names)[:60]
    path = _OUTPUT_DIR / f"report_{name}_{date_str}.html"
    path.write_text(html, encoding="utf-8")
    logger.info(f"HTML 报告已保存: {path}")
    return path


# ── 内部构建函数 ──────────────────────────────────────────

def _build_split_info(all_results: List[FactorStudyResults]) -> str:
    """构建 IS/OOS 日期分割信息"""
    if not all_results:
        return ""
    r = all_results[0]

    parts = []
    if r.is_dates:
        parts.append(
            f"<strong>In-Sample ({len(r.is_dates)} 日):</strong> "
            f"{r.is_dates[0]} ~ {r.is_dates[-1]}"
        )
    if r.oos_skipped:
        parts.append(
            f'<div class="oos-warn">OOS skipped: 数据不足 '
            f"({len(r.oos_dates)} < {r.config.min_oos_dates} 最小门槛)</div>"
        )
    elif r.oos_dates:
        parts.append(
            f"<strong>Out-of-Sample ({len(r.oos_dates)} 日):</strong> "
            f"{r.oos_dates[0]} ~ {r.oos_dates[-1]}"
        )

    return '<div class="config">' + " | ".join(parts) + "</div>"


def _build_oos_section(all_results: List[FactorStudyResults]) -> str:
    """构建 OOS 区块的 HTML"""
    if not all_results:
        return ""
    r = all_results[0]

    if r.oos_skipped:
        return (
            '<div class="oos-warn">'
            f"OOS skipped: 数据不足 ({len(r.oos_dates)} 日 < "
            f"{r.config.min_oos_dates} 最小门槛)"
            "</div>"
        )

    # 构建 OOS IC 表
    oos_ic_rows = ""
    has_oos_ic = False
    for res in all_results:
        if res.oos_ic_results:
            has_oos_ic = True
            for ic in res.oos_ic_results:
                sig_class = ' class="sig"' if abs(ic.ic_ir) >= 0.5 else ""
                oos_ic_rows += f"""<tr>
                    <td style="text-align:left">{ic.factor_name}</td>
                    <td>{ic.horizon}d</td>
                    <td{sig_class}>{ic.mean_ic:.4f}</td>
                    <td>{ic.std_ic:.4f}</td>
                    <td{sig_class}>{ic.ic_ir:.2f}</td>
                    <td>{ic.ic_hit_rate:.1%}</td>
                    <td>{ic.top_bottom_spread:.4f}</td>
                </tr>"""

    if not has_oos_ic:
        return ""

    oos_ic_table = f"""<table>
    <thead><tr>
        <th>因子</th><th>Horizon</th><th>Mean IC</th>
        <th>Std IC</th><th>IC_IR</th><th>Hit%</th><th>Q5-Q1</th>
    </tr></thead>
    <tbody>{oos_ic_rows}</tbody>
</table>"""

    # 构建 OOS 事件表
    oos_events = []
    for res in all_results:
        if res.oos_event_results:
            oos_events.extend(res.oos_event_results)

    if oos_events:
        oos_event_table = _build_event_table_from_list(oos_events)
    else:
        oos_event_table = "<p>无 OOS 事件研究结果</p>"

    return f"""
<h2>Out-of-Sample 结果</h2>

<h3>Track 1: IC 分析 — OOS</h3>
{oos_ic_table}

<h3>Track 2: 事件研究 — OOS</h3>
{oos_event_table}
"""


def _build_event_table_from_list(events) -> str:
    """从事件列表构建 HTML 事件表 (带 FDR)"""
    if not events:
        return "<p>无事件研究结果</p>"

    p_values = [ev.p_value for ev in events]
    p_fdr_values = _apply_bh_fdr(p_values)
    indexed = list(zip(events, p_fdr_values))

    significant = [(ev, pf) for ev, pf in indexed
                   if pf < 0.10 and ev.n_events >= 5]
    significant.sort(key=lambda x: abs(x[0].t_stat), reverse=True)
    display = significant[:30] if significant else sorted(
        indexed, key=lambda x: abs(x[0].t_stat), reverse=True
    )[:20]

    rows = ""
    for ev, p_fdr in display:
        sig_class = ' class="sig"' if p_fdr < 0.05 else ""
        star = "**" if p_fdr < 0.01 else ("*" if p_fdr < 0.05 else "")
        rows += f"""<tr>
            <td style="text-align:left">{ev.factor_name}</td>
            <td style="text-align:left">{ev.signal_label}</td>
            <td>{ev.horizon}d</td>
            <td>{ev.n_events}</td>
            <td>{ev.n_effective}</td>
            <td{sig_class}>{ev.mean_return:.4f}</td>
            <td>{ev.median_return:.4f}</td>
            <td>{ev.hit_rate:.1%}</td>
            <td{sig_class}>{ev.t_stat:.2f}{star}</td>
            <td>{ev.p_value:.4f}</td>
            <td{sig_class}>{p_fdr:.4f}</td>
        </tr>"""

    return f"""<p style="color:#888;font-size:12px;">BH-FDR corrected ({len(events)} hypotheses)</p>
<table>
    <thead><tr>
        <th>因子</th><th>信号</th><th>Horizon</th>
        <th>N</th><th>N_eff</th><th>Mean Ret</th><th>Median</th>
        <th>Hit%</th><th>t-stat</th><th>p-value</th><th>p-FDR</th>
    </tr></thead>
    <tbody>{rows}</tbody>
</table>"""


def _build_config_section(all_results: List[FactorStudyResults]) -> str:
    if not all_results:
        return ""
    r = all_results[0]
    return f"""<div class="config">
    <strong>配置:</strong>
    市场={r.config.market} | 频率={r.config.computation_freq} |
    Forward Horizons={r.config.forward_horizons} |
    Quantiles={r.config.n_quantiles} |
    计算日数={r.n_computation_dates} | 股票数={r.n_symbols}
</div>"""


def _build_ic_table(all_results: List[FactorStudyResults]) -> str:
    # 检测是否使用了 benchmark
    benchmark = None
    for r in all_results:
        if r.config.benchmark_symbol:
            benchmark = r.config.benchmark_symbol
            break

    ret_label = f"Excess Return (vs {benchmark})" if benchmark else "Forward Return"

    rows = ""
    for r in all_results:
        for ic in r.ic_results:
            sig_class = ' class="sig"' if abs(ic.ic_ir) >= 0.5 else ""
            rows += f"""<tr>
                <td style="text-align:left">{ic.factor_name}</td>
                <td>{ic.horizon}d</td>
                <td{sig_class}>{ic.mean_ic:.4f}</td>
                <td>{ic.std_ic:.4f}</td>
                <td{sig_class}>{ic.ic_ir:.2f}</td>
                <td>{ic.ic_hit_rate:.1%}</td>
                <td>{ic.top_bottom_spread:.4f}</td>
            </tr>"""

    return f"""<p style="color:#888;font-size:12px;">收益类型: {ret_label}</p>
<table>
    <thead><tr>
        <th>因子</th><th>Horizon</th><th>Mean IC</th>
        <th>Std IC</th><th>IC_IR</th><th>Hit%</th><th>Q5-Q1</th>
    </tr></thead>
    <tbody>{rows}</tbody>
</table>"""


def _build_decay_chart(all_results: List[FactorStudyResults]) -> str:
    datasets = []
    colors = ["#ffd700", "#4fc3f7", "#ff7043", "#66bb6a", "#ab47bc", "#26c6da"]

    for i, r in enumerate(all_results):
        if r.ic_decay and r.ic_decay.horizons:
            color = colors[i % len(colors)]
            datasets.append(f"""{{
                label: '{r.ic_decay.factor_name}',
                data: {r.ic_decay.mean_ics},
                borderColor: '{color}',
                borderWidth: 2,
                pointRadius: 4,
                fill: false,
            }}""")

    labels = "[]"
    if all_results and all_results[0].ic_decay:
        labels = str(all_results[0].ic_decay.horizons)

    return f"""
new Chart(document.getElementById('decayChart'), {{
    type: 'line',
    data: {{
        labels: {labels},
        datasets: [{','.join(datasets)}]
    }},
    options: {{
        responsive: true,
        plugins: {{ legend: {{ labels: {{ color: '#e0e0e0' }} }} }},
        scales: {{
            x: {{ title: {{ display: true, text: 'Horizon (days)', color: '#888' }}, ticks: {{ color: '#888' }}, grid: {{ color: '#333' }} }},
            y: {{ title: {{ display: true, text: 'Mean IC', color: '#888' }}, ticks: {{ color: '#888' }}, grid: {{ color: '#333' }} }}
        }}
    }}
}});"""


def _build_quantile_chart(all_results: List[FactorStudyResults]) -> str:
    # 取第一个因子的最长 horizon 分位数
    labels = []
    data = []

    for r in all_results:
        if r.ic_results:
            longest = r.ic_results[-1]
            if longest.quantile_returns:
                for q in sorted(longest.quantile_returns.keys()):
                    labels.append(f"Q{q}")
                    data.append(round(longest.quantile_returns[q], 6))
                break

    if not labels:
        labels = ["Q1", "Q2", "Q3", "Q4", "Q5"]
        data = [0, 0, 0, 0, 0]

    # 检测是否使用了 benchmark
    chart_label = "Mean Forward Return"
    for r in all_results:
        if r.config.benchmark_symbol:
            chart_label = f"Mean Excess Return (vs {r.config.benchmark_symbol})"
            break

    bg_colors = [
        "'#f44336'" if d < 0 else "'#4caf50'" for d in data
    ]

    return f"""
new Chart(document.getElementById('quantileChart'), {{
    type: 'bar',
    data: {{
        labels: {labels},
        datasets: [{{
            label: '{chart_label}',
            data: {data},
            backgroundColor: [{','.join(bg_colors)}],
        }}]
    }},
    options: {{
        responsive: true,
        plugins: {{ legend: {{ labels: {{ color: '#e0e0e0' }} }} }},
        scales: {{
            x: {{ ticks: {{ color: '#888' }}, grid: {{ color: '#333' }} }},
            y: {{ title: {{ display: true, text: 'Mean Return', color: '#888' }}, ticks: {{ color: '#888' }}, grid: {{ color: '#333' }} }}
        }}
    }}
}});"""


def _build_event_table(all_results: List[FactorStudyResults]) -> str:
    """构建 IS 事件表"""
    all_events = []
    for r in all_results:
        all_events.extend(r.event_results)
    return _build_event_table_from_list(all_events)
