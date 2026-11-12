"""
数据查询模块
- get_stock_data(symbol) - 返回一只股票的完整数据
- get_portfolio_overview() - 返回整个股票池的概览
- search_stocks(query) - 按名称/代码/行业搜索
"""
import logging
from typing import Optional, List, Dict, Any

import sys
sys.path.insert(0, str(__file__).rsplit("/src", 1)[0])

from src.data.pool_manager import load_universe, get_stock_info
from src.data.price_fetcher import get_price_df, load_price_cache, get_cache_latest_date
from src.data.fundamental_fetcher import (
    get_profile,
    get_ratios,
    get_income,
    get_fundamental_summary,
)

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)


def get_stock_data(symbol: str, price_days: int = 30) -> Dict[str, Any]:
    """
    获取一只股票的完整数据

    返回:
    {
        "symbol": "AAPL",
        "info": {...},          # 股票池基本信息
        "profile": {...},       # 公司概况
        "fundamentals": {...},  # 基本面摘要
        "ratios": [...],        # 财务比率 (最近几期)
        "income": [...],        # 收入报表 (最近几期)
        "price": {
            "latest_date": "2024-01-15",
            "latest_close": 185.92,
            "records": 30,
            "data": [...]       # 最近 N 天的量价数据
        }
    }
    """
    result = {
        "symbol": symbol,
        "info": None,
        "profile": None,
        "fundamentals": None,
        "ratios": [],
        "income": [],
        "price": None,
    }

    # 1. 股票池基本信息
    info = get_stock_info(symbol)
    if info:
        result["info"] = {
            "symbol": info.get("symbol"),
            "companyName": info.get("companyName"),
            "marketCap": info.get("marketCap"),
            "sector": info.get("sector"),
            "industry": info.get("industry"),
            "exchange": info.get("exchange"),
        }

    # 2. 公司概况
    profile = get_profile(symbol)
    if profile:
        result["profile"] = {
            "companyName": profile.get("companyName"),
            "description": profile.get("description"),
            "sector": profile.get("sector"),
            "industry": profile.get("industry"),
            "marketCap": profile.get("marketCap") or profile.get("mktCap"),
            "beta": profile.get("beta"),
            "price": profile.get("price"),
            "website": profile.get("website"),
            "ceo": profile.get("ceo"),
            "fullTimeEmployees": profile.get("fullTimeEmployees"),
            "ipoDate": profile.get("ipoDate"),
        }

    # 3. 基本面摘要
    result["fundamentals"] = get_fundamental_summary(symbol)

    # 4. 财务比率
    result["ratios"] = get_ratios(symbol)

    # 5. 收入报表
    result["income"] = get_income(symbol)

    # 6. 量价数据
    price_df = get_price_df(symbol, days=price_days)
    if price_df is not None and not price_df.empty:
        result["price"] = {
            "latest_date": str(price_df["date"].iloc[0].date()) if hasattr(price_df["date"].iloc[0], "date") else str(price_df["date"].iloc[0]),
            "latest_close": float(price_df["close"].iloc[0]),
            "records": len(price_df),
            "data": price_df.to_dict("records")
        }

    return result


def get_portfolio_overview() -> Dict[str, Any]:
    """
    获取整个股票池的概览

    返回:
    {
        "total_count": 150,
        "total_market_cap": 30000000000000,
        "sector_distribution": {
            "Technology": {"count": 30, "market_cap": ...},
            ...
        },
        "top_10": [...],
        "stocks": [
            {"symbol": "AAPL", "companyName": ..., "marketCap": ..., "sector": ...},
            ...
        ]
    }
    """
    stocks = load_universe()

    if not stocks:
        return {
            "total_count": 0,
            "total_market_cap": 0,
            "sector_distribution": {},
            "top_10": [],
            "stocks": []
        }

    # 计算总市值
    total_market_cap = sum(s.get("marketCap", 0) or 0 for s in stocks)

    # 按行业分组统计
    sector_dist = {}
    for s in stocks:
        sector = s.get("sector") or "Unknown"
        if sector not in sector_dist:
            sector_dist[sector] = {"count": 0, "market_cap": 0}
        sector_dist[sector]["count"] += 1
        sector_dist[sector]["market_cap"] += s.get("marketCap", 0) or 0

    # 按市值排序
    sorted_stocks = sorted(stocks, key=lambda x: x.get("marketCap", 0) or 0, reverse=True)

    # 简化股票列表
    simplified_stocks = [
        {
            "symbol": s.get("symbol"),
            "companyName": s.get("companyName"),
            "marketCap": s.get("marketCap"),
            "sector": s.get("sector"),
            "industry": s.get("industry"),
        }
        for s in sorted_stocks
    ]

    return {
        "total_count": len(stocks),
        "total_market_cap": total_market_cap,
        "sector_distribution": sector_dist,
        "top_10": simplified_stocks[:10],
        "stocks": simplified_stocks
    }


