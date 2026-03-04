"""
美股数据适配器 — 加载 market.db 量价数据 (CSV fallback) + 复用 RS 计算
"""

import logging
from typing import Callable, Dict, List, Optional, Tuple

import pandas as pd

logger = logging.getLogger(__name__)


class USStocksAdapter:
    """
    美股数据适配器

    加载 market.db 量价数据 (CSV fallback)，提供:
    - 价格数据加载 (全量 + 按日期切片)
    - RS 计算函数路由
    - 交易日期序列
    """

    def __init__(self, symbols: Optional[List[str]] = None):
        """
        Args:
            symbols: 要加载的股票列表。None = 自动发现 market.db 中所有股票
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
        """从 market.db 发现有价格数据的股票 (CSV fallback)"""
        try:
            from src.data.market_store import get_store
            store = get_store()
            symbols = store.get_symbols("daily_price")
            symbols = [s for s in symbols if s not in ("SPY", "QQQ")]
            if symbols:
                return symbols
        except Exception as e:
            logger.warning(f"market.db 发现股票失败: {e}")

        # CSV fallback
        try:
            from config.settings import PRICE_DIR
            if PRICE_DIR.exists():
                symbols = sorted(
                    f.stem for f in PRICE_DIR.glob("*.csv")
                    if f.stem not in ("SPY", "QQQ")
                )
                if symbols:
                    logger.info(f"CSV fallback: 发现 {len(symbols)} 只股票")
                    return symbols
        except Exception as e:
            logger.warning(f"CSV fallback 发现股票也失败: {e}")

        return []

    def _load_csv(self, symbol: str) -> Optional[pd.DataFrame]:
        """加载单只股票的量价数据 (market.db → CSV fallback)"""
        try:
            from src.data.price_fetcher import get_price_df
            df = get_price_df(symbol, max_age_days=0)
            if df is None or df.empty:
                return None
            # get_price_df returns descending; backtest needs ascending
            df = df.sort_values("date", ascending=True).reset_index(drop=True)
            return df
        except Exception as e:
            logger.warning(f"{symbol}: 加载失败: {e}")
            return None
