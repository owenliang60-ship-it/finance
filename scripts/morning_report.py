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
    EXTENDED_UNIVERSE_MIN_MCAP_B,
)
from src.data import get_symbols
from src.indicators.dv_acceleration import format_dv
from src.telegram_bot import send_message, split_message

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
)
logger = logging.getLogger(__name__)

EXTENDED_LAYER_MIN_MCAP = EXTENDED_UNIVERSE_MIN_MCAP_B * 1_000_000_000
MORNING_SIGNAL_PRICE_ROWS = 180
LAYER_ORDER = ["pool", "extend", "broad"]
LAYER_LABELS = {
    "pool": "Pool",
    "extend": "Extend ($10B+)",
    "broad": "Broad ($1B-$10B)",
}
LAYER_TOP_N = 8

CONCEPT_BUCKET_ORDER = [
    "AI算力/云",
    "半导体链",
    "数据中心电力",
    "通信/网络设备",
    "互联网/广告",
    "软件/SaaS",
    "自动驾驶/机器人",
    "金融/加密",
    "医药/生命科学",
    "工业/航天/国防",
    "消费/电商",
    "能源/材料",
    "地产/基础设施",
    "ETF/宏观工具",
    "其他",
]

THEME_BUCKET_HINTS = {
    "ai_chip": "AI算力/云",
    "ai_software": "AI算力/云",
    "ai_agent": "AI算力/云",
    "ai_infra": "AI算力/云",
    "cloud": "AI算力/云",
    "quantum": "AI算力/云",
    "memory": "半导体链",
    "semicap": "半导体链",
    "chip_design": "半导体链",
    "liquid_cooling": "数据中心电力",
    "nuclear_power": "数据中心电力",
    "cybersecurity": "软件/SaaS",
    "enterprise_sw": "软件/SaaS",
    "autonomous_driving": "自动驾驶/机器人",
    "humanoid_robot": "自动驾驶/机器人",
    "ev_battery": "自动驾驶/机器人",
    "streaming": "互联网/广告",
    "digital_ads": "互联网/广告",
    "fintech": "金融/加密",
    "crypto": "金融/加密",
    "glp1": "医药/生命科学",
    "biotech": "医药/生命科学",
    "space": "工业/航天/国防",
    "defense": "工业/航天/国防",
}

ETF_SYMBOLS = {
    "SPY", "QQQ", "IWM", "DIA", "SOXX", "SMH", "XLK", "XLF", "XLE", "XLV",
    "XLY", "XLI", "XLC", "EWY", "EWT", "FXI", "KWEB",
}


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

def _format_market_cap(market_cap: float | None) -> str:
    if not market_cap:
        return "N/A"
    if market_cap >= 1e12:
        return "${:.1f}T".format(market_cap / 1e12)
    if market_cap >= 1e9:
        return "${:.1f}B".format(market_cap / 1e9)
    return "${:.0f}M".format(market_cap / 1e6)


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


def _display_classification(item: dict) -> str:
    industry = item.get("industry") or ""
    sector = item.get("sector") or ""
    if industry and industry != "Unknown":
        return industry
    if sector and sector != "Unknown":
        return sector
    return "Unclassified"


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


def _theme_bucket_for_symbol(symbol: str) -> str | None:
    try:
        from config.settings import THEME_KEYWORDS_SEED
        for theme, bucket in THEME_BUCKET_HINTS.items():
            tickers = THEME_KEYWORDS_SEED.get(theme, {}).get("tickers", [])
            if symbol in {ticker.upper() for ticker in tickers}:
                return bucket
    except Exception:
        return None
    return None


