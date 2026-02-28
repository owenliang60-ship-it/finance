"""
基本面数据获取与缓存
- 公司概况 (profiles)
- 财务比率 (ratios)
- 收入报表 (income)
- 资产负债表 (balance_sheet)
- 现金流量表 (cash_flow)
"""
import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Optional, List, Dict

import sys
sys.path.insert(0, str(__file__).rsplit("/src", 1)[0])
from config.settings import FUNDAMENTAL_DIR
from src.data.pool_manager import get_symbols

# Use tool registry instead of direct fmp_client (Phase 1 migration)
try:
    from terminal.tools import registry
    USE_REGISTRY = True
except ImportError:
    # Fallback to direct fmp_client if registry not available
    from src.data.fmp_client import fmp_client
    USE_REGISTRY = False

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# 文件路径
PROFILES_FILE = FUNDAMENTAL_DIR / "profiles.json"
RATIOS_FILE = FUNDAMENTAL_DIR / "ratios.json"
INCOME_FILE = FUNDAMENTAL_DIR / "income.json"
BALANCE_SHEET_FILE = FUNDAMENTAL_DIR / "balance_sheet.json"
CASH_FLOW_FILE = FUNDAMENTAL_DIR / "cash_flow.json"


def _load_json(path: Path) -> Dict:
    """加载 JSON 文件"""
    if path.exists():
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


def _save_json(path: Path, data: Dict):
    """保存 JSON 文件"""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


# ========== 公司概况 ==========

