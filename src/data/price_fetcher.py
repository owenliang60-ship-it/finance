"""
量价数据获取与缓存
遵循 quant-development skill 规范：
- CSV 格式存储
- 增量更新
- 数据验证
"""
import pandas as pd
import logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional, List

import sys
sys.path.insert(0, str(__file__).rsplit("/src", 1)[0])
from config.settings import PRICE_DIR, PRICE_HISTORY_YEARS
from src.data.fmp_client import fmp_client
from src.data.pool_manager import get_symbols

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# 标准列名
PRICE_COLUMNS = ["date", "open", "high", "low", "close", "volume", "change", "changePercent"]


def _get_cache_path(symbol: str) -> Path:
    """获取缓存文件路径"""
    return PRICE_DIR / f"{symbol}.csv"


def load_price_cache(symbol: str) -> Optional[pd.DataFrame]:
    """加载本地缓存的量价数据"""
    cache_path = _get_cache_path(symbol)
    if not cache_path.exists():
        return None

    try:
        df = pd.read_csv(cache_path, parse_dates=["date"])
        df = df.sort_values("date", ascending=False).reset_index(drop=True)
        return df
    except Exception as e:
        logger.error(f"加载缓存失败 {symbol}: {e}")
        return None


def save_price_cache(symbol: str, df: pd.DataFrame):
    """保存量价数据到缓存"""
    PRICE_DIR.mkdir(parents=True, exist_ok=True)
    cache_path = _get_cache_path(symbol)

    # 确保列顺序和格式
    df = df.sort_values("date", ascending=False).reset_index(drop=True)
    df.to_csv(cache_path, index=False)
    logger.debug(f"保存缓存 {symbol}: {len(df)} 条")

    # Dual-write to market.db (non-fatal)
    try:
        from src.data.market_store import get_store
        get_store().upsert_daily_prices_df(symbol, df)
    except Exception as e:
        logger.warning(f"[market.db] price dual-write failed for {symbol}: {e}")


def get_cache_latest_date(symbol: str) -> Optional[datetime]:
    """获取缓存中最新的日期"""
    df = load_price_cache(symbol)
    if df is None or df.empty:
        return None
    return pd.to_datetime(df["date"].iloc[0])


def fetch_and_update_price(symbol: str, force_full: bool = False) -> Optional[pd.DataFrame]:
    """
    获取并更新量价数据
    - 如果本地有缓存，增量更新
    - 如果没有缓存或 force_full=True，全量获取
    """
    cache_df = load_price_cache(symbol)

    if cache_df is not None and not force_full:
        # 增量更新：只获取缓存之后的数据
        latest_date = get_cache_latest_date(symbol)
        logger.info(f"{symbol}: 缓存最新日期 {latest_date.strftime('%Y-%m-%d') if latest_date else 'None'}")
    else:
        latest_date = None

    # 从 API 获取数据
    raw_data = fmp_client.get_historical_price(symbol)
    if not raw_data:
        logger.warning(f"{symbol}: API 返回空数据")
        return cache_df

    # 转换为 DataFrame
    new_df = pd.DataFrame(raw_data)
    new_df["date"] = pd.to_datetime(new_df["date"])

    # 选择需要的列
    available_cols = [c for c in PRICE_COLUMNS if c in new_df.columns]
    new_df = new_df[available_cols]

    if cache_df is not None and not force_full:
        # 合并：只添加新数据
        new_df = new_df[new_df["date"] > latest_date]
        if not new_df.empty:
            combined_df = pd.concat([new_df, cache_df], ignore_index=True)
            combined_df = combined_df.drop_duplicates(subset=["date"]).sort_values("date", ascending=False)
            logger.info(f"{symbol}: 新增 {len(new_df)} 条数据")
        else:
            combined_df = cache_df
            logger.info(f"{symbol}: 无新数据")
    else:
        combined_df = new_df
        logger.info(f"{symbol}: 全量获取 {len(combined_df)} 条数据")

    # 保存
    save_price_cache(symbol, combined_df)
    return combined_df


