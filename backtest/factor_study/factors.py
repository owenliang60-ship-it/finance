"""
Factor 适配器 — 将现有指标包装为统一 Factor 接口

每个适配器调用 src/indicators/ 中的计算函数，
返回 {symbol: score} 字典。

注册表 ALL_FACTORS 提供 name → class 映射，
get_factor(name) 工厂函数创建实例。
"""

import logging
from typing import Dict, Optional, Type

import pandas as pd

from backtest.factor_study.protocol import Factor, FactorMeta

logger = logging.getLogger(__name__)


# ── RS Rating B ──────────────────────────────────────────

class RSRatingBFactor(Factor):
    """RS Rating Method B — 风险调整 Z-Score 横截面动量排名"""

    @property
    def meta(self) -> FactorMeta:
        return FactorMeta(
            name="RS_Rating_B",
            score_name="rs_rank",
            score_range=(0, 99),
            higher_is_stronger=True,
            min_data_days=70,
        )

    def compute(
        self,
        price_dict: Dict[str, pd.DataFrame],
        date: str,
    ) -> Dict[str, float]:
        from src.indicators.rs_rating import compute_rs_rating_b

        result_df = compute_rs_rating_b(price_dict)
        if result_df.empty:
            return {}
        return dict(zip(result_df["symbol"], result_df["rs_rank"].astype(float)))


# ── RS Rating C ──────────────────────────────────────────

class RSRatingCFactor(Factor):
    """RS Rating Method C — Clenow 回归动量排名"""

    @property
    def meta(self) -> FactorMeta:
        return FactorMeta(
            name="RS_Rating_C",
            score_name="rs_rank",
            score_range=(0, 99),
            higher_is_stronger=True,
            min_data_days=70,
        )

    def compute(
        self,
        price_dict: Dict[str, pd.DataFrame],
        date: str,
    ) -> Dict[str, float]:
        from src.indicators.rs_rating import compute_rs_rating_c

        result_df = compute_rs_rating_c(price_dict)
        if result_df.empty:
            return {}
        return dict(zip(result_df["symbol"], result_df["rs_rank"].astype(float)))


# ── PMARP ────────────────────────────────────────────────

class PMARPFactor(Factor):
    """PMARP — Price Moving Average Ratio Percentile"""

    @property
    def meta(self) -> FactorMeta:
        return FactorMeta(
            name="PMARP",
            score_name="current",
            score_range=(0, 100),
            higher_is_stronger=True,
            min_data_days=170,
        )

    def compute(
        self,
        price_dict: Dict[str, pd.DataFrame],
        date: str,
    ) -> Dict[str, float]:
        from src.indicators.pmarp import analyze_pmarp

        scores: Dict[str, float] = {}
        for symbol, df in price_dict.items():
            result = analyze_pmarp(df)
            if result["current"] is not None:
                scores[symbol] = float(result["current"])
        return scores


# ── RVOL ─────────────────────────────────────────────────

class RVOLFactor(Factor):
    """RVOL — Relative Volume (σ 标准差)"""

    @property
    def meta(self) -> FactorMeta:
        return FactorMeta(
            name="RVOL",
            score_name="sigma",
            score_range=(-5, 10),
            higher_is_stronger=True,
            min_data_days=121,
        )

    def compute(
        self,
        price_dict: Dict[str, pd.DataFrame],
        date: str,
    ) -> Dict[str, float]:
        from src.indicators.rvol import analyze_rvol

        scores: Dict[str, float] = {}
        for symbol, df in price_dict.items():
            result = analyze_rvol(df)
            if result["current"] is not None:
                scores[symbol] = float(result["current"])
        return scores


# ── DV Acceleration ──────────────────────────────────────

class DVAccelerationFactor(Factor):
    """DV Acceleration — Dollar Volume 5d/20d 加速比"""

    @property
    def meta(self) -> FactorMeta:
        return FactorMeta(
            name="DV_Acceleration",
            score_name="ratio",
            score_range=(0, 5),
            higher_is_stronger=True,
            min_data_days=20,
        )

    def compute(
        self,
        price_dict: Dict[str, pd.DataFrame],
        date: str,
    ) -> Dict[str, float]:
        from src.indicators.dv_acceleration import scan_dv_acceleration

        result_df = scan_dv_acceleration(price_dict, threshold=0.0)
        if result_df.empty:
            return {}
        return dict(zip(result_df["symbol"], result_df["ratio"].astype(float)))


