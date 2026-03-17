"""
择时回测报告 — 文本 + HTML

文本: 聚合统计 + Top/Bottom 10 个股
HTML: 信号对比表 + NAV 曲线 + 个股散点图 + 详细表格
"""

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import List

from backtest.timing.runner import AggregateResult

logger = logging.getLogger(__name__)

_PROJECT_ROOT = Path(__file__).parent.parent.parent
_OUTPUT_DIR = _PROJECT_ROOT / "data" / "timing"


def print_aggregate(result: AggregateResult):
    """打印单个信号的聚合结果"""
    print("")
    print("=" * 70)
    print("  Timing Study: %s" % result.signal_name)
    print("=" * 70)
    print("  Params: %s" % result.signal_params)
    print("  Stocks: %d (filtered by n_trades >= 2)" % result.n_stocks)
    print("-" * 70)
    print("  Mean Excess CAGR:    %+.4f (%+.2f%%)" % (
        result.mean_excess_cagr, result.mean_excess_cagr * 100))
    print("  Std Excess CAGR:      %.4f" % result.std_excess_cagr)
    print("  t-stat:              %+.4f" % result.t_stat)
    print("  p-value:              %.6f %s" % (
        result.p_value, "**" if result.p_value < 0.05 else ""))
    print("  Hit Rate:             %.1f%%" % (result.hit_rate * 100))
    print("  Mean Sharpe Diff:    %+.4f" % result.mean_sharpe_diff)
    print("  Mean Time in Market:  %.1f%%" % (result.mean_time_in_market * 100))
    print("  Mean # Trades:        %.1f" % result.mean_n_trades)

    # 指数结果
    if result.index_results:
        print("")
        print("-" * 70)
        print("  Index Results:")
        print("  %-8s %+10s %+10s %10s %6s %8s" % (
            "Symbol", "ExcessCAGR", "SharpeDif", "MDD_Diff", "Trades", "InMkt%"))
        print("  " + "-" * 62)
        for r in result.index_results:
            print("  %-8s %+10.4f %+10.4f %+10.4f %6d %7.1f%%" % (
                r.symbol, r.excess_cagr, r.sharpe_diff, r.mdd_diff,
                r.n_trades, r.time_in_market * 100))

    # Top 10 / Bottom 10
    if result.per_stock_results:
        sorted_results = sorted(
            result.per_stock_results,
            key=lambda r: r.excess_cagr,
            reverse=True,
        )

        print("")
        print("-" * 70)
        print("  Top 10 (Best Excess CAGR):")
        _print_stock_table(sorted_results[:10])

        print("")
        print("  Bottom 10 (Worst Excess CAGR):")
        _print_stock_table(sorted_results[-10:])

    print("=" * 70)
    print("")


def _print_stock_table(results: list):
    """打印个股表格"""
    print("  %-8s %+10s %+10s %+10s %6s %8s %10s %10s" % (
        "Symbol", "ExcessCAGR", "SharpeDif", "MDD_Diff",
        "Trades", "InMkt%", "Strat_CAGR", "BH_CAGR"))
    print("  " + "-" * 78)
    for r in results:
        print("  %-8s %+10.4f %+10.4f %+10.4f %6d %7.1f%% %+10.4f %+10.4f" % (
            r.symbol, r.excess_cagr, r.sharpe_diff, r.mdd_diff,
            r.n_trades, r.time_in_market * 100,
            r.strategy_metrics.cagr, r.buyhold_metrics.cagr))


