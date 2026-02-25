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
from config.settings import (
    POOL_DIR, PRICE_DIR, FUNDAMENTAL_DIR, MARKET_CAP_THRESHOLD,
    EXCLUDED_SECTORS, EXCLUDED_INDUSTRIES, PERMANENTLY_EXCLUDED,
    BENCHMARK_SYMBOLS,
)
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


def _apply_filters(stocks: List[Dict]) -> List[Dict]:
    """
    应用行业/个股过滤规则，排除不符合条件的股票。
    使用 config/settings.py 中的 EXCLUDED_SECTORS, EXCLUDED_INDUSTRIES, PERMANENTLY_EXCLUDED。
    """
    filtered = []
    excluded_count = 0
    for s in stocks:
        symbol = s.get("symbol", "")
        sector = s.get("sector", "")
        industry = s.get("industry", "")

        if symbol in PERMANENTLY_EXCLUDED:
            excluded_count += 1
            continue
        if sector in EXCLUDED_SECTORS:
            excluded_count += 1
            continue
        if industry in EXCLUDED_INDUSTRIES:
            excluded_count += 1
            continue
        filtered.append(s)

    if excluded_count:
        logger.info(f"过滤规则排除 {excluded_count} 只股票")
    return filtered


def _get_non_screener_stocks(stocks: List[Dict]) -> List[Dict]:
    """保留非 screener 来源的股票，这些不会被池刷新删除。

    所有带 source 标记（且非 screener）的股票永久保留，包括：
    - analysis: 深度分析纳入
    - attention: 周报/注意力引擎纳入
    - manual: 用户手动加入（如 ETF 成分股）
    - 未来新增的任何非 screener 来源
    """
    return [s for s in stocks if s.get("source") and s.get("source") != "screener"]


# backward compat alias
_get_analysis_stocks = _get_non_screener_stocks