# ── RVOL Sustained ───────────────────────────────────────

class RVOLSustainedFactor(Factor):
    """RVOL Sustained — 连续放量天数"""

    @property
    def meta(self) -> FactorMeta:
        return FactorMeta(
            name="RVOL_Sustained",
            score_name="days",
            score_range=(0, 30),
            higher_is_stronger=True,
            min_data_days=121,
        )

    def compute(
        self,
        price_dict: Dict[str, pd.DataFrame],
        date: str,
    ) -> Dict[str, float]:
        from src.indicators.rvol_sustained import scan_rvol_sustained

        results = scan_rvol_sustained(price_dict, threshold=2.0)
        scores: Dict[str, float] = {}
        for r in results:
            scores[r["symbol"]] = float(r["days"])

        # 没触发信号的股票 days=0
        for sym in price_dict:
            if sym not in scores:
                scores[sym] = 0.0

        return scores


# ── Crypto RS Rating B ───────────────────────────────────

class CryptoRSBFactor(Factor):
    """Crypto RS Rating Method B — 风险调整 Z-Score (7d/3d/1d)"""

    @property
    def meta(self) -> FactorMeta:
        return FactorMeta(
            name="Crypto_RS_B",
            score_name="rs_rank",
            score_range=(0, 99),
            higher_is_stronger=True,
            min_data_days=15,
        )

    def compute(
        self,
        price_dict,
        date: str,
    ) -> Dict[str, float]:
        from backtest.adapters.crypto_rs import compute_crypto_rs_b
        import numpy as np

        # price_dict 可能是 {sym: ndarray} 或 {sym: DataFrame}
        arr_dict = {}
        for sym, data in price_dict.items():
            if isinstance(data, pd.DataFrame):
                arr_dict[sym] = data["close"].values.astype(np.float64)
            else:
                arr_dict[sym] = np.asarray(data, dtype=np.float64)

        result_df = compute_crypto_rs_b(arr_dict)
        if result_df.empty:
            return {}
        return dict(zip(result_df["symbol"], result_df["rs_rank"].astype(float)))


# ── Crypto RS Rating C ───────────────────────────────────

class CryptoRSCFactor(Factor):
    """Crypto RS Rating Method C — Clenow 回归动量 (7d/3d/1d)"""

    @property
    def meta(self) -> FactorMeta:
        return FactorMeta(
            name="Crypto_RS_C",
            score_name="rs_rank",
            score_range=(0, 99),
            higher_is_stronger=True,
            min_data_days=15,
        )

    def compute(
        self,
        price_dict,
        date: str,
    ) -> Dict[str, float]:
        from backtest.adapters.crypto_rs import compute_crypto_rs_c
        import numpy as np

        arr_dict = {}
        for sym, data in price_dict.items():
            if isinstance(data, pd.DataFrame):
                arr_dict[sym] = data["close"].values.astype(np.float64)
            else:
                arr_dict[sym] = np.asarray(data, dtype=np.float64)

        result_df = compute_crypto_rs_c(arr_dict)
        if result_df.empty:
            return {}
        return dict(zip(result_df["symbol"], result_df["rs_rank"].astype(float)))


# ══════════════════════════════════════════════════════════
# 注册表
# ══════════════════════════════════════════════════════════

ALL_FACTORS: Dict[str, Type[Factor]] = {
    "RS_Rating_B": RSRatingBFactor,
    "RS_Rating_C": RSRatingCFactor,
    "PMARP": PMARPFactor,
    "RVOL": RVOLFactor,
    "DV_Acceleration": DVAccelerationFactor,
    "RVOL_Sustained": RVOLSustainedFactor,
    "Crypto_RS_B": CryptoRSBFactor,
    "Crypto_RS_C": CryptoRSCFactor,
}


def get_factor(name: str) -> Factor:
    """
    工厂函数: 按名称创建 Factor 实例

    Args:
        name: 因子名称 (如 "RS_Rating_B")

    Returns:
        Factor 实例

    Raises:
        KeyError: 未知因子名称
    """
    if name not in ALL_FACTORS:
        available = ", ".join(sorted(ALL_FACTORS.keys()))
        raise KeyError(f"未知因子: {name!r}。可用: {available}")
    return ALL_FACTORS[name]()


def list_factors() -> list:
    """返回所有已注册因子的名称列表"""
    return sorted(ALL_FACTORS.keys())
