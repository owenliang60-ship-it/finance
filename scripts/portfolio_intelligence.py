#!/usr/bin/env python3
"""Portfolio Intelligence — 持仓感知情报引擎.

每日 22:00 SGT cron 运行，推送持仓级信号到 Telegram。
三区块报告：行动信号 / 组合概览 / Kill Conditions。
"""
import sys
import logging
from pathlib import Path
from datetime import datetime

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd
from config.settings import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, MARKET_DB_PATH
from terminal.company_store import get_store
from src.indicators.pmarp import analyze_pmarp
from src.indicators.rvol import analyze_rvol

logger = logging.getLogger(__name__)

# ---- DNA 浮亏阈值 ----
DNA_LOSS_THRESHOLDS = {"S": -0.30, "A": -0.20, "B": -0.15, "C": -0.10}


# ---- 信号检测函数 ----

def check_ema120(df: pd.DataFrame) -> dict | None:
    """检测收盘价是否跌破 EMA120."""
    if len(df) < 120:
        return None
    ema120 = df["close"].ewm(span=120).mean().iloc[-1]
    price = df["close"].iloc[-1]
    if price < ema120:
        return {"signal": "below_ema120", "price": price, "ema120": ema120}
    return None


def check_cost_alert(symbol: str, avg_cost: float, current_price: float,
                     dna: str) -> dict | None:
    """检测浮亏是否超过 DNA 对应阈值."""
    threshold = DNA_LOSS_THRESHOLDS.get(dna, -0.10)
    pnl_pct = (current_price - avg_cost) / avg_cost if avg_cost > 0 else 0
    if pnl_pct < threshold:
        return {
            "signal": "cost_alert",
            "message": f"浮亏 {pnl_pct:.1%} (DNA={dna}, 阈值{threshold:.0%})",
            "pnl_pct": pnl_pct,
        }
    return None


def calc_sector_concentration(positions: list) -> dict:
    """计算行业集中度, >40% 标记警告."""
    sectors = {}
    for p in positions:
        s = p.get("sector", "Unknown")
        sectors[s] = sectors.get(s, 0) + p.get("weight", 0)
    warnings = [f"{s} {w:.0%}" for s, w in sectors.items() if w > 0.40]
    sectors["_warnings"] = warnings
    return sectors


def calc_qqq_beta(symbols: list, prices_map: dict, qqq_df: pd.DataFrame,
                  weights: dict, lookback: int = 60) -> float | None:
    """等效 QQQ Beta = sum(weight_i * beta_i)."""
    if qqq_df is None or len(qqq_df) < lookback:
        return None
    qqq_ret = qqq_df["close"].pct_change().dropna().tail(lookback)
    total_beta = 0.0
    for sym in symbols:
        df = prices_map.get(sym)
        if df is None or len(df) < lookback:
            continue
        sym_ret = df["close"].pct_change().dropna().tail(lookback)
        aligned = pd.concat([sym_ret, qqq_ret], axis=1, join="inner")
        if len(aligned) < 20:
            continue
        cov = aligned.iloc[:, 0].cov(aligned.iloc[:, 1])
        var = aligned.iloc[:, 1].var()
        beta = cov / var if var > 0 else 1.0
        total_beta += weights.get(sym, 0) * beta
    return total_beta


def detect_timing_change(ratings: list) -> dict | None:
    """比较最近两条 OPRMS 记录, 检测 DNA 或 Timing 变化."""
    if len(ratings) < 2:
        return None
    new, old = ratings[0], ratings[1]
    if new.get("dna") != old.get("dna") or new.get("timing") != old.get("timing"):
        return {
            "old_dna": old.get("dna"), "new_dna": new.get("dna"),
            "old_timing": old.get("timing"), "new_timing": new.get("timing"),
        }
    return None


# ---- Telegram ----

