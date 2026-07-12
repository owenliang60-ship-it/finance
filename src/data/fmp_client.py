"""
FMP API 客户端
遵循 quant-development skill 规范：
- 串行调用，间隔防限流
- 错误重试
- 统一日志
"""
import re
import requests
import time
import logging
from typing import Optional, Dict, Any, List

import sys
sys.path.insert(0, str(__file__).rsplit("/src", 1)[0])
from config.settings import FMP_API_KEY, FMP_BASE_URL, API_CALL_INTERVAL, API_RETRY_TIMES, API_TIMEOUT

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)


SCREENER_DEFAULT_LIMIT = 5000  # FMP screener page cap; ~2.8x $10B+ 全集 (1797 as of 2026-05-21)

# query-string / dict-repr 两种形态的 apikey 值都掩码
_APIKEY_RE = re.compile(r"(apikey(?:=|['\"]?\s*:\s*['\"]?))[^&\s,'\"}]+", re.IGNORECASE)


def _sanitize_log_text(value: object, api_key: str = "") -> str:
    """日志脱敏：正则掩码 apikey= 形态 + 字面 key 替换双层防线。"""
    text = _APIKEY_RE.sub(r"\1***", str(value))
    return text.replace(api_key, "***") if api_key else text


class FMPClient:
    """FMP API 客户端"""

    def __init__(self, api_key: str = FMP_API_KEY,
                 call_interval: Optional[float] = None):
        self.api_key = api_key
        self.base_url = FMP_BASE_URL
        # None → 全局默认（现有调用方行为不变）；forward CLI 显式传入独立间隔
        self.call_interval = API_CALL_INTERVAL if call_interval is None else call_interval
        self._last_call_time = 0

    def _safe(self, value: object) -> str:
        return _sanitize_log_text(value, self.api_key)

    def _rate_limit(self):
        """API 限流控制"""
        elapsed = time.time() - self._last_call_time
        if elapsed < self.call_interval:
            time.sleep(self.call_interval - elapsed)
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
                    logger.error(f"API error {resp.status_code}: {self._safe(resp.text[:200])}")
                    return None

            except requests.exceptions.Timeout:
                logger.warning(f"Timeout on attempt {attempt + 1}/{API_RETRY_TIMES}")
            except requests.exceptions.RequestException as e:
                logger.error(f"Request error: {self._safe(e)}")

        logger.error(f"Failed after {API_RETRY_TIMES} attempts: {self._safe(endpoint)}")
        return None

    # ========== 股票池相关 ==========

    def get_large_cap_stocks(
        self,
        market_cap_threshold: int,
        limit: int = SCREENER_DEFAULT_LIMIT,
    ) -> List[Dict]:
        """获取大市值股票列表

        Args:
            market_cap_threshold: 最小市值（美元）
            limit: FMP screener page size. Default 5000 覆盖当前 $10B+ 全 US universe.
                FMP 默认 limit=1000，必须显式传 limit 否则按 marketCap 降序截断。
                若未来 $10B+ 全集 > 5000，调高此 anchor 并审计 sentinel 日志。

        Returns:
            过滤掉 ETF/Fund 后的股票列表
        """
        params = {
            "marketCapMoreThan": market_cap_threshold,
            "exchange": "NYSE,NASDAQ",
            "isActivelyTrading": "true",
            "limit": limit,
        }
        data = self._request("company-screener", params)

        if not data:
            return []

        # Sentinel layer 1: 返回行数精确等于 limit = 大概率被 page 截断
        if isinstance(data, list) and len(data) == limit:
            logger.warning(
                "FMP screener returned exactly limit=%d rows for marketCapMoreThan=%d; "
                "possible truncation, increase limit or switch to get_screener_page().",
                limit, market_cap_threshold,
            )
        # Sentinel layer 2: 服务端忽略 limit 偷偷 cap 到 1000 (FMP 历史默认)
        # 触发条件: 显式传 limit>1000，但实际只回 1000 行 — 大概率是 server-side cap
        elif isinstance(data, list) and limit > 1000 and len(data) == 1000:
            logger.warning(
                "FMP screener returned exactly 1000 rows despite limit=%d (marketCapMoreThan=%d); "
                "server may be ignoring limit param — verify plan tier or paginate via get_screener_page().",
                limit, market_cap_threshold,
            )

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

    def get_historical_market_cap(
        self,
        symbol: str,
        from_date: Optional[str] = None,
        to_date: Optional[str] = None,
    ) -> List[Dict]:
        """
        获取历史市值 (日频)

        使用 stable 端点: /stable/historical-market-capitalization?symbol=XXX
        注意: legacy /api/v3/historical-market-capitalization/XXX 已废弃 (403)

        Args:
            symbol: 股票代码
            from_date: 起始日期 YYYY-MM-DD
            to_date: 结束日期 YYYY-MM-DD

        Returns:
            [{"symbol": str, "date": str, "market_cap": float}, ...]
        """
        params = {"symbol": symbol}
        if from_date:
            params["from"] = from_date
        if to_date:
            params["to"] = to_date
        data = self._request("historical-market-capitalization", params)
        if not data:
            return []
        return [
            {
                "symbol": row.get("symbol", symbol),
                "date": row["date"],
                "market_cap": row["marketCap"],
            }
            for row in data
            if "date" in row and "marketCap" in row
        ]

    def get_delisted_companies(self, page: int = 0, limit: int = 100) -> List[Dict]:
        """
        获取退市公司列表（分页）。

        注意：当前 FMP 套餐下能否翻完整个历史列表取决于 entitlement。
        这个 helper 只封装单页请求，不假设 page>0 一定可用。
        """
        data = self._request(
            "delisted-companies",
            {"page": page, "limit": limit},
        )
        return data if isinstance(data, list) else []

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
        """获取财务比率 (年度，FMP Starter 不支持季度)"""
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
