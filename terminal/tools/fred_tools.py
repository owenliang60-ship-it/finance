"""
FRED (Federal Reserve Economic Data) tools - 免费宏观经济数据

FRED API 完全免费，提供美联储官方经济数据。

获取 API Key (免费):
1. 访问 https://fred.stlouisfed.org/
2. 注册账号
3. 申请 API Key (即时批准)
4. 添加到 .env: FRED_API_KEY=your_key_here
"""
import os
import logging
from typing import Dict, List, Optional
from datetime import datetime, timedelta

import requests

from terminal.tools.protocol import (
    FinanceTool,
    ToolCategory,
    ToolMetadata,
    ToolExecutionError,
)

logger = logging.getLogger(__name__)


class BaseFREDTool(FinanceTool):
    """FRED 工具基类"""

    BASE_URL = "https://api.stlouisfed.org/fred/series/observations"

    def __init__(self):
        self._api_key_checked = False
        self._is_available = False

    def is_available(self) -> bool:
        """检查 FRED API Key 是否存在"""
        if not self._api_key_checked:
            self._is_available = os.getenv("FRED_API_KEY") is not None
            self._api_key_checked = True
        return self._is_available

    def _fetch_series(
        self, series_id: str, limit: int = 10, observation_start: str = None
    ) -> List[Dict]:
        """
        获取 FRED 数据系列

        Args:
            series_id: FRED 系列 ID
            limit: 返回数量
            observation_start: 开始日期 (YYYY-MM-DD)

        Returns:
            数据列表 (降序，最新在前)
        """
        if not self.is_available():
            raise ToolExecutionError(
                f"{self.metadata.name}: FRED API key not found. "
                "Set FRED_API_KEY in .env file."
            )

        api_key = os.getenv("FRED_API_KEY")

        params = {
            "series_id": series_id,
            "api_key": api_key,
            "file_type": "json",
            "limit": limit,
            "sort_order": "desc",  # 最新数据在前
        }

        if observation_start:
            params["observation_start"] = observation_start

        try:
            response = requests.get(self.BASE_URL, params=params, timeout=10)
            response.raise_for_status()
            data = response.json()

            observations = data.get("observations", [])

            # 过滤掉 "." (无数据) 的值
            valid_observations = [
                {
                    "date": obs["date"],
                    "value": float(obs["value"]),
                }
                for obs in observations
                if obs["value"] != "."
            ]

            return valid_observations

        except requests.exceptions.RequestException as e:
            logger.error(f"{self.metadata.name} failed: {e}")
            raise ToolExecutionError(f"FRED API call failed: {e}") from e
        except (KeyError, ValueError) as e:
            logger.error(f"{self.metadata.name} data parsing failed: {e}")
            raise ToolExecutionError(f"FRED data parsing failed: {e}") from e


# ========== Macro Tools ==========


class GetGDPGrowthTool(BaseFREDTool):
    """获取美国 GDP 增长率（季度）"""

    @property
    def metadata(self) -> ToolMetadata:
        return ToolMetadata(
            name="get_gdp_growth",
            category=ToolCategory.MACRO,
            description="Get US GDP growth rate (quarterly, annualized)",
            provider="FRED",
            requires_api_key=True,
            api_key_env_var="FRED_API_KEY",
        )

    def execute(self, limit: int = 8) -> List[Dict]:
        """
        获取 GDP 增长率

        Args:
            limit: 返回数量（默认 8 个季度 = 2 年）

        Returns:
            [{date: "2024-Q3", value: 2.8}, ...]
        """
        return self._fetch_series("A191RL1Q225SBEA", limit=limit)


class GetUnemploymentRateTool(BaseFREDTool):
    """获取美国失业率（月度）"""

    @property
    def metadata(self) -> ToolMetadata:
        return ToolMetadata(
            name="get_unemployment_rate",
            category=ToolCategory.MACRO,
            description="Get US unemployment rate (monthly)",
            provider="FRED",
            requires_api_key=True,
            api_key_env_var="FRED_API_KEY",
        )

    def execute(self, limit: int = 12) -> List[Dict]:
        """
        获取失业率

        Args:
            limit: 返回数量（默认 12 个月）

        Returns:
            [{date: "2024-10", value: 4.1}, ...]
        """
        return self._fetch_series("UNRATE", limit=limit)


