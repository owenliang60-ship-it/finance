#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
未来资本 Broad Market RVOL Scan — 池外广扫

每日扫描市值 >= 指定门槛的全市场股票，检测 RVOL 异常 + 强势上涨，
排除现有股票池后推送池外新面孔。

用法:
    python scripts/broad_market_scan.py
    python scripts/broad_market_scan.py --no-telegram
    python scripts/broad_market_scan.py --refresh-universe
    python scripts/broad_market_scan.py --min-mcap 100
"""

import os
import sys
import time
import json
import argparse
import logging
from datetime import datetime, date, timedelta
from pathlib import Path
from typing import Dict, Iterable, List, Optional

import pandas as pd
import requests

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from config.settings import (
    BROAD_SCAN_GROUP_ALERT_MIN_ADV,
    BROAD_SCAN_GROUP_ALERT_MIN_MCAP,
    BROAD_SCAN_HIGH_TIER_MCAP,
    BROAD_SCAN_HIGH_TIER_STREAK,
    BROAD_SCAN_UNIVERSE_SOURCE,
    BROAD_UNIVERSE_MIN_MCAP_USD,
    SCANS_DIR,
)
from src.data import load_universe
from src.indicators.rvol import calculate_rvol
from src.telegram_bot import send_message, split_message

os.environ.setdefault("XDG_CACHE_HOME", str(SCANS_DIR / ".cache"))

import yfinance as yf

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

BROAD_SCAN_LOOKBACK = 120
BROAD_SCAN_RVOL_THRESHOLD = 3.0
BROAD_SCAN_RETURN_THRESHOLD = 3.0
BROAD_SCAN_TOP_N = 20
BROAD_SCAN_CACHE_MAX_AGE_DAYS = 7
BROAD_SCAN_RETENTION_DAYS = 30
SCREEN_PAGE_SIZE = 250
DOWNLOAD_CHUNK_SIZE = 200

UNIVERSE_CACHE_PATH = SCANS_DIR / "broad_universe.json"
TRACKER_PATH = SCANS_DIR / "broad_scan_tracker.json"
YF_CACHE_DIR = SCANS_DIR / ".yfinance"

def _send_group_message(message: str) -> bool:
    """Route a single message to the public group."""
    return send_message(message, channel="group")


def _send_group_report(report: str) -> bool:
    """Send the broad scan report to the public group, splitting when needed."""
    ok = True
    for part in split_message(report, split_marker="🟡 今日新触发"):
        ok = _send_group_message(part) and ok
    return ok


def _read_json(path: Path) -> dict:
    if not path.exists():
        return {}
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _write_json(path: Path, data: dict):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2, default=str)


def _normalize_company_name(name: str) -> str:
    """复制 pool_manager 的公司名标准化逻辑，避免耦合到 FMP 模块。"""
    return (name.lower()
            .replace(" inc.", "")
            .replace(" inc", "")
            .replace(" corp.", "")
            .replace(" corp", "")
            .replace(" ltd.", "")
            .replace(" ltd", "")
            .replace(" llc", "")
            .replace(" plc", "")
            .replace(" class a", "")
            .replace(" class b", "")
            .replace(" class c", "")
            .replace(",", "")
            .strip())


def _format_market_cap(market_cap: Optional[float]) -> str:
    if not market_cap:
        return "N/A"
    return "${:.0f}亿".format(float(market_cap) / 1e8)


def _format_min_mcap_label(min_mcap_b: float) -> str:
    return "${:g}亿".format(min_mcap_b * 10)


def _get_weekday(dt: date) -> str:
    return ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"][dt.weekday()]


def _parse_iso_date(value: str) -> Optional[date]:
    if not value:
        return None
    return datetime.strptime(value, "%Y-%m-%d").date()


def _is_cache_fresh(cache: dict, min_mcap: int, max_age_days: int) -> bool:
    updated = _parse_iso_date(cache.get("updated", ""))
    if updated is None:
        return False
    if cache.get("market_cap_threshold") != min_mcap:
        return False
    return (date.today() - updated).days <= max_age_days


def _deduplicate_quotes(quotes: Iterable[dict]) -> Dict[str, dict]:
    """同公司保留市值最大的一条记录。"""
    seen = {}
    for quote in quotes:
        if quote.get("quoteType") and quote.get("quoteType") != "EQUITY":
            continue

        symbol = quote.get("symbol")
        if not symbol:
            continue

        company_name = (
            quote.get("longName")
            or quote.get("shortName")
            or quote.get("displayName")
            or symbol
        )
        market_cap = quote.get("marketCap") or quote.get("intradaymarketcap") or 0
        entry = {
            "symbol": symbol,
            "marketCap": market_cap,
            "shortName": quote.get("shortName") or company_name,
            "longName": quote.get("longName") or company_name,
            "exchange": quote.get("exchange"),
        }

        name_key = _normalize_company_name(company_name)
        if name_key not in seen or market_cap > seen[name_key]["marketCap"]:
            seen[name_key] = entry

    deduped = {}
    for entry in seen.values():
        deduped[entry["symbol"]] = {
            "marketCap": entry["marketCap"],
            "shortName": entry["shortName"],
            "longName": entry["longName"],
            "exchange": entry["exchange"],
        }
    return deduped


def _screen_page(query, offset: int, size: int = SCREEN_PAGE_SIZE) -> dict:
    return yf.screen(
        query,
        offset=offset,
        size=size,
        sortField="intradaymarketcap",
        sortAsc=False,
    )


def fetch_universe_metadata(
    min_mcap_b: float,
    refresh: bool = False,
    cache_path: Path = UNIVERSE_CACHE_PATH,
    as_of_date: Optional[str] = None,
) -> dict:
    """获取或刷新 universe metadata cache。"""
    as_of_date = as_of_date or date.today().isoformat()
    if BROAD_SCAN_UNIVERSE_SOURCE == "market_db":
        from src.data.market_store import get_store

        cache = _read_json(cache_path)
        store = get_store()
        symbols = store.get_symbols_with_market_cap_at(
            as_of_date,
            BROAD_UNIVERSE_MIN_MCAP_USD,
            freshness_days=90,
        )
        bulk_caps = store.get_bulk_market_caps_at(as_of_date)
        stocks = {
            symbol: {
                "marketCap": bulk_caps.get(symbol),
                "shortName": symbol,
                "longName": symbol,
                "exchange": "DB",
            }
            for symbol in symbols
        }
        payload = {
            "updated": date.today().isoformat(),
            "market_cap_threshold": BROAD_UNIVERSE_MIN_MCAP_USD,
            "last_scan_date": cache.get("last_scan_date"),
            "source": "market_db",
            "stocks": stocks,
        }
        return payload

    min_mcap = int(min_mcap_b * 1_000_000_000)
    cache = _read_json(cache_path)

    if not refresh and _is_cache_fresh(cache, min_mcap, BROAD_SCAN_CACHE_MAX_AGE_DAYS):
        stocks = cache.get("stocks", {})
        logger.info("使用缓存 universe: %d 只", len(stocks))
        return cache

    query = yf.EquityQuery("and", [
        yf.EquityQuery("gte", ["intradaymarketcap", min_mcap]),
        yf.EquityQuery("is-in", ["exchange", "NMS", "NYQ"]),
    ])

    try:
        all_quotes: List[dict] = []
        total = None
        offset = 0

        while True:
            result = _screen_page(query, offset=offset, size=SCREEN_PAGE_SIZE)
            quotes = result.get("quotes", [])
            total = result.get("total", total)
            if not quotes:
                break

            all_quotes.extend(quotes)
            offset += len(quotes)
            logger.info("Universe 获取进度: %d/%s", offset, total or "?")

            if total is not None and offset >= total:
                break

        stocks = _deduplicate_quotes(all_quotes)
        payload = {
            "updated": date.today().isoformat(),
            "market_cap_threshold": min_mcap,
            "last_scan_date": cache.get("last_scan_date"),
            "stocks": stocks,
        }
        _write_json(cache_path, payload)
        logger.info("Universe 已刷新: %d 只", len(stocks))
        return payload
    except Exception as e:
        stocks = cache.get("stocks", {})
        if stocks and cache.get("market_cap_threshold") == min_mcap:
            logger.warning("yf.screen 失败，回退到旧 cache: %s", e)
            return cache
        raise


def _extract_field_frame(data: pd.DataFrame, field: str, symbols: List[str]) -> pd.DataFrame:
    if data is None or data.empty:
        return pd.DataFrame()

    if isinstance(data.columns, pd.MultiIndex):
        level0 = set(data.columns.get_level_values(0))
        level1 = set(data.columns.get_level_values(1))

        if field in level0:
            frame = data[field]
        elif field in level1:
            frame = data.xs(field, axis=1, level=1)
        else:
            return pd.DataFrame()

        if isinstance(frame, pd.Series):
            name = symbols[0] if len(symbols) == 1 else str(frame.name)
            return frame.to_frame(name=name)
        return frame.copy()

    if field not in data.columns:
        return pd.DataFrame()

    series = data[field]
    if isinstance(series, pd.Series):
        name = symbols[0] if len(symbols) == 1 else str(series.name or field)
        return series.to_frame(name=name)
    return series.copy()


def normalize_downloaded_frames(data: pd.DataFrame, symbols: List[str]) -> Dict[str, pd.DataFrame]:
    """把 yf.download 返回值规范成 {symbol: DataFrame(close, volume)}。"""
    close_frame = _extract_field_frame(data, "Close", symbols)
    volume_frame = _extract_field_frame(data, "Volume", symbols)

    normalized = {}
    available = [sym for sym in symbols if sym in close_frame.columns and sym in volume_frame.columns]

    for sym in available:
        frame = pd.concat(
            [
                pd.to_numeric(close_frame[sym], errors="coerce").rename("close"),
                pd.to_numeric(volume_frame[sym], errors="coerce").rename("volume"),
            ],
            axis=1,
        ).dropna()

        if not frame.empty:
            frame = frame.sort_index()
            normalized[sym] = frame

    return normalized


def download_price_frames(symbols: List[str]) -> Dict[str, pd.DataFrame]:
    if not symbols:
        return {}

    if hasattr(yf, "set_tz_cache_location"):
        YF_CACHE_DIR.mkdir(parents=True, exist_ok=True)
        yf.set_tz_cache_location(str(YF_CACHE_DIR))

    total_chunks = (len(symbols) + DOWNLOAD_CHUNK_SIZE - 1) // DOWNLOAD_CHUNK_SIZE
    frames = {}

    for i in range(0, len(symbols), DOWNLOAD_CHUNK_SIZE):
        chunk = symbols[i:i + DOWNLOAD_CHUNK_SIZE]
        chunk_no = i // DOWNLOAD_CHUNK_SIZE + 1
        logger.info("下载 chunk %d/%d (%d 只)...", chunk_no, total_chunks, len(chunk))

        try:
            data = yf.download(
                chunk,
                period="6mo",
                interval="1d",
                auto_adjust=False,
                progress=False,
                group_by="column",
                threads=True,
                timeout=20,
            )
            frames.update(normalize_downloaded_frames(data, chunk))
        except Exception as e:
            logger.warning("chunk %d 下载失败，跳过: %s", chunk_no, e)

        if i + DOWNLOAD_CHUNK_SIZE < len(symbols):
            time.sleep(1)

    logger.info("价格下载完成: %d/%d 只可用", len(frames), len(symbols))
    return frames


def load_pool_symbols() -> set:
    return {entry.get("symbol") for entry in load_universe() if entry.get("symbol")}


def scan_candidates(
    price_frames: Dict[str, pd.DataFrame],
    metadata: Dict[str, dict],
    pool_symbols: set,
) -> Dict[str, object]:
    """计算全市场触发结果，并返回池外候选。"""
    triggered = []

    for symbol, frame in price_frames.items():
        if len(frame) < BROAD_SCAN_LOOKBACK + 1:
            continue

        rvol = calculate_rvol(frame["volume"], lookback=BROAD_SCAN_LOOKBACK)
        if rvol is None:
            continue

        latest_close = frame["close"].iloc[-1]
        prev_close = frame["close"].iloc[-2]
        if prev_close <= 0:
            continue

        return_pct = (latest_close / prev_close - 1) * 100
        if rvol >= BROAD_SCAN_RVOL_THRESHOLD and return_pct >= BROAD_SCAN_RETURN_THRESHOLD:
            triggered.append({
                "symbol": symbol,
                "rvol": round(rvol, 2),
                "return_pct": round(return_pct, 2),
                "marketCap": metadata.get(symbol, {}).get("marketCap"),
                "shortName": metadata.get(symbol, {}).get("shortName", symbol),
                "in_pool": symbol in pool_symbols,
            })

    outside = [item for item in triggered if not item["in_pool"]]
    outside.sort(key=lambda x: (-x["rvol"], -x["return_pct"], x["symbol"]))

    latest_dates = [frame.index.max() for frame in price_frames.values() if not frame.empty]
    scan_date = max(latest_dates).date().isoformat() if latest_dates else date.today().isoformat()

    return {
        "scan_date": scan_date,
        "symbols_scanned": len(metadata),
        "symbols_with_data": len(price_frames),
        "triggered_total": len(triggered),
        "outside_total": len(outside),
        "outside_candidates": outside,
        "all_triggered": triggered,
    }


def _cleanup_tracker_records(records: Dict[str, dict], scan_date: str) -> Dict[str, dict]:
    current_date = _parse_iso_date(scan_date)
    cleaned = {}
    for symbol, record in records.items():
        if symbol == "_meta":
            continue
        last_seen = _parse_iso_date(record.get("last_seen", ""))
        if last_seen is None:
            continue
        if (current_date - last_seen).days <= BROAD_SCAN_RETENTION_DAYS:
            cleaned[symbol] = record
    return cleaned


def update_streak_tracker(
    tracker: dict,
    candidates: List[dict],
    scan_date: str,
) -> dict:
    prev_scan_date = tracker.get("_meta", {}).get("last_scan_date")
    updated = _cleanup_tracker_records(tracker, scan_date)

    for candidate in candidates:
        symbol = candidate["symbol"]
        previous = updated.get(symbol, {})

        if previous.get("last_seen") == prev_scan_date:
            consecutive_days = previous.get("consecutive_days", 0) + 1
        else:
            consecutive_days = 1

        updated[symbol] = {
            "first_seen": previous.get("first_seen", scan_date),
            "last_seen": scan_date,
            "consecutive_days": consecutive_days,
            "appearances": previous.get("appearances", 0) + 1,
            "max_rvol": max(previous.get("max_rvol", candidate["rvol"]), candidate["rvol"]),
            "max_return": max(previous.get("max_return", candidate["return_pct"]), candidate["return_pct"]),
        }

    updated["_meta"] = {"last_scan_date": scan_date}
    return updated


def apply_tracker_stats(candidates: List[dict], tracker: dict) -> List[dict]:
    enriched = []
    for candidate in candidates:
        record = tracker.get(candidate["symbol"], {})
        item = dict(candidate)
        item["consecutive_days"] = record.get("consecutive_days", 1)
        item["appearances"] = record.get("appearances", 1)
        enriched.append(item)
    return enriched


def compute_adv_20d(frame: Optional[pd.DataFrame]) -> float:
    if frame is None or frame.empty or len(frame) < 20:
        return 0.0
    dollar_volume = frame["close"] * frame["volume"]
    return float(dollar_volume.tail(20).mean())


def classify_tier(hit_meta: dict) -> str:
    market_cap = hit_meta.get("marketCap") or 0
    streak = hit_meta.get("consecutive_days", 1)
    if market_cap >= BROAD_SCAN_HIGH_TIER_MCAP or streak >= BROAD_SCAN_HIGH_TIER_STREAK:
        return "🔥"
    return "📊"


def split_group_candidates(
    candidates: List[dict], price_frames: Dict[str, pd.DataFrame]
) -> tuple[List[dict], List[dict]]:
    group_candidates: List[dict] = []
    log_only_candidates: List[dict] = []
    for candidate in candidates:
        item = dict(candidate)
        item["adv_20d"] = compute_adv_20d(price_frames.get(item["symbol"]))
        market_cap = item.get("marketCap") or 0
        if item["adv_20d"] < BROAD_SCAN_GROUP_ALERT_MIN_ADV:
            log_only_candidates.append(item)
        elif market_cap < BROAD_SCAN_GROUP_ALERT_MIN_MCAP:
            log_only_candidates.append(item)
        else:
            group_candidates.append(item)
    return group_candidates, log_only_candidates


def format_broad_scan_report(
    candidates: List[dict],
    symbols_scanned: int,
    triggered_total: int,
    outside_total: int,
    scan_date: str,
    min_mcap_b: float,
) -> str:
    today = _parse_iso_date(scan_date) or date.today()
    lines = [
        "*未来资本 池外广扫*",
        "{} ({})".format(today.isoformat(), _get_weekday(today)),
        "",
        "━━━ 池外广扫 (市值≥{}, 排除池内) ━━━".format(_format_min_mcap_label(min_mcap_b)),
        "",
    ]

    streaks = [x for x in candidates if x.get("consecutive_days", 1) >= 3]
    today_hits = [x for x in candidates if x.get("consecutive_days", 1) < 3]

    lines.append("🔴 连续出现 ≥3 天:")
    if streaks:
        for item in streaks:
            tier = classify_tier(item)
            lines.append(
                "  {} {} | RVOL {:.1f}σ | {:+.1f}% | 连续{}天 | {}".format(
                    tier,
                    item["symbol"],
                    item["rvol"],
                    item["return_pct"],
                    item["consecutive_days"],
                    _format_market_cap(item.get("marketCap")),
                )
            )
    else:
        lines.append("  无")

    lines.append("")
    lines.append("🟡 今日新触发 (RVOL ≥3σ, 涨≥3%):")
    if today_hits:
        for item in today_hits:
            streak_text = "首次" if item.get("consecutive_days", 1) <= 1 else "第{}天".format(item["consecutive_days"])
            tier = classify_tier(item)
            lines.append(
                "  {} {} | RVOL {:.1f}σ | {:+.1f}% | {} | {}".format(
                    tier,
                    item["symbol"],
                    item["rvol"],
                    item["return_pct"],
                    streak_text,
                    _format_market_cap(item.get("marketCap")),
                )
            )
    else:
        lines.append("  无")

    truncated = outside_total > len(candidates)
    stats = "📊 扫描 {:,}只 | 触发 {}只 | 池外 {}只".format(
        symbols_scanned,
        triggered_total,
        outside_total,
    )
    if truncated:
        stats += " | Top {} 截断".format(len(candidates))
    lines.extend(["", stats])

    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description="未来资本 Broad Market RVOL Scan")
    parser.add_argument("--min-mcap", type=float, default=5.0,
                        help="最低市值 ($B), 默认 5")
    parser.add_argument("--no-telegram", action="store_true",
                        help="不推送 Telegram")
    parser.add_argument(
        "--dry-run-send",
        action="store_true",
        help="Send the report to Telegram without mutating DB/tracker/cache state",
    )
    parser.add_argument("--refresh-universe", action="store_true",
                        help="强制刷新 universe metadata cache")
    args = parser.parse_args()

    logger.info("=" * 60)
    logger.info("未来资本 Broad Market RVOL Scan")
    logger.info("=" * 60)
    start_time = time.time()

    try:
        universe_cache = fetch_universe_metadata(
            min_mcap_b=args.min_mcap,
            refresh=args.refresh_universe,
            as_of_date=date.today().isoformat(),
        )
        metadata = universe_cache.get("stocks", {})
        symbols = sorted(metadata.keys())

        if not symbols:
            raise RuntimeError("未获取到任何 universe 股票")

        pool_symbols = load_pool_symbols()
        price_frames = download_price_frames(symbols)
        if len(price_frames) < 10:
            raise RuntimeError("有效价格数据不足: {}".format(len(price_frames)))

        scan_result = scan_candidates(price_frames, metadata, pool_symbols)
        scan_date = scan_result["scan_date"]

        from src.data.market_store import get_store
        tracker = _read_json(TRACKER_PATH)
        updated_tracker = update_streak_tracker(
            tracker=tracker,
            candidates=scan_result["outside_candidates"],
            scan_date=scan_date,
        )
        ranked_candidates = apply_tracker_stats(scan_result["outside_candidates"], updated_tracker)
        group_candidates, log_only_candidates = split_group_candidates(
            ranked_candidates,
            price_frames,
        )
        top_candidates = group_candidates[:BROAD_SCAN_TOP_N]

        if log_only_candidates:
            logger.info(
                "Filtered %d low-liquidity/group-threshold hits to log-only: %s",
                len(log_only_candidates),
                [item["symbol"] for item in log_only_candidates[:10]],
            )

        if not args.dry_run_send:
            store = get_store()
            db_rows = [
                {
                    "symbol": item["symbol"],
                    "date": scan_date,
                    "rvol": item["rvol"],
                    "return_pct": item["return_pct"],
                    "market_cap": item.get("marketCap"),
                    "in_pool": item.get("in_pool", False),
                }
                for item in scan_result["all_triggered"]
            ]
            store.save_broad_scan_hits(db_rows)
            _write_json(TRACKER_PATH, updated_tracker)
            universe_cache["last_scan_date"] = scan_date
            _write_json(UNIVERSE_CACHE_PATH, universe_cache)

        report = format_broad_scan_report(
            candidates=top_candidates,
            symbols_scanned=scan_result["symbols_scanned"],
            triggered_total=scan_result["triggered_total"],
            outside_total=len(group_candidates),
            scan_date=scan_date,
            min_mcap_b=(
                BROAD_UNIVERSE_MIN_MCAP_USD / 1_000_000_000
                if BROAD_SCAN_UNIVERSE_SOURCE == "market_db"
                else args.min_mcap
            ),
        )

        print(report)

        if not args.no_telegram:
            _send_group_report(report)

    except Exception as e:
        logger.error("Broad Market RVOL Scan 异常: %s", e)
        import traceback
        traceback.print_exc()

        if not args.no_telegram:
            error_msg = "*Broad Market RVOL Scan 异常*\n\n错误: {}".format(str(e)[:200])
            _send_group_message(error_msg)

        raise
    finally:
        elapsed = time.time() - start_time
        logger.info("Broad Market RVOL Scan 完成，耗时 %.1f 秒 (%.1f 分钟)", elapsed, elapsed / 60)
        logger.info("=" * 60)


if __name__ == "__main__":
    main()