def _concept_bucket(item: dict) -> str:
    symbol = (item.get("symbol") or "").upper()
    if symbol in ETF_SYMBOLS:
        return "ETF/宏观工具"

    theme_bucket = _theme_bucket_for_symbol(symbol)
    if theme_bucket:
        return theme_bucket

    text = " ".join(
        str(item.get(key) or "")
        for key in ["companyName", "shortName", "longName", "sector", "industry"]
    ).lower()

    if any(k in text for k in ["semiconductor", "chip", "foundry", "memory", "dram", "nand"]):
        return "半导体链"
    if any(k in text for k in ["data center", "cloud", "gpu", "ai", "quantum"]):
        return "AI算力/云"
    if any(k in text for k in ["electrical", "electric", "power", "nuclear", "utility", "utilities", "grid", "fuel cell"]):
        return "数据中心电力"
    if any(k in text for k in ["communication equipment", "network", "optical", "telecom", "satellite"]):
        return "通信/网络设备"
    if any(k in text for k in ["internet content", "advertising", "media", "streaming", "entertainment"]):
        return "互联网/广告"
    if any(k in text for k in ["software", "saas", "cybersecurity", "information technology services"]):
        return "软件/SaaS"
    if any(k in text for k in ["auto", "vehicle", "robot", "lidar", "battery", "ev "]):
        return "自动驾驶/机器人"
    if any(k in text for k in ["financial", "bank", "capital markets", "crypto", "bitcoin", "insurance", "fintech"]):
        return "金融/加密"
    if any(k in text for k in ["health", "biotech", "drug", "pharma", "medical", "therapeutics"]):
        return "医药/生命科学"
    if any(k in text for k in ["aerospace", "defense", "industrial", "machinery", "logistics", "engineering"]):
        return "工业/航天/国防"
    if any(k in text for k in ["consumer", "retail", "e-commerce", "apparel", "restaurant", "travel"]):
        return "消费/电商"
    if any(k in text for k in ["energy", "materials", "mining", "chemical", "metal", "lithium", "oil", "gas"]):
        return "能源/材料"
    if any(k in text for k in ["real estate", "reit", "construction", "infrastructure"]):
        return "地产/基础设施"
    if any(k in text for k in ["etf", "fund", "trust"]):
        return "ETF/宏观工具"
    return "其他"


def _layer_for_symbol(symbol: str, metadata: dict, pool_symbols: set) -> str:
    if symbol in pool_symbols:
        return "pool"
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
        grouped.setdefault(item.get("layer", "broad"), []).append(item)
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
    grouped = {bucket: [] for bucket in CONCEPT_BUCKET_ORDER}
    for item in items:
        bucket = item.get("concept_bucket") or _concept_bucket(item)
        grouped.setdefault(bucket, []).append(item)
    return {bucket: grouped[bucket] for bucket in CONCEPT_BUCKET_ORDER if grouped.get(bucket)}


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


def _enrich_with_layer(item: dict, metadata: dict, pool_symbols: set) -> dict:
    symbol = item["symbol"]
    meta = metadata.get(symbol, {})
    enriched = dict(item)
    for key in ["companyName", "shortName", "longName", "sector", "industry", "exchange"]:
        if meta.get(key):
            enriched[key] = meta[key]
    enriched["marketCap"] = meta.get("marketCap")
    enriched["layer"] = _layer_for_symbol(symbol, metadata, pool_symbols)
    enriched["concept_bucket"] = _concept_bucket(enriched)
    return enriched