class GetFedFundsRateTool(BaseFREDTool):
    """获取联邦基金利率（月度）"""

    @property
    def metadata(self) -> ToolMetadata:
        return ToolMetadata(
            name="get_fed_funds_rate",
            category=ToolCategory.MACRO,
            description="Get Federal Funds Effective Rate (monthly)",
            provider="FRED",
            requires_api_key=True,
            api_key_env_var="FRED_API_KEY",
        )

    def execute(self, limit: int = 12) -> List[Dict]:
        """
        获取联邦基金利率

        Args:
            limit: 返回数量（默认 12 个月）

        Returns:
            [{date: "2024-10", value: 4.83}, ...]
        """
        return self._fetch_series("FEDFUNDS", limit=limit)


class Get10YTreasuryYieldTool(BaseFREDTool):
    """获取 10 年期国债收益率（日度）"""

    @property
    def metadata(self) -> ToolMetadata:
        return ToolMetadata(
            name="get_10y_treasury_yield",
            category=ToolCategory.MACRO,
            description="Get 10-Year Treasury Constant Maturity Rate (daily)",
            provider="FRED",
            requires_api_key=True,
            api_key_env_var="FRED_API_KEY",
        )

    def execute(self, limit: int = 30) -> List[Dict]:
        """
        获取 10 年期国债收益率

        Args:
            limit: 返回数量（默认 30 个交易日）

        Returns:
            [{date: "2024-11-01", value: 4.28}, ...]
        """
        return self._fetch_series("DGS10", limit=limit)


class GetCPIInflationTool(BaseFREDTool):
    """获取 CPI 价格指数（原始值，非同比变化率）"""

    @property
    def metadata(self) -> ToolMetadata:
        return ToolMetadata(
            name="get_cpi_inflation",
            category=ToolCategory.MACRO,
            description="Get CPI price index (raw, NOT YoY% — compute manually)",
            provider="FRED",
            requires_api_key=True,
            api_key_env_var="FRED_API_KEY",
        )

    def execute(self, limit: int = 12) -> List[Dict]:
        """获取 CPI 价格指数（原始值，非同比变化率）

        注意：返回的是 CPI 指数原始值（如 314.2），不是 YoY%。
        需要 YoY% 请使用 macro_fetcher.py 的 _compute_cpi_yoy()。

        Args:
            limit: 返回数量（默认 12 个月）

        Returns:
            [{date: "2024-10", value: 314.2}, ...]
        """
        return self._fetch_series("CPIAUCSL", limit=limit)


class Get2YTreasuryYieldTool(BaseFREDTool):
    """获取 2 年期国债收益率（日度）"""

    @property
    def metadata(self) -> ToolMetadata:
        return ToolMetadata(
            name="get_2y_treasury_yield",
            category=ToolCategory.MACRO,
            description="Get 2-Year Treasury Constant Maturity Rate (daily)",
            provider="FRED",
            requires_api_key=True,
            api_key_env_var="FRED_API_KEY",
        )

    def execute(self, limit: int = 30) -> List[Dict]:
        """
        获取 2 年期国债收益率

        Args:
            limit: 返回数量（默认 30 个交易日）

        Returns:
            [{date: "2024-11-01", value: 4.15}, ...]
        """
        return self._fetch_series("DGS2", limit=limit)


