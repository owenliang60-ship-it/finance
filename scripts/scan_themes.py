#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
未来资本 主线扫描 — Theme Engine (Momentum)

扫描 Engine A (量价动量) 信号，按主题归类，
输出"市场主线在哪"的统一报告。

用法:
    python scripts/scan_themes.py                    # 完整周扫描
    python scripts/scan_themes.py --clustering       # 强制运行聚类
"""

import sys
import time
import json
import argparse
import logging
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Any

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from config.settings import (
    SCANS_DIR,
    THEME_RS_THRESHOLD,
    THEME_KEYWORDS_SEED,
)
from src.data import get_symbols
from src.indicators.engine import run_all_indicators, get_indicator_summary, run_momentum_scan

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


# ============================================================
# Step 1: 动量信号检测
# ============================================================

def has_momentum_signal(
    symbol: str,
    momentum_results: Dict,
    indicator_summary: Dict,
    rs_threshold: int = THEME_RS_THRESHOLD,
) -> bool:
    """
    判断 symbol 是否有动量信号（任一触发即算）:
    - RS Rating B 或 C >= P{rs_threshold}
    - DV 加速 signal=True
    - RVOL sustained（任意级别）
    - PMARP breakout_98 或 recovery_2
    """
    # RS Rating
    rs_b = momentum_results.get("rs_rating_b")
    if rs_b is not None and len(rs_b) > 0:
        match = rs_b[rs_b["symbol"] == symbol]
        if len(match) > 0 and match.iloc[0]["rs_rank"] >= rs_threshold:
            return True

    rs_c = momentum_results.get("rs_rating_c")
    if rs_c is not None and len(rs_c) > 0:
        match = rs_c[rs_c["symbol"] == symbol]
        if len(match) > 0 and match.iloc[0]["rs_rank"] >= rs_threshold:
            return True

    # DV Acceleration
    dv = momentum_results.get("dv_acceleration")
    if dv is not None and len(dv) > 0:
        match = dv[dv["symbol"] == symbol]
        if len(match) > 0 and match.iloc[0].get("signal", False):
            return True

    # RVOL Sustained
    rvol_list = momentum_results.get("rvol_sustained", [])
    for item in rvol_list:
        if item.get("symbol") == symbol:
            return True

    # PMARP crossovers
    crossovers = indicator_summary.get("pmarp_crossovers", {})
    breakout = crossovers.get("breakout_98", [])
    recovery = crossovers.get("recovery_2", [])
    for entry in breakout:
        if entry.get("symbol") == symbol:
            return True
    for entry in recovery:
        if entry.get("symbol") == symbol:
            return True

    return False


def get_momentum_tickers(
    momentum_results: Dict,
    indicator_summary: Dict,
    all_symbols: List[str],
    rs_threshold: int = THEME_RS_THRESHOLD,
) -> List[str]:
    """返回所有有动量信号的 ticker 列表（已排序）。"""
    return sorted(
        sym for sym in all_symbols
        if has_momentum_signal(sym, momentum_results, indicator_summary, rs_threshold)
    )


# ============================================================
# Step 2: 主题匹配
# ============================================================

def match_themes(
    tickers: List[str],
    seed: Dict = None,
) -> Dict[str, List[str]]:
    """
    按 THEME_KEYWORDS_SEED 把 ticker 归类到主题。

    Returns:
        {"ai_chip": ["NVDA", "AMD"], "memory": ["MU"], ...}
    """
    if seed is None:
        seed = THEME_KEYWORDS_SEED

    ticker_set = set(t.upper() for t in tickers)
    theme_map = {}

    for theme_name, info in seed.items():
        theme_tickers = set(info.get("tickers", []))
        overlap = sorted(theme_tickers & ticker_set)
        if overlap:
            theme_map[theme_name] = overlap

    return theme_map


# ============================================================
# Step 3: 报告格式化
# ============================================================

def format_theme_report(
    momentum_tickers: List[str],
    theme_map: Dict[str, List[str]],
    cluster_result: Dict,
    elapsed: float,
) -> str:
    """格式化主线报告（终端文本）。"""
    now = datetime.now()
    weekday = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"][now.weekday()]

    lines = [
        "=" * 60,
        "未来资本 主线扫描 (Theme Engine)",
        "{} ({})".format(now.strftime("%Y-%m-%d %H:%M"), weekday),
        "=" * 60,
        "",
    ]

    # A: 动量信号
    lines.append("[ A. 动量信号 ({} 只) ]".format(len(momentum_tickers)))
    if momentum_tickers:
        for i in range(0, len(momentum_tickers), 8):
            chunk = momentum_tickers[i:i + 8]
            lines.append("  {}".format("  ".join(chunk)))
    else:
        lines.append("  无动量信号")
    lines.append("")

    # B: 主题热力图
    lines.append("[ B. 主题热力图 ]")
    if theme_map:
        for theme, tickers in sorted(
            theme_map.items(), key=lambda x: -len(x[1])
        ):
            lines.append("  {}: {}".format(theme, " ".join(tickers)))
    else:
        lines.append("  无主题信号")
    lines.append("")

    # C: 聚类周报
    lines.append("[ C. 聚类周报 ]")
    clusters = cluster_result.get("clusters", {})
    if clusters:
        lines.append("  {} 个集群".format(len(clusters)))
        for cid, members in clusters.items():
            members_str = " ".join(members[:10])
            if len(members) > 10:
                members_str += "..."
            lines.append("  C{}: {} ({})".format(cid, members_str, len(members)))
    else:
        lines.append("  无聚类数据")
    lines.append("")

    # D: 建议深度分析
    lines.append("[ D. 建议深度分析 ]")
    if momentum_tickers:
        lines.append("  动量标的: {}".format(" ".join(momentum_tickers[:10])))
    else:
        lines.append("  无建议")
    lines.append("")

    # Footer
    lines.append("-" * 60)
    lines.append("耗时: {:.0f}s".format(elapsed))

    return "\n".join(lines)


# ============================================================
# 主流程
# ============================================================

def run_theme_scan(
    force_clustering: bool = False,
) -> Dict[str, Any]:
    """
    执行完整主线扫描。

    Returns:
        {
            "momentum_tickers": [...],
            "theme_map": {...},
            "cluster_result": {...},
            "report": str,
        }
    """
    start_time = time.time()

    # Step 1: Engine A — 动量扫描
    symbols = get_symbols()
    logger.info("Step 1: 动量扫描 (%d 只)", len(symbols))

    indicator_results = run_all_indicators(symbols, parallel=True)
    indicator_summary = get_indicator_summary(indicator_results)
    momentum_results = run_momentum_scan(symbols, max_age_days=0)

    momentum_tickers = get_momentum_tickers(
        momentum_results, indicator_summary, symbols,
    )
    logger.info("动量信号: %d 只", len(momentum_tickers))

    # 聚类 (周六或强制)
    is_saturday = datetime.now().weekday() == 5
    cluster_result = {}
    if is_saturday or force_clustering:
        try:
            from scripts.morning_report import run_clustering
            cluster_result = run_clustering(symbols)
        except Exception as e:
            logger.warning("聚类失败: %s", e)

    # Step 2: 主题匹配
    logger.info("Step 2: 主题匹配")
    theme_map = match_themes(momentum_tickers)

    elapsed = time.time() - start_time

    # Step 3: 报告
    report = format_theme_report(
        momentum_tickers,
        theme_map,
        cluster_result,
        elapsed,
    )

    # 保存 JSON
    SCANS_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    save_path = SCANS_DIR / "theme_{}.json".format(timestamp)
    save_data = {
        "timestamp": timestamp,
        "momentum_tickers": momentum_tickers,
        "theme_map": theme_map,
        "elapsed": round(elapsed, 1),
    }
    with open(save_path, "w", encoding="utf-8") as f:
        json.dump(save_data, f, ensure_ascii=False, indent=2)
    logger.info("结果已保存: %s", save_path)

    return {
        "momentum_tickers": momentum_tickers,
        "theme_map": theme_map,
        "cluster_result": cluster_result,
        "report": report,
        "save_path": str(save_path),
    }


def main():
    parser = argparse.ArgumentParser(description="未来资本 主线扫描")
    parser.add_argument("--clustering", action="store_true", help="强制运行聚类")
    args = parser.parse_args()

    logger.info("=" * 60)
    logger.info("未来资本 主线扫描 开始")
    logger.info("=" * 60)

    result = run_theme_scan(
        force_clustering=args.clustering,
    )

    print(result["report"])

    logger.info("=" * 60)
    logger.info("主线扫描完成")
    logger.info("=" * 60)


if __name__ == "__main__":
    main()
