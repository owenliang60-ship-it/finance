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
import sqlite3
from datetime import datetime
from pathlib import Path

import pandas as pd

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from config.settings import (
    DATA_DIR, SCANS_DIR,
    DOLLAR_VOLUME_REPORT_N, DOLLAR_VOLUME_LOOKBACK,
    DV_ACCELERATION_THRESHOLD, RVOL_SUSTAINED_THRESHOLD,
    EXTENDED_UNIVERSE_MIN_MCAP_B, BROAD_UNIVERSE_MIN_MCAP_USD,
)
from src.data import get_symbols
from src.data.universe_resolver import current_base_universe
from src.data.premium_pool import load_premium_pool
from src.indicators.dv_acceleration import format_dv
from src.telegram_bot import send_document, send_message, send_photo, split_message

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
)
logger = logging.getLogger(__name__)

EXTENDED_LAYER_MIN_MCAP = EXTENDED_UNIVERSE_MIN_MCAP_B * 1_000_000_000
MORNING_SIGNAL_PRICE_ROWS = 180
SELECTION_COMPASS_PRICE_ROWS = 127
BETA_BENCHMARK = "SPY"   # 6 个月 beta 的回归基准（daily_price 始终含 SPY，price_fetcher.py:100）
LAYER_ORDER = ["extend"]  # R3: pool/extend merged into one layer (Task 15)
LAYER_LABELS = {
    "extend": "Extend ($10B+)",
}
LAYER_TOP_N = 8
MARKET_TIMING_TARGETS = ["SPY", "QQQ", "SOXX"]
MARKET_TIMING_PRICE_ROWS = 260
# S2 parameters are frozen from the buy-quality hardening study:
# docs/plans/2026-05-01-breadth-buy-quality-hardening.md
# docs/research/2026-05-01-breadth-buy-quality.md
S2_BREADTH_THRESHOLD = 0.30
S2_BREADTH_COOLDOWN_DAYS = 60

# 成交集中度 context（校准依据: docs/research/2026-07-24-volume-concentration-signal-stat-study.md）
VOLCONC_TOP_N = 50
VOLCONC_SMOOTH_DAYS = 20
VOLCONC_PCTILE_WINDOW = 252          # 分位分母 = 窗口内前 251 个值
VOLCONC_HIGH_PCTILE = 80.0
VOLCONC_DIR_LOOKBACK = 20
VOLCONC_ETF_EXCLUDE = ("SPY", "QQQ", "SOXX")
VOLCONC_LOOKBACK_TRADING_DAYS = 320  # 252 分位窗 + 20 平滑 + 20 churn lag + 余量
VOLCONC_MIN_ROWS = 292               # 有效截面日下限，低于此降级
VOLCONC_COMPLETENESS_RATIO = 0.95    # 末日截面完整性 guard
VOLCONC_MAX_STALE_STEPS = 3          # 末日不完整时最多回退天数

from terminal.concept_classifier import get_report_concept_classifier
from terminal.morning_html_report import compile_morning_html_report


def _get_concept_classifier():
    return get_report_concept_classifier()


def _concept_bucket_order() -> list[str]:
    # Delegate to the classifier's SINGLE gating rule (registry-usable → L2,
    # else legacy) so the visual order and group_items() never diverge — they
    # must agree, or text and image reports would group differently.
    return _get_concept_classifier()._active_bucket_order()


CONCEPT_BUCKET_ORDER = _concept_bucket_order()


def _send_group_message(message: str) -> bool:
    """Route a single message to the public group."""
    return send_message(message, channel="group")


def _send_group_report(message: str) -> bool:
    """Send the morning report to the public group, splitting when needed."""
    ok = True
    for part in split_message(message, split_marker="*D. Dollar Volume*"):
        ok = _send_group_message(part) and ok
    return ok


def _send_group_image_report(image_paths: list[Path], delivery: str = "document") -> bool:
    """Send each visual morning-report section to the public group."""
    ok = True
    total = len(image_paths)
    for idx, path in enumerate(image_paths, 1):
        caption = "未来资本晨报 {}/{} — {}".format(idx, total, path.stem)
        if delivery == "photo":
            sent = send_photo(str(path), caption=caption, channel="group")
        else:
            sent = send_document(str(path), caption=caption, channel="group")
        ok = sent and ok
    return ok


def _send_group_pdf_report(pdf_path: Path) -> bool:
    """Send the visual morning report as one PDF document."""
    caption = "未来资本晨报 PDF — {}".format(datetime.now().strftime("%Y-%m-%d"))
    return send_document(str(pdf_path), caption=caption, channel="group")


# ============================================================
# 格式化模块
# ============================================================

def _format_market_cap(market_cap: float | None) -> str:
    if not market_cap:
        return "N/A"
    if market_cap >= 1e12:
        return "${:.1f}T".format(market_cap / 1e12)
    if market_cap >= 1e9:
        return "${:.1f}B".format(market_cap / 1e9)
    return "${:.0f}M".format(market_cap / 1e6)


def _format_beta(beta: float | None) -> str:
    if beta is None or pd.isna(beta):
        return "—"
    return "{:.2f}".format(beta)


def _clean_company_name(name: str | None) -> str:
    if not name:
        return ""
    cleaned = str(name)
    suffixes = [
        ", Inc.", ", Inc", " Inc.", " Inc", " Corporation", " Corp.", " Corp", " Incorporated",
        " Class A Common Stock", " Common Stock", " plc", " Ltd.", " Ltd",
        " Limited", " N.V.", " S.A.",
    ]
    for suffix in suffixes:
        if cleaned.endswith(suffix):
            cleaned = cleaned[: -len(suffix)]
    return cleaned.strip()


def _display_company(item: dict, max_len: int = 22) -> str:
    symbol = item.get("symbol", "")
    name = (
        item.get("shortName")
        or item.get("companyName")
        or item.get("longName")
        or item.get("company_name")
        or ""
    )
    name = _clean_company_name(name)
    if not name or name.upper() == symbol.upper():
        return symbol
    if len(name) > max_len:
        name = name[: max_len - 1].rstrip() + "…"
    return "{} {}".format(symbol, name)


def _display_concept_tags(item: dict) -> str:
    """Return three-tier concept tags (e.g. '半导体 / 存储 / HBM') from the registry,
    or fall back to the legacy single-bucket label when the symbol is unregistered."""
    clf = _get_concept_classifier()
    tags = clf.display_tags(item)
    if tags:
        return tags
    return _concept_bucket(item)


def _normalize_metadata_entry(symbol: str, entry: dict) -> dict:
    return {
        "symbol": symbol.upper(),
        "companyName": (
            entry.get("companyName")
            or entry.get("company_name")
            or entry.get("name")
            or entry.get("longName")
            or entry.get("shortName")
            or ""
        ),
        "shortName": entry.get("shortName") or entry.get("companyName") or entry.get("company_name") or "",
        "longName": entry.get("longName") or entry.get("companyName") or entry.get("company_name") or "",
        "sector": entry.get("sector") or "",
        "industry": entry.get("industry") or "",
        "exchange": entry.get("exchange") or entry.get("exchangeShortName") or "",
        "marketCap": entry.get("marketCap") or entry.get("market_cap") or entry.get("mktCap"),
    }


def _merge_metadata_entry(metadata: dict, symbol: str, entry: dict) -> None:
    symbol = symbol.upper()
    normalized = _normalize_metadata_entry(symbol, entry)
    target = metadata.setdefault(symbol, {"symbol": symbol})
    for key, value in normalized.items():
        if value in (None, ""):
            continue
        if key == "marketCap":
            if not target.get(key):
                target[key] = value
        elif not target.get(key) or target.get(key) == symbol:
            target[key] = value


def _iter_profile_records(payload) -> list[dict]:
    if isinstance(payload, list):
        return [row for row in payload if isinstance(row, dict)]
    if isinstance(payload, dict):
        records = []
        for key, value in payload.items():
            if isinstance(value, dict):
                row = dict(value)
                row.setdefault("symbol", key)
                records.append(row)
        return records
    return []


def _merge_local_metadata(metadata: dict, symbols: list[str]) -> None:
    """Merge cheap local company metadata from company.db and JSON caches."""
    wanted = {symbol.upper() for symbol in symbols}

    try:
        from terminal.company_store import get_store
        for row in get_store().list_companies():
            symbol = (row.get("symbol") or "").upper()
            if symbol in wanted:
                _merge_metadata_entry(metadata, symbol, row)
    except Exception as exc:
        logger.info("company.db metadata unavailable for morning report: %s", exc)

    for path in [
        DATA_DIR / "pool" / "universe.json",
        DATA_DIR / "fundamental" / "profiles.json",
        SCANS_DIR / "broad_universe.json",
    ]:
        if not path.exists():
            continue
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:
            logger.info("metadata cache unreadable %s: %s", path, exc)
            continue
        if isinstance(payload, dict) and isinstance(payload.get("stocks"), dict):
            payload = payload["stocks"]
        for row in _iter_profile_records(payload):
            symbol = (row.get("symbol") or row.get("ticker") or "").upper()
            if symbol in wanted:
                _merge_metadata_entry(metadata, symbol, row)


def _metadata_has_company_classification(entry: dict) -> bool:
    name = (
        entry.get("companyName")
        or entry.get("shortName")
        or entry.get("longName")
        or ""
    )
    has_name = bool(name and name != entry.get("symbol"))
    return has_name and bool(entry.get("sector") or entry.get("industry"))


def _hydrate_signal_metadata(metadata: dict, symbols: list[str]) -> None:
    """Ensure triggered symbols have company names and classification if possible."""
    _merge_local_metadata(metadata, symbols)
    missing = [
        symbol for symbol in sorted({s.upper() for s in symbols})
        if not _metadata_has_company_classification(metadata.get(symbol, {}))
    ]
    if not missing:
        return

    try:
        from config.settings import FMP_API_KEY
        if not FMP_API_KEY:
            return
        from src.data.fmp_client import FMPClient
        client = FMPClient()
        for symbol in missing:
            profile = client.get_profile(symbol)
            if profile:
                _merge_metadata_entry(metadata, symbol, profile)
    except Exception as exc:
        logger.info("live metadata fallback skipped: %s", exc)


def _concept_bucket(item: dict) -> str:
    return _get_concept_classifier().classify(item)


def _grouping_bucket_for(item: dict) -> str:
    return _get_concept_classifier()._grouping_bucket(item)


def _layer_for_symbol(symbol: str, metadata: dict, pool_symbols: set) -> str:
    # R3 (Task 15): pool and extend used to be distinct layers; they are now
    # one merged "extend" layer. pool_symbols membership still bypasses the
    # $10B mcap filter (unchanged content), it just no longer gets its own
    # label.
    if symbol in pool_symbols:
        return "extend"
    market_cap = metadata.get(symbol, {}).get("marketCap") or 0
    if market_cap >= EXTENDED_LAYER_MIN_MCAP:
        return "extend"
    return "broad"


def _frame_with_date(symbol: str, frame) -> object:
    df = frame.reset_index().copy()
    if "date" not in df.columns:
        first = df.columns[0]
        df = df.rename(columns={first: "date"})
    df["symbol"] = symbol
    return df


def _group_by_layer(items: list) -> dict:
    grouped = {layer: [] for layer in LAYER_ORDER}
    for item in items:
        layer = item.get("layer", "broad")
        if layer not in LAYER_ORDER:
            logger.warning(
                "layer leak in _group_by_layer: %r (item=%s); row will be hidden",
                layer, item.get("symbol", "?"),
            )
        grouped.setdefault(layer, []).append(item)
    return grouped


def _format_layered_items(
    items: list,
    empty_text: str,
    formatter,
    limit_per_layer: int = LAYER_TOP_N,
) -> list[str]:
    if not items:
        return [empty_text]

    lines = []
    grouped = _group_by_layer(items)
    for layer in LAYER_ORDER:
        layer_items = grouped.get(layer, [])
        lines.append("{}:".format(LAYER_LABELS[layer]))
        if not layer_items:
            lines.append("  无")
            continue
        for item in layer_items[:limit_per_layer]:
            lines.append("  " + formatter(item))
        if len(layer_items) > limit_per_layer:
            lines.append("  ... +{} more".format(len(layer_items) - limit_per_layer))
    return lines


def _group_by_concept_bucket(items: list) -> dict:
    return _get_concept_classifier().group_items(items)


def _format_bucketed_items(items: list, empty_text: str, formatter) -> list[str]:
    if not items:
        return [empty_text]

    lines = []
    grouped = _group_by_concept_bucket(items)
    for bucket, bucket_items in grouped.items():
        lines.append("{} ({}):".format(bucket, len(bucket_items)))
        for item in bucket_items:
            lines.append("  " + formatter(item))
    return lines


def _format_bucketed_table(
    items: list,
    empty_text: str,
    header: str,
    formatter,
) -> list[str]:
    if not items:
        return [empty_text]

    lines = [header]
    grouped = _group_by_concept_bucket(items)
    for bucket, bucket_items in grouped.items():
        lines.append("{} ({}):".format(bucket, len(bucket_items)))
        for item in bucket_items:
            lines.append("  " + formatter(item))
    return lines


def _format_flat_table(
    items: list,
    empty_text: str,
    header: str,
    formatter,
) -> list[str]:
    """Render items as a single flat table in the given order (no grouping)."""
    if not items:
        return [empty_text]
    lines = [header]
    for item in items:
        lines.append("  " + formatter(item))
    return lines


def _compact_company(item: dict) -> str:
    symbol = item.get("symbol", "")
    name = _display_company(item, max_len=18)
    if name == symbol or name.startswith(symbol + " "):
        return name
    return symbol


def _premium_text_company(item: dict) -> str:
    company = _compact_company(item)
    return "🔴 *{}*".format(company) if item.get("is_premium") else company


def _premium_symbols_from_market_signals(market_signals: dict | None) -> set[str]:
    pool = (market_signals or {}).get("premium_pool") or {}
    if not pool.get("available"):
        return set()
    return {str(symbol).upper() for symbol in pool.get("symbols") or []}