def generate_html_report(results: List[AggregateResult]) -> str:
    """
    生成 HTML 报告

    包含:
    - 信号对比表 (按 t-stat 排序)
    - 指数 NAV 曲线 (策略 vs B&H)
    - 个股散点图 (excess_cagr vs sharpe_diff)
    - 全部个股详细表格
    """
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    signal_names = [r.signal_name for r in results]
    title = " vs ".join(signal_names)

    # ── 信号对比表 ──
    comparison_rows = ""
    sorted_results = sorted(results, key=lambda r: abs(r.t_stat), reverse=True)
    for r in sorted_results:
        sig_class = ' class="sig"' if r.p_value < 0.05 else ""
        comparison_rows += (
            "<tr>"
            '<td style="text-align:left">%s</td>'
            "<td>%s</td>"
            "<td>%d</td>"
            "<td%s>%+.4f</td>"
            "<td>%.4f</td>"
            "<td%s>%+.4f</td>"
            "<td>%.6f</td>"
            "<td>%.1f%%</td>"
            "<td%s>%+.4f</td>"
            "<td>%.1f%%</td>"
            "<td>%.1f</td>"
            "</tr>"
        ) % (
            r.signal_name,
            _params_label(r.signal_params),
            r.n_stocks,
            sig_class, r.mean_excess_cagr,
            r.std_excess_cagr,
            sig_class, r.t_stat,
            r.p_value,
            r.hit_rate * 100,
            sig_class, r.mean_sharpe_diff,
            r.mean_time_in_market * 100,
            r.mean_n_trades,
        )

    # ── 指数 NAV 曲线数据 ──
    nav_charts_js = _build_index_nav_charts(results)

    # ── 个股散点图数据 ──
    scatter_js = _build_scatter_chart(results)

    # ── 指数结果表 ──
    index_rows = ""
    for agg in results:
        for r in agg.index_results:
            cls = ' class="positive"' if r.excess_cagr > 0 else ' class="negative"'
            index_rows += (
                "<tr>"
                '<td style="text-align:left">%s</td>'
                '<td style="text-align:left">%s</td>'
                "<td%s>%+.2f%%</td>"
                "<td>%+.4f</td>"
                "<td>%+.4f</td>"
                "<td>%d</td>"
                "<td>%.1f%%</td>"
                "<td>%+.2f%%</td>"
                "<td>%+.2f%%</td>"
                "</tr>"
            ) % (
                r.symbol, agg.signal_name,
                cls, r.excess_cagr * 100,
                r.sharpe_diff, r.mdd_diff,
                r.n_trades, r.time_in_market * 100,
                r.strategy_metrics.cagr * 100,
                r.buyhold_metrics.cagr * 100,
            )

    # ── 全部个股详细表 ──
    detail_tables = _build_detail_tables(results)

    html = """<!DOCTYPE html>
<html lang="zh">
<head>
<meta charset="UTF-8">
<title>Timing Study Report - %(title)s</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
<style>
    body { font-family: -apple-system, sans-serif; max-width: 1400px; margin: auto; padding: 20px; background: #1a1a2e; color: #e0e0e0; }
    h1 { color: #ffd700; } h2 { color: #4fc3f7; margin-top: 30px; } h3 { color: #ff9800; }
    table { border-collapse: collapse; width: 100%%; margin: 15px 0; }
    th, td { border: 1px solid #333; padding: 6px 10px; text-align: right; font-size: 13px; }
    th { background: #2a2a4a; color: #ffd700; }
    tr:nth-child(even) { background: #1e1e3a; }
    .sig { color: #4caf50; font-weight: bold; }
    .positive { color: #4caf50; } .negative { color: #f44336; }
    .config { background: #2a2a4a; padding: 15px; border-radius: 8px; margin: 15px 0; }
    canvas { background: #1e1e3a; border-radius: 8px; margin: 10px 0; }
    .chart-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 20px; }
    details { margin: 10px 0; }
    summary { cursor: pointer; color: #4fc3f7; font-size: 14px; }
</style>
</head>
<body>
<h1>Timing Signal Backtest Report</h1>
<p>Generated: %(now)s</p>

<h2>Signal Comparison</h2>
<p style="color:#888;font-size:12px;">Sorted by |t-stat|. Green = statistically significant (p &lt; 0.05).</p>
<table>
    <thead><tr>
        <th>Signal</th><th>Params</th><th>N</th>
        <th>Mean Excess CAGR</th><th>Std</th><th>t-stat</th><th>p-value</th>
        <th>Hit Rate</th><th>Mean Sharpe Diff</th><th>Time in Market</th><th>Avg Trades</th>
    </tr></thead>
    <tbody>%(comparison_rows)s</tbody>
</table>

<h2>Index Results</h2>
<table>
    <thead><tr>
        <th>Index</th><th>Signal</th><th>Excess CAGR</th>
        <th>Sharpe Diff</th><th>MDD Diff</th><th>Trades</th>
        <th>Time in Mkt</th><th>Strategy CAGR</th><th>B&H CAGR</th>
    </tr></thead>
    <tbody>%(index_rows)s</tbody>
</table>

<h2>Index NAV Curves</h2>
<div class="chart-grid" id="navChartContainer"></div>

<h2>Excess CAGR vs Sharpe Diff (Per Stock)</h2>
<canvas id="scatterChart" width="1360" height="500"></canvas>

%(detail_tables)s

<script>
%(nav_charts_js)s
%(scatter_js)s
</script>
</body>
</html>""" % {
        "title": title,
        "now": now,
        "comparison_rows": comparison_rows,
        "index_rows": index_rows,
        "detail_tables": detail_tables,
        "nav_charts_js": nav_charts_js,
        "scatter_js": scatter_js,
    }

    return html


