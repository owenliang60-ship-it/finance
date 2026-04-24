#!/usr/bin/env python3
"""Portfolio Intelligence — 持仓感知情报引擎.

每日 22:00 SGT cron 运行，推送持仓级信号到 Telegram。
三区块报告：行动信号 / 组合概览 / Kill Conditions。
"""
import os
import sys
import logging
from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd
from config.settings import MARKET_DB_PATH
from terminal.company_store import get_store
from src.indicators.pmarp import analyze_pmarp
from src.indicators.rvol import analyze_rvol
from src.telegram_bot import send_message, split_message

logger = logging.getLogger(__name__)

# ---- DNA 浮亏阈值 ----
DNA_LOSS_THRESHOLDS = {"S": -0.30, "A": -0.20, "B": -0.15, "C": -0.10}

# ---- HK / USD 汇率 ----
USD_HKD_RATE = 7.8366


def require_cloud_env(allow_local: bool = False) -> None:
    """Guard MarketData live quotes behind the cloud runtime."""
    finance_env = os.environ.get("FINANCE_ENV")
    if finance_env == "cloud":
        return

    message = (
        "Portfolio Intelligence live quotes require FINANCE_ENV=cloud "
        f"(got {finance_env or 'unset'}) to avoid burning MarketData credits "
        "from a non-whitelisted IP"
    )
    if allow_local:
        logger.warning("%s; proceeding because local override was requested", message)
        return
    raise RuntimeError(message + ". Re-run with --allow-local only for explicit local testing.")


# ---- HK ticker helpers ----

def is_hk_ticker(symbol: str) -> bool:
    """Check if symbol is a HK stock (all-digit, typically 4-5 digits)."""
    return symbol.isdigit()


def to_yfinance_ticker(symbol: str) -> str | None:
    """Convert HK ticker to yfinance format. Returns None for non-HK.

    HKEX uses 5-digit codes (07709), Yahoo uses 4-digit (7709.HK).
    4-digit codes like 0700 stay as 0700.HK (not 700.HK).
    """
    if not is_hk_ticker(symbol):
        return None
    # Last 4 digits: handles both 5-digit HKEX (07709→7709) and 4-digit (0700→0700)
    code = symbol[-4:] if len(symbol) >= 4 else symbol
    return f"{code}.HK"


def _yf_download_hk(yf_symbol: str, period: str = "200d") -> pd.DataFrame | None:
    """Fetch HK price history via yfinance. Returns DataFrame or None."""
    try:
        import yfinance as yf
        import time
        time.sleep(1)  # yfinance rate limit
        data = yf.download(yf_symbol, period=period, progress=False, timeout=15)
        if data is not None and not data.empty:
            return data
    except Exception as e:
        logger.warning("[yfinance] Failed to fetch %s: %s", yf_symbol, e)
    return None


def fetch_hk_prices(hk_symbols: list) -> dict:
    """Fetch latest prices for HK tickers. Returns {symbol: price_usd}."""
    result = {}
    for sym in hk_symbols:
        yf_ticker = to_yfinance_ticker(sym)
        if not yf_ticker:
            continue
        data = _yf_download_hk(yf_ticker)
        if data is not None and len(data) > 0:
            close_col = "Close"
            if hasattr(data.columns, "get_level_values"):
                # MultiIndex columns from yf.download
                close_col = ("Close", yf_ticker) if ("Close", yf_ticker) in data.columns else "Close"
            hkd_price = float(data[close_col].iloc[-1])
            result[sym] = hkd_price / USD_HKD_RATE
            logger.info("[yfinance] %s (%s): HKD %.2f → USD %.4f", sym, yf_ticker, hkd_price, result[sym])
    return result


def fetch_hk_price_history(hk_symbols: list) -> dict:
    """Fetch price history DataFrames for HK tickers. Returns {symbol: DataFrame}."""
    result = {}
    for sym in hk_symbols:
        yf_ticker = to_yfinance_ticker(sym)
        if not yf_ticker:
            continue
        data = _yf_download_hk(yf_ticker)
        if data is not None and len(data) > 0:
            close_col = "Close"
            volume_col = "Volume"
            if hasattr(data.columns, "get_level_values"):
                close_col = ("Close", yf_ticker) if ("Close", yf_ticker) in data.columns else "Close"
                volume_col = ("Volume", yf_ticker) if ("Volume", yf_ticker) in data.columns else "Volume"
            df = pd.DataFrame({
                "date": data.index,
                "close": pd.to_numeric(data[close_col]) / USD_HKD_RATE,
                "volume": pd.to_numeric(data[volume_col]),
            }).reset_index(drop=True)
            # Add OHLC columns for indicator compatibility
            df["open"] = df["close"] * 0.999
            df["high"] = df["close"] * 1.001
            df["low"] = df["close"] * 0.999
            result[sym] = df
    return result

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


