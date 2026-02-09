"""
股票池管理
- 动态维护市值 > 1000亿的股票
- 记录进出历史
- 去重处理（同一公司多个股票类别）
"""
import json
import logging
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Set, Tuple

import sys
sys.path.insert(0, str(__file__).rsplit("/src", 1)[0])
from config.settings import POOL_DIR, MARKET_CAP_THRESHOLD
from src.data.fmp_client import fmp_client

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# 文件路径
UNIVERSE_FILE = POOL_DIR / "universe.json"
HISTORY_FILE = POOL_DIR / "pool_history.json"


def _normalize_company_name(name: str) -> str:
    """标准化公司名，用于去重"""
    return (name.lower()
            .replace(" inc.", "")
            .replace(" inc", "")
            .replace(" corp.", "")
            .replace(" corp", "")
            .replace(" ltd.", "")
            .replace(" ltd", "")
            .replace(" llc", "")
            .replace(" plc", "")
            .replace(",", "")
            .strip())


def _deduplicate_stocks(stocks: List[Dict]) -> List[Dict]:
    """去重：同一公司保留市值最大的股票"""
    seen = {}
    for s in stocks:
        name_key = _normalize_company_name(s.get("companyName", ""))
        if name_key not in seen:
            seen[name_key] = s
        elif s.get("marketCap", 0) > seen[name_key].get("marketCap", 0):
            seen[name_key] = s
    return list(seen.values())


def load_universe() -> List[Dict]:
    """加载当前股票池"""
    if UNIVERSE_FILE.exists():
        with open(UNIVERSE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return []


def save_universe(stocks: List[Dict]):
    """保存股票池"""
    POOL_DIR.mkdir(parents=True, exist_ok=True)
    with open(UNIVERSE_FILE, "w", encoding="utf-8") as f:
        json.dump(stocks, f, ensure_ascii=False, indent=2)
    logger.info(f"股票池已保存: {len(stocks)} 只股票")


def load_history() -> List[Dict]:
    """加载历史记录"""
    if HISTORY_FILE.exists():
        with open(HISTORY_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return []


def save_history(history: List[Dict]):
    """保存历史记录"""
    POOL_DIR.mkdir(parents=True, exist_ok=True)
    with open(HISTORY_FILE, "w", encoding="utf-8") as f:
        json.dump(history, f, ensure_ascii=False, indent=2)


def refresh_universe() -> Tuple[List[Dict], List[str], List[str]]:
    """
    刷新股票池
    返回: (新股票池, 新进入的股票, 退出的股票)
    """
    logger.info(f"开始刷新股票池 (市值阈值: ${MARKET_CAP_THRESHOLD/1e9:.0f}B)")

    # 获取最新大市值股票
    raw_stocks = fmp_client.get_large_cap_stocks(MARKET_CAP_THRESHOLD)
    if not raw_stocks:
        logger.error("获取股票列表失败")
        return [], [], []

    logger.info(f"API 返回 {len(raw_stocks)} 只股票")

    # 去重
    new_stocks = _deduplicate_stocks(raw_stocks)
    new_stocks = sorted(new_stocks, key=lambda x: x.get("marketCap", 0), reverse=True)
    logger.info(f"去重后 {len(new_stocks)} 只股票")

    # 对比变化
    old_stocks = load_universe()
    old_symbols = {s.get("symbol") for s in old_stocks}
    new_symbols = {s.get("symbol") for s in new_stocks}

    entered = new_symbols - old_symbols  # 新进入
    exited = old_symbols - new_symbols   # 退出

    # 记录历史
    if entered or exited:
        history = load_history()
        record = {
            "date": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "entered": list(entered),
            "exited": list(exited),
            "total_count": len(new_stocks)
        }
        history.append(record)
        save_history(history)

        if entered:
            logger.info(f"新进入: {entered}")
        if exited:
            logger.info(f"退出: {exited}")

    # 保存新股票池
    save_universe(new_stocks)

    return new_stocks, list(entered), list(exited)


def get_symbols() -> List[str]:
    """获取当前股票池的所有代码"""
    stocks = load_universe()
    return [s.get("symbol") for s in stocks if s.get("symbol")]


def get_stock_info(symbol: str) -> Dict:
    """获取单只股票的信息"""
    stocks = load_universe()
    for s in stocks:
        if s.get("symbol") == symbol:
            return s
    return {}


def ensure_in_pool(symbol: str) -> Dict:
    """
    确保股票在池中。如果不在，通过 FMP API 获取 profile 并加入 universe.json。
    分析即纳入，日后 cron 正常维护。

    Returns:
        stock info dict (from pool or freshly added), empty dict if API fails.
    """
    symbol = symbol.upper()

    # Already in pool?
    info = get_stock_info(symbol)
    if info:
        return info

    # Fetch profile from FMP
    logger.info(f"'{symbol}' 不在股票池中，正在通过 FMP API 获取并加入...")
    profile = fmp_client.get_profile(symbol)
    if not profile:
        logger.warning(f"FMP API 未返回 '{symbol}' 的 profile，无法加入股票池")
        return {}

    # Build stock info entry matching universe.json format
    new_entry = {
        "symbol": symbol,
        "companyName": profile.get("companyName", ""),
        "marketCap": profile.get("mktCap"),
        "sector": profile.get("sector", ""),
        "industry": profile.get("industry", ""),
        "exchange": profile.get("exchangeShortName", profile.get("exchange", "")),
        "country": profile.get("country", ""),
        "source": "analysis",  # 区分来源：analysis vs screener
        "added_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }

    # Add to universe
    stocks = load_universe()
    stocks.append(new_entry)
    save_universe(stocks)

    # Record in history
    history = load_history()
    history.append({
        "date": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "entered": [symbol],
        "exited": [],
        "total_count": len(stocks),
        "reason": "auto-admitted via analysis",
    })
    save_history(history)

    logger.info(f"'{symbol}' ({new_entry['companyName']}) 已加入股票池 (source: analysis)")
    return new_entry


def print_universe_summary():
    """打印股票池概况"""
    stocks = load_universe()
    if not stocks:
        print("股票池为空")
        return

    print(f"\n{'='*70}")
    print(f"股票池概况: {len(stocks)} 只股票")
    print(f"{'='*70}")

    # 按行业分组
    sector_count = {}
    for s in stocks:
        sector = s.get("sector", "Unknown") or "Unknown"
        sector_count[sector] = sector_count.get(sector, 0) + 1

    print("\n行业分布:")
    for sector, count in sorted(sector_count.items(), key=lambda x: -x[1]):
        print(f"  {sector}: {count} 家")

    # 前 20 大市值
    print(f"\n前 20 大市值:")
    print(f"{'排名':<4} {'代码':<8} {'公司名称':<30} {'市值($B)':<10} {'行业':<20}")
    print("-" * 75)
    for i, s in enumerate(stocks[:20], 1):
        print(f"{i:<4} {s.get('symbol', 'N/A'):<8} {s.get('companyName', 'N/A')[:28]:<30} "
              f"${s.get('marketCap', 0)/1e9:,.0f}B{'':<3} {s.get('industry', 'N/A')[:18]}")


if __name__ == "__main__":
    # 刷新股票池
    stocks, entered, exited = refresh_universe()

    if entered:
        print(f"\n新进入的股票: {entered}")
    if exited:
        print(f"\n退出的股票: {exited}")

    # 打印概况
    print_universe_summary()