class Get5YTreasuryYieldTool(BaseFREDTool):
    """获取 5 年期国债收益率（日度）"""

    @property
    def metadata(self) -> ToolMetadata:
        return ToolMetadata(
            name="get_5y_treasury_yield",
            category=ToolCategory.MACRO,
            description="Get 5-Year Treasury Constant Maturity Rate (daily)",
            provider="FRED",
            requires_api_key=True,
            api_key_env_var="FRED_API_KEY",
        )

    def execute(self, limit: int = 30) -> List[Dict]:
        """
        获取 5 年期国债收益率

        Args:
            limit: 返回数量（默认 30 个交易日）

        Returns:
            [{date: "2024-11-01", value: 4.20}, ...]
        """
        return self._fetch_series("DGS5", limit=limit)


class Get30YTreasuryYieldTool(BaseFREDTool):
    """获取 30 年期国债收益率（日度）"""

    @property
    def metadata(self) -> ToolMetadata:
        return ToolMetadata(
            name="get_30y_treasury_yield",
            category=ToolCategory.MACRO,
            description="Get 30-Year Treasury Constant Maturity Rate (daily)",
            provider="FRED",
            requires_api_key=True,
            api_key_env_var="FRED_API_KEY",
        )

    def execute(self, limit: int = 30) -> List[Dict]:
        """
        获取 30 年期国债收益率

        Args:
            limit: 返回数量（默认 30 个交易日）

        Returns:
            [{date: "2024-11-01", value: 4.45}, ...]
        """
        return self._fetch_series("DGS30", limit=limit)


class GetVIXTool(BaseFREDTool):
    """获取 VIX 恐慌指数（日度）"""

    @property
    def metadata(self) -> ToolMetadata:
        return ToolMetadata(
            name="get_vix",
            category=ToolCategory.MACRO,
            description="Get CBOE Volatility Index (daily)",
            provider="FRED",
            requires_api_key=True,
            api_key_env_var="FRED_API_KEY",
        )

    def execute(self, limit: int = 30) -> List[Dict]:
        """
        获取 VIX 恐慌指数

        Args:
            limit: 返回数量（默认 30 个交易日）

        Returns:
            [{date: "2024-11-01", value: 18.5}, ...]
        """
        return self._fetch_series("VIXCLS", limit=limit)


class GetDXYTool(BaseFREDTool):
    """获取美元指数（日度）"""

    @property
    def metadata(self) -> ToolMetadata:
        return ToolMetadata(
            name="get_dxy",
            category=ToolCategory.MACRO,
            description="Get Trade Weighted US Dollar Index (daily)",
            provider="FRED",
            requires_api_key=True,
            api_key_env_var="FRED_API_KEY",
        )

    def execute(self, limit: int = 30) -> List[Dict]:
        """
        获取美元指数

        Args:
            limit: 返回数量（默认 30 个交易日）

        Returns:
            [{date: "2024-11-01", value: 112.5}, ...]
        """
        return self._fetch_series("DTWEXBGS", limit=limit)


class GetYieldCurveSpreadTool(BaseFREDTool):
    """获取 10Y-2Y 国债利差（日度）"""

    @property
    def metadata(self) -> ToolMetadata:
        return ToolMetadata(
            name="get_yield_curve_spread",
            category=ToolCategory.MACRO,
            description="Get 10Y-2Y Treasury Spread (daily)",
            provider="FRED",
            requires_api_key=True,
            api_key_env_var="FRED_API_KEY",
        )

    def execute(self, limit: int = 30) -> List[Dict]:
        """
        获取 10Y-2Y 国债利差

        Args:
            limit: 返回数量（默认 30 个交易日）

        Returns:
            [{date: "2024-11-01", value: 0.15}, ...]
        """
        return self._fetch_series("T10Y2Y", limit=limit)


class Get10Y3MSpreadTool(BaseFREDTool):
    """获取 10Y-3M 国债利差（日度）"""

    @property
    def metadata(self) -> ToolMetadata:
        return ToolMetadata(
            name="get_10y3m_spread",
            category=ToolCategory.MACRO,
            description="Get 10Y-3M Treasury Spread (daily)",
            provider="FRED",
            requires_api_key=True,
            api_key_env_var="FRED_API_KEY",
        )

    def execute(self, limit: int = 30) -> List[Dict]:
        """
        获取 10Y-3M 国债利差

        Args:
            limit: 返回数量（默认 30 个交易日）

        Returns:
            [{date: "2024-11-01", value: 0.05}, ...]
        """
        return self._fetch_series("T10Y3M", limit=limit)


