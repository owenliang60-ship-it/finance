"""
回测报告生成 — 文本 + HTML + CSV 导出
"""

import logging
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import List, Optional, Tuple

import pandas as pd

from backtest.config import BacktestConfig
from backtest.metrics import BacktestMetrics

logger = logging.getLogger(__name__)

_PROJECT_ROOT = Path(__file__).parent.parent
_BACKTEST_DIR = _PROJECT_ROOT / "data" / "backtest"


def print_metrics(
    metrics: BacktestMetrics,
    config: BacktestConfig,
    title: str = "RS 动量回测结果",
):
    """
    打印绩效指标到 stdout

    格式化输出策略配置 + 完整指标表
    """
    print(f"\n{'='*60}")
    print(f"  {title}")
    print(f"{'='*60}")
    print(f"  市场: {config.market} | 方法: RS-{config.rs_method}")
    print(f"  Top N: {config.top_n} | Buffer: {config.sell_buffer}")
    print(f"  频率: {config.rebalance_freq} | 权重: {config.weighting}")
    print(f"  成本: {config.transaction_cost_bps} bps/边")
    print(f"  基准: {config.benchmark_symbol or 'N/A'}")
    print(f"{'─'*60}")

    m = metrics
    print(f"  {'总收益率':<16} {m.total_return:>10.2%}")
    print(f"  {'年化收益率 (CAGR)':<16} {m.cagr:>10.2%}")
    print(f"  {'年化波动率':<16} {m.annual_volatility:>10.2%}")
    print(f"  {'最大回撤':<16} {m.max_drawdown:>10.2%}")
    print(f"  {'回撤持续天数':<16} {m.max_dd_duration:>10d}")
    print(f"{'─'*60}")
    print(f"  {'Sharpe Ratio':<16} {m.sharpe_ratio:>10.4f}")
    print(f"  {'Sortino Ratio':<16} {m.sortino_ratio:>10.4f}")
    print(f"  {'Calmar Ratio':<16} {m.calmar_ratio:>10.4f}")
    print(f"{'─'*60}")
    print(f"  {'Alpha':<16} {m.alpha:>10.2%}")
    print(f"  {'Beta':<16} {m.beta:>10.4f}")
    print(f"  {'信息比率':<16} {m.information_ratio:>10.4f}")
    print(f"  {'跟踪误差':<16} {m.tracking_error:>10.2%}")
    print(f"{'─'*60}")
    print(f"  {'年化换手率':<16} {m.annual_turnover:>10.2%}")
    print(f"  {'总交易成本':<16} ${m.total_costs:>10,.2f}")
    print(f"  {'总交易笔数':<16} {m.n_trades:>10d}")
    print(f"  {'胜率 (日)':<16} {m.win_rate:>10.2%}")
    print(f"  {'回测天数':<16} {m.n_days:>10d}")
    print(f"{'='*60}\n")


def print_sweep_summary(df: pd.DataFrame, top_k: int = 10):
    """打印参数扫描 Top K 结果"""
    print(f"\n{'='*80}")
    print(f"  参数扫描结果 — Top {top_k} by Sharpe Ratio")
    print(f"{'='*80}")

    cols = ["label", "sharpe_ratio", "cagr", "max_drawdown",
            "sortino_ratio", "annual_turnover", "n_trades"]
    display_cols = [c for c in cols if c in df.columns]

    top = df.head(top_k)[display_cols].copy()

    # 格式化
    for c in ["sharpe_ratio", "sortino_ratio"]:
        if c in top.columns:
            top[c] = top[c].map(lambda x: f"{x:.4f}")
    for c in ["cagr", "max_drawdown", "annual_turnover"]:
        if c in top.columns:
            top[c] = top[c].map(lambda x: f"{x:.2%}")

    print(top.to_string(index=True))
    print(f"{'='*80}\n")


def export_sweep_csv(df: pd.DataFrame, market: str) -> Path:
    """
    导出参数扫描结果到 CSV

    Returns:
        CSV 文件路径
    """
    _BACKTEST_DIR.mkdir(parents=True, exist_ok=True)
    date_str = datetime.now().strftime("%Y%m%d")
    path = _BACKTEST_DIR / f"sweep_{market}_{date_str}.csv"
    df.to_csv(path, index=False)
    logger.info(f"扫描结果已导出: {path}")
    return path


