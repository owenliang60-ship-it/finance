"""
MarketData.app API 客户端
期权数据源 — Starter plan ($12/月)

Auth: Bearer token in Authorization header (不同于 FMP 的 query param)
Rate limit: 串行调用，间隔防限流
"""
import requests
import time
import logging
from typing import Any, Dict, List, Optional

import sys
sys.path.insert(0, str(__file__).rsplit("/src", 1)[0])
from config.settings import (
    MARKETDATA_API_KEY,
    MARKETDATA_BASE_URL,
    MARKETDATA_CALL_INTERVAL,
    API_RETRY_TIMES,
    API_TIMEOUT,
)

logger = logging.getLogger(__name__)


class MarketDataClient:
    """MarketData.app API 客户端"""

    def __init__(self, api_key: str = MARKETDATA_API_KEY):
        self.api_key = api_key
        self.base_url = MARKETDATA_BASE_URL
        self._last_call_time = 0.0

    def _rate_limit(self):
        """API 限流控制"""
        elapsed = time.time() - self._last_call_time
        if elapsed < MARKETDATA_CALL_INTERVAL:
            time.sleep(MARKETDATA_CALL_INTERVAL - elapsed)
        self._last_call_time = time.time()

    def _request(self, endpoint: str, params: Optional[Dict] = None) -> Any:
        """发送 API 请求，带重试。

        MarketData.app 用 Bearer token 认证（不是 query param）。
        """
        self._rate_limit()

        url = "{}/{}".format(self.base_url, endpoint)
        params = params or {}
        headers = {
            "Authorization": "Bearer {}".format(self.api_key),
            "Accept": "application/json",
        }

        for attempt in range(API_RETRY_TIMES):
            try:
                resp = requests.get(
                    url, params=params, headers=headers, timeout=API_TIMEOUT
                )

                if resp.status_code == 200:
                    data = resp.json()
                    # MarketData.app wraps responses in {"s": "ok", ...}
                    if isinstance(data, dict) and data.get("s") == "ok":
                        return data
                    elif isinstance(data, dict) and data.get("s") == "no_data":
                        logger.info("No data for %s: %s", endpoint, params)
                        return None
                    # Some endpoints return raw data
                    return data
                elif resp.status_code == 429:
                    wait_time = (attempt + 1) * 5
                    logger.warning("Rate limited, waiting %ds...", wait_time)
                    time.sleep(wait_time)
                elif resp.status_code == 402:
                    logger.error("MarketData.app credit limit reached")
                    return None
                elif resp.status_code == 401:
                    logger.error("MarketData.app auth failed — check API key")
                    return None
                else:
                    logger.error(
                        "MarketData API error %d: %s",
                        resp.status_code,
                        resp.text[:200],
                    )
                    return None

            except requests.exceptions.Timeout:
                logger.warning(
                    "Timeout on attempt %d/%d", attempt + 1, API_RETRY_TIMES
                )
            except requests.exceptions.RequestException as e:
                logger.error("Request error: %s", e)

        logger.error(
            "Failed after %d attempts: %s", API_RETRY_TIMES, endpoint
        )
        return None

    # ========== Options Chain ==========

    def get_options_chain(
        self,
        symbol: str,
        expiration: Optional[str] = None,
        dte_min: Optional[int] = None,
        dte_max: Optional[int] = None,
        strike_limit: Optional[int] = None,
        option_range: Optional[str] = None,
        side: Optional[str] = None,
    ) -> Optional[Dict]:
        """获取期权链数据。

        Args:
            symbol: 标的代码
            expiration: 指定到期日 (YYYY-MM-DD)
            dte_min: 最小 DTE
            dte_max: 最大 DTE
            strike_limit: 限制每个到期日的 strike 数量（省 credit）
            option_range: 'itm', 'otm', 'atm'
            side: 'call' 或 'put'

        Returns:
            Chain 数据 dict，包含 optionSymbol, strike, bid, ask, iv, delta 等数组
        """
        params = {}
        if expiration:
            params["expiration"] = expiration
        if dte_min is not None:
            params["from"] = "{}".format(dte_min)  # DTE from
        if dte_max is not None:
            params["to"] = "{}".format(dte_max)  # DTE to
        if strike_limit is not None:
            params["strikeLimit"] = strike_limit
        if option_range:
            params["range"] = option_range
        if side:
            params["side"] = side

        return self._request("options/chain/{}".format(symbol), params)

    def get_options_expirations(self, symbol: str) -> Optional[List[str]]:
        """获取可用到期日列表。

        Args:
            symbol: 标的代码

        Returns:
            到期日字符串列表 ['2026-03-21', '2026-04-17', ...]
        """
        data = self._request("options/expirations/{}".format(symbol))
        if data and isinstance(data, dict):
            return data.get("expirations", [])
        return None

    def get_options_quote(
        self, option_symbol: str
    ) -> Optional[Dict]:
        """获取单个期权合约报价。

        Args:
            option_symbol: OCC 标准期权代码 (e.g. AAPL260321C00200000)

        Returns:
            报价 dict
        """
        return self._request("options/quotes/{}".format(option_symbol))

    # ========== Options for IV Extraction ==========

    def get_atm_iv_data(self, symbol: str) -> Optional[Dict]:
        """获取 ATM 期权数据用于 IV 提取。

        使用 strikeLimit=2 + range=atm + DTE 约束压缩 credit 消耗。
        只拉近月 ATM call + put，取 IV 平均。

        Args:
            symbol: 标的代码

        Returns:
            Chain 数据（ATM 范围，近月，strike 限制 2）
        """
        return self.get_options_chain(
            symbol,
            strike_limit=2,
            option_range="atm",
            dte_min=7,
            dte_max=45,
        )

    # ========== Stock Quote (for underlying price) ==========

    def get_stock_quote(self, symbol: str) -> Optional[Dict]:
        """获取标的股价。

        Args:
            symbol: 标的代码

        Returns:
            报价 dict with 'last', 'bid', 'ask' 等
        """
        data = self._request("stocks/quotes/{}".format(symbol))
        if data and isinstance(data, dict) and data.get("s") == "ok":
            # Extract first element from arrays
            result = {}
            for key in ["last", "bid", "ask", "volume", "mid"]:
                arr = data.get(key, [])
                if arr:
                    result[key] = arr[0]
            return result
        return None


# 单例
marketdata_client = MarketDataClient()


if __name__ == "__main__":
    client = MarketDataClient()

    print("Testing get_options_expirations:")
    exps = client.get_options_expirations("AAPL")
    if exps:
        print("  Found {} expirations".format(len(exps)))
        print("  First 5:", exps[:5])

    print("\nTesting get_atm_iv_data:")
    iv_data = client.get_atm_iv_data("AAPL")
    if iv_data:
        print("  Got ATM IV data with keys:", list(iv_data.keys()))