def fetch_profile(symbol: str) -> Optional[Dict]:
    """获取单只股票的公司概况"""
    if USE_REGISTRY:
        data = registry.execute("get_profile", symbol=symbol)
    else:
        data = fmp_client.get_profile(symbol)

    if data:
        data["_updated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    return data


def update_profiles(symbols: List[str] = None) -> Dict[str, Dict]:
    """批量更新公司概况"""
    if symbols is None:
        symbols = get_symbols()

    logger.info(f"更新 {len(symbols)} 只股票的公司概况...")

    profiles = _load_json(PROFILES_FILE)
    updated_count = 0

    for i, symbol in enumerate(symbols, 1):
        logger.info(f"[{i}/{len(symbols)}] {symbol}")
        profile = fetch_profile(symbol)
        if profile:
            profiles[symbol] = profile
            updated_count += 1

    profiles["_meta"] = {
        "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "count": len(profiles) - 1  # 减去 _meta
    }

    _save_json(PROFILES_FILE, profiles)
    logger.info(f"公司概况更新完成: {updated_count}/{len(symbols)}")

    return profiles


def _sync_to_market_db(table_type: str, data: Dict) -> None:
    """Dual-write to market.db (non-fatal, failure only logs warning)."""
    try:
        from src.data.market_store import get_store
        store = get_store()
        upsert_fn = {
            "income": store.upsert_income,
            "balance_sheet": store.upsert_balance_sheet,
            "cash_flow": store.upsert_cash_flow,
            "ratios": store.upsert_ratios,
        }[table_type]
        for symbol, rows in data.items():
            if symbol == "_meta" or not isinstance(rows, list):
                continue
            upsert_fn(symbol, rows)
    except Exception as e:
        logger.warning("[market.db] dual-write %s failed: %s", table_type, e)


def get_profile(symbol: str) -> Optional[Dict]:
    """获取公司概况 (优先用缓存)"""
    profiles = _load_json(PROFILES_FILE)
    return profiles.get(symbol)


# ========== 财务比率 ==========

def fetch_ratios(symbol: str, limit: int = 4) -> List[Dict]:
    """获取单只股票的财务比率"""
    if USE_REGISTRY:
        data = registry.execute("get_ratios", symbol=symbol, limit=limit)
    else:
        data = fmp_client.get_ratios(symbol, limit=limit)
    return data


def update_ratios(symbols: List[str] = None) -> Dict[str, List[Dict]]:
    """批量更新财务比率"""
    if symbols is None:
        symbols = get_symbols()

    logger.info(f"更新 {len(symbols)} 只股票的财务比率...")

    ratios = _load_json(RATIOS_FILE)
    updated_count = 0

    for i, symbol in enumerate(symbols, 1):
        logger.info(f"[{i}/{len(symbols)}] {symbol}")
        data = fetch_ratios(symbol)
        if data:
            ratios[symbol] = data
            updated_count += 1

    ratios["_meta"] = {
        "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "count": len(ratios) - 1
    }

    _save_json(RATIOS_FILE, ratios)
    logger.info(f"财务比率更新完成: {updated_count}/{len(symbols)}")

    _sync_to_market_db("ratios", ratios)
    return ratios


def get_ratios(symbol: str) -> List[Dict]:
    """获取财务比率 (优先用缓存)"""
    ratios = _load_json(RATIOS_FILE)
    return ratios.get(symbol, [])


# ========== 收入报表 ==========

def fetch_income(symbol: str, period: str = "quarter", limit: int = 8) -> List[Dict]:
    """获取收入报表"""
    if USE_REGISTRY:
        data = registry.execute("get_income_statement", symbol=symbol, period=period, limit=limit)
    else:
        data = fmp_client.get_income_statement(symbol, period=period, limit=limit)
    return data


def update_income(symbols: List[str] = None) -> Dict[str, List[Dict]]:
    """批量更新收入报表"""
    if symbols is None:
        symbols = get_symbols()

    logger.info(f"更新 {len(symbols)} 只股票的收入报表...")

    income = _load_json(INCOME_FILE)
    updated_count = 0

    for i, symbol in enumerate(symbols, 1):
        logger.info(f"[{i}/{len(symbols)}] {symbol}")
        data = fetch_income(symbol)
        if data:
            income[symbol] = data
            updated_count += 1

    income["_meta"] = {
        "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "count": len(income) - 1
    }

    _save_json(INCOME_FILE, income)
    logger.info(f"收入报表更新完成: {updated_count}/{len(symbols)}")

    _sync_to_market_db("income", income)
    return income


def get_income(symbol: str) -> List[Dict]:
    """获取收入报表 (优先用缓存)"""
    income = _load_json(INCOME_FILE)
    return income.get(symbol, [])


# ========== 资产负债表 ==========

def fetch_balance_sheet(symbol: str, period: str = "quarter", limit: int = 8) -> List[Dict]:
    """获取资产负债表"""
    if USE_REGISTRY:
        data = registry.execute("get_balance_sheet", symbol=symbol, period=period, limit=limit)
    else:
        data = fmp_client.get_balance_sheet(symbol, period=period, limit=limit)
    return data


def update_balance_sheets(symbols: List[str] = None) -> Dict[str, List[Dict]]:
    """批量更新资产负债表"""
    if symbols is None:
        symbols = get_symbols()

    logger.info(f"更新 {len(symbols)} 只股票的资产负债表...")

    balance_sheets = _load_json(BALANCE_SHEET_FILE)
    updated_count = 0

    for i, symbol in enumerate(symbols, 1):
        logger.info(f"[{i}/{len(symbols)}] {symbol}")
        data = fetch_balance_sheet(symbol)
        if data:
            balance_sheets[symbol] = data
            updated_count += 1

    balance_sheets["_meta"] = {
        "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "count": len(balance_sheets) - 1
    }

    _save_json(BALANCE_SHEET_FILE, balance_sheets)
    logger.info(f"资产负债表更新完成: {updated_count}/{len(symbols)}")

    _sync_to_market_db("balance_sheet", balance_sheets)
    return balance_sheets


def get_balance_sheet(symbol: str) -> List[Dict]:
    """获取资产负债表 (优先用缓存)"""
    balance_sheets = _load_json(BALANCE_SHEET_FILE)
    return balance_sheets.get(symbol, [])


# ========== 现金流量表 ==========

def fetch_cash_flow(symbol: str, period: str = "quarter", limit: int = 8) -> List[Dict]:
    """获取现金流量表"""
    if USE_REGISTRY:
        data = registry.execute("get_cash_flow", symbol=symbol, period=period, limit=limit)
    else:
        data = fmp_client.get_cash_flow(symbol, period=period, limit=limit)
    return data


def update_cash_flows(symbols: List[str] = None) -> Dict[str, List[Dict]]:
    """批量更新现金流量表"""
    if symbols is None:
        symbols = get_symbols()

    logger.info(f"更新 {len(symbols)} 只股票的现金流量表...")

    cash_flows = _load_json(CASH_FLOW_FILE)
    updated_count = 0

    for i, symbol in enumerate(symbols, 1):
        logger.info(f"[{i}/{len(symbols)}] {symbol}")
        data = fetch_cash_flow(symbol)
        if data:
            cash_flows[symbol] = data
            updated_count += 1

    cash_flows["_meta"] = {
        "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "count": len(cash_flows) - 1
    }

    _save_json(CASH_FLOW_FILE, cash_flows)
    logger.info(f"现金流量表更新完成: {updated_count}/{len(symbols)}")

    _sync_to_market_db("cash_flow", cash_flows)
    return cash_flows


def get_cash_flow(symbol: str) -> List[Dict]:
    """获取现金流量表 (优先用缓存)"""
    cash_flows = _load_json(CASH_FLOW_FILE)
    return cash_flows.get(symbol, [])


# ========== 聚合接口 ==========

def update_all_fundamentals(symbols: List[str] = None):
    """更新所有基本面数据"""
    if symbols is None:
        symbols = get_symbols()

    logger.info(f"开始更新 {len(symbols)} 只股票的所有基本面数据...")

    update_profiles(symbols)
    update_ratios(symbols)
    update_income(symbols)
    update_balance_sheets(symbols)
    update_cash_flows(symbols)

    logger.info("所有基本面数据更新完成")


def ensure_fundamentals_cached(symbol: str) -> bool:
    """
    确保该股票的基本面数据已缓存。如果没有，立即从 FMP API 获取并写入缓存。
    配合 pool_manager.ensure_in_pool() 使用，分析即建库。

    Returns:
        True if data was fetched (or already cached), False on failure.
    """
    symbol = symbol.upper()
    all_ok = True

    # Profile
    if not get_profile(symbol):
        logger.info(f"[auto-cache] Fetching profile for {symbol}")
        profile = fetch_profile(symbol)
        if profile:
            profiles = _load_json(PROFILES_FILE)
            profiles[symbol] = profile
            _save_json(PROFILES_FILE, profiles)
        else:
            all_ok = False

    # Ratios
    if not get_ratios(symbol):
        logger.info(f"[auto-cache] Fetching ratios for {symbol}")
        data = fetch_ratios(symbol)
        if data:
            ratios = _load_json(RATIOS_FILE)
            ratios[symbol] = data
            _save_json(RATIOS_FILE, ratios)
            _sync_to_market_db("ratios", {symbol: data})
        else:
            all_ok = False

    # Income
    if not get_income(symbol):
        logger.info(f"[auto-cache] Fetching income for {symbol}")
        data = fetch_income(symbol)
        if data:
            income = _load_json(INCOME_FILE)
            income[symbol] = data
            _save_json(INCOME_FILE, income)
            _sync_to_market_db("income", {symbol: data})
        else:
            all_ok = False

    # Balance sheet
    if not get_balance_sheet(symbol):
        logger.info(f"[auto-cache] Fetching balance sheet for {symbol}")
        data = fetch_balance_sheet(symbol)
        if data:
            bs = _load_json(BALANCE_SHEET_FILE)
            bs[symbol] = data
            _save_json(BALANCE_SHEET_FILE, bs)
            _sync_to_market_db("balance_sheet", {symbol: data})
        else:
            all_ok = False

    # Cash flow
    if not get_cash_flow(symbol):
        logger.info(f"[auto-cache] Fetching cash flow for {symbol}")
        data = fetch_cash_flow(symbol)
        if data:
            cf = _load_json(CASH_FLOW_FILE)
            cf[symbol] = data
            _save_json(CASH_FLOW_FILE, cf)
            _sync_to_market_db("cash_flow", {symbol: data})
        else:
            all_ok = False

    if all_ok:
        logger.info(f"[auto-cache] Fundamental data cached for {symbol}")

    return all_ok


def get_fundamental_summary(symbol: str) -> Dict:
    """获取基本面数据摘要"""
    profile = get_profile(symbol) or {}
    ratios = get_ratios(symbol)
    income = get_income(symbol)

    latest_ratio = ratios[0] if ratios else {}
    latest_income = income[0] if income else {}

    return {
        "symbol": symbol,
        "companyName": profile.get("companyName"),
        "sector": profile.get("sector"),
        "industry": profile.get("industry"),
        "marketCap": profile.get("mktCap"),
        "beta": profile.get("beta"),
        "pe": latest_ratio.get("priceEarningsRatio"),
        "pb": latest_ratio.get("priceToBookRatio"),
        "roe": latest_ratio.get("returnOnEquity"),
        "grossMargin": latest_ratio.get("grossProfitMargin"),
        "netMargin": latest_ratio.get("netProfitMargin"),
        "latestRevenue": latest_income.get("revenue"),
        "latestNetIncome": latest_income.get("netIncome"),
        "latestEPS": latest_income.get("eps"),
    }


if __name__ == "__main__":
    # 测试单只股票
    print("测试 AAPL 基本面数据:")

    profile = fetch_profile("AAPL")
    if profile:
        print(f"  公司: {profile.get('companyName')}")
        print(f"  市值: ${profile.get('mktCap', 0)/1e9:.0f}B")

    ratios = fetch_ratios("AAPL")
    if ratios:
        r = ratios[0]
        print(f"  P/E: {r.get('priceEarningsRatio')}")
        print(f"  ROE: {r.get('returnOnEquity')}")

    income = fetch_income("AAPL")
    if income:
        i = income[0]
        print(f"  最新季度营收: ${i.get('revenue', 0)/1e9:.1f}B")