class GetJapanRateTool(BaseFREDTool):
    """获取日本央行短期利率（月度）"""

    @property
    def metadata(self) -> ToolMetadata:
        return ToolMetadata(
            name="get_japan_rate",
            category=ToolCategory.MACRO,
            description="Get BOJ Short-term Interest Rate (monthly)",
            provider="FRED",
            requires_api_key=True,
            api_key_env_var="FRED_API_KEY",
        )

    def execute(self, limit: int = 12) -> List[Dict]:
        """
        获取日本央行短期利率

        Args:
            limit: 返回数量（默认 12 个月）

        Returns:
            [{date: "2024-10", value: 0.25}, ...]
        """
        return self._fetch_series("IRSTCI01JPM156N", limit=limit)


class GetUSDJPYTool(BaseFREDTool):
    """获取美元/日元汇率（日度）"""

    @property
    def metadata(self) -> ToolMetadata:
        return ToolMetadata(
            name="get_usdjpy",
            category=ToolCategory.MACRO,
            description="Get USD/JPY Exchange Rate (daily)",
            provider="FRED",
            requires_api_key=True,
            api_key_env_var="FRED_API_KEY",
        )

    def execute(self, limit: int = 30) -> List[Dict]:
        """
        获取美元/日元汇率

        Args:
            limit: 返回数量（默认 30 个交易日）

        Returns:
            [{date: "2024-11-01", value: 151.5}, ...]
        """
        return self._fetch_series("DEXJPUS", limit=limit)


class GetHYSpreadTool(BaseFREDTool):
    """获取美国高收益债利差（日度）"""

    @property
    def metadata(self) -> ToolMetadata:
        return ToolMetadata(
            name="get_hy_spread",
            category=ToolCategory.MACRO,
            description="Get ICE BofA US High Yield Spread (daily)",
            provider="FRED",
            requires_api_key=True,
            api_key_env_var="FRED_API_KEY",
        )

    def execute(self, limit: int = 30) -> List[Dict]:
        """
        获取美国高收益债利差

        Args:
            limit: 返回数量（默认 30 个交易日）

        Returns:
            [{date: "2024-11-01", value: 2.85}, ...]
        """
        return self._fetch_series("BAMLH0A0HYM2", limit=limit)


class GetFedBalanceSheetTool(BaseFREDTool):
    """获取美联储总资产（周度）"""

    @property
    def metadata(self) -> ToolMetadata:
        return ToolMetadata(
            name="get_fed_balance_sheet",
            category=ToolCategory.MACRO,
            description="Get Fed Total Assets (weekly)",
            provider="FRED",
            requires_api_key=True,
            api_key_env_var="FRED_API_KEY",
        )

    def execute(self, limit: int = 12) -> List[Dict]:
        """
        获取美联储总资产

        Args:
            limit: 返回数量（默认 12 周）

        Returns:
            [{date: "2024-10-30", value: 6950000}, ...]
        """
        return self._fetch_series("WALCL", limit=limit)


# ========== Tool Factory ==========


def create_fred_tools() -> List[FinanceTool]:
    """
    Factory function to create all FRED tools.

    Returns:
        List of FRED tool instances
    """
    return [
        GetGDPGrowthTool(),
        GetUnemploymentRateTool(),
        GetFedFundsRateTool(),
        Get10YTreasuryYieldTool(),
        GetCPIInflationTool(),
        Get2YTreasuryYieldTool(),
        Get5YTreasuryYieldTool(),
        Get30YTreasuryYieldTool(),
        GetVIXTool(),
        GetDXYTool(),
        GetYieldCurveSpreadTool(),
        Get10Y3MSpreadTool(),
        GetJapanRateTool(),
        GetUSDJPYTool(),
        GetHYSpreadTool(),
        GetFedBalanceSheetTool(),
    ]