def update_all_prices(symbols: List[str] = None, force_full: bool = False) -> dict:
    """
    批量更新所有股票的量价数据
    返回: {"success": [...], "failed": [...]}
    """
    if symbols is None:
        symbols = get_symbols()

    # Always include benchmark symbols
    from config.settings import BENCHMARK_SYMBOLS
    symbols = list(set(symbols + BENCHMARK_SYMBOLS))
    logger.info(f"Including {len(BENCHMARK_SYMBOLS)} benchmark symbols: {BENCHMARK_SYMBOLS}")

    if not symbols:
        logger.warning("股票池为空")
        return {"success": [], "failed": []}

    logger.info(f"开始更新 {len(symbols)} 只股票的量价数据...")

    success = []
    failed = []

    for i, symbol in enumerate(symbols, 1):
        logger.info(f"[{i}/{len(symbols)}] 更新 {symbol}")
        try:
            df = fetch_and_update_price(symbol, force_full=force_full)
            if df is not None and not df.empty:
                success.append(symbol)
            else:
                failed.append(symbol)
        except Exception as e:
            logger.error(f"{symbol} 更新失败: {e}")
            failed.append(symbol)

    logger.info(f"更新完成: 成功 {len(success)}, 失败 {len(failed)}")
    if failed:
        logger.warning(f"失败列表: {failed}")

    return {"success": success, "failed": failed}


def get_price_df(symbol: str, days: int = None, max_age_days: int = 3) -> Optional[pd.DataFrame]:
    """
    获取量价数据 (优先用缓存，自动检查新鲜度)

    Args:
        days: 返回最近 N 天的数据，None 返回全部
        max_age_days: 缓存最大年龄(天)，超过则自动增量更新。
                      默认 3 天覆盖周末。设为 0 或 None 跳过检查。
    """
    df = load_price_cache(symbol)
    if df is not None and max_age_days:
        latest = pd.to_datetime(df["date"].iloc[0])
        age = (datetime.now() - latest).days
        if age > max_age_days:
            logger.info(f"{symbol}: 缓存过期 ({age}天 > {max_age_days}天限制), 自动刷新")
            df = fetch_and_update_price(symbol)
    elif df is None:
        df = fetch_and_update_price(symbol)

    if df is None or df.empty:
        return None

    if days:
        return df.head(days)
    return df


def validate_price_data(symbol: str) -> dict:
    """验证量价数据质量"""
    df = load_price_cache(symbol)
    if df is None:
        return {"valid": False, "error": "No cache"}

    issues = []

    # 检查基本列是否存在
    required = ["date", "open", "high", "low", "close", "volume"]
    missing = [c for c in required if c not in df.columns]
    if missing:
        issues.append(f"Missing columns: {missing}")

    # 检查空值
    null_counts = df[required].isnull().sum()
    if null_counts.any():
        issues.append(f"Null values: {null_counts[null_counts > 0].to_dict()}")

    # 检查数据量
    if len(df) < 100:
        issues.append(f"Too few records: {len(df)}")

    # 检查最新日期
    latest = df["date"].iloc[0]
    days_old = (datetime.now() - pd.to_datetime(latest)).days
    if days_old > 5:  # 超过5天未更新（考虑周末）
        issues.append(f"Data is {days_old} days old")

    return {
        "valid": len(issues) == 0,
        "record_count": len(df),
        "latest_date": str(latest.date()) if pd.notna(latest) else None,
        "issues": issues
    }


if __name__ == "__main__":
    # 测试单只股票
    print("测试 AAPL 量价数据获取:")
    df = fetch_and_update_price("AAPL")
    if df is not None:
        print(f"  获取 {len(df)} 条数据")
        print(f"  最新: {df['date'].iloc[0]} - ${df['close'].iloc[0]:.2f}")
        print(f"  最早: {df['date'].iloc[-1]}")

    print("\n数据验证:")
    result = validate_price_data("AAPL")
    print(f"  {result}")
