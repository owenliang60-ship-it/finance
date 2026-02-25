"""
FMP API 客户端
遵循 quant-development skill 规范：
- 串行调用，间隔防限流
- 错误重试
- 统一日志
"""
import requests
import time
import logging
from typing import Optional, Dict, Any, List

import sys
sys.path.insert(0, str(__file__).rsplit("/src", 1)[0])
from config.settings import FMP_API_KEY, FMP_BASE_URL, API_CALL_INTERVAL, API_RETRY_TIMES, API_TIMEOUT

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)


class FMPClient:
    """FMP API 客户端"""

    def __init__(self, api_key: str = FMP_API_KEY):
        self.api_key = api_key
        self.base_url = FMP_BASE_URL
        self._last_call_time = 0

    def _rate_limit(self):
        """API 限流控制"""
        elapsed = time.time() - self._last_call_time
        if elapsed < API_CALL_INTERVAL:
            time.sleep(API_CALL_INTERVAL - elapsed)
        self._last_call_time = time.time()

    def _request(self, endpoint: str, params: Optional[Dict] = None) -> Any:
        """发送 API 请求，带重试"""
        self._rate_limit()

        url = f"{self.base_url}/{endpoint}"
        params = params or {}
        params["apikey"] = self.api_key

        for attempt in range(API_RETRY_TIMES):
            try:
                resp = requests.get(url, params=params, timeout=API_TIMEOUT)

                if resp.status_code == 200:
                    return resp.json()
                elif resp.status_code == 429:
                    # Rate limited, wait and retry
                    wait_time = (attempt + 1) * 5
                    logger.warning(f"Rate limited, waiting {wait_time}s...")
                    time.sleep(wait_time)
                else:
                    logger.error(f"API error {resp.status_code}: {resp.text[:200]}")
                    return None

            except requests.exceptions.Timeout:
                logger.warning(f"Timeout on attempt {attempt + 1}/{API_RETRY_TIMES}")
            except requests.exceptions.RequestException as e:
                logger.error(f"Request error: {e}")

        logger.error(f"Failed after {API_RETRY_TIMES} attempts: {endpoint}")
        return None

    # ========== 股票池相关 ==========

    def get_large_cap_stocks(self, market_cap_threshold: int) -> List[Dict]:
        """获取大市值股票列表"""
        params = {
            "marketCapMoreThan": market_cap_threshold,
            "exchange": "NYSE,NASDAQ",
            "isActivelyTrading": "true",
        }
        data = self._request("company-screener", params)

        if not data:
            return []

        # 过滤 ETF 和基金
        stocks = [s for s in data if not s.get("isEtf") and not s.get("isFund")]
        return stocks

    # ========== 量价数据相关 ==========

    def get_screener_page(self, offset: int = 0, limit: int = 1000,
                          volume_more_than: int = None) -> List[Dict]:
        """分页获取全市场股票快照（含 price, volume）"""
        params = {
            "limit": limit,
            "offset": offset,
            "exchange": "NYSE,NASDAQ",
            "isActivelyTrading": "true",
        }
        if volume_more_than:
            params["volumeMoreThan"] = volume_more_than
        data = self._request("company-screener", params)
        if not data or not isinstance(data, list):
            return []
        return [s for s in data if not s.get("isEtf") and not s.get("isFund")]

    def get_historical_price(self, symbol: str, years: int = 5) -> List[Dict]:
        """获取历史日线数据"""
        data = self._request("historical-price-eod/full", {"symbol": symbol})

        if not data:
            return []

        # 数据可能直接是列表，也可能在 historical 字段
        if isinstance(data, list):
            return data
        elif isinstance(data, dict):
            return data.get("historical", [])
        return []

    def get_historical_price_range(self, symbol: str, from_date: str, to_date: str) -> List[Dict]:
        """获取指定日期范围的历史日线数据 (轻量级, 用于全量扫描)

        Args:
            symbol: 股票代码
            from_date: 开始日期 (YYYY-MM-DD)
            to_date: 结束日期 (YYYY-MM-DD)

        Returns:
            日线数据列表 (最新在前)
        """
        data = self._request("historical-price-eod/full", {
            "symbol": symbol,
            "from": from_date,
            "to": to_date,
        })

        if not data:
            return []

        if isinstance(data, list):
            return data
        elif isinstance(data, dict):
            return data.get("historical", [])
        return []

    def get_quote(self, symbol: str) -> Optional[Dict]:
        """获取实时报价"""
        data = self._request("quote", {"symbol": symbol})
        if data and isinstance(data, list) and len(data) > 0:
            return data[0]
        return None

    def get_realtime_price(self, symbol: str) -> Optional[float]:
        """获取实时价格 (用于 sanity check，单次 API 调用)"""
        data = self._request("profile", {"symbol": symbol})
        if data and isinstance(data, list) and len(data) > 0:
            return data[0].get("price")
        return None

    # ========== 基本面数据相关 ==========

    def get_profile(self, symbol: str) -> Optional[Dict]:
        """获取公司概况"""
        data = self._request("profile", {"symbol": symbol})
        if data and isinstance(data, list) and len(data) > 0:
            return data[0]
        return None

    def get_ratios(self, symbol: str, limit: int = 4) -> List[Dict]:
        """获取财务比率 (最近几个季度)"""
        data = self._request("ratios", {"symbol": symbol, "limit": limit})
        return data if isinstance(data, list) else []

    def get_income_statement(self, symbol: str, period: str = "quarter", limit: int = 8) -> List[Dict]:
        """获取收入报表"""
        data = self._request("income-statement", {
            "symbol": symbol,
            "period": period,
            "limit": limit
        })
        return data if isinstance(data, list) else []

    def get_key_metrics(self, symbol: str, limit: int = 4) -> List[Dict]:
        """获取关键指标"""
        data = self._request("key-metrics", {"symbol": symbol, "limit": limit})
        return data if isinstance(data, list) else []

    def get_balance_sheet(self, symbol: str, period: str = "quarter", limit: int = 8) -> List[Dict]:
        """获取资产负债表"""
        data = self._request("balance-sheet-statement", {
            "symbol": symbol,
            "period": period,
            "limit": limit
        })
        return data if isinstance(data, list) else []

    def get_cash_flow(self, symbol: str, period: str = "quarter", limit: int = 8) -> List[Dict]:
        """获取现金流量表"""
        data = self._request("cash-flow-statement", {
            "symbol": symbol,
            "period": period,
            "limit": limit
        })
        return data if isinstance(data, list) else []

    def get_earnings_calendar(self, from_date: str = None, to_date: str = None) -> List[Dict]:
        """
        获取财报日程

        Args:
            from_date: 开始日期 (YYYY-MM-DD)
            to_date: 结束日期 (YYYY-MM-DD)
        """
        params = {}
        if from_date:
            params["from"] = from_date
        if to_date:
            params["to"] = to_date
        data = self._request("earning_calendar", params)
        return data if isinstance(data, list) else []

    def get_analyst_estimates(self, symbol: str, period: str = "quarter", limit: int = 4) -> List[Dict]:
        """
        获取分析师盈利预期

        Args:
            symbol: 股票代码
            period: quarter 或 annual
            limit: 返回数量
        """
        data = self._request("analyst-estimates", {
            "symbol": symbol,
            "period": period,
            "limit": limit
        })
        return data if isinstance(data, list) else []

    def get_insider_trades(self, symbol: str, limit: int = 50) -> List[Dict]:
        """
        获取内部人交易记录

        Args:
            symbol: 股票代码
            limit: 返回数量
        """
        data = self._request("insider-trading", {
            "symbol": symbol,
            "limit": limit
        })
        return data if isinstance(data, list) else []

    def get_stock_news(self, tickers: str = None, limit: int = 50) -> List[Dict]:
        """
        获取股票新闻

        Args:
            tickers: 股票代码（多个用逗号分隔，如 "AAPL,MSFT"）
            limit: 返回数量
        """
        params = {"limit": limit}
        if tickers:
            params["tickers"] = tickers
        data = self._request("stock_news", params)
        return data if isinstance(data, list) else []

    def get_analyst_recommendations(self, symbol: str, limit: int = 200) -> List[Dict]:
        """
        获取分析师评级历史 (grades endpoint)

        每条记录含 gradingCompany, newGrade, action 等字段。
        调用方可聚合为 Buy/Hold/Sell 分布。

        Args:
            symbol: 股票代码
            limit: 返回数量（默认 200 条，覆盖近期评级）

        Returns:
            评级记录列表（最新在前）
        """
        data = self._request("grades", {"symbol": symbol, "limit": limit})
        return data if isinstance(data, list) else []


# 单例
fmp_client = FMPClient()


if __name__ == "__main__":
    # 简单测试
    client = FMPClient()

    print("测试 get_profile:")
    profile = client.get_profile("AAPL")
    if profile:
        print(f"  {profile.get('companyName')}: ${profile.get('mktCap', 0)/1e9:.0f}B")

    print("\n测试 get_historical_price:")
    prices = client.get_historical_price("AAPL")
    if prices:
        print(f"  获取到 {len(prices)} 条日线数据")
        print(f"  最新: {prices[0].get('date')} - ${prices[0].get('close')}")