def _enrich_with_layer(item: dict, metadata: dict, pool_symbols: set,
                       betas: dict | None = None,
                       premium_symbols: set[str] | None = None) -> dict:
    symbol = item["symbol"]
    meta = metadata.get(symbol, {})
    enriched = dict(item)
    for key in ["companyName", "shortName", "longName", "sector", "industry", "exchange"]:
        if meta.get(key):
            enriched[key] = meta[key]
    enriched["marketCap"] = meta.get("marketCap")
    layer = _layer_for_symbol(symbol, metadata, pool_symbols)
    if layer != "extend":
        raise ValueError(
            f"layer leak: {symbol!r} classified as {layer!r}; "
            f"expected extend after universe post-filter "
            f"(marketCap={meta.get('marketCap')!r})"
        )
    enriched["layer"] = layer
    enriched["beta_6m"] = (betas or {}).get(symbol)
    if premium_symbols is not None:
        enriched["is_premium"] = symbol in premium_symbols
    enriched["concept_bucket"] = _concept_bucket(enriched)
    return enriched


def _compute_signal_betas(
    price_frames: dict,
    signal_symbols: list,
) -> dict:
    """目标股票的 6 个月 beta（对 BETA_BENCHMARK）。基准序列只加载一次。

    price_frames: load_price_frames 输出（date 索引 close/volume frame，180 行）。
    基准缺失或个股不在 price_frames → None；调用层决定展示或门控语义。
    """
    from scripts.broad_market_scan import load_price_frames_from_market_db
    from src.indicators.beta import compute_beta

    targets = sorted(set(signal_symbols))
    if not targets:
        return {}
    try:
        bench_frames = load_price_frames_from_market_db(
            [BETA_BENCHMARK], rows_needed=MORNING_SIGNAL_PRICE_ROWS,
        )
    except Exception as exc:
        logger.warning("beta skipped: benchmark load failed (%s)", exc)
        return {symbol: None for symbol in targets}
    bench_frame = bench_frames.get(BETA_BENCHMARK)
    if bench_frame is None:
        logger.warning("beta skipped: benchmark %s missing from market.db", BETA_BENCHMARK)
        return {symbol: None for symbol in targets}
    bench_closes = bench_frame["close"]
    betas = {}
    for symbol in targets:
        frame = price_frames.get(symbol)
        betas[symbol] = compute_beta(frame["close"], bench_closes) if frame is not None else None
    return betas


def _load_selection_compass_market_cap_observations(
    store,
    symbols: list[str],
    as_of: str,
) -> dict[str, dict]:
    """Load each symbol's latest dated market-cap observation at ``as_of``.

    The existing bulk helper intentionally returns values only; the compass
    freshness gate also needs the observation date, so keep this read local
    and query the already-open MarketStore connection without mutating it.
    """
    universe = sorted(set(symbols))
    if not universe:
        return {}

    conn = store._get_conn()
    observations = {}
    chunk_size = 500
    for start in range(0, len(universe), chunk_size):
        chunk = universe[start:start + chunk_size]
        placeholders = ",".join("?" for _ in chunk)
        rows = conn.execute(
            f"""
            SELECT symbol, date, market_cap
            FROM (
                SELECT symbol, date, market_cap,
                       ROW_NUMBER() OVER (
                           PARTITION BY symbol ORDER BY date DESC
                       ) AS rn
                FROM historical_market_cap
                WHERE symbol IN ({placeholders})
                  AND date <= ?
            )
            WHERE rn = 1
            """,
            (*chunk, as_of),
        ).fetchall()
        for row in rows:
            observations[row["symbol"]] = {
                "date": row["date"],
                "marketCap": row["market_cap"],
            }
    return observations


def _load_market_timing_target_frames(
    targets: list[str] | None = None,
    rows_needed: int = MARKET_TIMING_PRICE_ROWS,
) -> dict[str, object]:
    """Load ETF close frames from market.db without mutating the database."""
    targets = targets or MARKET_TIMING_TARGETS
    db_path = DATA_DIR / "market.db"
    if not db_path.exists():
        logger.warning("market timing skipped: market.db missing at %s", db_path)
        return {}

    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        out = {}
        for symbol in targets:
            df = pd.read_sql(
                "SELECT date, close FROM daily_price "
                "WHERE symbol = ? ORDER BY date DESC LIMIT ?",
                conn,
                params=(symbol, rows_needed),
                parse_dates=["date"],
            )
            if df.empty:
                continue
            out[symbol] = df.sort_values("date").reset_index(drop=True)
        return out
    except Exception as exc:
        logger.warning("market timing target load failed: %s", exc)
        return {}
    finally:
        conn.close()


def _load_volume_concentration_frames(db_path: Path | None = None, as_of: str | None = None) -> dict:
    """Load market-level dollar-volume concentration frames from market.db.

    Read-only, single-pass CTE over daily_price; excludes VOLCONC_ETF_EXCLUDE
    from both the Top-N ranking and the total_dv denominator. db_path/as_of
    are test-injection hooks only (not exposed via CLI); default is
    DATA_DIR/market.db with no upper date bound (latest data).

    Never writes to market.db (P3 ownership — read-only connection only).
    Any failure (missing db/table, duplicate (date,symbol) rows, parse
    errors) degrades to {"available": False, "reason": <short phrase>}
    instead of raising; reason never contains a path or SQL fragment.

    Returns on success:
        {"available": True,
         "share_df": DataFrame indexed by ascending DatetimeIndex with
             columns "share" (top-N dv / total dv, float) and "n_symbols"
             (valid non-ETF symbol count that day, int),
         "members": Series aligned to share_df.index, values = frozenset of
             that day's Top-N symbols,
         "spy_close": Series indexed by ascending DatetimeIndex of SPY close}
    """
    start = time.time()
    db_path = Path(db_path) if db_path is not None else DATA_DIR / "market.db"
    if not db_path.exists():
        logger.warning("volconc loader: db missing")
        return {"available": False, "reason": "数据库读取失败"}

    etf_placeholders = ",".join("?" for _ in VOLCONC_ETF_EXCLUDE)
    as_of_clause = " AND date <= ?" if as_of else ""

    conn = None
    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        cutoff_sql = (
            "SELECT DISTINCT date FROM daily_price "
            "WHERE close > 0 AND volume > 0 "
            f"AND symbol NOT IN ({etf_placeholders})" + as_of_clause + " "
            "ORDER BY date DESC LIMIT ?"
        )
        cutoff_params = list(VOLCONC_ETF_EXCLUDE)
        if as_of:
            cutoff_params.append(as_of)
        cutoff_params.append(VOLCONC_LOOKBACK_TRADING_DAYS)
        cutoff_dates = pd.read_sql(cutoff_sql, conn, params=cutoff_params)["date"]
        if cutoff_dates.empty:
            return {"available": False, "reason": "历史数据不足"}
        start_date = cutoff_dates.min()

        rank_sql = (
            "WITH ranked AS ("
            " SELECT date, symbol, close*volume AS dv,"
            "        ROW_NUMBER() OVER (PARTITION BY date ORDER BY close*volume DESC) AS rn,"
            "        SUM(close*volume) OVER (PARTITION BY date) AS total_dv,"
            "        COUNT(*) OVER (PARTITION BY date) AS n_symbols"
            " FROM daily_price"
            f" WHERE date >= ?{as_of_clause} AND close > 0 AND volume > 0"
            f"   AND symbol NOT IN ({etf_placeholders}))"
            " SELECT date, symbol, dv, total_dv, n_symbols FROM ranked WHERE rn <= ?"
        )
        rank_params = [start_date]
        if as_of:
            rank_params.append(as_of)
        rank_params.extend(VOLCONC_ETF_EXCLUDE)
        rank_params.append(VOLCONC_TOP_N)
        ranked_df = pd.read_sql(rank_sql, conn, params=rank_params)

        spy_sql = (
            "SELECT date, close FROM daily_price "
            "WHERE symbol = 'SPY' AND date >= ?" + as_of_clause
        )
        spy_params = [start_date]
        if as_of:
            spy_params.append(as_of)
        spy_df = pd.read_sql(spy_sql, conn, params=spy_params)
    except (sqlite3.Error, pd.errors.DatabaseError) as exc:
        logger.warning("volconc loader: query failed (%s)", exc)
        return {"available": False, "reason": "数据库读取失败"}
    finally:
        if conn is not None:
            conn.close()

    try:
        ranked_df["date"] = pd.to_datetime(ranked_df["date"])
        if ranked_df.duplicated(subset=["date", "symbol"]).any():
            return {"available": False, "reason": "数据重复"}

        per_date = ranked_df.groupby("date").agg(
            top_dv=("dv", "sum"),
            total_dv=("total_dv", "first"),
            n_symbols=("n_symbols", "first"),
        )
        per_date["share"] = (per_date["top_dv"] / per_date["total_dv"]).astype(float)
        share_df = per_date[["share", "n_symbols"]].sort_index()
        share_df.index = pd.DatetimeIndex(share_df.index)
        share_df["n_symbols"] = share_df["n_symbols"].astype(int)

        members = ranked_df.groupby("date")["symbol"].apply(frozenset).sort_index()
        members.index = pd.DatetimeIndex(members.index)
        members = members.reindex(share_df.index)

        spy_df["date"] = pd.to_datetime(spy_df["date"])
        spy_close = spy_df.set_index("date")["close"].astype(float).sort_index()
        spy_close.index = pd.DatetimeIndex(spy_close.index)
    except (KeyError, ValueError, TypeError) as exc:
        logger.warning("volconc loader: parse failed (%s)", exc)
        return {"available": False, "reason": "数据库读取失败"}

    elapsed = time.time() - start
    logger.info("volconc loader: %d rows in %.2fs", len(ranked_df), elapsed)
    return {
        "available": True,
        "share_df": share_df,
        "members": members,
        "spy_close": spy_close,
    }


def _volconc_regime(share_pctile: float | None, spy_ret20: float | None) -> str:
    """Classify the volume-concentration regime from a share percentile and
    SPY 20d direction. Pure lookup, no rolling math — kept separate from
    _compute_volume_concentration_payload for isolated boundary testing.

    Labels mirror docs/research/2026-07-24-volume-concentration-signal-stat-study.md;
    spy_ret20 == 0 counts as non-positive (matches the research script's
    strict `> 0` boolean convention for direction).
    """
    if share_pctile is None:
        return "数据不足"
    if spy_ret20 is None:
        return "方向数据缺失"
    if share_pctile > VOLCONC_HIGH_PCTILE:
        return "高集中+上行（拥挤）" if spy_ret20 > 0 else "高集中+下行（恐慌）"
    return "常态"


def _volconc_roll_pctile(s: pd.Series, window: int) -> pd.Series:
    """Rolling percentile rank of the window's last value vs. its preceding
    values (denominator = window - 1). Matches research run_study.py
    roll_pctile() line-for-line."""
    return s.rolling(window).apply(lambda w: (w[-1] > w[:-1]).mean() * 100, raw=True)


def _volconc_section_complete(share_df: pd.DataFrame, idx: int) -> bool:
    """Dual-condition completeness check for the as_of candidate at position
    idx: n_symbols must clear both the absolute Top-N floor and
    VOLCONC_COMPLETENESS_RATIO x the trailing-20-row median (fewer than 20
    prior rows -> use what's available; zero prior rows -> incomplete)."""
    n = share_df["n_symbols"].iloc[idx]
    if n < VOLCONC_TOP_N:
        return False
    prior = share_df["n_symbols"].iloc[max(0, idx - 20):idx]
    if len(prior) == 0:
        return False
    return n >= VOLCONC_COMPLETENESS_RATIO * prior.median()


def _compute_volume_concentration_payload(
    share_df: pd.DataFrame, members_df: pd.Series, spy_close: pd.Series,
) -> dict:
    """Pure computation over _load_volume_concentration_frames' output ->
    volume-concentration payload. No IO, no global state.

    Order matters (see docs/plans/2026-07-24-morning-report-volume-concentration-context.md):
      1. empty/short-input guard
      2. as_of completeness guard (dual condition, up to
         VOLCONC_MAX_STALE_STEPS rollback days over the *untruncated* frame)
      3. staleness gate: if the *untruncated* spy_close (the market-calendar
         reference) has more than VOLCONC_MAX_STALE_STEPS sessions strictly
         after as_of, degrade — broad ingestion wrote zero rows for those
         trailing sessions, so share_df never saw them and the completeness
         guard above could never catch them by walking share_df's own dates
      4. truncate share_df/members_df/spy_close to index <= as_of BEFORE any
         rolling computation, so a partially-written latest session can
         never pollute a rolling window
      5. min-rows guard on the truncated frame
      6. SPY 20d direction (reindexed to share_df's own trading-day axis)
      7. share smoothing + rolling percentile
      8. churn (Top-N set turnover, 20-session lag) + smoothing + percentile
      9. NaN fallback if any as_of-row metric is still NaN
      10. payload assembly
    """
    if share_df is None or share_df.empty:
        return {"available": False, "reason": "历史数据不足"}

    n_rows = len(share_df)
    as_of_idx = None
    for step in range(VOLCONC_MAX_STALE_STEPS + 1):
        idx = n_rows - 1 - step
        if idx < 0:
            break
        if _volconc_section_complete(share_df, idx):
            as_of_idx = idx
            break
    if as_of_idx is None:
        return {"available": False, "reason": "最新截面不完整"}

    as_of = share_df.index[as_of_idx]

    if spy_close is not None and not spy_close.empty:
        if int((spy_close.index > as_of).sum()) > VOLCONC_MAX_STALE_STEPS:
            return {"available": False, "reason": "截面数据陈旧"}

    share_df = share_df.iloc[:as_of_idx + 1]
    members_df = members_df.iloc[:as_of_idx + 1]
    spy_close = spy_close.loc[spy_close.index <= as_of]

    if len(share_df) < VOLCONC_MIN_ROWS:
        return {"available": False, "reason": "历史数据不足"}

    spy_aligned = spy_close.reindex(share_df.index)
    ret20 = spy_aligned.pct_change(VOLCONC_DIR_LOOKBACK, fill_method=None)
    spy_ret20_raw = ret20.iloc[-1]
    spy_ret20 = None if pd.isna(spy_ret20_raw) else float(spy_ret20_raw)

    share_sm = share_df["share"].rolling(VOLCONC_SMOOTH_DAYS).mean()
    share_pctile = _volconc_roll_pctile(share_sm, VOLCONC_PCTILE_WINDOW)

    churn_lag = 20  # session-index lag; research run_study.py L59-62/261-264 (literal 20)
    member_values = members_df.to_list()
    churn_values = [float("nan")] * len(member_values)
    for i in range(churn_lag, len(member_values)):
        overlap = len(member_values[i - churn_lag] & member_values[i])
        churn_values[i] = 1 - overlap / VOLCONC_TOP_N
    churn = pd.Series(churn_values, index=share_df.index, dtype=float)
    churn_sm = churn.rolling(VOLCONC_SMOOTH_DAYS).mean()
    churn_pctile = _volconc_roll_pctile(churn_sm, VOLCONC_PCTILE_WINDOW)

    share_sm_last = share_sm.iloc[-1]
    share_pctile_last = share_pctile.iloc[-1]
    churn_sm_last = churn_sm.iloc[-1]
    churn_pctile_last = churn_pctile.iloc[-1]
    if (
        pd.isna(share_sm_last) or pd.isna(share_pctile_last)
        or pd.isna(churn_sm_last) or pd.isna(churn_pctile_last)
    ):
        return {"available": False, "reason": "历史数据不足"}

    regime = _volconc_regime(float(share_pctile_last), spy_ret20)
    return {
        "available": True,
        "as_of": as_of.date().isoformat(),
        "share_sm_pct": float(share_sm_last) * 100,
        "share_pctile_1y": float(share_pctile_last),
        "churn_sm_pct": float(churn_sm_last) * 100,
        "churn_pctile_1y": float(churn_pctile_last),
        "spy_ret20_pct": None if spy_ret20 is None else spy_ret20 * 100,
        "regime": regime,
    }


