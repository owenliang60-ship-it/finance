"""
币安合约数据适配器 — 加载本地 crypto 缓存的 OHLCV CSV

缓存结构:
- Finance/data/crypto/binance_daily_cache/{SYMBOL}.csv
- Finance/data/crypto/binance_4h_cache/{SYMBOL}.csv

每个 CSV 列: open_time, date, open, high, low, close, volume, quote_volume, ...
"""

import logging
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd
from src.path_utils import resolve_shared_repo_root

logger = logging.getLogger(__name__)

# Finance 本地 crypto 缓存
_FINANCE_ROOT = resolve_shared_repo_root(
    Path(__file__).parent.parent.parent,
    required_paths=(
        "data/crypto/binance_daily_cache",
        "data/crypto/binance_4h_cache",
    ),
)
_CACHE_DIRS = {
    "1d": _FINANCE_ROOT / "data" / "crypto" / "binance_daily_cache",
    "4h": _FINANCE_ROOT / "data" / "crypto" / "binance_4h_cache",
}


def _normalize_interval(interval: str) -> str:
    value = interval.strip().lower()
    if value in {"1d", "d", "day", "daily"}:
        return "1d"
    if value in {"4h", "4hr", "4hour", "240m"}:
        return "4h"
    raise ValueError(f"Unsupported crypto interval: {interval}")


class CryptoAdapter:
    """
    币安合约数据适配器

    加载 data/crypto/binance_daily_cache/*.csv，提供:
    - 价格数据加载 (全量 + 按日期切片)
    - RS 计算函数路由 (使用 crypto_rs 独立模块)
    - 交易日期序列
    """

    def __init__(
        self,
        symbols: Optional[List[str]] = None,
        cache_dir: Optional[Path] = None,
        interval: str = "1d",
    ):
        """
        Args:
            symbols: 要加载的币种列表 (e.g. ["BTCUSDT", "ETHUSDT"])
                     None = 自动发现 cache 目录下所有 CSV
            cache_dir: 覆盖默认缓存目录 (测试用)
            interval: "1d" 或 "4h"，决定默认 cache 目录
        """
        self._price_cache: Dict[str, pd.DataFrame] = {}
        self._symbols = symbols
        self.interval = _normalize_interval(interval)
        self._cache_dir = cache_dir or _CACHE_DIRS[self.interval]

    def load_all(self) -> Dict[str, pd.DataFrame]:
        """
        加载全部价格数据

        Returns:
            {symbol: price_df} — 每个 df 包含 [date, close, volume, ...] 列
        """
        if self._price_cache:
            return self._price_cache

        if self._symbols is not None:
            symbols = self._symbols
        else:
            symbols = self._discover_symbols()

        for sym in symbols:
            df = self._load_csv(sym)
            if df is not None and len(df) >= 15:  # 币圈最小数据要求
                self._price_cache[sym] = df

        logger.info(f"币安适配器: 加载 {len(self._price_cache)} 只标的")
        return self._price_cache

    def get_trading_dates(self) -> List[str]:
        """获取全部交易日期序列"""
        if not self._price_cache:
            self.load_all()

        all_dates = set()
        for df in self._price_cache.values():
            all_dates.update(df["date"].astype(str).tolist())

        return sorted(all_dates)

    def get_benchmark_nav(self, symbol: str = "BTCUSDT") -> List[Tuple[str, float]]:
        """获取基准 NAV 序列"""
        if not self._price_cache:
            self.load_all()

        df = self._price_cache.get(symbol)
        if df is None:
            df = self._load_csv(symbol)
        if df is None or df.empty:
            logger.warning(f"基准 {symbol} 数据不可用")
            return []

        return list(zip(df["date"].astype(str), df["close"].astype(float)))

    def slice_to_date(self, date: str) -> Dict[str, np.ndarray]:
        """
        防前视：对所有币种截取到指定日期

        Returns:
            {symbol: close_prices_array} — 适配 crypto_rs 输入格式
        """
        if not self._price_cache:
            self.load_all()

        sliced = {}
        cutoff = pd.to_datetime(date)
        for sym, df in self._price_cache.items():
            mask = pd.to_datetime(df["date"]) <= cutoff
            cut = df[mask]
            if len(cut) >= 15:  # 币圈最小数据要求
                sliced[sym] = cut["close"].values.astype(np.float64)

        return sliced

    def slice_to_date_df(self, date: str) -> Dict[str, pd.DataFrame]:
        """
        防前视：返回 DataFrame 格式 (因子研究用)

        Returns:
            {symbol: DataFrame[date, close, volume, ...]}
        """
        if not self._price_cache:
            self.load_all()

        sliced = {}
        cutoff = pd.to_datetime(date)
        for sym, df in self._price_cache.items():
            mask = pd.to_datetime(df["date"]) <= cutoff
            cut = df[mask]
            if len(cut) >= 15:
                sliced[sym] = cut.reset_index(drop=True)

        return sliced

    def get_prices_at(self, date: str) -> Dict[str, float]:
        """获取指定日期的收盘价"""
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
        获取 RS 计算函数 (使用独立 crypto_rs 模块)

        Args:
            method: "B" 或 "C"
        """
        from backtest.adapters.crypto_rs import (
            compute_crypto_rs_b,
            compute_crypto_rs_c,
        )
        if method == "C":
            return compute_crypto_rs_c
        return compute_crypto_rs_b

    def get_date_range(self) -> Tuple[str, str]:
        """返回数据的起止日期"""
        dates = self.get_trading_dates()
        if not dates:
            return ("", "")
        return (dates[0], dates[-1])

    # ── 内部方法 ──────────────────────────────────────

    def _discover_symbols(self) -> List[str]:
        """自动发现 cache 目录下所有 CSV"""
        if not self._cache_dir.exists():
            logger.warning(f"币安缓存目录不存在: {self._cache_dir}")
            return []
        return sorted(p.stem for p in self._cache_dir.glob("*.csv"))

    def _load_csv(self, symbol: str) -> Optional[pd.DataFrame]:
        """加载单只币种的 CSV"""
        csv_path = self._cache_dir / f"{symbol}.csv"
        if not csv_path.exists():
            return None

        try:
            df = pd.read_csv(csv_path)

            # 兼容不同格式的列名
            dt_series = None
            if "open_time" in df.columns:
                dt_series = pd.to_datetime(df["open_time"], unit="ms", utc=True)
            elif "timestamp" in df.columns:
                ts = df["timestamp"]
                if pd.api.types.is_numeric_dtype(ts):
                    dt_series = pd.to_datetime(ts, unit="ms", utc=True)
                else:
                    dt_series = pd.to_datetime(ts, utc=True)
            elif "date" in df.columns:
                dt_series = pd.to_datetime(df["date"], utc=False)

            if dt_series is not None:
                fmt = "%Y-%m-%d" if self.interval == "1d" else "%Y-%m-%d %H:%M:%S"
                df["date"] = dt_series.dt.strftime(fmt)

            if "date" not in df.columns or "close" not in df.columns:
                logger.warning(f"{symbol}: CSV 缺少 date/close 列")
                return None

            for col in ("open", "high", "low", "close", "volume", "quote_volume"):
                if col in df.columns:
                    df[col] = df[col].astype(float)

            df["_sort_dt"] = pd.to_datetime(df["date"])
            df = df.sort_values("_sort_dt", ascending=True).drop(columns="_sort_dt")
            df = df.reset_index(drop=True)
            return df
        except Exception as e:
            logger.warning(f"{symbol}: CSV 加载失败: {e}")
            return None