def search_stocks(
    query: str,
    field: str = "all",
    limit: int = 20
) -> List[Dict]:
    """
    搜索股票

    Args:
        query: 搜索关键词 (不区分大小写)
        field: 搜索字段 ("all", "symbol", "name", "sector", "industry")
        limit: 返回数量限制

    Returns:
        匹配的股票列表
    """
    stocks = load_universe()
    query_lower = query.lower()

    results = []
    for s in stocks:
        symbol = (s.get("symbol") or "").lower()
        name = (s.get("companyName") or "").lower()
        sector = (s.get("sector") or "").lower()
        industry = (s.get("industry") or "").lower()

        matched = False

        if field == "all":
            matched = (
                query_lower in symbol or
                query_lower in name or
                query_lower in sector or
                query_lower in industry
            )
        elif field == "symbol":
            matched = query_lower in symbol
        elif field == "name":
            matched = query_lower in name
        elif field == "sector":
            matched = query_lower in sector
        elif field == "industry":
            matched = query_lower in industry

        if matched:
            results.append({
                "symbol": s.get("symbol"),
                "companyName": s.get("companyName"),
                "marketCap": s.get("marketCap"),
                "sector": s.get("sector"),
                "industry": s.get("industry"),
            })

            if len(results) >= limit:
                break

    # 按市值排序
    results.sort(key=lambda x: x.get("marketCap", 0) or 0, reverse=True)

    return results


def get_stocks_by_sector(sector: str) -> List[Dict]:
    """获取指定行业的所有股票"""
    return search_stocks(sector, field="sector", limit=1000)


def get_stocks_by_industry(industry: str) -> List[Dict]:
    """获取指定细分行业的所有股票"""
    return search_stocks(industry, field="industry", limit=1000)


if __name__ == "__main__":
    # 测试单只股票查询
    print("=" * 60)
    print("测试 get_stock_data('AAPL'):")
    print("=" * 60)
    data = get_stock_data("AAPL", price_days=5)
    print(f"Symbol: {data['symbol']}")
    if data["info"]:
        print(f"Info: {data['info']['companyName']} - ${data['info'].get('marketCap', 0)/1e9:.0f}B")
    if data["fundamentals"]:
        print(f"P/E: {data['fundamentals'].get('pe')}")
        print(f"ROE: {data['fundamentals'].get('roe')}")
    if data["price"]:
        print(f"Latest Price: ${data['price']['latest_close']:.2f} ({data['price']['latest_date']})")

    # 测试股票池概览
    print("\n" + "=" * 60)
    print("测试 get_portfolio_overview():")
    print("=" * 60)
    overview = get_portfolio_overview()
    print(f"Total: {overview['total_count']} stocks")
    print(f"Total Market Cap: ${overview['total_market_cap']/1e12:.1f}T")
    print("\nSector Distribution:")
    for sector, info in sorted(overview["sector_distribution"].items(), key=lambda x: -x[1]["count"]):
        print(f"  {sector}: {info['count']} stocks (${info['market_cap']/1e12:.1f}T)")

    # 测试搜索
    print("\n" + "=" * 60)
    print("测试 search_stocks('tech'):")
    print("=" * 60)
    results = search_stocks("tech", limit=5)
    for r in results:
        print(f"  {r['symbol']}: {r['companyName']} ({r['sector']})")