def _compute_breadth_s2_status(
    daily_breadth: object,
    threshold: float = S2_BREADTH_THRESHOLD,
    cooldown_days: int = S2_BREADTH_COOLDOWN_DAYS,
) -> dict:
    """Compute broad S2 participation and cooldown-aware upcross status."""
    from backtest.breadth_study.percentile_events import detect_upcross_events

    df = daily_breadth.copy()
    if "date" not in df.columns or "breadth_20" not in df.columns:
        return {
            "current": None,
            "previous": None,
            "as_of": None,
            "threshold": threshold,
            "cooldown_days": cooldown_days,
            "upcross": False,
            "error": "missing date or breadth_20",
        }

    df["date"] = pd.to_datetime(df["date"])
    df = df.sort_values("date").dropna(subset=["breadth_20"]).reset_index(drop=True)
    if df.empty:
        return {
            "current": None,
            "previous": None,
            "as_of": None,
            "threshold": threshold,
            "cooldown_days": cooldown_days,
            "upcross": False,
            "error": "no breadth_20 data",
        }

    signal = pd.to_numeric(df["breadth_20"], errors="coerce")
    signal.index = pd.DatetimeIndex(df["date"])
    events = detect_upcross_events(signal, threshold=threshold, cooldown_days=cooldown_days)
    latest_date = pd.Timestamp(df["date"].iloc[-1])
    latest_event = pd.Timestamp(events[-1]["label"]) if events else None

    return {
        "current": float(signal.iloc[-1]),
        "previous": float(signal.iloc[-2]) if len(signal) >= 2 else None,
        "as_of": latest_date.date().isoformat(),
        "threshold": threshold,
        "cooldown_days": cooldown_days,
        "upcross": latest_event == latest_date,
        "last_event_date": latest_event.date().isoformat() if latest_event is not None else None,
    }


def _empty_breadth_s2_status(
    error: str,
    threshold: float = S2_BREADTH_THRESHOLD,
    cooldown_days: int = S2_BREADTH_COOLDOWN_DAYS,
    source: str | None = None,
) -> dict:
    payload = {
        "current": None,
        "previous": None,
        "as_of": None,
        "threshold": threshold,
        "cooldown_days": cooldown_days,
        "upcross": False,
        "error": error,
    }
    if source:
        payload["source"] = source
    return payload


def _load_market_db_broad_price_frames(rows_needed: int = MARKET_TIMING_PRICE_ROWS) -> dict[str, object]:
    """Load broad-universe close frames from market.db for S2 fallback."""
    db_path = DATA_DIR / "market.db"
    if not db_path.exists():
        logger.warning("breadth S2 fallback skipped: market.db missing at %s", db_path)
        return {}

    today = datetime.now().date().isoformat()
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        symbols_df = pd.read_sql(
            """
            WITH latest AS (
                SELECT symbol, market_cap, date,
                       ROW_NUMBER() OVER (
                           PARTITION BY symbol ORDER BY date DESC
                       ) AS rn
                FROM historical_market_cap
                WHERE date <= ?
            )
            SELECT symbol
            FROM latest
            WHERE rn = 1
              AND date >= date(?, '-90 days')
              AND market_cap >= ?
            ORDER BY symbol
            """,
            conn,
            params=(today, today, BROAD_UNIVERSE_MIN_MCAP_USD),
        )
        symbols = symbols_df["symbol"].dropna().astype(str).tolist()
        if not symbols:
            return {}

        frames = {}
        chunk_size = 500
        for start in range(0, len(symbols), chunk_size):
            chunk = symbols[start:start + chunk_size]
            placeholders = ",".join("?" for _ in chunk)
            price_df = pd.read_sql(
                f"""
                SELECT symbol, date, close
                FROM (
                    SELECT symbol, date, close,
                           ROW_NUMBER() OVER (
                               PARTITION BY symbol ORDER BY date DESC
                           ) AS rn
                    FROM daily_price
                    WHERE symbol IN ({placeholders})
                )
                WHERE rn <= ?
                ORDER BY symbol, date
                """,
                conn,
                params=chunk + [rows_needed],
                parse_dates=["date"],
            )
            if price_df.empty:
                continue
            price_df["close"] = pd.to_numeric(price_df["close"], errors="coerce")
            price_df = price_df.dropna(subset=["symbol", "date", "close"])
            for symbol, group in price_df.groupby("symbol"):
                frame = group[["date", "close"]].sort_values("date").set_index("date")
                if len(frame) >= 21:
                    frames[str(symbol)] = frame
        logger.info("breadth S2 market.db fallback loaded %d/%d symbols", len(frames), len(symbols))
        return frames
    except Exception as exc:
        logger.warning("breadth S2 market.db fallback failed: %s", exc)
        return {}
    finally:
        conn.close()


def _compute_breadth_s2_status_from_price_frames(
    price_frames: dict[str, object],
    threshold: float = S2_BREADTH_THRESHOLD,
    cooldown_days: int = S2_BREADTH_COOLDOWN_DAYS,
    min_symbols: int = 50,
    allow_market_db_fallback: bool = True,
    source: str = "live_broad_price_frames",
) -> dict:
    """Compute live broad S2 participation from current broad price frames."""
    if not price_frames:
        if allow_market_db_fallback:
            fallback_frames = _load_market_db_broad_price_frames()
            return _compute_breadth_s2_status_from_price_frames(
                fallback_frames,
                threshold=threshold,
                cooldown_days=cooldown_days,
                min_symbols=min_symbols,
                allow_market_db_fallback=False,
                source="market_db_broad_price_frames",
            )
        return _empty_breadth_s2_status(
            "no price frames",
            threshold=threshold,
            cooldown_days=cooldown_days,
            source=source,
        )

    above_ma20 = {}
    for symbol, frame in price_frames.items():
        if frame is None or frame.empty or "close" not in frame.columns:
            continue
        close = pd.to_numeric(frame["close"], errors="coerce").dropna()
        if len(close) < 21:
            continue
        ma20 = close.rolling(20, min_periods=20).mean()
        signal = (close > ma20).astype(float)
        above_ma20[symbol] = signal

    if len(above_ma20) < min_symbols:
        if allow_market_db_fallback:
            fallback_frames = _load_market_db_broad_price_frames()
            if fallback_frames:
                return _compute_breadth_s2_status_from_price_frames(
                    fallback_frames,
                    threshold=threshold,
                    cooldown_days=cooldown_days,
                    min_symbols=min_symbols,
                    allow_market_db_fallback=False,
                    source="market_db_broad_price_frames",
                )
        return _empty_breadth_s2_status(
            "insufficient breadth symbols: {}/{}".format(len(above_ma20), min_symbols),
            threshold=threshold,
            cooldown_days=cooldown_days,
            source=source,
        )

    panel = pd.concat(above_ma20, axis=1).sort_index()
    participation = panel.mean(axis=1, skipna=True).dropna()
    daily = pd.DataFrame({
        "date": participation.index,
        "breadth_20": participation.values,
    })
    result = _compute_breadth_s2_status(
        daily,
        threshold=threshold,
        cooldown_days=cooldown_days,
    )
    result["source"] = source
    result["symbols_with_breadth"] = len(above_ma20)
    return result


def build_market_timing_factor_report() -> dict:
    """Build market-level timing factor payload for SPY/QQQ/SOXX + broad S2.

    S2 breadth always sources from broad universe ($1B+) in market.db,
    decoupled from the selection-scan universe. This guarantees the
    historically-calibrated 30% threshold keeps its broad MA20 semantics
    even if the report's selection scan is narrowed (e.g. to extend $10B+).
    """
    from src.indicators.pmarp import analyze_pmarp

    broad_frames = _load_market_db_broad_price_frames()
    breadth = _compute_breadth_s2_status_from_price_frames(
        broad_frames,
        allow_market_db_fallback=False,
        source="market_db_broad_price_frames",
    )
    frames = _load_market_timing_target_frames(MARKET_TIMING_TARGETS)
    rows = []
    for symbol in MARKET_TIMING_TARGETS:
        frame = frames.get(symbol)
        pmarp = analyze_pmarp(frame) if frame is not None else {
            "current": None,
            "previous": None,
            "crossover_2_up": False,
            "description": "数据不足",
        }
        rows.append({
            "symbol": symbol,
            "as_of": (
                pd.Timestamp(frame["date"].iloc[-1]).date().isoformat()
                if frame is not None and not frame.empty else None
            ),
            "pmarp_current": pmarp.get("current"),
            "pmarp_previous": pmarp.get("previous"),
            "pmarp_up2": bool(pmarp.get("crossover_2_up")),
            "pmarp_description": pmarp.get("description", ""),
            "breadth_s2_current": breadth.get("current"),
            "breadth_s2_previous": breadth.get("previous"),
            "breadth_s2_upcross": bool(breadth.get("upcross")),
            "breadth_s2_as_of": breadth.get("as_of"),
        })

    alerts = []
    pmarp_alerts = [
        row for row in rows
        if row.get("pmarp_up2")
    ]
    if pmarp_alerts:
        labels = [
            "{} {:.1f}→{:.1f}".format(
                row["symbol"],
                row.get("pmarp_previous") or 0,
                row.get("pmarp_current") or 0,
            )
            for row in pmarp_alerts
        ]
        alerts.append({
            "kind": "pmarp_up2",
            "text": "PMARP 2% UPCROSS: " + " / ".join(labels),
        })
    if breadth.get("upcross"):
        alerts.append({
            "kind": "breadth_s2_upcross",
            "text": "BREADTH S2 UPCROSS: broad MA20 participation {:.1%}→{:.1%}".format(
                breadth.get("previous") or 0,
                breadth.get("current") or 0,
            ),
        })

    return {
        "criteria": "PMARP 上穿2% + Broad S2(MA20 breadth 上穿30%, cooldown=60)",
        "targets": MARKET_TIMING_TARGETS,
        "rows": rows,
        "breadth_s2": breadth,
        "alerts": alerts,
    }


PMARP_SIGNAL_LABELS = {
    "oversold_recovery": "上穿2%",
    "bullish_breakout": "上穿98%",
    "momentum_fading": "下穿98%",
}
# Display order matches plan: bullish_breakout → oversold_recovery → momentum_fading
# (up98 first as the strongest momentum entry, then up2 recovery, then down98 fade).
PMARP_SIGNAL_ORDER = {
    "bullish_breakout": 0,
    "oversold_recovery": 1,
    "momentum_fading": 2,
}

PMARP_MCAP_TIER_USD = 100e9  # 大盘/中小盘分界，可调
PMARP_MCAP_TIER_ORDER = ["大盘(≥$100B)", "中小盘(<$100B)"]


def _mcap_tier(market_cap: "float | None") -> str:
    if (market_cap or 0) >= PMARP_MCAP_TIER_USD:
        return "大盘(≥$100B)"
    return "中小盘(<$100B)"


# Display threshold for surfacing RVOL-only single-day spikes in the merged
# volume anomaly section. RVOL sustained signals use RVOL_SUSTAINED_THRESHOLD;
# RVOL-only single below this value is treated as moderate noise.
RVOL_ONLY_SINGLE_THRESHOLD = 3.0