def refresh_universe() -> Tuple[List[Dict], List[str], List[str]]:
    """
    刷新股票池
    - 应用行业/个股过滤规则
    - 保留通过分析加入的股票 (source=analysis)
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
    logger.info(f"去重后 {len(new_stocks)} 只股票")

    # 应用过滤规则
    new_stocks = _apply_filters(new_stocks)
    logger.info(f"过滤后 {len(new_stocks)} 只股票")

    new_stocks = sorted(new_stocks, key=lambda x: x.get("marketCap", 0), reverse=True)

    # 保留非 screener 来源的股票 (analysis + attention)
    old_stocks = load_universe()
    non_screener_stocks = _get_non_screener_stocks(old_stocks)
    screener_symbols = {s.get("symbol") for s in new_stocks}
    preserved = []
    for s in non_screener_stocks:
        if s.get("symbol") not in screener_symbols:
            preserved.append(s)
    if preserved:
        symbols = [s.get("symbol") for s in preserved]
        logger.info(f"保留 {len(preserved)} 只非 screener 来源股票: {symbols}")
        new_stocks.extend(preserved)

    # 对比变化
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

    # 同步 company.db 的 in_pool 列
    new_symbol_list = [s.get("symbol") for s in new_stocks if s.get("symbol")]
    try:
        from terminal.company_store import get_store
        store = get_store()
        synced = store.sync_pool(new_symbol_list)
        logger.info("DB pool synced: %d companies", synced)
    except Exception as e:
        logger.warning("DB pool sync failed (non-fatal): %s", e)

    # 清理已退出股票的残留数据
    if exited:
        cleanup_stale_data(new_symbol_list)

    return new_stocks, list(entered), list(exited)


def cleanup_stale_data(active_symbols: List[str] = None) -> Dict[str, int]:
    """
    清理不在当前池 + benchmark 中的过期数据：
    - 删除多余的价格 CSV
    - 清理基本面 JSON 中已退出股票的条目

    安全机制:
    - 删除比例超过 30% 时自动熔断
    - 删除前自动创建数据快照

    Args:
        active_symbols: 当前活跃股票列表。如果为 None，从 universe.json 读取。

    Returns:
        {"csv_deleted": N, "fundamental_cleaned": N} 统计结果
        熔断时额外返回 "aborted": True
    """
    if active_symbols is None:
        active_symbols = get_symbols()

    # 合法的 symbol 集合 = 股票池 + benchmark
    valid_symbols = set(active_symbols) | set(BENCHMARK_SYMBOLS)

    stats = {"csv_deleted": 0, "fundamental_cleaned": 0}

    # === 安全阈值检查 (熔断机制) ===
    SAFETY_THRESHOLD = 0.3  # 30%

    # 检查 CSV 删除比例
    existing_csvs = list(PRICE_DIR.glob("*.csv")) if PRICE_DIR.exists() else []
    csv_to_delete = [f for f in existing_csvs if f.stem not in valid_symbols]

    if existing_csvs and len(csv_to_delete) / len(existing_csvs) > SAFETY_THRESHOLD:
        logger.error(
            f"安全熔断: 将删除 {len(csv_to_delete)}/{len(existing_csvs)} 个 CSV "
            f"({len(csv_to_delete)/len(existing_csvs):.0%})，超过 {SAFETY_THRESHOLD:.0%} 阈值，中止操作"
        )
        return {"csv_deleted": 0, "fundamental_cleaned": 0, "aborted": True}

    # 检查基本面删除比例
    total_fundamental_keys = 0
    stale_fundamental_keys = 0
    if FUNDAMENTAL_DIR.exists():
        pool_symbols_check = set(active_symbols)
        for json_file in FUNDAMENTAL_DIR.glob("*.json"):
            try:
                with open(json_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if isinstance(data, dict):
                    non_meta_keys = [k for k in data if k != "_meta"]
                    total_fundamental_keys += len(non_meta_keys)
                    stale_fundamental_keys += len([k for k in non_meta_keys if k not in pool_symbols_check])
            except (json.JSONDecodeError, IOError):
                continue

    if total_fundamental_keys > 0 and stale_fundamental_keys / total_fundamental_keys > SAFETY_THRESHOLD:
        logger.error(
            f"安全熔断: 将清理 {stale_fundamental_keys}/{total_fundamental_keys} 条基本面条目 "
            f"({stale_fundamental_keys/total_fundamental_keys:.0%})，超过 {SAFETY_THRESHOLD:.0%} 阈值，中止操作"
        )
        return {"csv_deleted": 0, "fundamental_cleaned": 0, "aborted": True}

    # === 删除前自动快照 ===
    if csv_to_delete or stale_fundamental_keys > 0:
        try:
            from src.data.data_guardian import snapshot
            snapshot(reason="pre-cleanup")
        except Exception as e:
            logger.warning(f"删除前快照失败 (继续清理): {e}")

    # 1. 清理过期价格 CSV
    for csv_file in csv_to_delete:
        csv_file.unlink()
        stats["csv_deleted"] += 1
        logger.info(f"删除过期价格 CSV: {csv_file.stem}")

    # 2. 清理基本面 JSON 中的过期条目
    if FUNDAMENTAL_DIR.exists():
        pool_symbols = set(active_symbols)  # 基本面只保留池内股票，不含 benchmark
        for json_file in FUNDAMENTAL_DIR.glob("*.json"):
            try:
                with open(json_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
            except (json.JSONDecodeError, IOError):
                continue

            if not isinstance(data, dict):
                continue

            stale_keys = [k for k in data if k not in pool_symbols and k != "_meta"]
            if stale_keys:
                for k in stale_keys:
                    del data[k]
                with open(json_file, "w", encoding="utf-8") as f:
                    json.dump(data, f, ensure_ascii=False, indent=2)
                stats["fundamental_cleaned"] += len(stale_keys)
                logger.info(f"清理 {json_file.name}: 移除 {len(stale_keys)} 条过期条目")

    logger.info(f"数据清理完成: 删除 {stats['csv_deleted']} 个 CSV, "
                f"清理 {stats['fundamental_cleaned']} 条基本面条目")
    return stats


def sync_db_pool() -> int:
    """Sync universe.json -> company.db in_pool column.

    Standalone function for scripts/cron to call without refresh_universe().
    """
    symbols = get_symbols()
    if not symbols:
        return 0
    from terminal.company_store import get_store
    return get_store().sync_pool(symbols)


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


def batch_add_to_pool(symbols: List[str], source: str = "manual", reason: str = "") -> Dict:
    """
    批量加入股票到池中（通过 FMP API 获取 profile）。

    Args:
        symbols: 股票代码列表
        source: 来源标记（manual/analysis/attention 等，非 screener 均永久保留）
        reason: 加入原因（记入历史）

    Returns:
        {"added": [...], "skipped": [...], "failed": [...]}
    """
    import time
    from config.settings import API_CALL_INTERVAL

    symbols = [s.upper().strip() for s in symbols]
    stocks = load_universe()
    existing = {s.get("symbol") for s in stocks}

    result = {"added": [], "skipped": [], "failed": []}

    for sym in symbols:
        if sym in existing:
            result["skipped"].append(sym)
            continue

        profile = fmp_client.get_profile(sym)
        if not profile:
            logger.warning(f"FMP API 未返回 '{sym}' 的 profile，跳过")
            result["failed"].append(sym)
            time.sleep(API_CALL_INTERVAL)
            continue

        entry = {
            "symbol": sym,
            "companyName": profile.get("companyName", ""),
            "marketCap": profile.get("mktCap"),
            "sector": profile.get("sector", ""),
            "industry": profile.get("industry", ""),
            "exchange": profile.get("exchangeShortName", profile.get("exchange", "")),
            "country": profile.get("country", ""),
            "source": source,
            "added_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }
        stocks.append(entry)
        existing.add(sym)
        result["added"].append(sym)
        logger.info(f"'{sym}' ({entry['companyName']}) 已加入 (source: {source})")
        time.sleep(API_CALL_INTERVAL)

    if result["added"]:
        save_universe(stocks)

        history = load_history()
        history.append({
            "date": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "entered": result["added"],
            "exited": [],
            "total_count": len(stocks),
            "reason": reason or f"batch-add ({source})",
        })
        save_history(history)

    logger.info(
        f"批量加入完成: added={len(result['added'])}, "
        f"skipped={len(result['skipped'])}, failed={len(result['failed'])}"
    )
    return result


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