def generate_html_report(
    nav_series: List[Tuple[str, float]],
    benchmark_nav: Optional[List[Tuple[str, float]]],
    metrics: BacktestMetrics,
    config: BacktestConfig,
    sweep_df: Optional[pd.DataFrame] = None,
) -> str:
    """
    生成 HTML 报告

    包含: 配置摘要、绩效表、净值曲线 (Chart.js)、参数排行榜

    Returns:
        HTML 字符串
    """
    m = metrics
    now = datetime.now().strftime("%Y-%m-%d %H:%M")

    # 净值数据
    nav_dates = [d for d, _ in nav_series]
    nav_values = [round(v, 2) for _, v in nav_series]

    bm_section = ""
    if benchmark_nav:
        bm_dates = [d for d, _ in benchmark_nav]
        bm_values = [round(v, 2) for _, v in benchmark_nav]
        # 归一化到同一起点
        if bm_values and nav_values:
            scale = nav_values[0] / bm_values[0] if bm_values[0] != 0 else 1
            bm_values = [round(v * scale, 2) for v in bm_values]
        bm_section = f"""
            {{
                label: '{config.benchmark_symbol}',
                data: {bm_values},
                borderColor: '#888',
                borderWidth: 1.5,
                pointRadius: 0,
                fill: false,
            }},"""

    # 参数扫描排行榜
    sweep_html = ""
    if sweep_df is not None and not sweep_df.empty:
        top20 = sweep_df.head(20)
        rows = ""
        for _, row in top20.iterrows():
            rows += f"""
            <tr>
                <td>{row.get('label', '')}</td>
                <td>{row.get('sharpe_ratio', 0):.4f}</td>
                <td>{row.get('cagr', 0):.2%}</td>
                <td>{row.get('max_drawdown', 0):.2%}</td>
                <td>{row.get('sortino_ratio', 0):.4f}</td>
                <td>{row.get('annual_turnover', 0):.2%}</td>
            </tr>"""
        sweep_html = f"""
        <h2>参数扫描 Top 20</h2>
        <table>
            <thead>
                <tr>
                    <th>参数组合</th><th>Sharpe</th><th>CAGR</th>
                    <th>MaxDD</th><th>Sortino</th><th>换手率</th>
                </tr>
            </thead>
            <tbody>{rows}</tbody>
        </table>"""

    html = f"""<!DOCTYPE html>
<html lang="zh">
<head>
<meta charset="UTF-8">
<title>RS 动量回测报告 — {config.label()}</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
<style>
    body {{ font-family: -apple-system, sans-serif; max-width: 1200px; margin: auto; padding: 20px; background: #1a1a2e; color: #e0e0e0; }}
    h1 {{ color: #ffd700; }} h2 {{ color: #4fc3f7; margin-top: 30px; }}
    table {{ border-collapse: collapse; width: 100%; margin: 15px 0; }}
    th, td {{ border: 1px solid #333; padding: 8px 12px; text-align: right; }}
    th {{ background: #2a2a4a; color: #ffd700; }}
    tr:nth-child(even) {{ background: #1e1e3a; }}
    .config {{ background: #2a2a4a; padding: 15px; border-radius: 8px; margin: 15px 0; }}
    .metric-grid {{ display: grid; grid-template-columns: 1fr 1fr; gap: 15px; }}
    .metric-box {{ background: #2a2a4a; padding: 15px; border-radius: 8px; }}
    .metric-box h3 {{ color: #ffd700; margin: 0 0 10px 0; }}
    .positive {{ color: #4caf50; }} .negative {{ color: #f44336; }}
    canvas {{ background: #1e1e3a; border-radius: 8px; margin: 15px 0; }}
</style>
</head>
<body>
<h1>RS 动量回测报告</h1>
<p>生成时间: {now}</p>

<div class="config">
    <strong>配置:</strong>
    市场={config.market} | 方法=RS-{config.rs_method} |
    Top {config.top_n} | Buffer={config.sell_buffer} |
    频率={config.rebalance_freq} | 权重={config.weighting} |
    成本={config.transaction_cost_bps}bps
</div>

<div class="metric-grid">
    <div class="metric-box">
        <h3>收益</h3>
        <table>
            <tr><td>总收益率</td><td class="{'positive' if m.total_return >= 0 else 'negative'}">{m.total_return:.2%}</td></tr>
            <tr><td>CAGR</td><td class="{'positive' if m.cagr >= 0 else 'negative'}">{m.cagr:.2%}</td></tr>
            <tr><td>胜率 (日)</td><td>{m.win_rate:.2%}</td></tr>
        </table>
    </div>
    <div class="metric-box">
        <h3>风险</h3>
        <table>
            <tr><td>年化波动率</td><td>{m.annual_volatility:.2%}</td></tr>
            <tr><td>最大回撤</td><td class="negative">{m.max_drawdown:.2%}</td></tr>
            <tr><td>回撤持续天数</td><td>{m.max_dd_duration}</td></tr>
        </table>
    </div>
    <div class="metric-box">
        <h3>风险调整</h3>
        <table>
            <tr><td>Sharpe</td><td>{m.sharpe_ratio:.4f}</td></tr>
            <tr><td>Sortino</td><td>{m.sortino_ratio:.4f}</td></tr>
            <tr><td>Calmar</td><td>{m.calmar_ratio:.4f}</td></tr>
        </table>
    </div>
    <div class="metric-box">
        <h3>交易</h3>
        <table>
            <tr><td>年化换手率</td><td>{m.annual_turnover:.2%}</td></tr>
            <tr><td>总成本</td><td>${m.total_costs:,.2f}</td></tr>
            <tr><td>交易笔数</td><td>{m.n_trades}</td></tr>
        </table>
    </div>
</div>

<h2>净值曲线</h2>
<canvas id="navChart" width="1160" height="400"></canvas>

{sweep_html}

<script>
new Chart(document.getElementById('navChart'), {{
    type: 'line',
    data: {{
        labels: {nav_dates},
        datasets: [
            {{
                label: 'RS 动量策略',
                data: {nav_values},
                borderColor: '#ffd700',
                borderWidth: 2,
                pointRadius: 0,
                fill: false,
            }},{bm_section}
        ]
    }},
    options: {{
        responsive: true,
        plugins: {{ legend: {{ labels: {{ color: '#e0e0e0' }} }} }},
        scales: {{
            x: {{
                ticks: {{ color: '#888', maxTicksLimit: 12 }},
                grid: {{ color: '#333' }},
            }},
            y: {{
                ticks: {{ color: '#888' }},
                grid: {{ color: '#333' }},
            }}
        }}
    }}
}});
</script>
</body>
</html>"""

    return html


def save_html_report(html: str, config: BacktestConfig) -> Path:
    """保存 HTML 报告到文件"""
    _BACKTEST_DIR.mkdir(parents=True, exist_ok=True)
    date_str = datetime.now().strftime("%Y%m%d")
    path = _BACKTEST_DIR / f"report_{config.market}_{date_str}.html"
    path.write_text(html, encoding="utf-8")
    logger.info(f"HTML 报告已保存: {path}")
    return path
