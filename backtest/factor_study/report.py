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

    # IC 汇总
    if results.ic_results:
        print(f"\n{'─'*70}")
        print("  Track 1: IC 分析")
        print(f"{'─'*70}")
        print(f"  {'Horizon':>8} {'Mean IC':>10} {'Std IC':>10} "
              f"{'IC_IR':>8} {'Hit%':>8} {'Q5-Q1':>10}")
        print(f"  {'─'*8} {'─'*10} {'─'*10} {'─'*8} {'─'*8} {'─'*10}")

        for ic in results.ic_results:
            print(f"  {ic.horizon:>8d} {ic.mean_ic:>10.4f} {ic.std_ic:>10.4f} "
                  f"{ic.ic_ir:>8.2f} {ic.ic_hit_rate:>7.1%} {ic.top_bottom_spread:>10.4f}")

    # 分位数收益 (最长 horizon)
    if results.ic_results:
        longest = results.ic_results[-1]
        if longest.quantile_returns:
            print(f"\n  分位数收益 (horizon={longest.horizon}d):")
            for q in sorted(longest.quantile_returns.keys()):
                ret = longest.quantile_returns[q]
                bar = "█" * max(1, int(abs(ret) * 500))
                sign = "+" if ret >= 0 else ""
                print(f"    Q{q}: {sign}{ret:.4f}  {bar}")

    # 事件研究 Top 10
    if results.event_results:
        print(f"\n{'─'*70}")
        print("  Track 2: 事件研究 (Top 10 by |t-stat|)")
        print(f"{'─'*70}")

        # 按 |t_stat| 排序
        sorted_events = sorted(
            results.event_results,
            key=lambda x: abs(x.t_stat),
            reverse=True,
        )[:10]

        print(f"  {'Signal':<30} {'H':>4} {'N':>6} {'Mean':>8} "
              f"{'Hit%':>7} {'t-stat':>8} {'p-val':>8}")
        print(f"  {'─'*30} {'─'*4} {'─'*6} {'─'*8} {'─'*7} {'─'*8} {'─'*8}")

        for ev in sorted_events:
            sig = "**" if ev.p_value < 0.05 else "  "
            print(f"  {ev.signal_label:<30} {ev.horizon:>4d} {ev.n_events:>6d} "
                  f"{ev.mean_return:>8.4f} {ev.hit_rate:>6.1%} "
                  f"{ev.t_stat:>8.2f} {ev.p_value:>7.4f} {sig}")

    print(f"{'='*70}\n")


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
        ev_rows = []
        for ev in results.event_results:
            ev_rows.append({
                "factor": ev.factor_name,
                "signal": ev.signal_label,
                "horizon": ev.horizon,
                "n_events": ev.n_events,
                "mean_return": ev.mean_return,
                "median_return": ev.median_return,
                "hit_rate": ev.hit_rate,
                "t_stat": ev.t_stat,
                "p_value": ev.p_value,
            })

        ev_df = pd.DataFrame(ev_rows)
        ev_path = _OUTPUT_DIR / f"events_{name}_{date_str}.csv"
        ev_df.to_csv(ev_path, index=False)
        logger.info(f"事件研究结果已导出: {ev_path}")

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

    # IC 表格
    ic_table_html = _build_ic_table(all_results)

    # IC 衰减曲线数据
    decay_chart_js = _build_decay_chart(all_results)

    # 分位数图表
    quantile_chart_js = _build_quantile_chart(all_results)

    # 事件研究表格
    event_table_html = _build_event_table(all_results)

    html = f"""<!DOCTYPE html>
<html lang="zh">
<head>
<meta charset="UTF-8">
<title>因子研究报告 — {title}</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
<style>
    body {{ font-family: -apple-system, sans-serif; max-width: 1400px; margin: auto; padding: 20px; background: #1a1a2e; color: #e0e0e0; }}
    h1 {{ color: #ffd700; }} h2 {{ color: #4fc3f7; margin-top: 30px; }}
    table {{ border-collapse: collapse; width: 100%; margin: 15px 0; }}
    th, td {{ border: 1px solid #333; padding: 6px 10px; text-align: right; font-size: 13px; }}
    th {{ background: #2a2a4a; color: #ffd700; }}
    tr:nth-child(even) {{ background: #1e1e3a; }}
    .sig {{ color: #4caf50; font-weight: bold; }}
    .config {{ background: #2a2a4a; padding: 15px; border-radius: 8px; margin: 15px 0; }}
    .chart-row {{ display: grid; grid-template-columns: 1fr 1fr; gap: 20px; }}
    canvas {{ background: #1e1e3a; border-radius: 8px; margin: 10px 0; }}
</style>
</head>
<body>
<h1>因子研究报告</h1>
<p>生成时间: {now} | 因子: {title}</p>

{_build_config_section(all_results)}

<h2>Track 1: IC 分析</h2>
{ic_table_html}

<div class="chart-row">
    <div>
        <h2>IC 衰减曲线</h2>
        <canvas id="decayChart" width="600" height="350"></canvas>
    </div>
    <div>
        <h2>分位数收益</h2>
        <canvas id="quantileChart" width="600" height="350"></canvas>
    </div>
</div>

<h2>Track 2: 事件研究 (显著信号)</h2>
{event_table_html}

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

    return f"""<table>
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

    bg_colors = [
        "'#f44336'" if d < 0 else "'#4caf50'" for d in data
    ]

    return f"""
new Chart(document.getElementById('quantileChart'), {{
    type: 'bar',
    data: {{
        labels: {labels},
        datasets: [{{
            label: 'Mean Forward Return',
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
    # 合并所有因子的事件结果，按 |t_stat| 排序
    all_events = []
    for r in all_results:
        all_events.extend(r.event_results)

    # 只展示显著的 (p < 0.10) 或 Top 30
    significant = [e for e in all_events if e.p_value < 0.10 and e.n_events >= 5]
    significant.sort(key=lambda x: abs(x.t_stat), reverse=True)
    display = significant[:30] if significant else sorted(
        all_events, key=lambda x: abs(x.t_stat), reverse=True
    )[:20]

    rows = ""
    for ev in display:
        sig_class = ' class="sig"' if ev.p_value < 0.05 else ""
        star = "**" if ev.p_value < 0.01 else ("*" if ev.p_value < 0.05 else "")
        rows += f"""<tr>
            <td style="text-align:left">{ev.factor_name}</td>
            <td style="text-align:left">{ev.signal_label}</td>
            <td>{ev.horizon}d</td>
            <td>{ev.n_events}</td>
            <td{sig_class}>{ev.mean_return:.4f}</td>
            <td>{ev.median_return:.4f}</td>
            <td>{ev.hit_rate:.1%}</td>
            <td{sig_class}>{ev.t_stat:.2f}{star}</td>
            <td>{ev.p_value:.4f}</td>
        </tr>"""

    return f"""<table>
    <thead><tr>
        <th>因子</th><th>信号</th><th>Horizon</th>
        <th>N</th><th>Mean Ret</th><th>Median</th>
        <th>Hit%</th><th>t-stat</th><th>p-value</th>
    </tr></thead>
    <tbody>{rows}</tbody>
</table>"""
