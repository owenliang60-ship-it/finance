"""
美股数据适配器 — 加载 data/price/*.csv + 复用 RS 计算
"""

import logging
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

import pandas as pd

logger = logging.getLogger(__name__)

# 项目路径
_PROJECT_ROOT = Path(__file__).parent.parent.parent
_PRICE_DIR = _PROJECT_ROOT / "data" / "price"


class USStocksAdapter:
    """
    美股数据适配器

    加载 data/price/*.csv，提供:
    - 价格数据加载 (全量 + 按日期切片)
    - RS 计算函数路由
    - 交易日期序列
    """

    def __init__(self, symbols: Optional[List[str]] = None):
        """
        Args:
            symbols: 要加载的股票列表。None = 自动发现 price 目录下所有 CSV
        """
        self._price_cache: Dict[str, pd.DataFrame] = {}
        self._symbols = symbols

    def load_all(self) -> Dict[str, pd.DataFrame]:
        """
        加载全部价格数据

        Returns:
            {symbol: price_df} — 每个 df 包含 [date, close, ...] 列
        """
        if self._price_cache:
            return self._price_cache

        if self._symbols is not None:
            symbols = self._symbols
        else:
            symbols = self._discover_symbols()

        for sym in symbols:
            df = self._load_csv(sym)
            if df is not None and len(df) >= 70:
                self._price_cache[sym] = df

        logger.info(f"美股适配器: 加载 {len(self._price_cache)} 只股票")
        return self._price_cache

    def get_trading_dates(self) -> List[str]:
        """
        获取全部交易日期序列 (所有股票的日期并集，排序)

        Returns:
            ["2021-01-04", "2021-01-05", ...]
        """
        if not self._price_cache:
            self.load_all()

        all_dates = set()
        for df in self._price_cache.values():
            all_dates.update(df["date"].astype(str).tolist())

        return sorted(all_dates)

    def get_benchmark_nav(self, symbol: str = "SPY") -> List[Tuple[str, float]]:
        """
        获取基准的 NAV 序列

        Returns:
            [(date_str, close_price), ...]
        """
        df = self._load_csv(symbol)
        if df is None or df.empty:
            logger.warning(f"基准 {symbol} 数据不可用")
            return []

        return list(zip(df["date"].astype(str), df["close"].astype(float)))

    def slice_to_date(
        self, date: str
    ) -> Dict[str, pd.DataFrame]:
        """
        防前视：对所有股票截取到指定日期

        Args:
            date: 截止日期 (含)

        Returns:
            {symbol: sliced_df}
        """
        if not self._price_cache:
            self.load_all()

        sliced = {}
        for sym, df in self._price_cache.items():
            mask = df["date"].astype(str) <= date
            cut = df[mask]
            if len(cut) >= 70:  # RS 最小数据要求
                sliced[sym] = cut.reset_index(drop=True)

        return sliced

    def get_prices_at(self, date: str) -> Dict[str, float]:
        """
        获取指定日期的收盘价

        Args:
            date: 日期字符串

        Returns:
            {symbol: close_price}
        """
        if not self._price_cache:
            self.load_all()

        prices = {}
        for sym, df in self._price_cache.items():
            row = df[df["date"].astype(str) == date]
            if not row.empty:
                prices[sym] = float(row.iloc[-1]["close"])

        return prices

    def get_rs_function(self, method: str) -> Callable:
        """
        获取 RS 计算函数

        Args:
            method: "B" 或 "C"

        Returns:
            compute_rs_rating_b 或 compute_rs_rating_c
        """
        from src.indicators.rs_rating import (
            compute_rs_rating_b,
            compute_rs_rating_c,
        )
        if method == "C":
            return compute_rs_rating_c
        return compute_rs_rating_b

    def get_date_range(self) -> Tuple[str, str]:
        """返回数据的起止日期"""
        dates = self.get_trading_dates()
        if not dates:
            return ("", "")
        return (dates[0], dates[-1])

    # ── 内部方法 ──────────────────────────────────────

    def _discover_symbols(self) -> List[str]:
        """自动发现 data/price/ 下所有 CSV"""
        if not _PRICE_DIR.exists():
            return []
        return sorted(
            p.stem for p in _PRICE_DIR.glob("*.csv")
            if p.stem not in ("SPY", "QQQ")  # 基准单独处理
        )

    def _load_csv(self, symbol: str) -> Optional[pd.DataFrame]:
        """加载单只股票的 CSV"""
        csv_path = _PRICE_DIR / f"{symbol}.csv"
        if not csv_path.exists():
            return None

        try:
            df = pd.read_csv(csv_path)
            if "date" not in df.columns or "close" not in df.columns:
                logger.warning(f"{symbol}: CSV 缺少 date/close 列")
                return None
            df = df.sort_values("date", ascending=True).reset_index(drop=True)
            return df
        except Exception as e:
            logger.warning(f"{symbol}: CSV 加载失败: {e}")
            return None
