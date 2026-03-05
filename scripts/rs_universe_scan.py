#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
未来资本 RS Universe Scan — 全市场动量排名

对美股 $10B+ 市值的所有股票做 RS 动量排名，寻找强势股。
池内已有缓存的股票直接用 market.db，池外股票调用 FMP API 获取 4 个月数据。

用法:
    python scripts/rs_universe_scan.py                   # 默认 $10B+
    python scripts/rs_universe_scan.py --min-mcap 50     # $50B+
    python scripts/rs_universe_scan.py --no-telegram     # 不推送
"""

import sys
import time
import json
import argparse
import logging
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from config.settings import (
    DATA_DIR, SCANS_DIR,
    TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID,
)
from src.data import get_price_df, get_symbols
from src.data.fmp_client import FMPClient
from src.indicators.rs_rating import compute_rs_rating_b, compute_rs_rating_c

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
)
logger = logging.getLogger(__name__)

RS_UNIVERSE_TOP_N = 20
RS_UNIVERSE_BOTTOM_N = 10
RS_CONSOLE_TOP_N = 50
PRICE_LOOKBACK_DAYS = 120  # 4 months of data for RS calculation


def send_telegram(message: str, max_retries: int = 3) -> bool:
    """发送 Telegram 消息 (Markdown 格式)"""
    import requests

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


def fetch_universe(client: FMPClient, min_mcap_b: float) -> list:
    """获取全市场大市值股票列表 (不过滤行业)"""
    min_mcap = int(min_mcap_b * 1_000_000_000)
    logger.info("获取市值 > $%dB 的股票...", int(min_mcap_b))
    stocks = client.get_large_cap_stocks(min_mcap)
    symbols = [s["symbol"] for s in stocks if s.get("symbol")]
    logger.info("Screener 返回 %d 只股票", len(symbols))
    return sorted(set(symbols))


def load_price_data(symbols: list, client: FMPClient) -> dict:
    """加载价格数据: 池内用缓存，池外调 API"""
    pool_symbols = set(get_symbols())
    price_dict = {}
    api_calls = 0

    to_date = datetime.now().strftime("%Y-%m-%d")
    from_date = (datetime.now() - timedelta(days=PRICE_LOOKBACK_DAYS)).strftime("%Y-%m-%d")

    for i, sym in enumerate(symbols):
        if sym in pool_symbols:
            # 池内: 用本地缓存 (免 API)
            df = get_price_df(sym, max_age_days=0)
            if df is not None and not df.empty:
                if 'date' in df.columns:
                    df = df.sort_values('date').reset_index(drop=True)
                price_dict[sym] = df
        else:
            # 池外: 调 API 取 4 个月
            raw = client.get_historical_price_range(sym, from_date, to_date)
            api_calls += 1
            if raw:
                df = pd.DataFrame(raw)
                if 'date' in df.columns and 'close' in df.columns:
                    df = df.sort_values('date').reset_index(drop=True)
                    price_dict[sym] = df

        if (i + 1) % 50 == 0:
            logger.info("价格加载进度: %d/%d (API: %d)", i + 1, len(symbols), api_calls)

    logger.info("价格加载完成: %d/%d 成功 (API: %d 次)", len(price_dict), len(symbols), api_calls)
    return price_dict


def format_rs_report(
    rs_b: pd.DataFrame,
    rs_c: pd.DataFrame,
    n_scanned: int,
    elapsed: float,
) -> str:
    """格式化 Telegram 报告"""
    now = datetime.now()
    weekday = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"][now.weekday()]
    elapsed_min = elapsed / 60

    lines = [
        "*未来资本 RS Universe Scan*",
        "{} ({})".format(now.strftime("%Y-%m-%d"), weekday),
        "扫描: {}只 | 耗时: {:.0f}m".format(n_scanned, elapsed_min),
        "",
    ]

    # Method B Top N
    if len(rs_b) > 0:
        rs_b_sorted = rs_b.sort_values("rs_rank", ascending=False)
        lines.append("*Method B (Risk-Adj Z):*")
        lines.append("```")
        lines.append(" # Symbol  P%   Z3m   Z1m   Z1w")
        for i, (_, row) in enumerate(rs_b_sorted.head(RS_UNIVERSE_TOP_N).iterrows(), 1):
            lines.append("{:>2} {:<7} {:>3.0f}  {:>5.2f} {:>5.2f} {:>5.2f}".format(
                i, row["symbol"], row["rs_rank"],
                row.get("z_3m", 0), row.get("z_1m", 0), row.get("z_1w", 0)))
        lines.append("```")

        # Bottom N
        bottom = rs_b_sorted.tail(RS_UNIVERSE_BOTTOM_N)
        bottom_str = "  ".join("{} P{:.0f}".format(row["symbol"], row["rs_rank"])
                               for _, row in bottom.iterrows())
        lines.append("Bottom {}: {}".format(RS_UNIVERSE_BOTTOM_N, bottom_str))
        lines.append("")

    # Method C Top N
    if len(rs_c) > 0:
        rs_c_sorted = rs_c.sort_values("rs_rank", ascending=False)
        lines.append("*Method C (Clenow):*")
        lines.append("```")
        lines.append(" # Symbol  P%   63d    21d   10d")
        for i, (_, row) in enumerate(rs_c_sorted.head(RS_UNIVERSE_TOP_N).iterrows(), 1):
            lines.append("{:>2} {:<7} {:>3.0f}  {:>5.2f} {:>5.2f} {:>5.2f}".format(
                i, row["symbol"], row["rs_rank"],
                row.get("clenow_63d", 0), row.get("clenow_21d", 0), row.get("clenow_10d", 0)))
        lines.append("```")

    return "\n".join(lines)


def format_console_report(rs_b: pd.DataFrame, rs_c: pd.DataFrame) -> str:
    """格式化控制台输出 (Top 50)"""
    lines = []

    if len(rs_b) > 0:
        rs_b_sorted = rs_b.sort_values("rs_rank", ascending=False)
        lines.append("=" * 60)
        lines.append("Method B (Risk-Adj Z) — Top {}".format(RS_CONSOLE_TOP_N))
        lines.append("=" * 60)
        lines.append(" #  Symbol   P%    Z3m    Z1m    Z1w")
        lines.append("-" * 50)
        for i, (_, row) in enumerate(rs_b_sorted.head(RS_CONSOLE_TOP_N).iterrows(), 1):
            lines.append("{:>3} {:<8} {:>3.0f}  {:>6.2f} {:>6.2f} {:>6.2f}".format(
                i, row["symbol"], row["rs_rank"],
                row.get("z_3m", 0), row.get("z_1m", 0), row.get("z_1w", 0)))
        lines.append("")

    if len(rs_c) > 0:
        rs_c_sorted = rs_c.sort_values("rs_rank", ascending=False)
        lines.append("=" * 60)
        lines.append("Method C (Clenow) — Top {}".format(RS_CONSOLE_TOP_N))
        lines.append("=" * 60)
        lines.append(" #  Symbol   P%    63d    21d    10d")
        lines.append("-" * 50)
        for i, (_, row) in enumerate(rs_c_sorted.head(RS_CONSOLE_TOP_N).iterrows(), 1):
            lines.append("{:>3} {:<8} {:>3.0f}  {:>6.2f} {:>6.2f} {:>6.2f}".format(
                i, row["symbol"], row["rs_rank"],
                row.get("clenow_63d", 0), row.get("clenow_21d", 0), row.get("clenow_10d", 0)))
        lines.append("")

    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description="未来资本 RS Universe Scan")
    parser.add_argument("--min-mcap", type=float, default=10,
                        help="最低市值 ($B), 默认 10")
    parser.add_argument("--no-telegram", action="store_true",
                        help="不推送 Telegram")
    args = parser.parse_args()

    logger.info("=" * 60)
    logger.info("未来资本 RS Universe Scan")
    logger.info("=" * 60)

    start_time = time.time()

    try:
        # 1. 获取股票列表
        client = FMPClient()
        symbols = fetch_universe(client, args.min_mcap)

        if not symbols:
            logger.error("未获取到任何股票")
            return

        # 2. 加载价格数据
        price_dict = load_price_data(symbols, client)

        if len(price_dict) < 10:
            logger.error("有效价格数据不足 (%d只)", len(price_dict))
            return

        # 3. 计算 RS Rating
        logger.info("计算 RS Rating...")
        rs_b = compute_rs_rating_b(price_dict)
        rs_c = compute_rs_rating_c(price_dict)
        logger.info("RS B: %d 只, RS C: %d 只", len(rs_b), len(rs_c))

        elapsed = time.time() - start_time

        # 4. 保存 JSON
        SCANS_DIR.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d")
        save_path = SCANS_DIR / "rs_universe_{}.json".format(timestamp)

        rs_b_sorted = rs_b.sort_values("rs_rank", ascending=False) if len(rs_b) > 0 else rs_b
        rs_c_sorted = rs_c.sort_values("rs_rank", ascending=False) if len(rs_c) > 0 else rs_c

        save_data = {
            "timestamp": timestamp,
            "min_mcap_b": args.min_mcap,
            "symbols_scanned": len(symbols),
            "symbols_with_data": len(price_dict),
            "elapsed": round(elapsed, 1),
            "rs_b_count": len(rs_b),
            "rs_c_count": len(rs_c),
            "rs_b_full": rs_b_sorted.to_dict("records") if len(rs_b) > 0 else [],
            "rs_c_full": rs_c_sorted.to_dict("records") if len(rs_c) > 0 else [],
        }
        with open(save_path, "w", encoding="utf-8") as f:
            json.dump(save_data, f, ensure_ascii=False, indent=2, default=str)
        logger.info("结果已保存: %s", save_path)

        # 5. 控制台输出
        print(format_console_report(rs_b, rs_c))

        # 6. Telegram 推送
        if not args.no_telegram:
            msg = format_rs_report(rs_b, rs_c, len(price_dict), elapsed)
            if len(msg) > 4000:
                # 拆分: Method B + Method C
                split_idx = msg.rfind("*Method C")
                if split_idx > 0:
                    send_telegram(msg[:split_idx].strip())
                    send_telegram(msg[split_idx:].strip())
                else:
                    send_telegram(msg[:4000])
            else:
                send_telegram(msg)

    except Exception as e:
        logger.error("RS Universe Scan 异常: %s", e)
        import traceback
        traceback.print_exc()

        if not args.no_telegram:
            error_msg = "*RS Universe Scan 异常*\n\n错误: {}".format(str(e)[:200])
            send_telegram(error_msg)

    elapsed = time.time() - start_time
    logger.info("RS Universe Scan 完成，耗时 %.1f 秒 (%.1f 分钟)", elapsed, elapsed / 60)
    logger.info("=" * 60)


if __name__ == "__main__":
    main()
