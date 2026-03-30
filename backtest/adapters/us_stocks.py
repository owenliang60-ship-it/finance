"""
美股数据适配器 — 加载 market.db 量价数据 + 复用 RS 计算
"""

import logging
from typing import Callable, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


def _get_bulk_mcaps(date: str) -> Dict[str, float]:
    """从 market.db 查询所有 symbol 在 date 的历史市值"""
    from src.data.market_store import get_store
    return get_store().get_bulk_market_caps_at(date)


class USStocksAdapter:
    """
    美股数据适配器

    加载 market.db 量价数据，提供:
    - 价格数据加载 (全量 + 按日期切片)
    - RS 计算函数路由
    - 交易日期序列
    """

    def __init__(
        self,
        symbols: Optional[List[str]] = None,
        universe: Optional[str] = None,
        mcap_threshold: Optional[float] = None,
    ):
        """
        Args:
            symbols: 要加载的股票列表。None = 自动发现
            universe: 过滤模式: "pool" (池内 ~147), "extended" (~533), None (all in db)
            mcap_threshold: 历史市值阈值 (e.g. 10e9)。每次 slice_to_date 时过滤低于阈值的股票
        """
        self._price_cache: Dict[str, pd.DataFrame] = {}
        self._symbols = symbols
        self._universe = universe
        self._mcap_threshold = mcap_threshold

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
            df = self._load_prices(sym)
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

        Args:
            symbol: 基准代码。"POOL_AVG" 为哨兵值，合成池内等权 NAV。

        Returns:
            [(date_str, nav_float), ...]
        """
        if symbol == "POOL_AVG":
            return self._compute_pool_avg_nav()

        df = self._load_prices(symbol)
        if df is None or df.empty:
            logger.warning(f"基准 {symbol} 数据不可用")
            return []

        return list(zip(df["date"].astype(str), df["close"].astype(float)))

    def _compute_pool_avg_nav(self) -> List[Tuple[str, float]]:
        """
        合成池内等权平均 NAV

        用 _price_cache 中所有股票:
        1. 每个交易日算各股票日收益率
        2. 取横截面均值得到池平均日收益
        3. 累乘得到 NAV 序列 (起始=100)
        """
        if not self._price_cache:
            self.load_all()

        if not self._price_cache:
            logger.warning("POOL_AVG: 无股票数据")
            return []

        # 构建收盘价矩阵 (index=date, columns=symbol)
        close_series = {}
        for sym, df in self._price_cache.items():
            s = df.set_index(df["date"].astype(str))["close"].astype(float)
            s = s[~s.index.duplicated(keep="last")]
            close_series[sym] = s

        close_df = pd.DataFrame(close_series)
        close_df = close_df.sort_index()

        # 日收益率 → 横截面均值
        daily_returns = close_df.pct_change()
        pool_avg_return = daily_returns.mean(axis=1).fillna(0)  # 等权, 首行=0

        # 累乘得 NAV (起始=100)
        nav = (1 + pool_avg_return).cumprod() * 100

        result = [(d, float(v)) for d, v in nav.items() if not np.isnan(v)]
        logger.info(f"POOL_AVG NAV 合成: {len(result)} 交易日, "
                    f"{len(close_series)} 只股票")
        return result

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

        # ── universe reconstitution ──
        if self._mcap_threshold and sliced:
            mcaps = _get_bulk_mcaps(date)

            # 覆盖率门卫 — 每次 rebalance 都检查
            has_data = sum(1 for sym in sliced if sym in mcaps)
            coverage = has_data / len(sliced) if sliced else 0
            if coverage < 0.9:
                missing = [sym for sym in sliced if sym not in mcaps]
                raise ValueError(
                    f"{date}: reconstitution 覆盖率 {coverage:.1%} < 90% "
                    f"({has_data}/{len(sliced)}). "
                    f"缺失 mcap 数据的 symbols: {missing[:20]}..."
                )

            before = len(sliced)
            sliced = {
                sym: df for sym, df in sliced.items()
                if sym not in mcaps  # 无数据 → 保留（覆盖率门卫已确保 <10%）
                or mcaps[sym] >= self._mcap_threshold  # 有数据且达标 → 保留
            }
            filtered = before - len(sliced)
            if filtered > 0:
                logger.debug(f"{date}: reconstitution 过滤 {filtered} 只 (阈值 {self._mcap_threshold:.0e})")

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
        """从 market.db 发现有价格数据的股票，按 universe 参数过滤"""
        try:
            if self._universe == "pool":
                from src.data.pool_manager import get_symbols as get_pool_symbols
                return get_pool_symbols()
            elif self._universe == "extended":
                from src.data.extended_universe_manager import get_extended_symbols
                return get_extended_symbols()
            else:
                # Default: all symbols in market.db
                from src.data.market_store import get_store
                store = get_store()
                symbols = store.get_symbols("daily_price")
                symbols = [s for s in symbols if s not in ("SPY", "QQQ", "^VIX")]
                return symbols
        except Exception as e:
            logger.warning("market.db 发现股票失败: %s", e)
            return []

    def _load_prices(self, symbol: str) -> Optional[pd.DataFrame]:
        """加载单只股票的量价数据 (直接读 market.db，不触发 FMP fetch)"""
        try:
            from src.data.market_store import get_store
            store = get_store()
            df = store.get_daily_prices_df(symbol)
            if df is None or df.empty:
                return None
            # Ensure ascending order for backtest
            df = df.sort_values("date", ascending=True).reset_index(drop=True)
            return df
        except Exception as e:
            logger.warning("%s: 加载失败: %s", symbol, e)
            return None
