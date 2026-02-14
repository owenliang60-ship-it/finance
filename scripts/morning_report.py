#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
未来资本 晨报 — 量价动量引擎 (Engine A)

替代 daily_scan.py，整合所有动量信号：
A. PMARP 极值
B. RS 动量评级 (Method B + C)
C. 量能加速 (DV Acceleration)
D. RVOL 持续放量
E. Dollar Volume Top 50 + 新面孔
F. 相关性聚类 (仅周六)

用法:
    python scripts/morning_report.py                  # 完整晨报
    python scripts/morning_report.py --no-telegram    # 本地测试，不推送
    python scripts/morning_report.py --clustering     # 强制运行聚类
"""

import sys
import time
import json
import argparse
import logging
import requests
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from config.settings import (
    DATA_DIR, SCANS_DIR,
    DOLLAR_VOLUME_REPORT_N, DOLLAR_VOLUME_LOOKBACK,
    RS_RATING_TOP_N, RS_RATING_BOTTOM_N,
    DV_ACCELERATION_THRESHOLD, RVOL_SUSTAINED_THRESHOLD,
    TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID,
    CLUSTERING_DIR,
)
from src.data import get_price_df, get_symbols
from src.indicators.engine import run_all_indicators, get_indicator_summary, run_momentum_scan
from src.indicators.dv_acceleration import format_dv

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
)
logger = logging.getLogger(__name__)


# ============================================================
# Telegram
# ============================================================

def send_telegram(message: str, max_retries: int = 3) -> bool:
    """发送 Telegram 消息 (Markdown 格式)"""
    token = TELEGRAM_BOT_TOKEN
    chat_id = TELEGRAM_CHAT_ID

    if not token or not chat_id:
        logger.info("[Telegram] 未配置，跳过发送")
        return False

    url = "https://api.telegram.org/bot{}/sendMessage".format(token)
    payload = {
        "chat_id": chat_id,
        "text": message,
        "parse_mode": "Markdown",
    }

    for attempt in range(1, max_retries + 1):
        try:
            response = requests.post(url, json=payload, timeout=15)
            response.raise_for_status()
            logger.info("[Telegram] 消息已发送")
            return True
        except Exception as e:
            logger.warning("[Telegram] 第%d次发送失败: %s", attempt, e)
            if attempt < max_retries:
                time.sleep(attempt * 2)

    return False


# ============================================================
# 格式化模块
# ============================================================

def format_section_a(indicator_summary: dict) -> str:
    """A. PMARP 极值 (四种穿越信号)"""
    lines = ["*A. PMARP 极值*"]

    crossovers = indicator_summary.get("pmarp_crossovers", {})

    # 优先使用穿越事件数据
    breakout = crossovers.get("breakout_98", [])
    fading = crossovers.get("fading_98", [])
    crashed = crossovers.get("crashed_2", [])
    recovery = crossovers.get("recovery_2", [])

    has_any = breakout or fading or crashed or recovery

    if not has_any:
        # 向后兼容: 如果没有 pmarp_crossovers，用旧的 value 过滤方式
        high = [x for x in indicator_summary.get("top_pmarp", []) if x["value"] >= 98]
        low = [x for x in indicator_summary.get("low_pmarp", []) if x["value"] <= 2]
        if high:
            items = "  ".join("{} {:.1f}%".format(x["symbol"], x["value"]) for x in high)
            lines.append("突破98%: {}".format(items))
            has_any = True
        if low:
            items = "  ".join("{} {:.1f}%".format(x["symbol"], x["value"]) for x in low)
            lines.append("跌破2%: {}".format(items))
            has_any = True
    else:
        if breakout:
            items = "  ".join("{} {:.1f}%".format(x["symbol"], x["value"]) for x in breakout)
            lines.append("上穿98%: {}".format(items))
        if fading:
            items = "  ".join("{} {:.1f}%".format(x["symbol"], x["value"]) for x in fading)
            lines.append("下穿98%: {}".format(items))
        if crashed:
            items = "  ".join("{} {:.1f}%".format(x["symbol"], x["value"]) for x in crashed)
            lines.append("下穿2%: {}".format(items))
        if recovery:
            items = "  ".join("{} {:.1f}%".format(x["symbol"], x["value"]) for x in recovery)
            lines.append("上穿2%: {}".format(items))

    if not has_any:
        lines.append("今日无极值信号")

    return "\n".join(lines)


def format_section_b(rs_b, rs_c) -> str:
    """B. RS 动量评级"""
    lines = ["*B. RS 动量评级*"]

    # Method B — Top N (sorted by rs_rank descending)
    if len(rs_b) > 0:
        rs_b_sorted = rs_b.sort_values("rs_rank", ascending=False)
        lines.append("_Method B (Risk-Adj Z):_")
        lines.append("```")
        lines.append(" # Symbol  P%   Z3m   Z1m   Z1w")
        top = rs_b_sorted.head(RS_RATING_TOP_N)
        for i, (_, row) in enumerate(top.iterrows(), 1):
            lines.append("{:>2} {:<7} {:>3.0f}  {:>5.2f} {:>5.2f} {:>5.2f}".format(
                i, row["symbol"], row["rs_rank"],
                row.get("z_3m", 0), row.get("z_1m", 0), row.get("z_1w", 0)))
        lines.append("```")

        # Bottom N
        bottom = rs_b_sorted.tail(RS_RATING_BOTTOM_N)
        bottom_str = "  ".join("{} P{:.0f}".format(row["symbol"], row["rs_rank"])
                               for _, row in bottom.iterrows())
        lines.append("Bottom {}: {}".format(RS_RATING_BOTTOM_N, bottom_str))

    # Method C — Top N (sorted by rs_rank descending)
    if len(rs_c) > 0:
        rs_c_sorted = rs_c.sort_values("rs_rank", ascending=False)
        lines.append("")
        lines.append("_Method C (Clenow):_")
        lines.append("```")
        lines.append(" # Symbol  P%   63d    21d   10d")
        top = rs_c_sorted.head(RS_RATING_TOP_N)
        for i, (_, row) in enumerate(top.iterrows(), 1):
            lines.append("{:>2} {:<7} {:>3.0f}  {:>5.2f} {:>5.2f} {:>5.2f}".format(
                i, row["symbol"], row["rs_rank"],
                row.get("clenow_63d", 0), row.get("clenow_21d", 0), row.get("clenow_10d", 0)))
        lines.append("```")

    return "\n".join(lines)


def format_section_c(dv_df) -> str:
    """C. 量能加速"""
    lines = ["*C. 量能加速 (DV>{:.1f}x)*".format(DV_ACCELERATION_THRESHOLD)]

    fired = dv_df[dv_df["signal"]] if len(dv_df) > 0 else dv_df
    if len(fired) == 0:
        lines.append("无加速信号")
    else:
        for _, row in fired.head(10).iterrows():
            lines.append("{}: 5d={}/20d={} = {:.1f}x".format(
                row["symbol"],
                format_dv(row["dv_5d"]),
                format_dv(row["dv_20d"]),
                row["ratio"]))

    return "\n".join(lines)


def format_section_d(rvol_list: list) -> str:
    """D. RVOL 持续放量"""
    lines = ["*D. RVOL 持续放量*"]

    level_icons = {
        "sustained_5d": "5日连续:",
        "sustained_3d": "3日连续:",
        "single": "单日>2s:",
    }

    if not rvol_list:
        lines.append("无持续放量信号")
    else:
        for item in rvol_list[:15]:
            icon = level_icons.get(item["level"], "")
            vals = " ".join("{:.1f}s".format(v) for v in item["values"][:5])
            lines.append("{} {} ({})".format(icon, item["symbol"], vals))

    return "\n".join(lines)


def format_section_e(dv_result: dict) -> str:
    """E. Dollar Volume"""
    lines = ["*E. Dollar Volume*"]

    rankings = dv_result.get("rankings", [])
    new_faces = dv_result.get("new_faces", [])

    # 新面孔
    if new_faces:
        nf_items = "  ".join(
            "#{} {} {}".format(nf["rank"], nf["symbol"], format_dv(nf["dollar_volume"]))
            for nf in new_faces[:5])
        lines.append("新面孔: {}".format(nf_items))

    # Top 10
    if rankings:
        lines.append("```")
        lines.append(" # Symbol  $Vol      Price")
        for r in rankings[:10]:
            lines.append("{:>2} {:<7} {:>8} ${:>7.0f}".format(
                r["rank"], r["symbol"], format_dv(r["dollar_volume"]), r["price"]))
        lines.append("```")

    return "\n".join(lines)




# 行业中英映射 (常见 FMP industry → 中文)
INDUSTRY_CN = {
    "Semiconductors": "半导体",
    "Software - Infrastructure": "基础软件",
    "Software - Application": "应用软件",
    "Internet Content & Information": "互联网",
    "Consumer Electronics": "消费电子",
    "Banks - Diversified": "银行",
    "Capital Markets": "资本市场",
    "Financial Data & Stock Exchanges": "金融数据",
    "Credit Services": "信贷",
    "Insurance - Diversified": "保险",
    "Drug Manufacturers - General": "制药",
    "Healthcare Plans": "医疗保险",
    "Medical Devices": "医疗器械",
    "Biotechnology": "生物科技",
    "Entertainment": "娱乐",
    "Auto Manufacturers": "汽车",
    "Internet Retail": "电商",
    "Specialty Retail": "零售",
    "Restaurants": "餐饮",
    "Aerospace & Defense": "航空航天",
    "Information Technology Services": "IT服务",
    "Communication Equipment": "通信设备",
    "Electronic Components": "电子元件",
    "Semiconductor Equipment & Materials": "半导体设备",
}

SECTOR_CN = {
    "Technology": "科技",
    "Financial Services": "金融",
    "Healthcare": "医疗",
    "Consumer Cyclical": "消费",
    "Communication Services": "传媒",
    "Industrials": "工业",
    "Energy": "能源",
    "Consumer Defensive": "必需消费",
    "Real Estate": "地产",
    "Utilities": "公用事业",
    "Basic Materials": "基础材料",
}


def _build_symbol_info(universe: list) -> dict:
    """从 universe 构建 {symbol: {industry, sector}} 映射"""
    return {
        item["symbol"]: {
            "industry": item.get("industry", ""),
            "sector": item.get("sector", ""),
        }
        for item in universe
    }


def _label_cluster(members: list, symbol_info: dict) -> str:
    """给一个 cluster 打中文标签: industry 投票 > sector 投票 > '混合'"""
    from collections import Counter

    industries = [symbol_info[s]["industry"] for s in members if s in symbol_info and symbol_info[s]["industry"]]
    if industries:
        counter = Counter(industries)
        top_industry, top_count = counter.most_common(1)[0]
        if top_count / len(members) > 0.5:
            return INDUSTRY_CN.get(top_industry, top_industry)

    sectors = [symbol_info[s]["sector"] for s in members if s in symbol_info and symbol_info[s]["sector"]]
    if sectors:
        counter = Counter(sectors)
        top_sector, top_count = counter.most_common(1)[0]
        if top_count / len(members) > 0.5:
            return SECTOR_CN.get(top_sector, top_sector)

    return "混合"


def format_section_f(cluster_result: dict, universe: list = None) -> str:
    """F. 聚类报告 (周报，独立消息)

    Args:
        cluster_result: run_weekly_clustering() 的返回值
        universe: load_universe() 返回的 [{symbol, sector, industry, ...}]，
                  用于生成中文标签。None 时尝试自动加载。
    """
    lines = ["*F. 相关性聚类 (周报)*"]

    clusters = cluster_result.get("clusters", {})
    comparison = cluster_result.get("comparison")
    n_clusters = len(clusters)

    # 自动加载 universe（零 API 调用，读本地 JSON）
    symbol_info = {}
    if universe is None:
        try:
            from src.data.pool_manager import load_universe
            universe = load_universe()
        except Exception:
            universe = []
    if universe:
        symbol_info = _build_symbol_info(universe)

    # 摘要行
    jaccard = comparison.get("jaccard", 0) if comparison else 0
    if comparison and comparison.get("new_formation"):
        stability = "重组"
    elif jaccard >= 0.8:
        stability = "稳定"
    elif jaccard >= 0.5:
        stability = "微调"
    else:
        stability = "变动"
    summary_parts = ["30d", "{}组".format(n_clusters)]
    if comparison:
        summary_parts.append("Jaccard={:.2f} {}".format(jaccard, stability))
    lines.append(" | ".join(summary_parts))
    lines.append("")

    # 各 cluster 带标签
    for cid, members in clusters.items():
        label = _label_cluster(members, symbol_info)
        members_str = " ".join(members[:10])
        if len(members) > 10:
            members_str += "..."
        lines.append("{} ({}): {}".format(label, len(members), members_str))

    # 变动行
    if comparison:
        changes = comparison.get("changes", [])
        moves = []
        for change in changes:
            cid = change.get("current_cluster")
            # 找到该 cluster 的标签
            if cid is not None and str(cid) in clusters:
                cluster_members = clusters[str(cid)]
            elif cid is not None and cid in clusters:
                cluster_members = clusters[cid]
            else:
                cluster_members = []
            cluster_label = _label_cluster(cluster_members, symbol_info) if cluster_members else "?"

            for sym in change.get("added", []):
                moves.append("{}→{}".format(sym, cluster_label))
            for sym in change.get("removed", []):
                moves.append("{}←{}".format(sym, cluster_label))

        if moves:
            lines.append("")
            lines.append("变动: {}".format("  ".join(moves)))

    return "\n".join(lines)


def format_morning_report(
    indicator_summary: dict,
    momentum_results: dict,
    dv_result: dict = None,
    elapsed: float = 0,
) -> str:
    """格式化完整晨报"""
    now = datetime.now()
    weekday = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"][now.weekday()]

    lines = [
        "*未来资本 晨报*",
        "{} ({}) 07:00".format(now.strftime("%Y-%m-%d"), weekday),
        "",
    ]

    # A. PMARP
    lines.append(format_section_a(indicator_summary))
    lines.append("")

    # B. RS Rating
    rs_b = momentum_results.get("rs_rating_b")
    rs_c = momentum_results.get("rs_rating_c")
    if rs_b is not None and rs_c is not None:
        lines.append(format_section_b(rs_b, rs_c))
        lines.append("")

    # C. DV Acceleration
    dv_acc = momentum_results.get("dv_acceleration")
    if dv_acc is not None:
        lines.append(format_section_c(dv_acc))
        lines.append("")

    # D. RVOL Sustained
    rvol_list = momentum_results.get("rvol_sustained", [])
    lines.append(format_section_d(rvol_list))
    lines.append("")

    # E. Dollar Volume
    if dv_result:
        lines.append(format_section_e(dv_result))
        lines.append("")

    # Footer
    n_scanned = momentum_results.get("symbols_scanned", 0)
    lines.append("扫描: {}只 | 耗时: {:.0f}s".format(n_scanned, elapsed))

    return "\n".join(lines)


# ============================================================
# 主流程
# ============================================================

def run_dollar_volume() -> dict:
    """运行 Dollar Volume 采集"""
    try:
        scripts_dir = str(Path(__file__).parent)
        if scripts_dir not in sys.path:
            sys.path.insert(0, scripts_dir)
        from collect_dollar_volume import collect_daily

        logger.info("开始采集 Dollar Volume...")
        result = collect_daily()
        logger.info("Dollar Volume 采集完成: %s", result.get("status"))
        return result
    except Exception as e:
        logger.warning("Dollar Volume 采集失败: %s", e)
        return {"rankings": [], "new_faces": []}


def run_clustering(symbols: list) -> dict:
    """运行相关性聚类"""
    try:
        from src.analysis.clustering import run_weekly_clustering

        logger.info("开始相关性聚类...")
        # 加载价格数据
        price_dict = {}
        for sym in symbols:
            df = get_price_df(sym, max_age_days=0)
            if df is not None and not df.empty:
                if 'date' in df.columns:
                    df = df.sort_values('date').reset_index(drop=True)
                price_dict[sym] = df

        history_path = CLUSTERING_DIR / "cluster_history.json"
        result = run_weekly_clustering(price_dict, history_path=history_path)
        logger.info("聚类完成: %d 个集群", result.get("n_clusters", 0))
        return result
    except Exception as e:
        logger.warning("聚类失败: %s", e)
        return {}


def main():
    parser = argparse.ArgumentParser(description="未来资本 晨报")
    parser.add_argument("--no-telegram", action="store_true", help="不推送 Telegram")
    parser.add_argument("--clustering", action="store_true", help="强制运行聚类")
    parser.add_argument("--symbols", type=str, help="指定股票代码，逗号分隔")
    args = parser.parse_args()

    logger.info("=" * 60)
    logger.info("未来资本 晨报 开始")
    logger.info("=" * 60)

    start_time = time.time()

    try:
        # 1. 获取股票列表
        if args.symbols:
            symbols = [s.strip().upper() for s in args.symbols.split(",")]
        else:
            symbols = get_symbols()
        logger.info("股票池: %d 只", len(symbols))

        # 2. PMARP + RVOL (per-stock indicators)
        indicator_results = run_all_indicators(symbols, parallel=True)
        indicator_summary = get_indicator_summary(indicator_results)

        # 3. 跨截面动量信号 (RS Rating, DV Accel, RVOL Sustained)
        momentum_results = run_momentum_scan(symbols, max_age_days=0)

        # 4. Dollar Volume 采集
        dv_result = run_dollar_volume()

        # 5. 聚类 (仅周六或强制)
        is_saturday = datetime.now().weekday() == 5
        cluster_result = None
        if is_saturday or args.clustering:
            cluster_result = run_clustering(symbols)

        elapsed = time.time() - start_time

        # 6. 格式化
        daily_msg = format_morning_report(
            indicator_summary, momentum_results, dv_result, elapsed)

        # 7. 保存 JSON
        SCANS_DIR.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        save_path = SCANS_DIR / "morning_{}.json".format(timestamp)
        save_data = {
            "timestamp": timestamp,
            "symbols_scanned": len(symbols),
            "elapsed": round(elapsed, 1),
            "indicator_summary": indicator_summary,
            "rs_rating_b_top10": momentum_results["rs_rating_b"].sort_values("rs_rank", ascending=False).head(10).to_dict("records") if len(momentum_results.get("rs_rating_b", [])) > 0 else [],
            "rs_rating_c_top10": momentum_results["rs_rating_c"].sort_values("rs_rank", ascending=False).head(10).to_dict("records") if len(momentum_results.get("rs_rating_c", [])) > 0 else [],
            "dv_acceleration_fired": momentum_results["dv_acceleration"][momentum_results["dv_acceleration"]["signal"]].to_dict("records") if len(momentum_results.get("dv_acceleration", [])) > 0 else [],
            "rvol_sustained": momentum_results.get("rvol_sustained", []),
        }
        with open(save_path, "w", encoding="utf-8") as f:
            json.dump(save_data, f, ensure_ascii=False, indent=2, default=str)
        logger.info("结果已保存: %s", save_path)

        # 8. 发送 Telegram
        if not args.no_telegram:
            # 日报 (拆分如果超长)
            if len(daily_msg) > 4000:
                # 拆分: A-D 一条, E 一条
                split_idx = daily_msg.rfind("*E. Dollar Volume*")
                if split_idx > 0:
                    send_telegram(daily_msg[:split_idx].strip())
                    send_telegram(daily_msg[split_idx:].strip())
                else:
                    send_telegram(daily_msg[:4000])
            else:
                send_telegram(daily_msg)

            # 聚类周报 (独立消息)
            if cluster_result and cluster_result.get("clusters"):
                cluster_msg = format_section_f(cluster_result)
                send_telegram(cluster_msg)
        else:
            print(daily_msg)
            if cluster_result and cluster_result.get("clusters"):
                print("\n" + "=" * 60)
                print(format_section_f(cluster_result))

    except Exception as e:
        logger.error("晨报异常: %s", e)
        import traceback
        traceback.print_exc()

        if not args.no_telegram:
            error_msg = "*未来资本 晨报异常*\n\n错误: {}".format(str(e)[:200])
            send_telegram(error_msg)

    elapsed = time.time() - start_time
    logger.info("晨报完成，耗时 %.1f 秒", elapsed)
    logger.info("=" * 60)


if __name__ == "__main__":
    main()