def send_telegram(message: str, max_retries: int = 3) -> bool:
    """发送 Telegram 消息 (Markdown 格式)."""
    import requests
    token = TELEGRAM_BOT_TOKEN
    chat_id = TELEGRAM_CHAT_ID
    if not token or not chat_id:
        logger.info("[Telegram] 未配置，跳过发送")
        return False
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {"chat_id": chat_id, "text": message, "parse_mode": "Markdown"}
    import time
    for attempt in range(1, max_retries + 1):
        try:
            resp = requests.post(url, json=payload, timeout=15)
            resp.raise_for_status()
            logger.info("[Telegram] 消息已发送")
            return True
        except Exception as e:
            logger.warning("[Telegram] 第%d次发送失败: %s", attempt, e)
            if attempt < max_retries:
                time.sleep(attempt * 2)
    return False


# ---- 格式化 ----

def format_report(action_signals: list, summary: dict, kill_conditions: dict) -> str:
    """格式化 3 区块 Telegram 报告."""
    lines = []

    # Block 1: 行动信号
    if action_signals:
        lines.append("🚨 *行动信号*\n")
        for sig in action_signals:
            lines.append(sig)
        lines.append("")

    # Block 2: 组合概览
    lines.append("📊 *组合概览*\n")
    lines.append(f"总资产: ${summary['total_nav']:,.0f} | "
                 f"仓位 {summary['invested_pct']:.0%} | "
                 f"现金 {summary['cash_pct']:.0%}")
    if summary.get("qqq_beta") is not None:
        lines.append(f"QQQ等效β: {summary['qqq_beta']:.2f}")
    total_pnl = summary.get("total_pnl", 0)
    total_pnl_pct = summary.get("total_pnl_pct", 0)
    lines.append(f"累计: ${total_pnl:+,.0f} ({total_pnl_pct:+.1%})")

    if summary.get("sector_warnings"):
        lines.append("\n行业集中度:")
        for sector, weight in summary.get("sectors", {}).items():
            if sector.startswith("_"):
                continue
            flag = " ⚠️" if weight > 0.40 else ""
            lines.append(f"  {sector} {weight:.0%}{flag}")

    dna_dist = summary.get("dna_distribution", "")
    if dna_dist:
        lines.append(f"\n持仓: {summary['total_positions']} 只 | {dna_dist}")
    lines.append("")

    # Block 3: Kill Conditions
    if kill_conditions:
        lines.append("📋 *退出条件审视*\n")
        for symbol, kcs in kill_conditions.items():
            dna = kcs.get("dna", "?")
            for kc in kcs.get("conditions", []):
                lines.append(f"{symbol} ({dna}): {kc}")
        lines.append("")

    return "\n".join(lines)


# ---- 主流程 ----