def build_market_signal_report(symbols_override: list[str] | None = None) -> dict:
    """Build technical signal payload for the merged morning report.

    Technical signals scan the current Extended universe. Selection Compass
    separately consumes the validated weekly Premium Pool and applies only
    the daily EMA30 timing gate; Premium membership also annotates technical
    signal rows for shared report highlighting.

    --symbols override grants extend privilege: every override symbol is
    treated as layer="extend", bypassing the $10B mcap filter so manual
    debugging (e.g. OKLO at $8B) renders without fail-fast.
    """
    from datetime import date

    from scripts.broad_market_scan import (
        fetch_universe_metadata,
        load_price_frames,
        load_price_frames_from_market_db,
    )
    from src.data.market_store import get_store
    from src.indicators.dv_acceleration import scan_dv_acceleration
    from src.indicators.pmarp import analyze_pmarp
    from src.indicators.rvol_sustained import scan_rvol_sustained

    premium_pool = load_premium_pool()
    premium_symbols = {
        row["symbol"] for row in premium_pool.get("members", [])
        if isinstance(row, dict) and row.get("symbol")
    } if premium_pool.get("available") else set()
    selection_compass_symbols = sorted(premium_symbols)
    try:
        pool_symbols = set(current_base_universe())
    except Exception as e:
        # R3 迁移期兼容：current_base_universe() 在 SM/extended_membership 尚未
        # bootstrap 时 fail-loud 抛 RuntimeError（本 worktree 的骨架 DB 即此状
        # 态；见 tests 的 real-DB 路径）；其他异常同样保守退回，晨报不能因分母
        # 切换而崩溃。退回迁移前的 pool.json 行为（get_symbols()）。broad except
        # 故意保留（缺失 resolver 也不能让晨报挂掉），但必须留痕——否则 bootstrap
        # 之后如果 resolver 接线出 bug（TypeError/sqlite3 error 等），会永久静默
        # 退回旧语义而无人发现 R3 切换实际上没生效。
        logger.warning(
            "current_base_universe unavailable, falling back to legacy pool symbols: %s", e
        )
        pool_symbols = set(get_symbols())
    if symbols_override:
        symbols = sorted({s.strip().upper() for s in symbols_override if s.strip()})
        store = get_store()
        bulk_caps = store.get_bulk_market_caps_at(date.today().isoformat())
        metadata = {
            symbol: {
                "marketCap": bulk_caps.get(symbol),
                "shortName": symbol,
                "longName": symbol,
                "exchange": "DB",
            }
            for symbol in symbols
        }
        # Override grants extend privilege — bypass mcap filter, layer always "extend".
        pool_symbols = pool_symbols | set(symbols)
        logger.info("override mode: %d symbols treated as extend layer", len(symbols))
    else:
        universe_cache = fetch_universe_metadata(
            as_of_date=date.today().isoformat(), min_mcap_b=10.0,
        )
        raw_metadata = universe_cache.get("stocks", {})
        # Post-filter: market_db source ignores min_mcap_b and uses
        # BROAD_UNIVERSE_MIN_MCAP_USD ($1B), so we must enforce ≥$10B locally.
        metadata = {
            sym: meta for sym, meta in raw_metadata.items()
            if (meta.get("marketCap") or 0) >= EXTENDED_LAYER_MIN_MCAP
        }
        # Pool symbols always scanned regardless of mcap.
        for sym in pool_symbols:
            if sym not in metadata:
                metadata[sym] = {
                    "marketCap": (raw_metadata.get(sym) or {}).get("marketCap"),
                    "shortName": sym, "longName": sym, "exchange": "DB",
                }
        symbols = sorted(metadata.keys())

    _merge_local_metadata(metadata, symbols)

    price_frames = load_price_frames(symbols, rows_needed=MORNING_SIGNAL_PRICE_ROWS)
    selection_compass_price_frames = {
        symbol: price_frames[symbol]
        for symbol in selection_compass_symbols
        if symbol in price_frames
    }
    if premium_pool.get("available"):
        missing_compass_symbols = sorted(
            set(selection_compass_symbols) - set(selection_compass_price_frames)
        )
        if missing_compass_symbols:
            try:
                selection_compass_price_frames.update(
                    load_price_frames_from_market_db(
                        missing_compass_symbols,
                        rows_needed=SELECTION_COMPASS_PRICE_ROWS,
                    )
                )
            except Exception as exc:
                logger.warning("selection compass DB price supplement failed: %s", exc)
    price_dict = {
        symbol: _frame_with_date(symbol, frame)
        for symbol, frame in price_frames.items()
    }

    pmarp_raw = []
    for symbol, frame in price_dict.items():
        result = analyze_pmarp(frame)
        if result.get("signal") in PMARP_SIGNAL_LABELS:
            pmarp_raw.append({
                "symbol": symbol,
                "value": result.get("current"),
                "previous": result.get("previous"),
                "signal": result.get("signal"),
            })

    dv_df = scan_dv_acceleration(price_dict, threshold=DV_ACCELERATION_THRESHOLD)
    dv_raw = []
    if len(dv_df) > 0:
        for row in dv_df[dv_df["signal"]].to_dict("records"):
            dv_raw.append(row)

    rvol_raw = scan_rvol_sustained(price_dict, threshold=RVOL_SUSTAINED_THRESHOLD)

    signal_symbols = [
        item["symbol"]
        for item in (pmarp_raw + dv_raw + rvol_raw)
    ]
    _hydrate_signal_metadata(metadata, signal_symbols)
    betas = _compute_signal_betas(price_frames, signal_symbols)

    pmarp_signals = [
        _enrich_with_layer(item, metadata, pool_symbols, betas, premium_symbols)
        for item in pmarp_raw
    ]
    # Group by signal kind first (up98 / down98 / up2), then by value.
    pmarp_signals.sort(key=lambda x: (
        PMARP_SIGNAL_ORDER.get(x.get("signal"), 99),
        x.get("value") or 0,
        x["symbol"],
    ))

    dv_hits = [
        _enrich_with_layer(item, metadata, pool_symbols, betas, premium_symbols)
        for item in dv_raw
    ]
    dv_hits.sort(key=lambda x: (-(x.get("ratio") or 0), x["symbol"]))

    rvol_hits = [
        _enrich_with_layer(item, metadata, pool_symbols, betas, premium_symbols)
        for item in rvol_raw
    ]

    volume_anomaly_hits = _merge_volume_anomaly_hits(dv_hits, rvol_hits)

    scan_dates = [frame.index.max() for frame in price_frames.values() if not frame.empty]
    as_of = max(scan_dates).date().isoformat() if scan_dates else date.today().isoformat()

    from terminal.selection_compass import scan_selection_compass
    if not premium_pool.get("available"):
        selection_compass = scan_selection_compass(
            premium_pool=premium_pool, as_of=as_of, price_frames={},
            market_cap_observations={},
        )
    else:
        try:
            selection_store = get_store()
            selection_compass = scan_selection_compass(
                premium_pool=premium_pool,
                as_of=as_of,
                price_frames=selection_compass_price_frames,
                market_cap_observations=(
                    _load_selection_compass_market_cap_observations(
                        selection_store,
                        selection_compass_symbols,
                        as_of,
                    )
                ),
            )
        except Exception as exc:
            logger.warning("selection compass unavailable: %s", exc)
            failed_coverage = dict(premium_pool.get("coverage") or {})
            failed_coverage["ema30_ready"] = {
                "covered": 0,
                "total": len(selection_compass_symbols),
                "ratio": 0.0,
            }
            selection_compass = {
                "available": False,
                "reason": "selection_compass_error",
                "coverage": failed_coverage,
                "hits": [],
            }

    try:
        volconc_frames = _load_volume_concentration_frames()
        if volconc_frames.get("available"):
            volconc = _compute_volume_concentration_payload(
                volconc_frames["share_df"],
                volconc_frames["members"],
                volconc_frames["spy_close"],
            )
        else:
            volconc = volconc_frames  # loader 已给 {"available": False, "reason": ...}
    except Exception:
        volconc = {"available": False, "reason": "计算异常"}

    return {
        "as_of": as_of,
        "symbols_scanned": len(symbols),
        "symbols_with_data": len(price_frames),
        "market_timing_factor": build_market_timing_factor_report(),
        "volume_concentration": volconc,
        "selection_compass": selection_compass,
        "premium_pool": {
            "available": premium_pool.get("available", False),
            "reason": premium_pool.get("reason"),
            "name": premium_pool.get("name"),
            "as_of": premium_pool.get("as_of"),
            "generated_at": premium_pool.get("generated_at"),
            "member_count": len(premium_symbols),
            "symbols": sorted(premium_symbols),
        },
        "layer_counts": {
            layer: sum(
                1 for symbol in symbols
                if _layer_for_symbol(symbol, metadata, pool_symbols) == layer
            )
            for layer in LAYER_ORDER
        },
        "pmarp": {
            "criteria": "PMARP 上穿2% / 上穿98% / 下穿98%",
            "hits": pmarp_signals,
        },
        "volume_anomaly": {
            "criteria": "DV >{:.1f}x | RVOL >{:.1f}σ sustained 或 single >={:.1f}σ".format(
                DV_ACCELERATION_THRESHOLD,
                RVOL_SUSTAINED_THRESHOLD,
                RVOL_ONLY_SINGLE_THRESHOLD,
            ),
            "hits": volume_anomaly_hits,
        },
        "dv_acceleration": {
            "criteria": "DV >{:.1f}x".format(DV_ACCELERATION_THRESHOLD),
            "hits": dv_hits,
        },
        "rvol_sustained": {
            "criteria": "RVOL >{:.1f}σ 持续".format(RVOL_SUSTAINED_THRESHOLD),
            "hits": rvol_hits,
        },
    }


def _fmt_pct_value(value: float | None, decimals: int = 1) -> str:
    if value is None or pd.isna(value):
        return "N/A"
    return f"{value:.{decimals}f}%"


def _fmt_participation(value: float | None) -> str:
    if value is None or pd.isna(value):
        return "N/A"
    return "{:.1%}".format(value)


def _fmt_transition(previous: float | None, current: float | None, formatter) -> str:
    current_text = formatter(current)
    if previous is None or pd.isna(previous):
        return current_text
    return "{}→{}".format(formatter(previous), current_text)


def _format_market_timing_as_of(section: dict) -> str:
    rows = section.get("rows", [])

    def _dates(key: str) -> list[str]:
        return sorted({str(row.get(key)) for row in rows if row.get(key)})

    def _label(values: list[str]) -> str:
        if not values:
            return "N/A"
        if len(values) == 1:
            return values[0]
        return "{}..{}".format(values[0], values[-1])

    pmarp_dates = _dates("as_of")
    breadth_dates = _dates("breadth_s2_as_of")
    if not breadth_dates and section.get("breadth_s2", {}).get("as_of"):
        breadth_dates = [str(section["breadth_s2"]["as_of"])]
    return "PMARP as_of {} | S2 as_of {}".format(
        _label(pmarp_dates),
        _label(breadth_dates),
    )


def _format_timing_alerts(alerts: list[dict]) -> list[str]:
    if not alerts:
        return []
    lines = ["🔴 *大盘择时触发*"]
    for alert in alerts:
        lines.append("🔴 *{}*".format(alert.get("text", "")))
    return lines


def format_section_market_timing_factor(market_signals: dict) -> str:
    section = market_signals.get("market_timing_factor", {}) if market_signals else {}
    lines = ["*0. 大盘择时因子*"]
    as_of_label = _format_market_timing_as_of(section)
    if section.get("criteria"):
        lines.append("{} | {}".format(section["criteria"], as_of_label))
    else:
        lines.append(as_of_label)

    lines.extend(_format_timing_alerts(section.get("alerts", [])))

    rows = section.get("rows", [])
    if not rows:
        lines.append("数据不足")
        return "\n".join(lines)

    lines.append("指数 | PMARP | PMARP 2%上穿 | S2参与度(broad) | S2触发")
    for row in rows:
        pmarp_display = _fmt_transition(
            row.get("pmarp_previous"),
            row.get("pmarp_current"),
            _fmt_pct_value,
        )
        pmarp_cross = "YES" if row.get("pmarp_up2") else "—"
        breadth_display = _fmt_transition(
            row.get("breadth_s2_previous"),
            row.get("breadth_s2_current"),
            _fmt_participation,
        )
        breadth_cross = "YES" if row.get("breadth_s2_upcross") else "—"
        if row.get("pmarp_up2") or row.get("breadth_s2_upcross"):
            pmarp_cross = "*{}*".format(pmarp_cross)
            breadth_cross = "*{}*".format(breadth_cross)
        lines.append("{} | {} | {} | {} | {}".format(
            row.get("symbol", ""),
            pmarp_display,
            pmarp_cross,
            breadth_display,
            breadth_cross,
        ))
    return "\n".join(lines)


def _volconc_display_rows(payload: dict) -> dict:
    """Shared 0b 成交集中度 display-data assembly for text/HTML/PNG faces.

    Each face renders its own markup from this dict's labels/values/status,
    so the three surfaces are guaranteed to match by construction rather
    than by keeping three hand-written format strings in sync (see
    docs/plans/2026-07-24-morning-report-volume-concentration-context.md).
    Labels are byte-identical to the text face's original sample;
    values are pre-formatted (1 decimal + %; percentile rounded to int) so
    no face re-derives rounding independently.

    available=False payloads return only {"available": False, "reason": ...}.
    spy_ret20_pct is never surfaced here — it is a regime-only input, never
    rendered on any face (see docs/plans/2026-07-24-morning-report-volume-concentration-context.md 禁词表)."""
    payload = payload or {}
    if not payload.get("available"):
        return {"available": False, "reason": payload.get("reason", "N/A")}

    return {
        "available": True,
        "as_of": payload.get("as_of"),
        "rows": [
            {
                "label": "Top50 成交额占比(20日平滑)",
                "value": "{:.1f}%".format(payload.get("share_sm_pct")),
                "pctile": str(round(payload.get("share_pctile_1y"))),
            },
            {
                "label": "Top50 名单20日换手率(平滑)",
                "value": "{:.1f}%".format(payload.get("churn_sm_pct")),
                "pctile": str(round(payload.get("churn_pctile_1y"))),
            },
        ],
        "status": payload.get("regime"),
    }


def format_section_volume_concentration(payload: dict) -> str:
    """Section 0b text face — Top50 成交额占比 / 名单换手率 + regime label
    only. No directional/predictive language: spy_ret20_pct is regime-only
    input and is never rendered (see
    docs/plans/2026-07-24-morning-report-volume-concentration-context.md 禁词表)."""
    display = _volconc_display_rows(payload)
    if not display["available"]:
        return "*0b. 成交集中度*\n成交集中度: 数据不足（{}）".format(display["reason"])

    lines = ["*0b. 成交集中度*（截至 {}）".format(display["as_of"])]
    for row in display["rows"]:
        lines.append("{}   {}   1年分位 {}".format(row["label"], row["value"], row["pctile"]))
    lines.append("状态: {}".format(display["status"]))
    return "\n".join(lines)