def _date_returns(df: pd.DataFrame) -> pd.Series:
    """Extract close returns as a string-date-indexed Series.

    Normalizes dates to 'YYYY-MM-DD' strings so Timestamp (yfinance)
    and string (SQLite) sources align correctly on inner join.
    """
    closes = df["close"].astype(float).values
    if "date" in df.columns:
        idx = pd.to_datetime(df["date"]).dt.strftime("%Y-%m-%d").values
    else:
        idx = range(len(closes))
    s = pd.Series(closes, index=idx)
    return s.pct_change().dropna()


def calc_qqq_beta(symbols: list, prices_map: dict, qqq_df: pd.DataFrame,
                  weights: dict, lookback: int = 60) -> float | None:
    """等效 QQQ Beta = sum(weight_i * beta_i). Aligns on date column."""
    if qqq_df is None or "close" not in qqq_df.columns:
        return None
    qqq_ret = _date_returns(qqq_df)
    if len(qqq_ret) < 20:
        return None
    total_beta = 0.0
    for sym in symbols:
        df = prices_map.get(sym)
        if df is None:
            continue
        sym_ret = _date_returns(df)
        aligned = pd.DataFrame({"sym": sym_ret, "qqq": qqq_ret}).dropna().tail(lookback)
        if len(aligned) < 20:
            continue
        cov = aligned["sym"].cov(aligned["qqq"])
        var = aligned["qqq"].var()
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


def _send_private_report(message: str, dry_run: bool = False) -> str:
    """Deliver a portfolio report to the private channel unless dry-run."""
    if not dry_run:
        for part in split_message(message):
            send_message(part, channel="private")
    return message


def _latest_signal_date(df: pd.DataFrame) -> str | None:
    """Extract the most recent YYYY-MM-DD date from a price frame."""
    if df is None or len(df) == 0 or "date" not in df.columns:
        return None
    value = pd.to_datetime(df["date"].iloc[-1])
    return value.strftime("%Y-%m-%d")


# ---- positions-as-of helper ----

def get_positions_as_of(store) -> dict:
    """Return position-book freshness timestamps as YYYY-MM-DD strings.

    Returns a dict with two keys:
      - "latest": MAX(last_updated) across holdings/option_positions/portfolio_cash.
        Represents "was the position book touched recently"; None if all empty.
      - "oldest_open_option": MIN(last_updated) among OPEN option_positions only.
        Represents "is there a stale OPEN leg hiding"; None if no OPEN legs.

    The header renderer uses both so a frequently-updated cash row cannot mask
    a months-old OPEN option leg — the exact visibility gap this helper is
    designed to close.
    """
    conn = store._get_conn()
    candidates: list[str] = []

    row = conn.execute(
        "SELECT MAX(last_updated) FROM holdings WHERE status = 'OPEN'"
    ).fetchone()
    if row and row[0]:
        candidates.append(row[0])

    row = conn.execute(
        "SELECT MAX(last_updated) FROM option_positions WHERE status = 'OPEN'"
    ).fetchone()
    if row and row[0]:
        candidates.append(row[0])

    row = conn.execute(
        "SELECT MAX(updated_at) FROM portfolio_cash"
    ).fetchone()
    if row and row[0]:
        candidates.append(row[0])

    # Oldest OPEN option leg — independent field to surface stale legs.
    row = conn.execute(
        "SELECT MIN(last_updated) FROM option_positions WHERE status = 'OPEN'"
    ).fetchone()
    oldest_open_option = row[0][:10] if row and row[0] else None

    # ISO timestamps sort lexicographically. Slice to date portion.
    latest = max(candidates)[:10] if candidates else None
    return {"latest": latest, "oldest_open_option": oldest_open_option}


# ---- 格式化 ----

