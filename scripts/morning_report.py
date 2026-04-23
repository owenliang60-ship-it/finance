#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
未来资本 晨报 — 量价动量引擎 (Engine A)

替代 daily_scan.py，整合所有动量信号：
A. PMARP 极值
B. 量能加速 (DV Acceleration)
C. RVOL 持续放量
D. Dollar Volume Top 50 + 新面孔
E. 市场情绪脉搏 (Adanos market-level)
F. 社交热门 Top 10 + 热门板块 (Adanos trending)

用法:
    python scripts/morning_report.py                  # 完整晨报
    python scripts/morning_report.py --no-telegram    # 本地测试，不推送
"""

import sys
import time
import json
import argparse
import logging
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from config.settings import (
    DATA_DIR, SCANS_DIR,
    DOLLAR_VOLUME_REPORT_N, DOLLAR_VOLUME_LOOKBACK,
    DV_ACCELERATION_THRESHOLD, RVOL_SUSTAINED_THRESHOLD,
)
from src.data import get_price_df, get_symbols
from src.indicators.engine import run_all_indicators, get_indicator_summary, run_momentum_scan
from src.indicators.dv_acceleration import format_dv
from src.telegram_bot import send_message, split_message

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
)
logger = logging.getLogger(__name__)


def _send_group_message(message: str) -> bool:
    """Route a single message to the public group."""
    return send_message(message, channel="group")


def _send_group_report(message: str) -> bool:
    """Send the morning report to the public group, splitting when needed."""
    ok = True
    for part in split_message(message, split_marker="*D. Dollar Volume*"):
        ok = _send_group_message(part) and ok
    return ok


# ============================================================
# 格式化模块
# ============================================================

def format_section_a(indicator_summary: dict) -> str:
    """A. PMARP 极值 (仅保留上穿2%报警)"""
    lines = ["*A. PMARP 极值*"]

    crossovers = indicator_summary.get("pmarp_crossovers", {})

    # 只保留上穿2%报警。
    # 98% 上下穿已移除；下穿2%也不再作为晨报报警信号。
    recovery = crossovers.get("recovery_2", [])

    if recovery:
        items = "  ".join("{} {:.1f}%".format(x["symbol"], x["value"]) for x in recovery)
        lines.append("上穿2%: {}".format(items))
    else:
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



def format_section_market_pulse(market_data: dict) -> str:
    """E. 市场情绪脉搏 (Adanos market-level sentiment)"""
    # Show data date if not today
    dates = set(r.get("date") for r in market_data.values() if isinstance(r, dict) and r.get("date"))
    date_tag = ""
    if dates:
        from datetime import datetime as _dt, timezone as _tz
        _today = _dt.now(_tz.utc).strftime("%Y-%m-%d")
        stale = [d for d in dates if d != _today]
        if stale:
            date_tag = " [{}]".format(max(dates))
    lines = ["*E. 市场情绪脉搏{}*".format(date_tag)]

    reddit = market_data.get("reddit")
    x_data = market_data.get("x")

    if not reddit and not x_data:
        lines.append("无市场情绪数据")
        return "\n".join(lines)

    for source, label in [("reddit", "Reddit"), ("x", "𝕏")]:
        row = market_data.get(source)
        if not row:
            continue
        buzz = row.get("buzz_score", 0) or 0
        trend = row.get("trend", "—")
        bull = row.get("bullish_pct", 0) or 0
        bear = row.get("bearish_pct", 0) or 0
        mentions = row.get("mentions", 0) or 0
        sentiment = row.get("sentiment_score")
        sent_str = "{:+.2f}".format(sentiment) if sentiment is not None else "n/a"
        # Trend arrow
        arrow = {"bullish": "↑", "bearish": "↓", "neutral": "→"}.get(trend, "·")
        lines.append("{} {} buzz={:.0f} {}bull/{}bear sent={} ({}提及)".format(
            label, arrow, buzz, bull, bear, sent_str, mentions))

    return "\n".join(lines)


def format_section_trending(trending_data: dict) -> str:
    """F. 社交热门 + 热门板块 (Adanos trending)"""
    data_date = trending_data.get("date", "")
    date_tag = ""
    if data_date:
        from datetime import datetime as _dt, timezone as _tz
        _today = _dt.now(_tz.utc).strftime("%Y-%m-%d")
        if data_date != _today:
            date_tag = " [{}]".format(data_date)
    lines = ["*F. 社交热门{}*".format(date_tag)]

    # Sub-section 1: Trending stocks (merge Reddit + X, dedupe by ticker, rank by buzz)
    stocks = trending_data.get("stocks", [])
    if stocks:
        # Merge across sources: keep highest buzz per ticker
        merged = {}
        for row in stocks:
            ticker = row.get("ticker", "")
            if not ticker:
                continue
            buzz = row.get("buzz_score", 0) or 0
            existing = merged.get(ticker)
            if existing is None or buzz > (existing.get("buzz_score", 0) or 0):
                merged[ticker] = row
        ranked = sorted(merged.values(), key=lambda x: x.get("buzz_score", 0) or 0, reverse=True)[10:20]
        lines.append("热门个股 #11-20:")
        for i, row in enumerate(ranked, 11):
            ticker = row.get("ticker", "?")
            buzz = row.get("buzz_score", 0) or 0
            trend = row.get("trend", "")
            sentiment = row.get("sentiment_score")
            sent_str = "{:+.2f}".format(sentiment) if sentiment is not None else ""
            arrow = {"bullish": "↑", "bearish": "↓", "neutral": "→"}.get(trend, "")
            lines.append("  {:>2}. {:<6} buzz={:>5.0f} {} {}".format(
                i, ticker, buzz, arrow, sent_str).rstrip())
    else:
        lines.append("热门个股: 无数据")

    # Sub-section 2: Trending sectors
    sectors = trending_data.get("sectors", [])
    if sectors:
        # Merge across sources: keep highest buzz per sector
        merged_s = {}
        for row in sectors:
            sector = row.get("sector", "")
            if not sector:
                continue
            buzz = row.get("buzz_score", 0) or 0
            existing = merged_s.get(sector)
            if existing is None or buzz > (existing.get("buzz_score", 0) or 0):
                merged_s[sector] = row
        ranked_s = sorted(merged_s.values(), key=lambda x: x.get("buzz_score", 0) or 0, reverse=True)[:8]
        lines.append("")
        lines.append("热门板块:")
        for row in ranked_s:
            sector = row.get("sector", "?")
            buzz = row.get("buzz_score", 0) or 0
            top_tickers = row.get("top_tickers", "")
            if isinstance(top_tickers, list):
                top_tickers = ", ".join(top_tickers[:4])
            elif isinstance(top_tickers, str) and top_tickers.startswith("["):
                try:
                    top_tickers = ", ".join(json.loads(top_tickers)[:4])
                except Exception:
                    pass
            lines.append("  {}: buzz={:.0f} ({})".format(sector, buzz, top_tickers or "—"))

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
    market_pulse: dict = None,
    trending_data: dict = None,
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

    # E. 市场情绪脉搏
    if market_pulse:
        lines.append(format_section_market_pulse(market_pulse))
        lines.append("")

    # F. 社交热门
    if trending_data:
        lines.append(format_section_trending(trending_data))
        lines.append("")

    # G. 社交情绪雷达
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

            # Section E + F: 市场级社交数据 (Adanos market-level)
            from src.data.market_store import get_store
            from datetime import timezone, timedelta
            store = get_store()
            now_utc = datetime.now(timezone.utc)
            today_utc = now_utc.strftime("%Y-%m-%d")
            yesterday_utc = (now_utc - timedelta(days=1)).strftime("%Y-%m-%d")
            fresh_dates = {today_utc, yesterday_utc}

            market_pulse = None
            pulse = {}
            for src in ["reddit", "x"]:
                row = store.get_latest_market_sentiment(source=src)
                if row and row.get("date") in fresh_dates:
                    pulse[src] = row
            if pulse:
                market_pulse = pulse
                logger.info("市场情绪脉搏: %s", list(pulse.keys()))

            trending_data = None
            t_data = {"stocks": [], "sectors": []}
            trending_date = None
            for candidate_date in [today_utc, yesterday_utc]:
                for src in ["reddit", "x"]:
                    t_data["stocks"].extend(store.get_social_trending(candidate_date, src))
                    t_data["sectors"].extend(store.get_social_trending_sectors(candidate_date, src))
                if t_data["stocks"] or t_data["sectors"]:
                    trending_date = candidate_date
                    break
                t_data = {"stocks": [], "sectors": []}
            if t_data["stocks"] or t_data["sectors"]:
                t_data["date"] = trending_date
                trending_data = t_data
                logger.info("社交热门: %d stocks, %d sectors", len(t_data["stocks"]), len(t_data["sectors"]))

            # Section G: per-stock 社交情绪雷达
            from src.indicators.social_attention import scan_social_signals
            social_scan = scan_social_signals(symbols)
            logger.info("社交情绪扫描完成: %d 只有数据", social_scan.get("symbols_with_data", 0))

            # 组装消息: E + F + G
            sections = []
            if market_pulse:
                sections.append(format_section_market_pulse(market_pulse))
            if trending_data:
                sections.append(format_section_trending(trending_data))
            sections.append(format_section_social(social_scan))

            social_msg = "*未来资本 社交情绪日报*\n{}\n\n{}".format(
                datetime.now().strftime("%Y-%m-%d %H:%M"),
                "\n\n".join(sections),
            )

            if not args.no_telegram:
                _send_group_message(social_msg)
            else:
                print(social_msg)
        except Exception as e:
            logger.error("社交情绪日报异常: %s", e)
            if not args.no_telegram:
                _send_group_message("*社交情绪日报异常*\n\n错误: {}".format(str(e)[:200]))

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

        # 5. 市场情绪脉搏 + 社交热门 (Adanos market-level)
        market_pulse = None
        trending_data = None
        if not args.no_social:
            try:
                from src.data.market_store import get_store
                from datetime import timezone, timedelta
                store = get_store()
                now_utc = datetime.now(timezone.utc)
                today_utc = now_utc.strftime("%Y-%m-%d")
                yesterday_utc = (now_utc - timedelta(days=1)).strftime("%Y-%m-%d")
                fresh_dates = {today_utc, yesterday_utc}

                # Market sentiment (Reddit + X) — accept latest within 2 days
                pulse = {}
                for src in ["reddit", "x"]:
                    row = store.get_latest_market_sentiment(source=src)
                    if row and row.get("date") in fresh_dates:
                        pulse[src] = row
                if pulse:
                    market_pulse = pulse
                    dates_seen = set(r.get("date") for r in pulse.values())
                    logger.info("市场情绪脉搏: %s (data: %s)", list(pulse.keys()), dates_seen)

                # Trending stocks + sectors — try today first, fallback to yesterday
                t_data = {"stocks": [], "sectors": []}
                trending_date = None
                for candidate_date in [today_utc, yesterday_utc]:
                    for src in ["reddit", "x"]:
                        t_data["stocks"].extend(store.get_social_trending(candidate_date, src))
                        t_data["sectors"].extend(store.get_social_trending_sectors(candidate_date, src))
                    if t_data["stocks"] or t_data["sectors"]:
                        trending_date = candidate_date
                        break
                    # Reset for next candidate
                    t_data = {"stocks": [], "sectors": []}
                if t_data["stocks"] or t_data["sectors"]:
                    t_data["date"] = trending_date
                    trending_data = t_data
                    logger.info("社交热门: %d stocks, %d sectors (data: %s)",
                                len(t_data["stocks"]), len(t_data["sectors"]), trending_date)
            except Exception as e:
                logger.warning("市场级社交数据加载失败: %s", e)

        # 6. 社交情绪雷达（--no-social 时跳过）
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

        # 7. 格式化
        daily_msg = format_morning_report(
            indicator_summary, momentum_results, dv_result,
            market_pulse=market_pulse, trending_data=trending_data,
            social_scan=social_scan, elapsed=elapsed)

        # 8. 保存 JSON
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

        # 9. 发送 Telegram
        if not args.no_telegram:
            _send_group_report(daily_msg)
        else:
            print(daily_msg)

    except Exception as e:
        logger.error("晨报异常: %s", e)
        import traceback
        traceback.print_exc()

        if not args.no_telegram:
            error_msg = "*未来资本 晨报异常*\n\n错误: {}".format(str(e)[:200])
            _send_group_message(error_msg)

    elapsed = time.time() - start_time
    logger.info("晨报完成，耗时 %.1f 秒", elapsed)
    logger.info("=" * 60)


if __name__ == "__main__":
    main()