SELECTION_COMPASS_COLUMNS = [
    "标的", "EPS YoY", "EPS QoQ", "营收4Q CAGR", "净利4Q CAGR",
    "成长均值", "收盘", "EMA30", "当前市值", "β6M",
]
SELECTION_COMPASS_WIDTHS = [180, 150, 150, 190, 190, 160, 210, 160, 170, 120]
SELECTION_COMPASS_REASON_LABELS = {
    "fundamental_coverage_below_threshold": "基本面覆盖不足",
    "rvol_coverage_below_threshold": "RVOL 覆盖不足",
    "ema30_coverage_below_threshold": "EMA30 价格覆盖不足",
    "beta_coverage_below_threshold": "Beta 数据覆盖不足",
    "premium_pool_missing": "精选Premium池尚未生成",
    "premium_pool_stale": "精选Premium池已过期",
    "premium_pool_criteria_mismatch": "精选Premium池条件版本不匹配",
    "premium_pool_invalid": "精选Premium池数据异常",
    "market_cap_unavailable": "当前市值数据不足",
    "empty_universe": "股票池读取异常",
    "universe_resolver_error": "股票池读取异常",
    "fundamental_store_error": "筛选计算异常",
    "selection_compass_error": "筛选计算异常",
    "scanner_error": "筛选计算异常",
    "store_error": "筛选计算异常",
}


def _format_compass_growth(value: float | None, turnaround: bool = False) -> str:
    if turnaround:
        return "扭亏"
    if value is None or pd.isna(value):
        return "—"
    return "{:.1%}".format(value)


def _selection_compass_coverage_subtitle(coverage: dict | None) -> str:
    coverage = coverage or {}
    fundamental = coverage.get("fundamental_ready") or {}
    if "rvol_ready" not in coverage:
        beta = coverage.get("beta_ready") or {}
        ema30 = coverage.get("ema30_ready") or {}
        return (
            "精选Premium池周频覆盖：基本面 {}/{} | Beta {}/{} | "
            "EMA30 {}/{} | 条件：EPS YoY/QoQ ≥20% / "
            "成长均值 ≥10%或扭亏成长 / β6M ≥1.35 / 收盘价 > EMA30"
        ).format(
            fundamental.get("covered", 0), fundamental.get("total", 0),
            beta.get("covered", 0), beta.get("total", 0),
            ema30.get("covered", 0), ema30.get("total", 0),
        )
    rvol = coverage.get("rvol_ready") or {}
    subtitle = "Extended 扫描覆盖：基本面 {}/{} | RVOL {}/{}".format(
        fundamental.get("covered", 0),
        fundamental.get("total", 0),
        rvol.get("covered", 0),
        rvol.get("total", 0),
    )
    # Legacy saved reports did not apply EMA30; do not relabel their semantics.
    if "ema30_ready" in coverage:
        ema30 = coverage["ema30_ready"]
        subtitle += " | EMA30 {}/{}".format(
            ema30.get("covered", 0), ema30.get("total", 0),
        )
        if "beta_ready" not in coverage:
            subtitle += " | 收盘价 > EMA30"
    if "beta_ready" in coverage:
        beta = coverage["beta_ready"]
        subtitle += (
            " | Beta {}/{} | 条件：EPS YoY/QoQ ≥20% / "
            "成长均值 ≥10%或扭亏成长 / 收盘价 > EMA30 / β6M ≥1.35"
        ).format(beta.get("covered", 0), beta.get("total", 0))
    return subtitle


def _selection_compass_reason_label(reason: str | None) -> str:
    return SELECTION_COMPASS_REASON_LABELS.get(reason, "暂时无法生成")


def _selection_compass_display(payload: dict | None) -> dict:
    """Build the shared display contract for text, HTML and visual faces."""
    if payload is None:
        return {
            "state": "hidden",
            "subtitle": "",
            "columns": SELECTION_COMPASS_COLUMNS,
            "rows": [],
        }
    payload = payload or {}
    coverage_subtitle = _selection_compass_coverage_subtitle(payload.get("coverage"))
    if not payload.get("available"):
        return {
            "state": "warning",
            "subtitle": coverage_subtitle,
            "warning": "⚠️ 选股罗盘不可用（{}）".format(
                _selection_compass_reason_label(payload.get("reason"))
            ),
            "columns": SELECTION_COMPASS_COLUMNS,
            "rows": [],
        }

    hits = sorted(
        payload.get("hits") or [],
        key=lambda hit: (-(hit.get("marketCap") or 0), hit.get("symbol", "")),
    )
    if not hits:
        return {
            "state": "hidden",
            "subtitle": coverage_subtitle,
            "columns": SELECTION_COMPASS_COLUMNS,
            "rows": [],
        }

    rows = []
    for hit in hits:
        rows.append([
            hit.get("symbol", ""),
            _format_compass_growth(
                hit.get("eps_yoy_growth"), hit.get("eps_yoy_turnaround", False)
            ),
            _format_compass_growth(
                hit.get("eps_qoq_growth"), hit.get("eps_qoq_turnaround", False)
            ),
            _format_compass_growth(hit.get("revenue_cagr_4q")),
            _format_compass_growth(hit.get("net_income_cagr_4q")),
            ("扭亏成长" if hit.get("growth_route") == "turnaround"
             else _format_compass_growth(hit.get("growth_avg_4q"))),
            "{:.2f}".format(hit.get("close")),
            "{:.2f}".format(hit.get("ema30")),
            _format_market_cap(hit.get("marketCap")),
            _format_beta(hit.get("beta_6m")),
        ])
    return {
        "state": "table",
        "subtitle": coverage_subtitle,
        "columns": SELECTION_COMPASS_COLUMNS,
        "rows": rows,
    }


def format_section_selection_compass(payload: dict | None) -> str:
    display = _selection_compass_display(payload)
    if display["state"] == "hidden":
        return ""
    lines = ["*0c. 选股罗盘*"]
    if display["state"] == "warning":
        lines.extend([display["warning"], display["subtitle"]])
        return "\n".join(lines)
    lines.extend([display["subtitle"], " | ".join(display["columns"])])
    lines.extend(" | ".join(row) for row in display["rows"])
    return "\n".join(lines)


def _pmarp_signal_cap_groups(hits: list) -> list:
    """纯分组：返回 [(signal_label, tier_label, sorted_hits), ...]，空组已抑制。
    供文本/视觉/HTML 三处复用——HTML renderer 消费其输出，不反向 import morning_report。"""
    l2_order = {b: i for i, b in enumerate(CONCEPT_BUCKET_ORDER)}   # 运行时 61 桶 L2 顺序
    groups = []
    for signal_key in ("bullish_breakout", "oversold_recovery", "momentum_fading"):
        sig_hits = [h for h in hits if h.get("signal") == signal_key]
        if not sig_hits:
            continue                                  # 空信号组抑制
        for tier in PMARP_MCAP_TIER_ORDER:
            tier_hits = [h for h in sig_hits if _mcap_tier(h.get("marketCap")) == tier]
            if not tier_hits:
                continue                              # 空市值档抑制
            tier_hits.sort(key=lambda x: (            # 同 L2 相邻：(L2 顺序, value, symbol)
                l2_order.get(_grouping_bucket_for(x), 999),
                x.get("value") or 0, x["symbol"],
            ))
            groups.append((PMARP_SIGNAL_LABELS[signal_key], tier, tier_hits))
    return groups


def format_section_pmarp_by_signal_and_cap(market_signals: dict) -> str:
    section = market_signals.get("pmarp", {})
    lines = ["*1. PMARP 信号 ({})*".format(section.get("criteria", ""))]
    hits = section.get("hits", [])
    if not hits:
        return "\n".join(lines + ["无 PMARP 信号"])
    last_signal = None
    for signal_label, tier, tier_hits in _pmarp_signal_cap_groups(hits):
        if signal_label != last_signal:
            lines.append("【{}】".format(signal_label)); last_signal = signal_label
        lines.append("  {}".format(tier))
        lines.append("  标的 | 概念 | 信号 | 当前 | 变化 | 市值 | β6M")
        for item in tier_hits:
            lines.append("    {} | {} | {} | {:.1f}% | {:.1f}→{:.1f} | {} | {}".format(
                _premium_text_company(item), _display_concept_tags(item),
                PMARP_SIGNAL_LABELS.get(item.get("signal"), "—"),
                item.get("value") or 0, item.get("previous") or 0, item.get("value") or 0,
                _format_market_cap(item.get("marketCap")),
                _format_beta(item.get("beta_6m")),
            ))
    return "\n".join(lines)


def format_section_layered_dv(market_signals: dict) -> str:
    section = market_signals.get("dv_acceleration", {})
    lines = ["*2. 量能加速 ({})*".format(section.get("criteria", ""))]
    lines.extend(_format_bucketed_table(
        section.get("hits", []),
        "无加速信号",
        "标的 | 概念 | 倍数 | 5d/20d | 市值",
        lambda item: "{} | {} | {:.1f}x | {}/{} | {}".format(
            _premium_text_company(item),
            _display_concept_tags(item),
            item.get("ratio") or 0,
            format_dv(item.get("dv_5d") or 0),
            format_dv(item.get("dv_20d") or 0),
            _format_market_cap(item.get("marketCap")),
        ),
    ))
    return "\n".join(lines)


def format_section_layered_rvol(market_signals: dict) -> str:
    section = market_signals.get("rvol_sustained", {})
    level_labels = {
        "sustained_5d": "5日连续",
        "sustained_3d": "3日连续",
        "single": "单日",
    }
    lines = ["*3. RVOL 持续放量 ({})*".format(section.get("criteria", ""))]
    lines.extend(_format_bucketed_table(
        section.get("hits", []),
        "无持续放量信号",
        "标的 | 概念 | 形态 | 最新 | 市值",
        lambda item: "{} | {} | {} | {:.1f}σ | {}".format(
            _premium_text_company(item),
            _display_concept_tags(item),
            level_labels.get(item.get("level"), item.get("level", "")),
            item.get("latest_rvol") or 0,
            _format_market_cap(item.get("marketCap")),
        ),
    ))
    return "\n".join(lines)


def _volume_anomaly_priority_group(item: dict) -> int:
    has_dv = bool(item.get("from_dv"))
    has_rvol = bool(item.get("from_rvol"))
    rvol_level = item.get("rvol_level")
    if has_dv and has_rvol and rvol_level in {"sustained_3d", "sustained_5d"}:
        return 0
    if has_dv and has_rvol:
        return 1
    if has_rvol and rvol_level == "single":
        return 2
    if has_rvol:
        return 3
    return 4


def _classify_volume_signal(item: dict) -> str:
    has_dv = bool(item.get("from_dv"))
    has_rvol = bool(item.get("from_rvol"))
    if has_dv and has_rvol:
        return "共振"
    if has_dv:
        return "流动性加速"
    if item.get("rvol_level") in {"sustained_3d", "sustained_5d"}:
        return "持续放量"
    return "单日爆量"


def _volume_anomaly_sort_key(item: dict) -> tuple:
    priority = item.get("priority_group", 99)
    latest_rvol = item.get("latest_rvol") or 0.0
    dv_ratio = item.get("dv_ratio") or item.get("ratio") or 0.0
    return (priority, -latest_rvol, -dv_ratio, item.get("symbol", ""))


_VOLUME_ANOMALY_RVOL_LEVEL_LABELS = {
    "sustained_5d": "5日",
    "sustained_3d": "3日",
    "single": "单日",
}


def _format_volume_anomaly_dv_cell(item: dict) -> str:
    if not item.get("from_dv"):
        return "—"
    ratio = item.get("dv_ratio") or item.get("ratio") or 0.0
    return "{:.1f}x ({}/{})".format(
        ratio,
        format_dv(item.get("dv_5d") or 0),
        format_dv(item.get("dv_20d") or 0),
    )


def _format_volume_anomaly_rvol_cell(item: dict) -> str:
    if not item.get("from_rvol"):
        return "—"
    level = item.get("rvol_level") or ""
    label = _VOLUME_ANOMALY_RVOL_LEVEL_LABELS.get(level, level)
    return "{} {:.1f}σ".format(label, item.get("latest_rvol") or 0)


def format_section_layered_volume_anomaly(market_signals: dict) -> str:
    section = market_signals.get("volume_anomaly", {})
    lines = ["*2. 量能异常 ({})*".format(section.get("criteria", ""))]
    lines.extend(_format_bucketed_table(
        section.get("hits", []),
        "无量能异常信号",
        "标的 | 概念 | 类型 | DV 5d/20d | RVOL | 市值 | β6M",
        lambda item: "{} | {} | {} | {} | {} | {} | {}".format(
            _premium_text_company(item),
            _display_concept_tags(item),
            item.get("volume_signal_kind") or "—",
            _format_volume_anomaly_dv_cell(item),
            _format_volume_anomaly_rvol_cell(item),
            _format_market_cap(item.get("marketCap")),
            _format_beta(item.get("beta_6m")),
        ),
    ))
    return "\n".join(lines)


def _merge_volume_anomaly_hits(dv_hits: list[dict], rvol_hits: list[dict]) -> list[dict]:
    """Merge DV-acceleration and RVOL-sustained hits into a single anomaly list.

    Keeps all DV hits. For RVOL-only rows, keeps sustained_3d/5d and any single
    above RVOL_ONLY_SINGLE_THRESHOLD. Each output row carries from_dv/from_rvol
    flags, a volume_signal_kind label, and a priority_group for downstream sort.
    """
    merged: dict[str, dict] = {}
    for row in dv_hits:
        symbol = row["symbol"]
        item = dict(row)
        item["dv_ratio"] = row.get("ratio")
        item["from_dv"] = True
        item["from_rvol"] = False
        merged[symbol] = item

    for row in rvol_hits:
        symbol = row["symbol"]
        if symbol in merged:
            item = merged[symbol]
        else:
            # Copy the enriched RVOL row (carries layer / concept_bucket /
            # marketCap / companyName / sector / industry from
            # _enrich_with_layer). Without this, RVOL-only rows lose layer
            # and get hidden by visual renderers that only iterate LAYER_ORDER.
            item = dict(row)
            item["from_dv"] = False
            merged[symbol] = item
        item["from_rvol"] = True
        item["rvol_level"] = row.get("level")
        item["rvol_days"] = row.get("days")
        item["latest_rvol"] = row.get("latest_rvol")
        item["rvol_values"] = row.get("values", [])

    rows = []
    for item in merged.values():
        has_dv = bool(item.get("from_dv"))
        rvol_level = item.get("rvol_level")
        latest_rvol = item.get("latest_rvol") or 0
        keep = (
            has_dv
            or rvol_level in {"sustained_3d", "sustained_5d"}
            or latest_rvol >= RVOL_ONLY_SINGLE_THRESHOLD
        )
        if not keep:
            continue
        item["volume_signal_kind"] = _classify_volume_signal(item)
        item["priority_group"] = _volume_anomaly_priority_group(item)
        rows.append(item)

    rows.sort(key=_volume_anomaly_sort_key)
    return rows


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


