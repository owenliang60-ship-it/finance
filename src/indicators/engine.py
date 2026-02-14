"""
指标引擎
- 批量运行指标分析
- 聚合结果
- 生成信号汇总
"""
import logging
from typing import Dict, List, Optional, Any
from concurrent.futures import ThreadPoolExecutor, as_completed

import sys
sys.path.insert(0, str(__file__).rsplit("/src", 1)[0])

from src.data import get_price_df, get_symbols, load_universe
from src.indicators.pmarp import analyze_pmarp
from src.indicators.rvol import analyze_rvol

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)


def run_indicators(
    symbol: str,
    indicators: List[str] = None
) -> Dict[str, Any]:
    """
    对单只股票运行指标分析

    Args:
        symbol: 股票代码
        indicators: 要运行的指标列表，默认全部 ["pmarp", "rvol"]

    Returns:
        {
            "symbol": "NVDA",
            "pmarp": {...},
            "rvol": {...},
            "signals": ["bullish_breakout", "extreme_volume"],
            "score": 综合得分 (可选)
        }
    """
    if indicators is None:
        indicators = ["pmarp", "rvol"]

    result = {
        "symbol": symbol,
        "signals": [],
        "error": None
    }

    # 获取量价数据
    df = get_price_df(symbol)
    if df is None or df.empty:
        result["error"] = "无量价数据"
        return result

    # 按日期正序排列
    if 'date' in df.columns:
        df = df.sort_values('date')

    # 运行各指标
    if "pmarp" in indicators:
        pmarp_result = analyze_pmarp(df)
        result["pmarp"] = pmarp_result
        if pmarp_result.get("signal") not in ["neutral", None]:
            result["signals"].append(f"pmarp:{pmarp_result['signal']}")

    if "rvol" in indicators:
        rvol_result = analyze_rvol(df)
        result["rvol"] = rvol_result
        if rvol_result.get("signal") not in ["normal", "unknown", None]:
            result["signals"].append(f"rvol:{rvol_result['signal']}")

    return result


def run_all_indicators(
    symbols: List[str] = None,
    indicators: List[str] = None,
    parallel: bool = False
) -> Dict[str, Dict]:
    """
    对多只股票批量运行指标分析

    Args:
        symbols: 股票列表，默认使用股票池全部
        indicators: 指标列表
        parallel: 是否并行执行（默认否，避免 API 问题）

    Returns:
        {
            "NVDA": {...},
            "AAPL": {...},
            ...
        }
    """
    if symbols is None:
        symbols = get_symbols()

    logger.info(f"开始分析 {len(symbols)} 只股票...")

    results = {}

    if parallel:
        # 并行执行（如果数据已缓存，可以用这个加速）
        with ThreadPoolExecutor(max_workers=4) as executor:
            futures = {executor.submit(run_indicators, s, indicators): s for s in symbols}
            for future in as_completed(futures):
                symbol = futures[future]
                try:
                    results[symbol] = future.result()
                except Exception as e:
                    results[symbol] = {"symbol": symbol, "error": str(e)}
    else:
        # 串行执行
        for i, symbol in enumerate(symbols, 1):
            logger.info(f"[{i}/{len(symbols)}] {symbol}")
            try:
                results[symbol] = run_indicators(symbol, indicators)
            except Exception as e:
                logger.error(f"{symbol} 分析失败: {e}")
                results[symbol] = {"symbol": symbol, "error": str(e)}

    logger.info("分析完成")
    return results


def get_indicator_summary(results: Dict[str, Dict]) -> Dict:
    """
    生成指标分析汇总

    Args:
        results: run_all_indicators 的返回结果

    Returns:
        {
            "total": 总数,
            "with_signals": 有信号的数量,
            "signals": {
                "pmarp:bullish_breakout": ["NVDA", "AAPL"],
                "rvol:extreme_volume": ["TSLA"],
                ...
            },
            "top_pmarp": 前10高PMARP,
            "top_rvol": 前10高RVOL
        }
    """
    summary = {
        "total": len(results),
        "with_signals": 0,
        "errors": 0,
        "signals": {},
        "top_pmarp": [],
        "top_rvol": [],
        "low_pmarp": [],
        "pmarp_crossovers": {
            "breakout_98": [],   # 上穿 98% — 进入极强
            "fading_98": [],     # 下穿 98% — 强势衰减
            "crashed_2": [],     # 下穿 2% — 进入极弱
            "recovery_2": [],    # 上穿 2% — 极端下跌结束
        },
    }

    pmarp_list = []
    rvol_list = []

    for symbol, data in results.items():
        if data.get("error"):
            summary["errors"] += 1
            continue

        # 收集信号
        signals = data.get("signals", [])
        if signals:
            summary["with_signals"] += 1
            for sig in signals:
                if sig not in summary["signals"]:
                    summary["signals"][sig] = []
                summary["signals"][sig].append(symbol)

        # 收集 PMARP 值
        pmarp_data = data.get("pmarp", {})
        if pmarp_data.get("current") is not None:
            entry = {
                "symbol": symbol,
                "value": pmarp_data["current"],
                "previous": pmarp_data.get("previous"),
                "signal": pmarp_data.get("signal")
            }
            pmarp_list.append(entry)

            # 收集穿越事件
            sig = pmarp_data.get("signal")
            if sig == "bullish_breakout":
                summary["pmarp_crossovers"]["breakout_98"].append(entry)
            elif sig == "momentum_fading":
                summary["pmarp_crossovers"]["fading_98"].append(entry)
            elif sig == "oversold_bounce":
                summary["pmarp_crossovers"]["crashed_2"].append(entry)
            elif sig == "oversold_recovery":
                summary["pmarp_crossovers"]["recovery_2"].append(entry)

        # 收集 RVOL 值
        rvol_data = data.get("rvol", {})
        if rvol_data.get("current") is not None:
            rvol_list.append({
                "symbol": symbol,
                "value": rvol_data["current"],
                "signal": rvol_data.get("signal")
            })

    # 排序
    pmarp_list.sort(key=lambda x: x["value"], reverse=True)
    rvol_list.sort(key=lambda x: x["value"], reverse=True)

    summary["top_pmarp"] = pmarp_list[:10]
    summary["low_pmarp"] = pmarp_list[-10:] if len(pmarp_list) >= 10 else pmarp_list
    summary["top_rvol"] = rvol_list[:10]

    return summary


