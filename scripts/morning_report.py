#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
未来资本 晨报 — 量价动量引擎 (Engine A)

替代 daily_scan.py，整合所有动量信号：
A. PMARP 极值
B. 量能加速 (DV Acceleration)
C. RVOL 持续放量
D. Dollar Volume Top 50 + 新面孔

用法:
    python scripts/morning_report.py                  # 完整晨报
    python scripts/morning_report.py --no-telegram    # 本地测试，不推送
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
    DV_ACCELERATION_THRESHOLD, RVOL_SUSTAINED_THRESHOLD,
    TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID,
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
    # 注: 上穿98% (breakout_98) 已移除 — 因子研究证明不显著
    # 详见 docs/research/2026-03-17-pmarp-crossover-factor-study.md
    fading = crossovers.get("fading_98", [])
    crashed = crossovers.get("crashed_2", [])
    recovery = crossovers.get("recovery_2", [])

    has_any = fading or crashed or recovery

    if not has_any:
        # 向后兼容: 如果没有 pmarp_crossovers，用旧的 value 过滤方式
        low = [x for x in indicator_summary.get("low_pmarp", []) if x["value"] <= 2]
        if low:
            items = "  ".join("{} {:.1f}%".format(x["symbol"], x["value"]) for x in low)
            lines.append("跌破2%: {}".format(items))
            has_any = True
    else:
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


def format_section_b(dv_df) -> str:
    """B. 量能加速"""
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


def format_section_c(rvol_list: list) -> str:
    """C. RVOL 持续放量"""
    lines = ["*C. RVOL 持续放量*"]

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


def format_section_d(dv_result: dict) -> str:
    """D. Dollar Volume"""
    lines = ["*D. Dollar Volume*"]

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



def format_section_social(social_scan: dict) -> str:
    """G. 社交情绪雷达"""
    lines = ["*G. 社交情绪雷达*"]

    alerts = social_scan.get("alerts", [])
    all_signals = social_scan.get("all_signals", {})
    n_data = social_scan.get("symbols_with_data", 0)

    # Sub-section 1: 注意力异动 (Z-score >= 2.0)
    if alerts:
        lines.append("注意力异动 (Z>=2.0):")
        for sig in alerts[:8]:
            z = sig.get("attention_zscore", 0)
            buzz = sig.get("weighted_buzz", 0)
            r_m = sig.get("reddit_mentions", 0)
            x_m = sig.get("x_mentions", 0)
            r_s = sig.get("reddit_sentiment")
            x_s = sig.get("x_sentiment")
            total_m = r_m + x_m
            if r_s is not None and x_s is not None and total_m > 0:
                sent = (r_s * r_m + x_s * x_m) / total_m
            elif r_s is not None:
                sent = r_s
            elif x_s is not None:
                sent = x_s
            else:
                sent = 0.0
            tag = "!!!" if z >= 4.0 else ""
            lines.append("  {} Z={:.1f} buzz={:.0f} sent={:+.2f} (R{}+X{}){}"
                         .format(sig["symbol"], z, buzz if buzz is not None else 0, sent, r_m, x_m, tag))
    else:
        lines.append("注意力异动: 无")

    # Sub-section 2: Buzz Score 前十
    if all_signals:
        buzz_ranked = sorted(
            [(sym, sig) for sym, sig in all_signals.items()
             if sig.get("weighted_buzz") is not None],
            key=lambda x: x[1]["weighted_buzz"],
            reverse=True,
        )[:10]
        if buzz_ranked:
            lines.append("")
            lines.append("Buzz Score Top 10:")
            for sym, sig in buzz_ranked:
                buzz = sig["weighted_buzz"]
                r_m = sig.get("reddit_mentions", 0)
                x_m = sig.get("x_mentions", 0)
                lines.append("  {:<6} buzz={:>6.1f}  (R{}+X{})".format(
                    sym, buzz, r_m, x_m))

    # Sub-section 3: 提及量前十
    if all_signals:
        mentions_ranked = sorted(
            [(sym, sig) for sym, sig in all_signals.items()
             if sig.get("combined_mentions", 0) > 0],
            key=lambda x: x[1]["combined_mentions"],
            reverse=True,
        )[:10]
        if mentions_ranked:
            lines.append("")
            lines.append("提及量 Top 10:")
            for sym, sig in mentions_ranked:
                total = sig["combined_mentions"]
                r_m = sig.get("reddit_mentions", 0)
                x_m = sig.get("x_mentions", 0)
                lines.append("  {:<6} {:>5}次  (R{}+X{})".format(
                    sym, total, r_m, x_m))

    lines.append("")
    lines.append("覆盖: {}只".format(n_data))

    return "\n".join(lines)


