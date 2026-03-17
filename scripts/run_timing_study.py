#!/usr/bin/env python3
"""
择时信号回测 — CLI 入口

用法:
    # 单信号研究
    python3 scripts/run_timing_study.py --signal MACD

    # 全信号对比
    python3 scripts/run_timing_study.py --signal all --html

    # 自定义日期范围
    python3 scripts/run_timing_study.py --signal RSI --start 2023-01-01 --end 2025-12-31

    # 指定标的
    python3 scripts/run_timing_study.py --signal MA_Cross --symbols AAPL,NVDA,MSFT --html
"""

import argparse
import logging
import sys
from pathlib import Path

# 项目根目录
_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))

from backtest.adapters.us_stocks import USStocksAdapter
from backtest.timing.runner import TimingStudyConfig, run_timing_study
from backtest.timing.signals import SIGNAL_REGISTRY
from backtest.timing.report import (
    print_aggregate,
    generate_html_report,
    save_html_report,
)


def main():
    parser = argparse.ArgumentParser(description="择时信号回测研究")
    parser.add_argument(
        "--signal", type=str, required=True,
        help="Signal name (MACD, RSI, MA_Cross, or 'all')",
    )
    parser.add_argument("--start", type=str, help="Start date (YYYY-MM-DD)")
    parser.add_argument("--end", type=str, help="End date (YYYY-MM-DD)")
    parser.add_argument("--html", action="store_true", help="Generate HTML report")
    parser.add_argument(
        "--symbols", type=str,
        help="Specific symbols (comma-separated, default: full pool + indices)",
    )
    parser.add_argument(
        "--indices", type=str, default="QQQ,SPY",
        help="Index symbols (comma-separated, default: QQQ,SPY)",
    )
    parser.add_argument("-v", "--verbose", action="store_true", help="Verbose logging")

    args = parser.parse_args()

    # 日志
    level = logging.DEBUG if args.verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    # 确定信号列表
    if args.signal.lower() == "all":
        signal_names = list(SIGNAL_REGISTRY.keys())
    else:
        signal_names = [args.signal]
        for name in signal_names:
            if name not in SIGNAL_REGISTRY:
                parser.error(
                    "Unknown signal: %s. Available: %s"
                    % (name, list(SIGNAL_REGISTRY.keys()))
                )

    # 解析参数
    symbols = args.symbols.split(",") if args.symbols else None
    indices = [s.strip() for s in args.indices.split(",")]

    # 创建适配器 (加载一次数据)
    adapter = USStocksAdapter(symbols=symbols)

    print("")
    print("=" * 60)
    print("  Timing Signal Backtest Study")
    print("=" * 60)
    print("  Signals: %s" % ", ".join(signal_names))
    print("  Indices: %s" % ", ".join(indices))
    if args.start:
        print("  Start: %s" % args.start)
    if args.end:
        print("  End: %s" % args.end)
    print("=" * 60)

    # 跑所有信号
    all_results = []
    for signal_name in signal_names:
        _, default_params = SIGNAL_REGISTRY[signal_name]

        config = TimingStudyConfig(
            signal_name=signal_name,
            signal_params=default_params,
            symbols=symbols,
            include_indices=indices,
            start_date=args.start,
            end_date=args.end,
        )

        print("")
        print("Running %s (%s)..." % (signal_name, default_params))
        result = run_timing_study(config, adapter)
        all_results.append(result)

        print_aggregate(result)

    # HTML 报告
    if args.html and all_results:
        html = generate_html_report(all_results)
        path = save_html_report(html, signal_names)
        print("HTML report: %s" % path)


if __name__ == "__main__":
    main()