def print_indicator_report(results: Dict[str, Dict]):
    """打印指标分析报告"""
    summary = get_indicator_summary(results)

    print("\n" + "=" * 70)
    print("技术指标分析报告")
    print("=" * 70)

    print(f"\n总股票数: {summary['total']}")
    print(f"有信号: {summary['with_signals']}")
    print(f"分析失败: {summary['errors']}")

    # 打印信号汇总
    if summary["signals"]:
        print("\n" + "-" * 40)
        print("信号汇总:")
        print("-" * 40)
        for sig, symbols in summary["signals"].items():
            print(f"  {sig}: {', '.join(symbols)}")

    # 打印 PMARP 排名
    print("\n" + "-" * 40)
    print("PMARP 最高 (强势):")
    print("-" * 40)
    for item in summary["top_pmarp"][:5]:
        print(f"  {item['symbol']}: {item['value']:.1f}% ({item['signal']})")

    print("\nPMARP 最低 (弱势):")
    for item in summary["low_pmarp"][:5]:
        print(f"  {item['symbol']}: {item['value']:.1f}% ({item['signal']})")

    # 打印 RVOL 排名
    print("\n" + "-" * 40)
    print("RVOL 最高 (放量):")
    print("-" * 40)
    for item in summary["top_rvol"][:5]:
        print(f"  {item['symbol']}: {item['value']:.1f}σ ({item['signal']})")

    print("\n" + "=" * 70)


def run_momentum_scan(
    symbols: List[str] = None,
    max_age_days: int = 0,
) -> Dict[str, Any]:
    """
    跨截面动量扫描 — 一次性计算所有动量信号

    与 run_all_indicators() 不同，此函数执行跨截面计算（RS Rating 等）
    需要所有股票的数据同时参与。

    Args:
        symbols: 股票列表，默认使用股票池全部
        max_age_days: 价格数据最大容忍天数（0=不自动刷新，依赖 cron）

    Returns:
        {
            "rs_rating_b": pd.DataFrame,      # RS Rating Method B
            "rs_rating_c": pd.DataFrame,      # RS Rating Method C
            "dv_acceleration": pd.DataFrame,   # DV 加速
            "rvol_sustained": list[dict],      # RVOL 持续放量
            "symbols_scanned": int,
            "price_dict_size": int,
        }
    """
    from src.indicators.rs_rating import compute_rs_rating_b, compute_rs_rating_c
    from src.indicators.dv_acceleration import scan_dv_acceleration
    from src.indicators.rvol_sustained import scan_rvol_sustained

    if symbols is None:
        symbols = get_symbols()

    logger.info(f"动量扫描: 加载 {len(symbols)} 只股票价格数据...")

    # 加载所有股票的价格数据
    price_dict = {}
    for sym in symbols:
        df = get_price_df(sym, max_age_days=max_age_days)
        if df is not None and not df.empty:
            if 'date' in df.columns:
                df = df.sort_values('date').reset_index(drop=True)
            price_dict[sym] = df

    logger.info(f"成功加载 {len(price_dict)}/{len(symbols)} 只股票")

    results = {
        "symbols_scanned": len(symbols),
        "price_dict_size": len(price_dict),
    }

    # RS Rating (跨截面)
    results["rs_rating_b"] = compute_rs_rating_b(price_dict)
    results["rs_rating_c"] = compute_rs_rating_c(price_dict)
    logger.info(f"RS Rating 完成: B={len(results['rs_rating_b'])} C={len(results['rs_rating_c'])}")

    # DV 加速
    results["dv_acceleration"] = scan_dv_acceleration(price_dict)
    logger.info(f"DV 加速完成: {len(results['dv_acceleration'])} 只")

    # RVOL 持续放量
    results["rvol_sustained"] = scan_rvol_sustained(price_dict)
    logger.info(f"RVOL 持续完成: {len(results['rvol_sustained'])} 只触发信号")

    return results


if __name__ == "__main__":
    # 测试少量股票
    test_symbols = ["NVDA", "AAPL", "TSLA", "GOOG", "META", "MSFT", "AMZN", "AVGO", "AMD", "MU"]

    print(f"测试 {len(test_symbols)} 只股票的指标分析...")
    results = run_all_indicators(test_symbols, parallel=True)

    print_indicator_report(results)