def build_market_signal_report(symbols_override: list[str] | None = None) -> dict:
    """Build broad-universe technical signal payload for the merged morning report."""
    from datetime import date

    from scripts.broad_market_scan import (
        BROAD_SCAN_RETURN_THRESHOLD,
        BROAD_SCAN_RVOL_THRESHOLD,
        fetch_universe_metadata,
        load_price_frames,
        scan_candidates,
    )
    from src.data.market_store import get_store
    from src.indicators.dv_acceleration import scan_dv_acceleration
    from src.indicators.pmarp import analyze_pmarp
    from src.indicators.rvol_sustained import scan_rvol_sustained

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
    else:
        universe_cache = fetch_universe_metadata(as_of_date=date.today().isoformat(), min_mcap_b=1.0)
        metadata = universe_cache.get("stocks", {})
        symbols = sorted(metadata.keys())

    _merge_local_metadata(metadata, symbols)

    price_frames = load_price_frames(symbols, rows_needed=MORNING_SIGNAL_PRICE_ROWS)
    price_dict = {
        symbol: _frame_with_date(symbol, frame)
        for symbol, frame in price_frames.items()
    }

    broad_scan = scan_candidates(price_frames, metadata, pool_symbols)

    db_rows = [
        {
            "symbol": item["symbol"],
            "date": broad_scan["scan_date"],
            "rvol": item["rvol"],
            "return_pct": item["return_pct"],
            "market_cap": item.get("marketCap"),
            "in_pool": item.get("in_pool", False),
        }
        for item in broad_scan["all_triggered"]
    ]
    if db_rows:
        get_store().save_broad_scan_hits(db_rows)

    pmarp_raw = []
    for symbol, frame in price_dict.items():
        result = analyze_pmarp(frame)
        if result.get("signal") == "oversold_recovery":
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
        for item in (
            list(broad_scan["all_triggered"])
            + pmarp_raw
            + dv_raw
            + rvol_raw
        )
    ]
    _hydrate_signal_metadata(metadata, signal_symbols)

    broad_hits = sorted(
        [_enrich_with_layer(item, metadata, pool_symbols) for item in broad_scan["all_triggered"]],
        key=lambda x: (-x["rvol"], -x["return_pct"], x["symbol"]),
    )

    pmarp_signals = [
        _enrich_with_layer(item, metadata, pool_symbols)
        for item in pmarp_raw
    ]
    pmarp_signals.sort(key=lambda x: (x.get("value") or 0, x["symbol"]))

    dv_hits = [
        _enrich_with_layer(item, metadata, pool_symbols)
        for item in dv_raw
    ]
    dv_hits.sort(key=lambda x: (-(x.get("ratio") or 0), x["symbol"]))

    rvol_hits = [
        _enrich_with_layer(item, metadata, pool_symbols)
        for item in rvol_raw
    ]

    scan_dates = [frame.index.max() for frame in price_frames.values() if not frame.empty]
    as_of = max(scan_dates).date().isoformat() if scan_dates else date.today().isoformat()

    return {
        "as_of": as_of,
        "symbols_scanned": len(symbols),
        "symbols_with_data": len(price_frames),
        "layer_counts": {
            layer: sum(
                1 for symbol in symbols
                if _layer_for_symbol(symbol, metadata, pool_symbols) == layer
            )
            for layer in LAYER_ORDER
        },
        "broad_scan": {
            "criteria": "RVOL ≥{:.0f}σ + 涨 ≥{:.0f}%".format(
                BROAD_SCAN_RVOL_THRESHOLD,
                BROAD_SCAN_RETURN_THRESHOLD,
            ),
            "hits": broad_hits,
            "triggered_total": broad_scan["triggered_total"],
        },
        "pmarp": {
            "criteria": "PMARP 上穿 2%",
            "hits": pmarp_signals,
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


def format_section_broad_signal(market_signals: dict) -> str:
    section = market_signals.get("broad_scan", {})
    lines = ["*1. 广扫标准 ({})*".format(section.get("criteria", ""))]
    lines.extend(_format_bucketed_items(
        section.get("hits", []),
        "无广扫触发",
        lambda item: "{} | {} | RVOL {:.1f}σ | {:+.1f}% | {}".format(
            _display_company(item),
            _display_classification(item),
            item["rvol"],
            item["return_pct"],
            _format_market_cap(item.get("marketCap")),
        ),
    ))
    return "\n".join(lines)


def format_section_layered_pmarp(market_signals: dict) -> str:
    section = market_signals.get("pmarp", {})
    lines = ["*2. PMARP 信号 ({})*".format(section.get("criteria", ""))]
    lines.extend(_format_bucketed_items(
        section.get("hits", []),
        "无 PMARP 信号",
        lambda item: "{} | {} | {:.1f}% ({:.1f}→{:.1f}) | {}".format(
            _display_company(item),
            _display_classification(item),
            item.get("value") or 0,
            item.get("previous") or 0,
            item.get("value") or 0,
            _format_market_cap(item.get("marketCap")),
        ),
    ))
    return "\n".join(lines)


def format_section_layered_dv(market_signals: dict) -> str:
    section = market_signals.get("dv_acceleration", {})
    lines = ["*3. 量能加速 ({})*".format(section.get("criteria", ""))]
    lines.extend(_format_bucketed_items(
        section.get("hits", []),
        "无加速信号",
        lambda item: "{} | {} | {:.1f}x | 5d={} / 20d={} | {}".format(
            _display_company(item),
            _display_classification(item),
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
    lines = ["*4. RVOL 持续放量 ({})*".format(section.get("criteria", ""))]
    lines.extend(_format_bucketed_items(
        section.get("hits", []),
        "无持续放量信号",
        lambda item: "{} | {} | {} | 最新 {:.1f}σ | {}".format(
            _display_company(item),
            _display_classification(item),
            level_labels.get(item.get("level"), item.get("level", "")),
            item.get("latest_rvol") or 0,
            _format_market_cap(item.get("marketCap")),
        ),
    ))
    return "\n".join(lines)

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

    def normalize(row: dict) -> dict:
        item = dict(row)
        if row.get("company_name") and not item.get("companyName"):
            item["companyName"] = row.get("company_name")
        if row.get("market_cap") and not item.get("marketCap"):
            item["marketCap"] = row.get("market_cap")
        item.setdefault("concept_bucket", _concept_bucket(item))
        return item

    # 新面孔
    if new_faces:
        lines.append("新面孔:")
        lines.extend(_format_bucketed_items(
            [normalize(row) for row in new_faces],
            "无新面孔",
            lambda item: "{} | {} | #{} {}".format(
                _display_company(item),
                _display_classification(item),
                item["rank"],
                format_dv(item["dollar_volume"]),
            ),
        ))

    # Full ranking payload. Telegram splitting handles long reports.
    if rankings:
        lines.append("成交额 Top {}:".format(len(rankings)))
        lines.extend(_format_bucketed_items(
            [normalize(row) for row in rankings],
            "无成交额排行",
            lambda item: "{} | {} | #{} {} | ${:.0f}".format(
                _display_company(item),
                _display_classification(item),
                item["rank"],
                format_dv(item["dollar_volume"]),
                item["price"],
            ),
        ))

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

        lines.append(format_section_broad_signal(market_signals))
        lines.append("")
        lines.append(format_section_layered_pmarp(market_signals))
        lines.append("")
        lines.append(format_section_layered_dv(market_signals))
        lines.append("")
        lines.append(format_section_layered_rvol(market_signals))
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

    # D. Dollar Volume — also concept-bucketed for readability.
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
        symbols_override = None
        if args.symbols:
            symbols_override = [s.strip().upper() for s in args.symbols.split(",")]
            symbols = symbols_override
        else:
            symbols = get_symbols()
        logger.info("股票池: %d 只", len(symbols))

        # 2. 广义市场技术信号（broad universe + market.db 价格）
        market_signals = build_market_signal_report(symbols_override=symbols_override)
        logger.info(
            "市场信号完成: scanned=%d data=%d",
            market_signals.get("symbols_scanned", 0),
            market_signals.get("symbols_with_data", 0),
        )

        # 3. Dollar Volume 采集
        dv_result = run_dollar_volume()

        # 4. 市场情绪脉搏 + 社交热门 (Adanos market-level)
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
            dv_result=dv_result, market_signals=market_signals,
            market_pulse=market_pulse, trending_data=trending_data,
            social_scan=social_scan, elapsed=elapsed)

        # 7. 保存 JSON
        SCANS_DIR.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        save_path = SCANS_DIR / "morning_{}.json".format(timestamp)
        save_data = {
            "timestamp": timestamp,
            "symbols_scanned": market_signals.get("symbols_scanned", len(symbols)),
            "elapsed": round(elapsed, 1),
            "market_signals": market_signals,
        }
        with open(save_path, "w", encoding="utf-8") as f:
            json.dump(save_data, f, ensure_ascii=False, indent=2, default=str)
        logger.info("结果已保存: %s", save_path)

        # 8. 发送 Telegram
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