def save_html_report(html: str, signal_names: List[str]) -> Path:
    """保存 HTML 报告"""
    _OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    date_str = datetime.now().strftime("%Y%m%d")
    name = "_".join(signal_names)[:60]
    path = _OUTPUT_DIR / ("report_%s_%s.html" % (name, date_str))
    path.write_text(html, encoding="utf-8")
    logger.info("HTML report saved: %s", path)
    return path


# ── 内部构建函数 ──────────────────────────────────────────

def _params_label(params: dict) -> str:
    """参数简短标签"""
    parts = []
    for k, v in params.items():
        parts.append("%s=%s" % (k, v))
    return ", ".join(parts)


def _build_index_nav_charts(results: List[AggregateResult]) -> str:
    """构建指数 NAV 曲线的 Chart.js 代码"""
    js_blocks = []
    chart_idx = 0

    # 先注入动态创建 canvas 的代码
    canvas_setup = "var container = document.getElementById('navChartContainer');\n"

    for agg in results:
        for idx_result in agg.index_results:
            canvas_id = "navChart%d" % chart_idx
            chart_title = "%s - %s" % (idx_result.symbol, agg.signal_name)

            # 创建 canvas
            canvas_setup += (
                "var div%(idx)d = document.createElement('div');\n"
                "div%(idx)d.innerHTML = '<h3>%(title)s</h3>"
                "<canvas id=\"%(cid)s\" width=\"600\" height=\"350\"></canvas>';\n"
                "container.appendChild(div%(idx)d);\n"
            ) % {"idx": chart_idx, "title": chart_title, "cid": canvas_id}

            # 采样数据 (每 5 天取一个点，避免数据过多)
            step = max(1, len(idx_result.strategy_nav) // 200)
            strat_dates = [d for d, _ in idx_result.strategy_nav[::step]]
            strat_vals = [round(v, 2) for _, v in idx_result.strategy_nav[::step]]
            bh_vals = [round(v, 2) for _, v in idx_result.buyhold_nav[::step]]

            js_blocks.append("""
new Chart(document.getElementById('%(cid)s'), {
    type: 'line',
    data: {
        labels: %(dates)s,
        datasets: [
            {
                label: 'Strategy (%(signal)s)',
                data: %(strat)s,
                borderColor: '#ffd700',
                borderWidth: 2,
                pointRadius: 0,
                fill: false,
            },
            {
                label: 'Buy & Hold',
                data: %(bh)s,
                borderColor: '#888',
                borderWidth: 1.5,
                pointRadius: 0,
                fill: false,
            }
        ]
    },
    options: {
        responsive: true,
        plugins: { legend: { labels: { color: '#e0e0e0' } } },
        scales: {
            x: { ticks: { color: '#888', maxTicksLimit: 10 }, grid: { color: '#333' } },
            y: { ticks: { color: '#888' }, grid: { color: '#333' } }
        }
    }
});""" % {
                "cid": canvas_id,
                "signal": agg.signal_name,
                "dates": json.dumps(strat_dates),
                "strat": json.dumps(strat_vals),
                "bh": json.dumps(bh_vals),
            })

            chart_idx += 1

    return canvas_setup + "\n".join(js_blocks)


def _build_scatter_chart(results: List[AggregateResult]) -> str:
    """构建个股散点图 (excess_cagr vs sharpe_diff)"""
    colors = ["#ffd700", "#4fc3f7", "#ff7043", "#66bb6a", "#ab47bc", "#26c6da"]
    datasets = []

    for i, agg in enumerate(results):
        points = []
        for r in agg.per_stock_results:
            points.append({"x": round(r.excess_cagr * 100, 2),
                           "y": round(r.sharpe_diff, 4)})
        color = colors[i % len(colors)]
        datasets.append("""{
            label: '%s',
            data: %s,
            backgroundColor: '%s',
            pointRadius: 3,
        }""" % (agg.signal_name, json.dumps(points), color))

    return """
new Chart(document.getElementById('scatterChart'), {
    type: 'scatter',
    data: {
        datasets: [%s]
    },
    options: {
        responsive: true,
        plugins: { legend: { labels: { color: '#e0e0e0' } } },
        scales: {
            x: { title: { display: true, text: 'Excess CAGR (%%)', color: '#888' }, ticks: { color: '#888' }, grid: { color: '#333' } },
            y: { title: { display: true, text: 'Sharpe Diff', color: '#888' }, ticks: { color: '#888' }, grid: { color: '#333' } }
        }
    }
});""" % ",".join(datasets)


def _build_detail_tables(results: List[AggregateResult]) -> str:
    """构建每个信号的个股详细表格 (折叠)"""
    html_parts = []

    for agg in results:
        sorted_stocks = sorted(
            agg.per_stock_results,
            key=lambda r: r.excess_cagr,
            reverse=True,
        )

        rows = ""
        for r in sorted_stocks:
            cls = "positive" if r.excess_cagr > 0 else "negative"
            rows += (
                "<tr>"
                '<td style="text-align:left">%s</td>'
                '<td class="%s">%+.2f%%</td>'
                "<td>%+.4f</td>"
                "<td>%+.4f</td>"
                "<td>%d</td>"
                "<td>%.1f%%</td>"
                "<td>%+.2f%%</td>"
                "<td>%.4f</td>"
                "<td>%+.2f%%</td>"
                "<td>%.4f</td>"
                "<td>%.2f%%</td>"
                "</tr>"
            ) % (
                r.symbol,
                cls, r.excess_cagr * 100,
                r.sharpe_diff, r.mdd_diff,
                r.n_trades, r.time_in_market * 100,
                r.strategy_metrics.cagr * 100, r.strategy_metrics.sharpe_ratio,
                r.buyhold_metrics.cagr * 100, r.buyhold_metrics.sharpe_ratio,
                r.strategy_metrics.max_drawdown * 100,
            )

        html_parts.append("""
<details>
<summary>%s - All %d Stocks (click to expand)</summary>
<table>
    <thead><tr>
        <th>Symbol</th><th>Excess CAGR</th><th>Sharpe Diff</th><th>MDD Diff</th>
        <th>Trades</th><th>Time in Mkt</th>
        <th>Strat CAGR</th><th>Strat Sharpe</th>
        <th>B&H CAGR</th><th>B&H Sharpe</th><th>Strat MDD</th>
    </tr></thead>
    <tbody>%s</tbody>
</table>
</details>""" % (agg.signal_name, len(sorted_stocks), rows))

    return "\n".join(html_parts)