def _normalize_dv_items(dv_result: dict, premium_symbols: set[str] | None = None) -> dict:
    """Normalize Dollar Volume rows into the same enriched item shape as signals."""
    rankings = dv_result.get("rankings", [])
    new_faces = dv_result.get("new_faces", [])
    metadata = {}
    symbols = []
    for row in rankings + new_faces:
        symbol = (row.get("symbol") or "").upper()
        if not symbol:
            continue
        symbols.append(symbol)
        metadata[symbol] = dict(row)
    if symbols:
        _merge_local_metadata(metadata, symbols)

    try:
        pool_symbols = set(current_base_universe())
    except Exception as e:
        # R3 迁移期兼容：见 build_market_signal_report 同类注释——
        # current_base_universe() 预 bootstrap 时 fail-loud，退回 pool.json；
        # broad except 故意保留但必须留痕，防止 bootstrap 之后 resolver 接线
        # 出 bug 时永久静默退回旧语义。
        logger.warning(
            "current_base_universe unavailable, falling back to legacy pool symbols: %s", e
        )
        try:
            pool_symbols = set(get_symbols())
        except Exception:
            pool_symbols = set()

    def normalize(row: dict) -> dict | None:
        symbol = (row.get("symbol") or "").upper()
        item = dict(metadata.get(symbol) or {})
        item.update({k: v for k, v in row.items() if v not in (None, "")})
        item["symbol"] = symbol or item.get("symbol", "")
        item["is_premium"] = symbol in (premium_symbols or set())
        if row.get("company_name") and not item.get("companyName"):
            item["companyName"] = row.get("company_name")
        # DV row's market_cap is freshly collected — override any stale local
        # metadata. Without this, a name that has dropped below $10B today
        # would still pass the broad filter based on stale universe cache.
        if row.get("market_cap"):
            item["marketCap"] = row.get("market_cap")
        item.setdefault("concept_bucket", _concept_bucket(item))
        layer_meta = {symbol: {"marketCap": item.get("marketCap") or 0}}
        layer = _layer_for_symbol(symbol, layer_meta, pool_symbols)
        if layer != "extend":
            logger.debug(
                "DV row dropped (layer=%s, mcap=%s): %s",
                layer, item.get("marketCap"), symbol,
            )
            return None
        item["layer"] = layer
        return item

    def _filter(rows):
        return [r for r in (normalize(row) for row in rows) if r is not None]

    return {
        "rankings": _filter(rankings),
        "new_faces": _filter(new_faces),
    }


def format_section_d(dv_result: dict, premium_symbols: set[str] | None = None) -> str:
    """D. Dollar Volume — flat ranking with L2 concept tag, original rank order."""
    lines = ["*D. Dollar Volume*"]
    normalized = _normalize_dv_items(dv_result, premium_symbols)

    if normalized["new_faces"]:
        lines.append("真·新面孔（{} 日内首次进榜）:".format(DOLLAR_VOLUME_LOOKBACK))
        lines.extend(_format_flat_table(
            normalized["new_faces"],
            "无新面孔",
            "标的 | 概念(L2) | 排名 | 成交额",
            lambda item: "{} | {} | #{} | {}".format(
                _premium_text_company(item),
                _grouping_bucket_for(item),
                item["rank"],
                format_dv(item["dollar_volume"]),
            ),
        ))

    if normalized["rankings"]:
        lines.append("成交额 Top {}:".format(len(normalized["rankings"])))
        lines.extend(_format_flat_table(
            normalized["rankings"],
            "无成交额排行",
            "标的 | 概念(L2) | 排名 | 排名变化 | 成交额 | 价格",
            lambda item: "{} | {} | #{} | {} | {} | ${:.0f}".format(
                _premium_text_company(item),
                _grouping_bucket_for(item),
                item["rank"],
                item.get("rank_change_label", "—"),
                format_dv(item["dollar_volume"]),
                item["price"],
            ),
        ))

    return "\n".join(lines)


def build_html_payload(market_signals: dict, dv_result: dict, as_of: str) -> dict:
    """Assemble the HTML-report payload (heading/columns/rows blocks).

    Columns and cells stay one-to-one with the text/visual sections — no
    business-role column — so the three delivery surfaces never drift.
    Reuses _pmarp_signal_cap_groups (C2) so PMARP grouping is shared, and
    _normalize_dv_items so DV layering matches format_section_d. The HTML
    renderer (compile_morning_html_report) consumes this dict without
    importing morning_report back.
    """
    blocks = []

    # 0. 大盘择时因子 — main-path section; must match text/PNG (do NOT drop on HTML path)
    timing = (market_signals or {}).get("market_timing_factor") or {}
    if timing:
        subtitle_bits = [bit for bit in (
            timing.get("criteria"),
            _format_market_timing_as_of(timing),
            "信号日 {}".format(as_of),
        ) if bit]
        tf_rows = timing.get("rows", [])
        if not tf_rows:
            subtitle_bits.append("数据不足")
        title_block = {"heading": "0. 大盘择时因子", "subtitle": " | ".join(subtitle_bits)}
        alerts = timing.get("alerts") or []
        if alerts:
            title_block["alerts"] = ["🔴 大盘择时触发"] + [
                "🔴 {}".format(a.get("text", "")) for a in alerts]
        blocks.append(title_block)
        if tf_rows:
            tf_cols = ["指数", "PMARP", "PMARP 2%上穿", "S2参与度(broad)", "S2触发"]
            blocks.append({"heading": "SPY / QQQ / SOXX", "columns": tf_cols, "rows": [
                {"指数": row.get("symbol", ""),
                 "PMARP": _fmt_transition(row.get("pmarp_previous"), row.get("pmarp_current"), _fmt_pct_value),
                 "PMARP 2%上穿": "YES" if row.get("pmarp_up2") else "—",
                 "S2参与度(broad)": _fmt_transition(row.get("breadth_s2_previous"), row.get("breadth_s2_current"), _fmt_participation),
                 "S2触发": "YES" if row.get("breadth_s2_upcross") else "—"}
                for row in tf_rows]})

    # 0b. 成交集中度 — shares _volconc_display_rows with text/PNG faces so
    # labels/values/status match by construction (Task 4).
    volconc_display = _volconc_display_rows((market_signals or {}).get("volume_concentration"))
    if volconc_display["available"]:
        blocks.append({
            "heading": "0b. 成交集中度",
            "subtitle": "截至 {} | 状态: {}".format(
                volconc_display["as_of"], volconc_display["status"]),
            "columns": ["指标", "数值", "1年分位"],
            "rows": [
                {"指标": row["label"], "数值": row["value"], "1年分位": row["pctile"]}
                for row in volconc_display["rows"]
            ],
        })
    else:
        blocks.append({
            "heading": "0b. 成交集中度",
            "subtitle": "成交集中度: 数据不足（{}）".format(volconc_display["reason"]),
        })

    compass_display = _selection_compass_display(
        (market_signals or {}).get("selection_compass")
    )
    if compass_display["state"] == "table":
        blocks.append({
            "heading": "0c. 选股罗盘",
            "subtitle": compass_display["subtitle"],
            "columns": compass_display["columns"],
            "rows": [
                dict(zip(compass_display["columns"], row))
                for row in compass_display["rows"]
            ],
        })
    elif compass_display["state"] == "warning":
        blocks.append({
            "heading": "0c. 选股罗盘",
            "subtitle": "{} | {}".format(
                compass_display["warning"], compass_display["subtitle"]
            ),
        })

    blocks.append({"heading": "1. PMARP 信号"})

    # PMARP — signal -> cap-tier sub-blocks (columns one-to-one with text)
    pm_cols = ["标的", "概念", "信号", "当前", "变化", "市值", "β6M"]
    pmarp_hits = (market_signals.get("pmarp") or {}).get("hits", [])
    for signal_label, tier, tier_hits in _pmarp_signal_cap_groups(pmarp_hits):
        rows = [{"标的": _compact_company(h), "概念": _display_concept_tags(h),
                 "信号": PMARP_SIGNAL_LABELS.get(h.get("signal"), "—"),
                 "当前": "{:.1f}%".format(h.get("value") or 0),
                 "变化": "{:.1f}→{:.1f}".format(h.get("previous") or 0, h.get("value") or 0),
                 "市值": _format_market_cap(h.get("marketCap")),
                 "β6M": _format_beta(h.get("beta_6m")),
                 "_premium": bool(h.get("is_premium"))} for h in tier_hits]
        blocks.append({"heading": "{} — {}".format(signal_label, tier),
                       "columns": pm_cols, "rows": rows})

    # 量能异常 — columns one-to-one with format_section_layered_volume_anomaly
    va_cols = ["标的", "概念", "类型", "DV 5d/20d", "RVOL", "市值", "β6M"]
    va_hits = (market_signals.get("volume_anomaly") or {}).get("hits", [])
    va_rows = [{"标的": _compact_company(h), "概念": _display_concept_tags(h),
                "类型": h.get("volume_signal_kind") or "—",
                "DV 5d/20d": _format_volume_anomaly_dv_cell(h),
                "RVOL": _format_volume_anomaly_rvol_cell(h),
                "市值": _format_market_cap(h.get("marketCap")),
                "β6M": _format_beta(h.get("beta_6m")),
                "_premium": bool(h.get("is_premium"))} for h in va_hits]
    blocks.append({"heading": "2. 量能异常", "columns": va_cols, "rows": va_rows})

    # Dollar Volume — flat ranking; columns one-to-one with format_section_d
    if dv_result:
        normalized = _normalize_dv_items(
            dv_result, _premium_symbols_from_market_signals(market_signals),
        )
        if normalized["new_faces"]:
            nf_cols = ["标的", "概念(L2)", "排名", "成交额"]
            nf_rows = [{"标的": _compact_company(item), "概念(L2)": _grouping_bucket_for(item),
                        "排名": "#{}".format(item["rank"]),
                        "成交额": format_dv(item["dollar_volume"]),
                        "_premium": bool(item.get("is_premium"))}
                       for item in normalized["new_faces"]]
            blocks.append({"heading": "3. Dollar Volume — 真·新面孔（{} 日内首次进榜）".format(
                DOLLAR_VOLUME_LOOKBACK), "columns": nf_cols, "rows": nf_rows})
        if normalized["rankings"]:
            dv_cols = ["标的", "概念(L2)", "排名", "排名变化", "成交额", "价格"]
            dv_rows = [{"标的": _compact_company(item), "概念(L2)": _grouping_bucket_for(item),
                        "排名": "#{}".format(item["rank"]),
                        "排名变化": item.get("rank_change_label", "—"),
                        "成交额": format_dv(item["dollar_volume"]),
                        "价格": "${:.0f}".format(item["price"]),
                        "_premium": bool(item.get("is_premium"))}
                       for item in normalized["rankings"]]
            blocks.append({"heading": "3. Dollar Volume — 成交额 Top {}".format(
                len(normalized["rankings"])), "columns": dv_cols, "rows": dv_rows})

    return {"as_of": as_of, "blocks": blocks}


def _visual_row(item: dict, cells: list[str]) -> dict:
    return {
        "layer": item.get("layer", "broad"),
        "bucket": _grouping_bucket_for(item),
        "premium": bool(item.get("is_premium")),
        "cells": [str(cell) for cell in cells],
    }


def _visual_company(item: dict) -> str:
    return _display_company(item, max_len=30)


def _build_visual_block(
    title: str,
    columns: list[str],
    items: list[dict],
    row_builder,
    widths: list[int],
) -> dict:
    return {
        "title": title,
        "columns": columns,
        "widths": widths,
        "rows": [_visual_row(item, row_builder(item)) for item in items],
    }


