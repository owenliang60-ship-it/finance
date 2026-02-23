#!/usr/bin/env python3
"""
RS 动量回测引擎入口脚本

用法:
    # 单次回测
    python3 scripts/run_rs_backtest.py --market us_stocks --method B --top-n 10 --freq M

    # 参数扫描
    python3 scripts/run_rs_backtest.py --market us_stocks --sweep

    # 优化 (sweep + 稳健性 + walk-forward)
    python3 scripts/run_rs_backtest.py --market us_stocks --optimize

    # 币圈
    python3 scripts/run_rs_backtest.py --market crypto --method B --top-n 10 --freq W
    python3 scripts/run_rs_backtest.py --market crypto --optimize
"""

import argparse
import logging
import sys
from pathlib import Path

# 确保项目根目录在 sys.path
_PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(_PROJECT_ROOT))

from backtest.config import us_preset, crypto_preset
from backtest.engine import BacktestEngine
from backtest.sweep import ParameterSweep
from backtest.optimizer import ParamOptimizer
from backtest.report import (
    print_metrics,
    print_sweep_summary,
    export_sweep_csv,
    generate_html_report,
    save_html_report,
)


def setup_logging(verbose: bool = False):
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )


def run_single(args):
    """单次回测"""
    factory = crypto_preset if args.market == "crypto" else us_preset
    config = factory(
        rs_method=args.method,
        top_n=args.top_n,
        rebalance_freq=args.freq,
        sell_buffer=args.buffer,
        weighting=args.weighting,
        start_date=args.start_date,
        end_date=args.end_date,
    )

    print(f"\n启动回测: {config.label()}")
    engine = BacktestEngine(config)
    metrics = engine.run()

    print_metrics(metrics, config)

    # 生成 HTML
    if args.html:
        benchmark_nav = None
        if config.benchmark_symbol:
            benchmark_nav = engine.adapter.get_benchmark_nav(config.benchmark_symbol)
            if benchmark_nav:
                nav_series = engine.portfolio.nav_series()
                start = nav_series[0][0] if nav_series else ""
                end = nav_series[-1][0] if nav_series else ""
                benchmark_nav = [(d, v) for d, v in benchmark_nav if start <= d <= end]

        html = generate_html_report(
            nav_series=engine.portfolio.nav_series(),
            benchmark_nav=benchmark_nav,
            metrics=metrics,
            config=config,
        )
        path = save_html_report(html, config)
        print(f"HTML 报告: {path}")

    return metrics


def run_sweep(args):
    """参数扫描"""
    sweep = ParameterSweep(args.market)

    def progress(current, total, config):
        if current % 5 == 0 or current == 1:
            print(f"  [{current}/{total}] {config.label()}")

    print(f"\n启动参数扫描: {args.market} ({sweep.total_combinations()} 组合)")
    df = sweep.run(
        start_date=args.start_date,
        end_date=args.end_date,
        progress_callback=progress,
    )

    print_sweep_summary(df, top_k=15)

    # 导出 CSV
    csv_path = export_sweep_csv(df, args.market)
    print(f"CSV 导出: {csv_path}")

    # 稳健性排名
    optimizer = ParamOptimizer(args.market)
    robust_df = optimizer.rank_with_robustness(df)

    print("\n稳健性排名 Top 10:")
    display_cols = ["label", "sharpe_ratio", "robustness_score", "neighbor_count", "cagr", "max_drawdown"]
    display_cols = [c for c in display_cols if c in robust_df.columns]
    print(robust_df[display_cols].head(10).to_string(index=True))

    return df


def run_optimize(args):
    """完整优化: sweep + 稳健性 + walk-forward"""
    if args.market == "crypto":
        train_months, test_months, step_months = 6, 3, 3
    else:
        train_months, test_months, step_months = 36, 12, 12

    print(f"\n启动优化: {args.market}")
    print(f"  Walk-Forward: 训练 {train_months}月 | 测试 {test_months}月 | 步进 {step_months}月")

    optimizer = ParamOptimizer(args.market)
    result = optimizer.walk_forward(
        train_months=train_months,
        test_months=test_months,
        step_months=step_months,
    )

    # 输出结果
    print(f"\n{'='*60}")
    print(f"  Walk-Forward 验证结果")
    print(f"{'='*60}")

    for r in result.rounds:
        print(f"\n  第 {r.round_num} 轮:")
        print(f"    训练: {r.train_start} → {r.train_end}")
        print(f"    测试: {r.test_start} → {r.test_end}")
        print(f"    最优参数: {r.best_config_label}")
        print(f"    样本内  Sharpe={r.in_sample_sharpe:.4f}  CAGR={r.in_sample_cagr:.2%}")
        print(f"    样本外  Sharpe={r.out_sample_sharpe:.4f}  CAGR={r.out_sample_cagr:.2%}  MaxDD={r.out_sample_max_dd:.2%}")

    print(f"\n{'─'*60}")
    print(f"  平均样本内 Sharpe:  {result.avg_in_sample_sharpe:.4f}")
    print(f"  平均样本外 Sharpe:  {result.avg_out_sample_sharpe:.4f}")
    print(f"  平均样本外 CAGR:    {result.avg_out_sample_cagr:.2%}")
    print(f"  过拟合比率:         {result.overfit_ratio:.4f} {'⚠️ 严重过拟合' if result.overfit_ratio > 0.5 else '✓'}")
    print(f"  参数一致性:         {result.param_consistency:.2%}")

    if result.recommended_config:
        print(f"\n  推荐配置: {result.recommended_config.label()}")

    print(f"{'='*60}\n")

    return result


def main():
    parser = argparse.ArgumentParser(description="RS 动量回测引擎")
    parser.add_argument("--market", choices=["us_stocks", "crypto"], default="us_stocks")
    parser.add_argument("--method", choices=["B", "C"], default="B")
    parser.add_argument("--top-n", type=int, default=10)
    parser.add_argument("--freq", default="M", choices=["D", "3D", "W", "2W", "M"])
    parser.add_argument("--buffer", type=int, default=5)
    parser.add_argument("--weighting", choices=["equal", "rs_weighted"], default="equal")
    parser.add_argument("--start-date", type=str, default=None)
    parser.add_argument("--end-date", type=str, default=None)
    parser.add_argument("--sweep", action="store_true", help="参数扫描模式")
    parser.add_argument("--optimize", action="store_true", help="优化模式 (sweep + walk-forward)")
    parser.add_argument("--html", action="store_true", help="生成 HTML 报告")
    parser.add_argument("-v", "--verbose", action="store_true")

    args = parser.parse_args()
    setup_logging(args.verbose)

    if args.optimize:
        run_optimize(args)
    elif args.sweep:
        run_sweep(args)
    else:
        run_single(args)


if __name__ == "__main__":
    main()
