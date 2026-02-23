"""
Theme Engine P2 — 池子动态扩展管理器

热股从 Engine B (Attention Engine) 进入股票池，自动拉取价格数据。
池子只增不减（除手动删除外），source="attention" 标记来源。

Usage:
    from terminal.theme_pool import expand_pool_from_attention
    result = expand_pool_from_attention(["IONQ", "RKLB", "PLTR"])
"""
import logging
import time
from datetime import datetime
from typing import Dict, List, Optional

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from config.settings import (
    THEME_MAX_NEW_TICKERS,
    POOL_SOURCE_ATTENTION,
    API_CALL_INTERVAL,
)
from src.data.pool_manager import (
    load_universe,
    save_universe,
    load_history,
    save_history,
    get_symbols,
)
from src.data.fmp_client import fmp_client

logger = logging.getLogger(__name__)


def expand_pool_from_attention(
    hot_tickers: List[str],
    max_new: int = THEME_MAX_NEW_TICKERS,
    dry_run: bool = False,
) -> Dict:
    """
    热股不在池 -> FMP profile -> price CSV -> 加入 universe.json

    Args:
        hot_tickers: Engine B 返回的热门 ticker 列表
        max_new: 单次最多新增几只（安全阀）
        dry_run: True = 只看不做

    Returns:
        {
            "added": [{"symbol": ..., "companyName": ...}, ...],
            "skipped_in_pool": ["NVDA", ...],
            "failed": ["BADTICKER", ...],
            "dry_run": bool,
        }
    """
    current_symbols = set(get_symbols())
    result = {
        "added": [],
        "skipped_in_pool": [],
        "failed": [],
        "dry_run": dry_run,
    }

    candidates = []
    for ticker in hot_tickers:
        ticker = ticker.upper().strip()
        if not ticker:
            continue
        if ticker in current_symbols:
            result["skipped_in_pool"].append(ticker)
        else:
            if ticker not in [c for c in candidates]:
                candidates.append(ticker)

    if not candidates:
        logger.info("池扩展: 所有热股已在池中，无需扩展")
        return result

    # 安全阀
    if len(candidates) > max_new:
        logger.info(
            "池扩展: 候选 %d 只，限制 max_new=%d",
            len(candidates), max_new,
        )
        candidates = candidates[:max_new]

    if dry_run:
        logger.info("池扩展 [dry-run]: 将新增 %s", candidates)
        result["added"] = [{"symbol": t} for t in candidates]
        return result

    # 逐个拉取 profile + 价格
    stocks = load_universe()
    added_entries = []

    for ticker in candidates:
        # FMP profile
        profile = fmp_client.get_profile(ticker)
        if not profile:
            logger.warning("池扩展: %s FMP 无数据，跳过", ticker)
            result["failed"].append(ticker)
            continue

        new_entry = {
            "symbol": ticker,
            "companyName": profile.get("companyName", ""),
            "marketCap": profile.get("mktCap"),
            "sector": profile.get("sector", ""),
            "industry": profile.get("industry", ""),
            "exchange": profile.get(
                "exchangeShortName", profile.get("exchange", "")
            ),
            "country": profile.get("country", ""),
            "source": POOL_SOURCE_ATTENTION,
            "added_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }

        # 拉取价格数据
        try:
            from src.data.price_fetcher import fetch_and_update_price

            price_df = fetch_and_update_price(ticker, force_full=True)
            if price_df is None or price_df.empty:
                logger.warning("池扩展: %s 价格数据为空，仍加入池", ticker)
        except Exception as e:
            logger.warning("池扩展: %s 价格拉取异常: %s，仍加入池", ticker, e)

        stocks.append(new_entry)
        added_entries.append(new_entry)
        logger.info(
            "池扩展: +%s (%s) source=%s",
            ticker, new_entry["companyName"], POOL_SOURCE_ATTENTION,
        )

    # 写入 universe
    if added_entries:
        save_universe(stocks)

        # 记录历史
        history = load_history()
        history.append({
            "date": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "entered": [e["symbol"] for e in added_entries],
            "exited": [],
            "total_count": len(stocks),
            "reason": "theme-engine-attention",
        })
        save_history(history)

    result["added"] = added_entries
    return result


def get_attention_pool() -> List[Dict]:
    """返回所有 source='attention' 的池内股票。"""
    stocks = load_universe()
    return [s for s in stocks if s.get("source") == POOL_SOURCE_ATTENTION]


def remove_from_attention_pool(symbol: str) -> bool:
    """
    手动移除 attention 源股票（唯一的移除方式）。

    Returns:
        True if removed, False if not found or not attention source.
    """
    symbol = symbol.upper().strip()
    stocks = load_universe()

    target = None
    for s in stocks:
        if s.get("symbol") == symbol and s.get("source") == POOL_SOURCE_ATTENTION:
            target = s
            break

    if target is None:
        logger.warning("移除失败: %s 不在 attention 池中", symbol)
        return False

    stocks.remove(target)
    save_universe(stocks)

    # 记录历史
    history = load_history()
    history.append({
        "date": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "entered": [],
        "exited": [symbol],
        "total_count": len(stocks),
        "reason": "manual-remove-attention",
    })
    save_history(history)

    logger.info("已从 attention 池移除: %s", symbol)
    return True


def get_pool_expansion_stats() -> Dict:
    """
    池子来源统计。

    Returns:
        {
            "total": int,
            "screener": int,
            "analysis": int,
            "attention": int,
            "unknown": int,
        }
    """
    stocks = load_universe()
    stats = {
        "total": len(stocks),
        "screener": 0,
        "analysis": 0,
        "attention": 0,
        "manual": 0,
        "unknown": 0,
    }

    for s in stocks:
        source = s.get("source", "")
        if source == "analysis":
            stats["analysis"] += 1
        elif source == POOL_SOURCE_ATTENTION:
            stats["attention"] += 1
        elif source == "manual":
            stats["manual"] += 1
        elif source == "screener" or not source:
            # 无 source 字段 = screener
            stats["screener"] += 1
        else:
            stats["unknown"] += 1

    return stats