def build_morning_visual_sections(
    market_signals: dict | None = None,
    dv_result: dict | None = None,
) -> list[dict]:
    """Build image-report section specs. The layered signal sections (PMARP /
    量能异常) group extend → L2 concept (R3: pool merged into extend, Task 15);
    the Dollar Volume section is a flat, rank-ordered table with a 概念(L2)
    column (not layer/concept grouped)."""
    sections = []
    as_of = (market_signals or {}).get("as_of") or datetime.now().strftime("%Y-%m-%d")
    common_subtitle = "信号日 {} | Extend 分层，层内按题材聚类".format(as_of)

    if market_signals:
        timing = market_signals.get("market_timing_factor", {})
        if timing:
            sections.append({
                "slug": "00_market_timing_factor",
                "title": "0. 大盘择时因子",
                "subtitle": "{} | {} | 信号日 {}".format(
                    timing.get("criteria", ""),
                    _format_market_timing_as_of(timing),
                    as_of,
                ),
                "alerts": timing.get("alerts", []),
                "blocks": [
                    {
                        "title": "SPY / QQQ / SOXX",
                        "columns": ["指数", "PMARP", "PMARP 2%上穿", "S2参与度(broad)", "S2触发"],
                        "widths": [180, 260, 260, 330, 200],
                        "grouped": False,
                        "rows": [
                            {
                                "alert": row.get("pmarp_up2") or row.get("breadth_s2_upcross"),
                                "cells": [
                                    row.get("symbol", ""),
                                    _fmt_transition(
                                        row.get("pmarp_previous"),
                                        row.get("pmarp_current"),
                                        _fmt_pct_value,
                                    ),
                                    "YES" if row.get("pmarp_up2") else "—",
                                    _fmt_transition(
                                        row.get("breadth_s2_previous"),
                                        row.get("breadth_s2_current"),
                                        _fmt_participation,
                                    ),
                                    "YES" if row.get("breadth_s2_upcross") else "—",
                                ],
                            }
                            for row in timing.get("rows", [])
                        ],
                    },
                ],
            })

        # 0b. 成交集中度 — shares _volconc_display_rows with text/HTML faces so
        # labels/values/status match by construction (Task 4). Only adds a
        # spec entry; the existing grouped=False table renderer draws it.
        volconc_display = _volconc_display_rows(market_signals.get("volume_concentration"))
        if volconc_display["available"]:
            sections.append({
                "slug": "00b_volume_concentration",
                "title": "0b. 成交集中度",
                "subtitle": "截至 {} | 状态: {}".format(
                    volconc_display["as_of"], volconc_display["status"]),
                "blocks": [
                    {
                        "title": "Top50 成交集中度指标",
                        "columns": ["指标", "数值", "1年分位"],
                        "widths": [420, 220, 220],
                        "grouped": False,
                        "rows": [
                            {"cells": [row["label"], row["value"], row["pctile"]]}
                            for row in volconc_display["rows"]
                        ],
                    },
                ],
            })
        else:
            sections.append({
                "slug": "00b_volume_concentration",
                "title": "0b. 成交集中度",
                "subtitle": "成交集中度: 数据不足（{}）".format(volconc_display["reason"]),
                "blocks": [],
            })

        compass_display = _selection_compass_display(
            market_signals.get("selection_compass")
        )
        if compass_display["state"] == "table":
            sections.append({
                "slug": "00c_selection_compass",
                "title": "0c. 选股罗盘",
                "subtitle": compass_display["subtitle"],
                "blocks": [
                    {
                        "title": "当前命中（按市值降序）",
                        "columns": compass_display["columns"],
                        "widths": SELECTION_COMPASS_WIDTHS,
                        "grouped": False,
                        "rows": [
                            {"cells": row}
                            for row in compass_display["rows"]
                        ],
                    },
                ],
            })
        elif compass_display["state"] == "warning":
            sections.append({
                "slug": "00c_selection_compass",
                "title": "0c. 选股罗盘",
                "subtitle": "{} | {}".format(
                    compass_display["warning"], compass_display["subtitle"]
                ),
                "blocks": [],
            })

        pmarp = market_signals.get("pmarp", {})
        _pmarp_cols = ["标的", "概念", "信号", "当前", "变化", "市值", "β6M"]
        _pmarp_widths = [300, 320, 140, 130, 170, 150, 120]
        _pmarp_blocks = []
        # signal -> cap-tier sub-blocks; shared with text/HTML via _pmarp_signal_cap_groups
        # so the 大盘/中小盘 tier is explicit on every surface (P2 review fix).
        for _signal_label, _tier, _tier_hits in _pmarp_signal_cap_groups(pmarp.get("hits", [])):
            _pmarp_blocks.append({
                "title": "{} — {}".format(_signal_label, _tier),
                "columns": _pmarp_cols,
                "widths": _pmarp_widths,
                "grouped": False,
                "rows": [_visual_row(item, [
                    _visual_company(item),
                    _display_concept_tags(item),
                    PMARP_SIGNAL_LABELS.get(item.get("signal"), "—"),
                    "{:.1f}%".format(item.get("value") or 0),
                    "{:.1f}→{:.1f}".format(item.get("previous") or 0, item.get("value") or 0),
                    _format_market_cap(item.get("marketCap")),
                    _format_beta(item.get("beta_6m")),
                ]) for item in _tier_hits],
            })
        sections.append({
            "slug": "01_pmarp",
            "title": "1. PMARP 信号",
            "subtitle": "{} | {}".format(pmarp.get("criteria", ""), common_subtitle),
            "blocks": _pmarp_blocks,
        })

        volume_anomaly = market_signals.get("volume_anomaly", {})
        sections.append({
            "slug": "02_volume_anomaly",
            "title": "2. 量能异常",
            "subtitle": "{} | {}".format(volume_anomaly.get("criteria", ""), common_subtitle),
            "blocks": [
                _build_visual_block(
                    "量能异常",
                    ["标的", "概念", "类型", "DV 5d/20d", "RVOL", "市值", "β6M"],
                    volume_anomaly.get("hits", []),
                    lambda item: [
                        _visual_company(item),
                        _display_concept_tags(item),
                        item.get("volume_signal_kind") or "—",
                        _format_volume_anomaly_dv_cell(item),
                        _format_volume_anomaly_rvol_cell(item),
                        _format_market_cap(item.get("marketCap")),
                        _format_beta(item.get("beta_6m")),
                    ],
                    [280, 320, 140, 280, 180, 150, 120],
                ),
            ],
        })

    if dv_result:
        normalized = _normalize_dv_items(
            dv_result, _premium_symbols_from_market_signals(market_signals),
        )
        blocks = []
        if normalized["new_faces"]:
            cols = ["标的", "概念", "排名", "成交额"]
            widths = [380, 320, 150, 230]
            blocks.append({
                "title": "新面孔",
                "columns": cols,
                "widths": widths,
                "grouped": False,
                "rows": [
                    {"layer": item.get("layer", "broad"),
                     "bucket": _grouping_bucket_for(item),
                     "premium": bool(item.get("is_premium")),
                     "cells": [
                         _visual_company(item),
                         _grouping_bucket_for(item),
                         "#{}".format(item.get("rank", "")),
                         format_dv(item.get("dollar_volume") or 0),
                     ]}
                    for item in normalized["new_faces"]
                ],
            })
        if normalized["rankings"]:
            cols = ["标的", "概念", "排名", "排名变化", "成交额", "价格"]
            widths = [340, 300, 130, 130, 210, 140]
            blocks.append({
                "title": "成交额 Top {}".format(len(normalized["rankings"])),
                "columns": cols,
                "widths": widths,
                "grouped": False,
                "rows": [
                    {"layer": item.get("layer", "broad"),
                     "bucket": _grouping_bucket_for(item),
                     "premium": bool(item.get("is_premium")),
                     "cells": [
                         _visual_company(item),
                         _grouping_bucket_for(item),
                         "#{}".format(item.get("rank", "")),
                         item.get("rank_change_label", "—"),
                         format_dv(item.get("dollar_volume") or 0),
                         "${:.0f}".format(item.get("price") or 0),
                     ]}
                    for item in normalized["rankings"]
                ],
            })
        if blocks:
            sections.append({
                "slug": "03_dollar_volume",
                "title": "3. Dollar Volume",
                "subtitle": "信号日 {} | 按成交额排名，附概念(L2)".format(as_of),
                "blocks": blocks,
            })

    return sections