def format_report(
    action_signals: list,
    summary: dict,
    kill_conditions: dict,
    snapshot_line: str | None = None,
) -> str:
    """格式化 3 区块 Telegram 报告."""
    lines = []

    if snapshot_line:
        lines.append(snapshot_line)
        lines.append("")

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

def run_intelligence(dry_run: bool = False, allow_local: bool = False) -> str:
    """运行完整 Intelligence 管道, 返回格式化报告."""
    import sqlite3
    from portfolio.holdings.manager import PortfolioManager
    from portfolio.holdings.live_quote_provider import (
        QuoteResult,
        fetch_option_live_quotes,
        fetch_stock_live_quotes,
    )

    store = get_store()
    mgr = PortfolioManager(store=store)
    positions = mgr.load_holdings()
    option_positions = store.get_open_option_positions()
    cash = store.get_cash_balance()

    if not positions and not option_positions and cash <= 0:
        msg = "📊 Portfolio Intelligence: 无持仓"
        return _send_private_report(msg, dry_run=dry_run)

    # Load prices from market.db
    conn = sqlite3.connect(str(MARKET_DB_PATH))
    conn.row_factory = sqlite3.Row
    prices_map = {}  # symbol -> DataFrame
    price_latest = {}  # symbol -> float

    no_price_symbols = []
    fallback_symbols = []
    signals_as_of = None
    hk_symbols = [p.symbol for p in positions if is_hk_ticker(p.symbol)]

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
    conn.close()

    for df in prices_map.values():
        latest_date = _latest_signal_date(df)
        if latest_date and (signals_as_of is None or latest_date > signals_as_of):
            signals_as_of = latest_date

    us_symbols = [p.symbol for p in positions if not is_hk_ticker(p.symbol)]
    if us_symbols or option_positions:
        require_cloud_env(allow_local=allow_local)

    stock_live_result = QuoteResult()
    if us_symbols:
        stock_live_result = fetch_stock_live_quotes(us_symbols)
        price_latest.update(stock_live_result.prices)
        if stock_live_result.credit_header_available:
            logger.info(
                "Stock live quotes: %d/%d success, failed=%s, request_count=%d, credits=%s/%s",
                len(stock_live_result.prices), len(us_symbols), stock_live_result.failed,
                stock_live_result.request_count, stock_live_result.credits_used,
                stock_live_result.credits_remaining,
            )
        else:
            logger.info(
                "Stock live quotes: %d/%d success, failed=%s, request_count=%d (credit header unavailable)",
                len(stock_live_result.prices), len(us_symbols), stock_live_result.failed,
                stock_live_result.request_count,
            )

        for sym in stock_live_result.failed:
            df = prices_map.get(sym)
            if df is not None and len(df) > 0:
                fallback_price = float(df["close"].iloc[-1])
                fallback_date = _latest_signal_date(df) or "unknown"
                price_latest[sym] = fallback_price
                fallback_symbols.append(sym)
                logger.warning(
                    "[fallback] %s -> T-1 close $%.2f from %s",
                    sym, fallback_price, fallback_date,
                )

    # Fetch HK prices via yfinance (not in market.db)
    if hk_symbols:
        logger.info("Fetching HK prices for %s...", hk_symbols)
        hk_prices = fetch_hk_prices(hk_symbols)
        hk_history = fetch_hk_price_history(hk_symbols)
        for sym, usd_price in hk_prices.items():
            price_latest[sym] = usd_price
        for sym, df in hk_history.items():
            prices_map[sym] = df
            latest_date = _latest_signal_date(df)
            if latest_date and (signals_as_of is None or latest_date > signals_as_of):
                signals_as_of = latest_date

    # Mark symbols with no price at all
    for p in positions:
        if p.symbol not in price_latest and p.cost_basis > 0:
            price_latest[p.symbol] = p.cost_basis
            no_price_symbols.append(p.symbol)

    # Fetch option live prices via MarketData
    option_prices = {}
    option_live_result = QuoteResult()
    if option_positions:
        logger.info("Fetching option prices for %d legs...", len(option_positions))
        option_live_result = fetch_option_live_quotes(option_positions)
        option_prices = option_live_result.prices
        if option_live_result.credit_header_available:
            logger.info(
                "Option live quotes: %d/%d success, failed=%s, request_count=%d, credits=%s/%s",
                len(option_prices), len(option_positions), option_live_result.failed,
                option_live_result.request_count, option_live_result.credits_used,
                option_live_result.credits_remaining,
            )
        else:
            logger.info(
                "Option live quotes: %d/%d success, failed=%s, request_count=%d (credit header unavailable)",
                len(option_prices), len(option_positions), option_live_result.failed,
                option_live_result.request_count,
            )

    # NAV + weights (now with HK + option prices)
    nav = mgr.get_total_nav(price_latest, option_prices)
    invested = nav - cash
    positions_refreshed = mgr.refresh_prices(price_latest, option_prices)

    weights = {p.symbol: p.current_weight for p in positions_refreshed}

    # ---- 信号检测 ----
    action_signals = []

    for sym in no_price_symbols:
        action_signals.append(f"{sym} | 无市场数据，使用成本价估算 ⚠️")

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
    except Exception as e:
        logger.warning("Failed to load QQQ benchmark prices: %s", e)

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
    # Option P&L: (live_premium - avg_premium) * quantity * 100
    option_pnl = 0.0
    for op in option_positions:
        key = (op["symbol"], op["expiration"], op["strike"], op["side"])
        live = option_prices.get(key)
        if live is not None:
            option_pnl += (live - op["avg_premium"]) * op["quantity"] * 100

    stock_pnl = sum(p.unrealized_pnl for p in positions_refreshed)
    total_pnl = stock_pnl + option_pnl

    summary = {
        "total_nav": nav,
        "invested_pct": invested / nav if nav > 0 else 0,
        "cash_pct": cash / nav if nav > 0 else 0,
        "qqq_beta": qqq_beta,
        "total_pnl": total_pnl,
        "total_pnl_pct": total_pnl / invested if invested > 0 else 0,
        "sectors": {k: v for k, v in sector_conc.items() if not k.startswith("_")},
        "sector_warnings": sector_conc.get("_warnings", []),
        "total_positions": len(positions_refreshed),
        "dna_distribution": dna_dist,
    }

    et_now = datetime.now(ZoneInfo("America/New_York"))
    positions_as_of = get_positions_as_of(store)
    latest = positions_as_of["latest"]
    oldest_open_option = positions_as_of["oldest_open_option"]
    snapshot_line = (
        f"📍 NAV 快照 ET {et_now.strftime('%Y-%m-%d %H:%M')} "
        f"| positions as of {latest or 'unknown'} "
        f"| live {len(stock_live_result.prices)}/{len(us_symbols)} "
        f"| signals as of {signals_as_of or 'unknown'}"
    )
    if oldest_open_option and oldest_open_option != latest:
        snapshot_line += f" | oldest open option {oldest_open_option}"
    if fallback_symbols:
        snapshot_line += f" | ⚠️ fallback: {','.join(fallback_symbols)}"
    if option_positions:
        snapshot_line += f" | opt {len(option_prices)}/{len(option_positions)}"
        if option_live_result.failed:
            snapshot_line += f" (⚠️ fail {len(option_live_result.failed)})"

    credit_fragments = []
    if stock_live_result.request_count > 0:
        if stock_live_result.credit_header_available:
            credit_fragments.append(
                f"stock credits {stock_live_result.credits_used or '?'}"
                f"/{stock_live_result.credits_remaining or '?'}"
            )
    if option_live_result.request_count > 0:
        if option_live_result.credit_header_available:
            credit_fragments.append(
                f"option credits {option_live_result.credits_used or '?'}"
                f"/{option_live_result.credits_remaining or '?'}"
            )
    if credit_fragments:
        snapshot_line += " | " + " | ".join(credit_fragments)
    elif stock_live_result.request_count > 0 or option_live_result.request_count > 0:
        snapshot_line += " | credit header unavailable"

    report = format_report(action_signals, summary, kc_data, snapshot_line=snapshot_line)

    return _send_private_report(report, dry_run=dry_run)


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Portfolio Intelligence")
    parser.add_argument("--dry-run", action="store_true", help="Print report without sending Telegram")
    parser.add_argument(
        "--allow-local",
        action="store_true",
        help="Explicitly allow local runs outside FINANCE_ENV=cloud",
    )
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO)
    report = run_intelligence(dry_run=args.dry_run, allow_local=args.allow_local)
    print(report)