def run_intelligence(dry_run: bool = False) -> str:
    """运行完整 Intelligence 管道, 返回格式化报告."""
    import sqlite3
    from portfolio.holdings.manager import PortfolioManager

    store = get_store()
    mgr = PortfolioManager(store=store)
    positions = mgr.load_holdings()

    if not positions:
        msg = "📊 Portfolio Intelligence: 无持仓"
        if not dry_run:
            send_telegram(msg)
        return msg

    # Load prices from market.db
    conn = sqlite3.connect(str(MARKET_DB_PATH))
    conn.row_factory = sqlite3.Row
    prices_map = {}  # symbol -> DataFrame
    price_latest = {}  # symbol -> float

    for p in positions:
        rows = conn.execute(
            "SELECT date, open, high, low, close, volume FROM daily_price "
            "WHERE symbol = ? ORDER BY date DESC LIMIT 200",
            (p.symbol,),
        ).fetchall()
        if rows:
            df = pd.DataFrame([dict(r) for r in reversed(rows)])
            df["close"] = pd.to_numeric(df["close"])
            df["volume"] = pd.to_numeric(df["volume"])
            prices_map[p.symbol] = df
            price_latest[p.symbol] = df["close"].iloc[-1]
    conn.close()

    # NAV + weights
    nav = mgr.get_total_nav(price_latest)
    cash = store.get_cash_balance()
    invested = nav - cash
    positions_refreshed = mgr.refresh_prices(price_latest)

    weights = {p.symbol: p.current_weight for p in positions_refreshed}

    # ---- 信号检测 ----
    action_signals = []

    for p in positions_refreshed:
        df = prices_map.get(p.symbol)
        if df is None:
            continue

        # PMARP
        pmarp = analyze_pmarp(df)
        if pmarp and pmarp.get("current") is not None:
            if pmarp["current"] >= 98:
                action_signals.append(f"{p.symbol} | PMARP {pmarp['current']:.1f}% ⬆️ 超涨预警")
            elif pmarp["current"] <= 2:
                action_signals.append(f"{p.symbol} | PMARP {pmarp['current']:.1f}% ⬇️ 超跌")

        # RVOL
        rvol = analyze_rvol(df)
        if rvol and rvol.get("sigma") is not None and rvol["sigma"] >= 2:
            chg = (df["close"].iloc[-1] / df["close"].iloc[-2] - 1) * 100 if len(df) > 1 else 0
            action_signals.append(f"{p.symbol} | RVOL {rvol['sigma']:.1f}σ 异常放量 | 当日 {chg:+.1f}%")

        # EMA120
        ema_signal = check_ema120(df)
        if ema_signal:
            action_signals.append(
                f"{p.symbol} | 跌破 EMA120 (${ema_signal['price']:.2f} < ${ema_signal['ema120']:.2f})"
            )

        # 成本预警
        cost_signal = check_cost_alert(p.symbol, p.cost_basis, price_latest.get(p.symbol, 0), p.dna_rating)
        if cost_signal:
            action_signals.append(f"{p.symbol} | {cost_signal['message']} ⚠️")

    # ---- 组合指标 ----
    sector_conc = calc_sector_concentration([
        {"sector": p.sector, "weight": p.current_weight} for p in positions_refreshed
    ])

    # QQQ Beta
    qqq_df = None
    try:
        conn = sqlite3.connect(str(MARKET_DB_PATH))
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT date, close FROM daily_price WHERE symbol = 'QQQ' ORDER BY date DESC LIMIT 200"
        ).fetchall()
        conn.close()
        if rows:
            qqq_df = pd.DataFrame([dict(r) for r in reversed(rows)])
            qqq_df["close"] = pd.to_numeric(qqq_df["close"])
    except Exception:
        pass

    qqq_beta = calc_qqq_beta(
        [p.symbol for p in positions_refreshed], prices_map, qqq_df, weights
    )

    # DNA distribution
    dna_counts = {}
    for p in positions_refreshed:
        d = p.dna_rating or "?"
        dna_counts[d] = dna_counts.get(d, 0) + 1
    dna_dist = " ".join(f"{k}×{v}" for k, v in sorted(dna_counts.items()))

    # ---- Kill Conditions + Timing Changes ----
    kc_data = {}
    for p in positions_refreshed:
        kcs = store.get_kill_conditions(p.symbol, active_only=True)
        if kcs:
            kc_data[p.symbol] = {
                "dna": p.dna_rating,
                "conditions": [c["description"] for c in kcs],
            }

        # Timing change
        history = store.get_oprms_history(p.symbol)
        if len(history) >= 2:
            change = detect_timing_change(history)
            if change:
                action_signals.append(
                    f"{p.symbol} | OPRMS 变化: "
                    f"DNA {change['old_dna']}→{change['new_dna']} "
                    f"Timing {change['old_timing']}→{change['new_timing']}"
                )

    # ---- 格式化 ----
    summary = {
        "total_nav": nav,
        "invested_pct": invested / nav if nav > 0 else 0,
        "cash_pct": cash / nav if nav > 0 else 0,
        "qqq_beta": qqq_beta,
        "total_pnl": sum(p.unrealized_pnl for p in positions_refreshed),
        "total_pnl_pct": sum(p.unrealized_pnl for p in positions_refreshed) / invested if invested > 0 else 0,
        "sectors": {k: v for k, v in sector_conc.items() if not k.startswith("_")},
        "sector_warnings": sector_conc.get("_warnings", []),
        "total_positions": len(positions_refreshed),
        "dna_distribution": dna_dist,
    }

    report = format_report(action_signals, summary, kc_data)

    if not dry_run:
        send_telegram(report)

    return report


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Portfolio Intelligence")
    parser.add_argument("--dry-run", action="store_true", help="Print report without sending Telegram")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO)
    report = run_intelligence(dry_run=args.dry_run)
    print(report)