_VISUAL_FONT_CANDIDATES = {
    "regular": [
        "/System/Library/Fonts/STHeiti Light.ttc",
        "/System/Library/Fonts/Supplemental/Arial Unicode.ttf",
        "/Library/Fonts/Arial Unicode.ttf",
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
        "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",
        "/usr/share/fonts/truetype/unifont/unifont.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    ],
    "bold": [
        "/System/Library/Fonts/STHeiti Medium.ttc",
        "/System/Library/Fonts/Supplemental/Arial Unicode.ttf",
        "/Library/Fonts/Arial Unicode.ttf",
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
        "/usr/share/fonts/truetype/noto/NotoSansCJK-Bold.ttc",
        "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
        "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",
        "/usr/share/fonts/truetype/unifont/unifont.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    ],
}

_VISUAL_LAYER_COLORS = {
    # R3 (Task 15): "pool" color retired — pool/extend merged into one layer.
    "extend": ("#b45309", "#fef3c7"),
    "broad": ("#334155", "#e2e8f0"),
}
_TELEGRAM_PHOTO_MAX_DIMENSION_SUM = 9800
_VISUAL_WIDTH = 2400
_VISUAL_MARGIN = 76
_VISUAL_TABLE_HEADER_H = 54
_VISUAL_TABLE_ROW_H = 58


def _load_visual_font(size: int, bold: bool = False):
    from PIL import ImageFont

    key = "bold" if bold else "regular"
    for candidate in _VISUAL_FONT_CANDIDATES[key]:
        path = Path(candidate)
        if not path.exists():
            continue
        try:
            return ImageFont.truetype(str(path), size)
        except Exception:
            continue
    return ImageFont.load_default()


def _fit_text(draw, text: str, font, max_width: int) -> str:
    text = str(text)
    if draw.textlength(text, font=font) <= max_width:
        return text
    ellipsis = "…"
    while text and draw.textlength(text + ellipsis, font=font) > max_width:
        text = text[:-1]
    return (text + ellipsis) if text else ellipsis


def _draw_fit(draw, xy: tuple[int, int], text: str, font, fill: str, max_width: int) -> None:
    draw.text(xy, _fit_text(draw, text, font, max_width), font=font, fill=fill)


def _rows_by_layer_and_bucket(rows: list[dict]) -> dict:
    grouped = {
        layer: {bucket: [] for bucket in CONCEPT_BUCKET_ORDER}
        for layer in LAYER_ORDER
    }
    for row in rows:
        layer = row.get("layer", "broad")
        bucket = row.get("bucket") or "其他"
        if layer not in LAYER_ORDER:
            logger.warning(
                "layer leak in _rows_by_layer_and_bucket: %r (row=%s); row will be hidden",
                layer, row.get("cells", ["?"])[0] if row.get("cells") else "?",
            )
        grouped.setdefault(layer, {}).setdefault(bucket, []).append(row)
    return grouped


def _estimate_visual_height(section: dict) -> int:
    height = 260
    height += 96 * len(section.get("alerts", []))
    for block in section.get("blocks", []):
        height += 90
        if block.get("grouped") is False:
            rows = block.get("rows", [])
            height += _VISUAL_TABLE_HEADER_H + _VISUAL_TABLE_ROW_H * max(1, len(rows)) + 46
            continue
        grouped = _rows_by_layer_and_bucket(block.get("rows", []))
        for layer in LAYER_ORDER:
            layer_rows = sum(len(rows) for rows in grouped.get(layer, {}).values())
            height += 72
            if not layer_rows:
                height += 58
                continue
            layer_dict = grouped.get(layer, {})
            for bucket in CONCEPT_BUCKET_ORDER:
                rows = layer_dict.get(bucket, [])
                if rows:
                    height += 50 + _VISUAL_TABLE_HEADER_H + _VISUAL_TABLE_ROW_H * len(rows) + 28
            # trailing-extras: buckets emitted by _grouping_bucket that are NOT
            # in the L2 order (e.g. legacy fallback labels for unregistered symbols)
            for bucket, rows in layer_dict.items():
                if rows and bucket not in CONCEPT_BUCKET_ORDER:
                    height += 50 + _VISUAL_TABLE_HEADER_H + _VISUAL_TABLE_ROW_H * len(rows) + 28
    return max(height + 260, 640)


def _scaled_widths(widths: list[int], total_width: int) -> list[int]:
    raw_total = sum(widths) or total_width
    scaled = [max(80, int(w * total_width / raw_total)) for w in widths]
    diff = total_width - sum(scaled)
    if scaled:
        scaled[-1] += diff
    return scaled


def _draw_visual_table_header(draw, x: int, y: int, col_widths: list[int], columns: list[str], font) -> int:
    row_h = _VISUAL_TABLE_HEADER_H
    draw.rectangle([x, y, x + sum(col_widths), y + row_h], fill="#f1f5f9")
    cur_x = x
    for width, column in zip(col_widths, columns):
        _draw_fit(draw, (cur_x + 18, y + 12), column, font, "#334155", width - 34)
        cur_x += width
    return y + row_h


def _resize_for_telegram_photo(image):
    width, height = image.size
    if width + height <= _TELEGRAM_PHOTO_MAX_DIMENSION_SUM:
        return image

    scale = _TELEGRAM_PHOTO_MAX_DIMENSION_SUM / float(width + height)
    new_size = (max(1, int(width * scale)), max(1, int(height * scale)))
    try:
        from PIL import Image
        resampling = Image.Resampling.LANCZOS
    except Exception:
        resampling = 1
    return image.resize(new_size, resampling)


def render_morning_report_images(
    market_signals: dict | None = None,
    dv_result: dict | None = None,
    output_dir: str | Path | None = None,
    photo_safe: bool = False,
) -> list[Path]:
    """Render each morning-report section as one PNG image."""
    from PIL import Image, ImageDraw

    sections = build_morning_visual_sections(market_signals=market_signals, dv_result=dv_result)
    if not sections:
        return []

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_dir = Path(output_dir) if output_dir else SCANS_DIR / "morning_images_{}".format(timestamp)
    out_dir.mkdir(parents=True, exist_ok=True)

    width = _VISUAL_WIDTH
    margin = _VISUAL_MARGIN
    content_w = width - margin * 2
    title_font = _load_visual_font(58, bold=True)
    subtitle_font = _load_visual_font(32)
    block_font = _load_visual_font(38, bold=True)
    layer_font = _load_visual_font(34, bold=True)
    bucket_font = _load_visual_font(31, bold=True)
    header_font = _load_visual_font(29, bold=True)
    row_font = _load_visual_font(30)
    small_font = _load_visual_font(26)

    image_paths = []
    for index, section in enumerate(sections, 1):
        height = _estimate_visual_height(section) + 2000
        image = Image.new("RGB", (width, height), "#f8fafc")
        draw = ImageDraw.Draw(image)

        y = 60
        draw.rounded_rectangle([margin, y, width - margin, y + 120], radius=24, fill="#0f172a")
        _draw_fit(draw, (margin + 44, y + 30), section["title"], title_font, "#ffffff", content_w - 88)
        y += 146
        _draw_fit(draw, (margin + 4, y), section.get("subtitle", ""), subtitle_font, "#475569", content_w)
        y += 72

        for alert in section.get("alerts", []):
            draw.rounded_rectangle([margin, y, width - margin, y + 78], radius=18, fill="#fee2e2")
            _draw_fit(
                draw,
                (margin + 28, y + 15),
                "🔴 " + alert.get("text", ""),
                _load_visual_font(40, bold=True),
                "#b91c1c",
                content_w - 56,
            )
            y += 96

        for block in section.get("blocks", []):
            draw.text((margin, y), block["title"], font=block_font, fill="#111827")
            y += 62
            col_widths = _scaled_widths(block.get("widths", []), content_w)

            if block.get("grouped") is False:
                y = _draw_visual_table_header(draw, margin, y, col_widths, block["columns"], header_font)
                rows = block.get("rows", [])
                if not rows:
                    draw.text((margin + 18, y), "无数据", font=small_font, fill="#64748b")
                    y += 58
                for row_idx, row in enumerate(rows):
                    row_h = _VISUAL_TABLE_ROW_H
                    fill = "#fee2e2" if row.get("alert") else ("#ffffff" if row_idx % 2 == 0 else "#f8fafc")
                    text_fill = "#b91c1c" if row.get("alert") else "#111827"
                    font = _load_visual_font(32, bold=True) if row.get("alert") else row_font
                    draw.rectangle([margin, y, width - margin, y + row_h], fill=fill)
                    cur_x = margin
                    for cell_idx, (col_width, cell) in enumerate(zip(col_widths, row["cells"])):
                        cell_font = font
                        cell_fill = text_fill
                        if row.get("premium") and cell_idx == 0 and not row.get("alert"):
                            cell_font = _load_visual_font(32, bold=True)
                            cell_fill = "#b91c1c"
                        _draw_fit(draw, (cur_x + 18, y + 13), cell,
                                  cell_font, cell_fill, col_width - 34)
                        cur_x += col_width
                    y += row_h
                y += 46
                continue

            grouped = _rows_by_layer_and_bucket(block.get("rows", []))

            for layer in LAYER_ORDER:
                layer_rows = sum(len(rows) for rows in grouped.get(layer, {}).values())
                dark, light = _VISUAL_LAYER_COLORS[layer]
                draw.rounded_rectangle([margin, y, width - margin, y + 52], radius=13, fill=light)
                layer_label = "{}  {}家公司".format(LAYER_LABELS[layer], layer_rows)
                draw.text((margin + 20, y + 9), layer_label, font=layer_font, fill=dark)
                y += 66

                if not layer_rows:
                    draw.text((margin + 18, y), "无触发", font=small_font, fill="#64748b")
                    y += 58
                    continue

                layer_dict = grouped.get(layer, {})
                # Build the ordered bucket sequence: L2-ordered first, then any
                # trailing-extras (legacy fallback labels for unregistered symbols)
                # that are NOT in CONCEPT_BUCKET_ORDER — mirrors group_items().
                extra_buckets = [
                    b for b in layer_dict if b not in CONCEPT_BUCKET_ORDER and layer_dict[b]
                ]
                for bucket in list(CONCEPT_BUCKET_ORDER) + extra_buckets:
                    rows = layer_dict.get(bucket, [])
                    if not rows:
                        continue
                    draw.text(
                        (margin + 14, y),
                        "{} ({})".format(bucket, len(rows)),
                        font=bucket_font,
                        fill="#0f172a",
                    )
                    y += 50
                    y = _draw_visual_table_header(draw, margin, y, col_widths, block["columns"], header_font)
                    for row_idx, row in enumerate(rows):
                        row_h = _VISUAL_TABLE_ROW_H
                        fill = "#ffffff" if row_idx % 2 == 0 else "#f8fafc"
                        draw.rectangle([margin, y, width - margin, y + row_h], fill=fill)
                        cur_x = margin
                        for cell_idx, (col_width, cell) in enumerate(zip(col_widths, row["cells"])):
                            cell_font = row_font
                            cell_fill = "#111827"
                            if row.get("premium") and cell_idx == 0:
                                cell_font = _load_visual_font(32, bold=True)
                                cell_fill = "#b91c1c"
                            _draw_fit(draw, (cur_x + 18, y + 14), cell,
                                      cell_font, cell_fill, col_width - 34)
                            cur_x += col_width
                        y += row_h
                    y += 26
                y += 12
            y += 20

        footer_y = y + 18
        draw.text(
            (margin, footer_y),
            "Generated {} | Future Capital Morning Report".format(datetime.now().strftime("%Y-%m-%d %H:%M")),
            font=small_font,
            fill="#64748b",
        )
        final_height = min(height, footer_y + 58)
        image = image.crop((0, 0, width, final_height))
        path = out_dir / "{:02d}_{}.png".format(index, section["slug"])
        if photo_safe:
            image = _resize_for_telegram_photo(image)
        image.save(path, "PNG", optimize=True)
        image_paths.append(path)

    return image_paths


def render_morning_report_pdf(
    image_paths: list[Path],
    output_path: str | Path | None = None,
) -> Path | None:
    """Combine section PNGs into a single multi-page PDF."""
    if not image_paths:
        return None

    from PIL import Image

    paths = [Path(path) for path in image_paths if Path(path).exists()]
    if not paths:
        return None

    pdf_path = Path(output_path) if output_path else paths[0].parent / "morning_report.pdf"
    pdf_path.parent.mkdir(parents=True, exist_ok=True)

    pages = []
    for path in paths:
        with Image.open(path) as image:
            pages.append(image.convert("RGB"))

    first, rest = pages[0], pages[1:]
    try:
        first.save(
            pdf_path,
            "PDF",
            save_all=True,
            append_images=rest,
            resolution=150.0,
        )
    finally:
        for page in pages:
            page.close()
    return pdf_path



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
    indicator_summary: dict = None,
    momentum_results: dict = None,
    dv_result: dict = None,
    market_signals: dict = None,
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

    indicator_summary = indicator_summary or {}
    momentum_results = momentum_results or {}

    if market_signals:
        lines.append("信号日: {} | 数据覆盖: {}/{}".format(
            market_signals.get("as_of"),
            market_signals.get("symbols_with_data", 0),
            market_signals.get("symbols_scanned", 0),
        ))
        lines.append("")

        lines.append(format_section_market_timing_factor(market_signals))
        lines.append("")

        lines.append(format_section_volume_concentration(
            market_signals.get("volume_concentration", {})))
        lines.append("")

        compass_text = format_section_selection_compass(
            market_signals.get("selection_compass")
        )
        if compass_text:
            lines.append(compass_text)
            lines.append("")

        lines.append(format_section_pmarp_by_signal_and_cap(market_signals))
        lines.append("")
        lines.append(format_section_layered_volume_anomaly(market_signals))
        lines.append("")
    else:
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

    # D. Dollar Volume — flat ranking with a 概念(L2) column (not concept-bucketed).
    if dv_result:
        lines.append(format_section_d(
            dv_result, _premium_symbols_from_market_signals(market_signals),
        ))
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
    n_scanned = (
        market_signals.get("symbols_scanned", 0)
        if market_signals
        else momentum_results.get("symbols_scanned", 0)
    )
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


def _deliver_morning_report(market_signals, dv_result, daily_msg, image_delivery,
                            image_report, image_output_dir, photo_safe, as_of,
                            no_telegram=False):
    """Render + deliver the morning report, preserving every legacy branch.

    HTML is a new front branch (image_delivery == "html"): on any failure or
    send_document() returning False it falls back to the original PDF/PNG path
    by rewriting image_delivery to "pdf". The legacy path below is kept verbatim
    — Pillow ImportError text degrade plus the pdf/document/photo/text send
    branches via the real _send_group_* helpers.
    """
    # ── 新增 HTML 分支（仅 image_delivery == "html"）──
    if image_delivery == "html":
        try:
            html_path = compile_morning_html_report(
                build_html_payload(market_signals, dv_result, as_of), as_of)
            if no_telegram:
                print(str(html_path))
                return True
            if send_document(str(html_path),
                             caption="未来资本晨报 — {}".format(as_of), channel="group"):
                send_message("晨报 HTML — {}".format(as_of), channel="group")
                return True
            logger.warning("HTML send_document 返回 False → 回退 PDF")
        except Exception as exc:
            logger.warning("HTML 渲染/发送异常 (%s) → 回退 PDF", exc)
        image_delivery = "pdf"        # 落空 → 走下方旧路径

    # ── 旧路径（原样保留：渲染含 Pillow 缺失文本降级；发送含 pdf/document/photo/text 分支）──
    image_paths, pdf_path = [], None
    if image_report:
        try:
            image_paths = render_morning_report_images(
                market_signals=market_signals, dv_result=dv_result,
                output_dir=image_output_dir, photo_safe=photo_safe)
            logger.info("晨报图片已生成: %d 张", len(image_paths))
            if image_delivery == "pdf" and image_paths:
                pdf_path = render_morning_report_pdf(image_paths)
                logger.info("晨报 PDF 已生成: %s", pdf_path)
        except ImportError as exc:
            # Pillow 缺失 (云端 git pull 部署后未自动装新依赖) → 降级到文本模式，
            # 不让 cron 整体异常。文本路径是 first-class fallback。
            logger.warning("图片渲染依赖缺失 (%s)，降级到文本模式发送晨报", exc)
            image_report = False
            image_paths = []
    if no_telegram:
        if image_report and pdf_path:
            print(str(pdf_path))
        elif image_report and image_paths:
            print("\n".join(str(p) for p in image_paths))
        else:
            print(daily_msg)
        return True
    if image_report and pdf_path:
        _send_group_pdf_report(pdf_path)
        return True
    if image_report and image_paths:
        _send_group_image_report(image_paths, delivery=image_delivery)
        return True
    _send_group_report(daily_msg)
    return True


def main():
    parser = argparse.ArgumentParser(description="未来资本 晨报")
    parser.add_argument("--no-telegram", action="store_true", help="不推送 Telegram")
    parser.add_argument(
        "--symbols", type=str,
        help="指定股票代码，逗号分隔（override 模式：所有指定标的视为 extend 层，绕过 mcap 分层）",
    )
    parser.add_argument("--include-social", action="store_true",
                        help="启用社交情绪段（默认 skip：Adanos 采集 cron 已下线）")
    parser.add_argument("--no-social", action="store_true",
                        help="[DEPRECATED] no-op；社交段默认已 skip，保留兼容老 cron 命令行")
    parser.add_argument("--image-report", action="store_true",
                        help="每个晨报 section 生成一张图片；Telegram 发送图片而不是长文本")
    parser.add_argument("--image-delivery", choices=["html", "pdf", "document", "photo"],
                        default="pdf",
                        help="视觉晨报发送方式：html 单文件可滚动表格；pdf 合并为单文件；document/photo 逐张发送 PNG")
    parser.add_argument("--image-output-dir", type=str,
                        help="图片输出目录（默认 data/scans/morning_images_<timestamp>）")
    args = parser.parse_args()

    logger.info("=" * 60)
    logger.info("未来资本 晨报 开始")
    logger.info("=" * 60)

    start_time = time.time()

    try:
        # 1. 获取股票列表
        symbols_override = None
        if args.symbols:
            symbols_override = [s.strip().upper() for s in args.symbols.split(",")]

        # 2. 市场技术信号（extend $10B+ ∪ pool.json 成员；broad universe 已退出选股扫描，仅保留给 Section 0 S2 大盘广度）
        # R3 (Task 15): 弃用的独立 get_symbols() 预取已删除——build_market_signal_report
        # 内部经 resolver 自行解析扫描宇宙，此处不再需要重复计算 symbols 列表。
        market_signals = build_market_signal_report(symbols_override=symbols_override)
        logger.info(
            "市场信号完成: scanned=%d data=%d",
            market_signals.get("symbols_scanned", 0),
            market_signals.get("symbols_with_data", 0),
        )

        # 3. Dollar Volume 采集
        dv_result = run_dollar_volume()
        if dv_result and dv_result.get("rankings"):
            from src.data.dollar_volume import get_previous_day_ranks, annotate_rank_changes
            # P2-4 修：无 _today_iso() helper；用 inline strftime（codebase 惯例，见 :1416/:1726）
            dv_date = dv_result.get("date") or datetime.now().strftime("%Y-%m-%d")
            prev_ranks = get_previous_day_ranks(dv_date)
            annotate_rank_changes(dv_result["rankings"], prev_ranks)
            annotate_rank_changes(dv_result.get("new_faces", []), prev_ranks)

        # 4. 市场情绪脉搏 + 社交热门 (Adanos market-level) — opt-in 默认 skip。
        market_pulse = None
        trending_data = None
        if args.include_social:
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

        # 5. 社交情绪雷达 — opt-in 默认 skip。
        social_scan = None
        if args.include_social:
            try:
                from src.indicators.social_attention import scan_social_signals
                logger.info("开始社交情绪扫描...")
                # R3: 只有这条已默认 skip 的分支还需要完整股票列表，惰性取用
                # （旧的无条件 get_symbols() 预取已删除，见上方"1. 获取股票列表"注释）。
                social_symbols = (
                    symbols_override if symbols_override is not None else get_symbols()
                )
                social_scan = scan_social_signals(social_symbols)
                logger.info("社交情绪扫描完成: %d 只有数据", social_scan.get("symbols_with_data", 0))
            except Exception as e:
                logger.warning("社交情绪扫描失败: %s", e)

        elapsed = time.time() - start_time

        # 6. 格式化
        daily_msg = format_morning_report(
            dv_result=dv_result, market_signals=market_signals,
            market_pulse=market_pulse, trending_data=trending_data,
            social_scan=social_scan, elapsed=elapsed)

        # 7. 保存 JSON
        SCANS_DIR.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        save_path = SCANS_DIR / "morning_{}.json".format(timestamp)
        save_data = {
            "timestamp": timestamp,
            "symbols_scanned": market_signals.get("symbols_scanned", 0),
            "elapsed": round(elapsed, 1),
            "market_signals": market_signals,
        }
        with open(save_path, "w", encoding="utf-8") as f:
            json.dump(save_data, f, ensure_ascii=False, indent=2, default=str)
        logger.info("结果已保存: %s", save_path)

        # 8. 渲染 + 投递（HTML 新分支 + 旧 pdf/document/photo/text 全保留）
        as_of = market_signals.get("as_of") or datetime.now().strftime("%Y-%m-%d")
        _deliver_morning_report(
            market_signals, dv_result, daily_msg,
            args.image_delivery, args.image_report, args.image_output_dir,
            args.image_delivery == "photo", as_of,
            no_telegram=args.no_telegram,
        )

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