def format_morning_report(
    indicator_summary: dict,
    momentum_results: dict,
    dv_result: dict = None,
    social_scan: dict = None,
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

    # B. DV Acceleration
    dv_acc = momentum_results.get("dv_acceleration")
    if dv_acc is not None:
        lines.append(format_section_b(dv_acc))
        lines.append("")

    # C. RVOL Sustained
    rvol_list = momentum_results.get("rvol_sustained", [])
    lines.append(format_section_c(rvol_list))
    lines.append("")

    # D. Dollar Volume
    if dv_result:
        lines.append(format_section_d(dv_result))
        lines.append("")

    # E. 社交情绪雷达
    if social_scan and social_scan.get("symbols_with_data", 0) > 0:
        lines.append(format_section_social(social_scan))
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


def main():
    parser = argparse.ArgumentParser(description="未来资本 晨报")
    parser.add_argument("--no-telegram", action="store_true", help="不推送 Telegram")
    parser.add_argument("--symbols", type=str, help="指定股票代码，逗号分隔")
    parser.add_argument("--no-social", action="store_true",
                        help="跳过社交情绪 Section G（社交数据延后采集时使用）")
    parser.add_argument("--social-only", action="store_true",
                        help="仅发送社交情绪日报（配合延后 cron 使用）")
    args = parser.parse_args()

    # --social-only: 仅发送社交情绪日报（独立 cron 调用）
    if args.social_only:
        logger.info("=" * 60)
        logger.info("社交情绪日报 开始")
        logger.info("=" * 60)
        start_time = time.time()
        try:
            if args.symbols:
                symbols = [s.strip().upper() for s in args.symbols.split(",")]
            else:
                symbols = get_symbols()

            from src.indicators.social_attention import scan_social_signals
            social_scan = scan_social_signals(symbols)
            logger.info("社交情绪扫描完成: %d 只有数据", social_scan.get("symbols_with_data", 0))

            social_msg = "*未来资本 社交情绪日报*\n{}\n\n{}".format(
                datetime.now().strftime("%Y-%m-%d %H:%M"),
                format_section_social(social_scan),
            )

            if not args.no_telegram:
                send_telegram(social_msg)
            else:
                print(social_msg)
        except Exception as e:
            logger.error("社交情绪日报异常: %s", e)
            if not args.no_telegram:
                send_telegram("*社交情绪日报异常*\n\n错误: {}".format(str(e)[:200]))

        elapsed = time.time() - start_time
        logger.info("社交情绪日报完成，耗时 %.1f 秒", elapsed)
        logger.info("=" * 60)
        return

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

        # 5. 社交情绪雷达（--no-social 时跳过）
        social_scan = None
        if not args.no_social:
            try:
                from src.indicators.social_attention import scan_social_signals
                logger.info("开始社交情绪扫描...")
                social_scan = scan_social_signals(symbols)
                logger.info("社交情绪扫描完成: %d 只有数据", social_scan.get("symbols_with_data", 0))
            except Exception as e:
                logger.warning("社交情绪扫描失败: %s", e)
        else:
            logger.info("跳过社交情绪（--no-social），将由 10:20 社交日报独立发送")

        elapsed = time.time() - start_time

        # 6. 格式化
        daily_msg = format_morning_report(
            indicator_summary, momentum_results, dv_result,
            social_scan=social_scan, elapsed=elapsed)

        # 7. 保存 JSON
        SCANS_DIR.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        save_path = SCANS_DIR / "morning_{}.json".format(timestamp)
        save_data = {
            "timestamp": timestamp,
            "symbols_scanned": len(symbols),
            "elapsed": round(elapsed, 1),
            "indicator_summary": indicator_summary,
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
                split_idx = daily_msg.rfind("*D. Dollar Volume*")
                if split_idx > 0:
                    send_telegram(daily_msg[:split_idx].strip())
                    send_telegram(daily_msg[split_idx:].strip())
                else:
                    send_telegram(daily_msg[:4000])
            else:
                send_telegram(daily_msg)
        else:
            print(daily_msg)

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
