#!/usr/bin/env python3
"""
因子有效性研究 — CLI 入口

用法:
    # 单因子研究 (美股)
    python3 scripts/run_factor_study.py --market us_stocks --factor RS_Rating_B

    # 全因子扫描
    python3 scripts/run_factor_study.py --market us_stocks --all-factors

    # 币圈
    python3 scripts/run_factor_study.py --market crypto --factor RS_Rating_B

    # 自定义阈值
    python3 scripts/run_factor_study.py --market us_stocks --factor PMARP --thresholds 90,95,98

    # 指定日期范围
    python3 scripts/run_factor_study.py --market us_stocks --factor RS_Rating_B --start 2023-01-01 --end 2025-12-31
"""

import argparse
import logging
import sys
from pathlib import Path

# 项目根目录
_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))

from backtest.config import us_factor_study, crypto_factor_study
from backtest.factor_study import (
    FactorStudyRunner,
    get_factor,
    list_factors,
    build_custom_sweep,
)
from backtest.factor_study.report import (
    print_results,
    export_csv,
    generate_html_report,
    save_html_report,
)


def main():
    parser = argparse.ArgumentParser(description="因子有效性研究")
    parser.add_argument("--market", choices=["us_stocks", "crypto"], default="us_stocks",
                        help="市场 (默认: us_stocks)")
    parser.add_argument("--factor", type=str, help="因子名称 (如 RS_Rating_B)")
    parser.add_argument("--all-factors", action="store_true", help="扫描全部因子")
    parser.add_argument("--thresholds", type=str, help="自定义阈值 (逗号分隔, 如 90,95,98)")
    parser.add_argument("--benchmark", type=str,
                        help="基准 (逗号分隔, 默认: QQQ,POOL_AVG)")
    parser.add_argument("--horizons", type=str, help="前瞻窗口 (逗号分隔, 如 7,30,60)")
    parser.add_argument("--symbols", type=str, help="指定标的 (逗号分隔, 如 BTCUSDT,ETHUSDT)")
    parser.add_argument("--cache-dir", type=str, help="加密货币数据目录 (覆盖默认)")
    parser.add_argument("--start", type=str, help="起始日期 (YYYY-MM-DD)")
    parser.add_argument("--end", type=str, help="结束日期 (YYYY-MM-DD)")
    parser.add_argument("--freq", choices=["D", "W"], help="计算频率 (覆盖默认)")
    parser.add_argument("--html", action="store_true", help="生成 HTML 报告")
    parser.add_argument("--csv", action="store_true", help="导出 CSV")
    parser.add_argument("--list", action="store_true", help="列出所有可用因子")
    parser.add_argument("--no-oos", action="store_true", help="不做 IS/OOS 分割，全量数据作为单一样本")
    parser.add_argument("-v", "--verbose", action="store_true", help="详细日志")

    args = parser.parse_args()

    # 日志
    level = logging.DEBUG if args.verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    # 列出因子
    if args.list:
        print("可用因子:")
        for name in list_factors():
            f = get_factor(name)
            print(f"  {f.meta}")
        return

    # 确定因子列表
    if args.all_factors:
        factor_names = list_factors()
    elif args.factor:
        factor_names = [args.factor]
    else:
        parser.error("请指定 --factor <name> 或 --all-factors")
        return

    # 配置
    overrides = {}
    if args.benchmark:
        overrides["benchmark_symbols"] = [
            b.strip() for b in args.benchmark.split(",")
        ]
    if args.start:
        overrides["start_date"] = args.start
    if args.end:
        overrides["end_date"] = args.end
    if args.freq:
        overrides["computation_freq"] = args.freq
    if args.horizons:
        overrides["forward_horizons"] = [int(h) for h in args.horizons.split(",")]
    if args.no_oos:
        overrides["oos_fraction"] = 0.0

    if args.market == "crypto":
        config = crypto_factor_study(**overrides)
    else:
        config = us_factor_study(**overrides)

    # 适配器
    symbols = args.symbols.split(",") if args.symbols else None
    adapter = _create_adapter(args.market, symbols=symbols, cache_dir_override=args.cache_dir)

    # Runner
    runner = FactorStudyRunner(config, adapter)

    for name in factor_names:
        factor = get_factor(name)
        runner.add_factor(factor)

        # 自定义阈值覆盖
        if args.thresholds and len(factor_names) == 1:
            thresholds = [float(t) for t in args.thresholds.split(",")]
            custom_sweep = build_custom_sweep(
                thresholds,
                signal_types=["threshold", "cross_up", "cross_down", "sustained"],
            )
            runner.set_sweep(name, custom_sweep)

    # 运行
    bench_display = ", ".join(config.benchmark_symbols) if config.benchmark_symbols else "无"
    print(f"\n开始因子研究: {', '.join(factor_names)}")
    print(f"市场={config.market}, 频率={config.computation_freq}, "
          f"Horizons={config.forward_horizons}")
    print(f"基准={bench_display}\n")

    all_results = runner.run()

    # 输出
    for result in all_results:
        print_results(result)

        if args.csv:
            export_csv(result)

    if args.html and all_results:
        html = generate_html_report(all_results)
        path = save_html_report(html, factor_names)
        print(f"HTML 报告: {path}")


def _create_adapter(market: str, symbols=None, cache_dir_override=None):
    """创建数据适配器"""
    if market == "crypto":
        from pathlib import Path
        from backtest.adapters.crypto import CryptoAdapter

        if cache_dir_override:
            cache_dir = Path(cache_dir_override)
        else:
            # 优先使用 Finance 本地 crypto 数据（完整历史）
            local_crypto = _ROOT / "data" / "crypto"
            if local_crypto.exists() and any(local_crypto.glob("*.csv")):
                cache_dir = local_crypto
            else:
                # 回退到 Quant 缓存
                quant_root = _ROOT.parent / "Quant"
                cache_v2 = quant_root / "cache" / "binance_daily_cache"
                cache_v1 = quant_root / "cache" / "daily_klines"
                cache_dir = cache_v2 if cache_v2.exists() else cache_v1

        return CryptoAdapter(symbols=symbols, cache_dir=cache_dir)
    else:
        from backtest.adapters.us_stocks import USStocksAdapter
        return USStocksAdapter()


if __name__ == "__main__":
    main()
