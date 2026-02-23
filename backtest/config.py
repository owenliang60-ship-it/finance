"""
BacktestConfig + FactorStudyConfig — 回测/因子研究配置数据类
"""

from dataclasses import dataclass, field
from typing import List, Literal, Optional


@dataclass
class BacktestConfig:
    """回测参数配置"""

    market: Literal["us_stocks", "crypto"]
    rs_method: Literal["B", "C"]
    top_n: int = 10
    sell_buffer: int = 5
    weighting: Literal["equal", "rs_weighted"] = "equal"
    rebalance_freq: Literal["D", "3D", "W", "2W", "M"] = "M"
    transaction_cost_bps: float = 5.0
    initial_capital: float = 1_000_000.0
    benchmark_symbol: Optional[str] = None
    start_date: Optional[str] = None
    end_date: Optional[str] = None

    @property
    def cost_rate(self) -> float:
        """交易成本比率 (单边)"""
        return self.transaction_cost_bps / 10_000

    def label(self) -> str:
        """参数组合的可读标签"""
        return (
            f"{self.market}_{self.rs_method}_top{self.top_n}"
            f"_{self.rebalance_freq}_buf{self.sell_buffer}"
        )


# ── 频率常量 ──────────────────────────────────────────
FREQ_DAYS = {
    "D": 1,
    "3D": 3,
    "W": 5,      # 交易日
    "2W": 10,
    "M": 21,
}


# ── 参数扫描网格 ─────────────────────────────────────
US_SWEEP_GRID = {
    "rs_method": ["B", "C"],
    "top_n": [5, 10, 15, 20],
    "rebalance_freq": ["W", "2W", "M"],
    "sell_buffer": [0, 5, 10],
}

CRYPTO_SWEEP_GRID = {
    "rs_method": ["B", "C"],
    "top_n": [5, 10, 15, 20],
    "rebalance_freq": ["D", "3D", "W"],
    "sell_buffer": [0, 3, 5],
}


# ── 预设工厂 ──────────────────────────────────────────

def us_preset(**overrides) -> BacktestConfig:
    """美股预设: 月度换仓, 5bps, SPY 基准"""
    defaults = dict(
        market="us_stocks",
        rs_method="B",
        top_n=10,
        sell_buffer=5,
        rebalance_freq="M",
        transaction_cost_bps=5.0,
        initial_capital=1_000_000.0,
        benchmark_symbol="SPY",
    )
    defaults.update(overrides)
    return BacktestConfig(**defaults)


def crypto_preset(**overrides) -> BacktestConfig:
    """币圈预设: 周换仓, 4bps, BTCUSDT 基准"""
    defaults = dict(
        market="crypto",
        rs_method="B",
        top_n=10,
        sell_buffer=3,
        rebalance_freq="W",
        transaction_cost_bps=4.0,
        initial_capital=1_000_000.0,
        benchmark_symbol="BTCUSDT",
    )
    defaults.update(overrides)
    return BacktestConfig(**defaults)


# ══════════════════════════════════════════════════════════
# Factor Study 配置
# ══════════════════════════════════════════════════════════

_US_HORIZONS = [5, 10, 20, 40, 60]
_CRYPTO_HORIZONS = [1, 3, 5, 7, 14]


@dataclass
class FactorStudyConfig:
    """因子有效性研究配置"""

    market: Literal["us_stocks", "crypto"]
    computation_freq: Literal["D", "W"] = "W"
    forward_horizons: List[int] = field(default_factory=list)
    n_quantiles: int = 5
    benchmark_symbol: Optional[str] = None
    start_date: Optional[str] = None
    end_date: Optional[str] = None

    def __post_init__(self):
        if not self.forward_horizons:
            if self.market == "us_stocks":
                self.forward_horizons = list(_US_HORIZONS)
            else:
                self.forward_horizons = list(_CRYPTO_HORIZONS)

    def label(self) -> str:
        return f"{self.market}_{self.computation_freq}"


def us_factor_study(**overrides) -> FactorStudyConfig:
    """美股因子研究预设: 周频, [5,10,20,40,60]d, SPY 基准"""
    defaults = dict(
        market="us_stocks",
        computation_freq="W",
        forward_horizons=list(_US_HORIZONS),
        n_quantiles=5,
        benchmark_symbol="SPY",
    )
    defaults.update(overrides)
    return FactorStudyConfig(**defaults)


def crypto_factor_study(**overrides) -> FactorStudyConfig:
    """币圈因子研究预设: 日频, [1,3,5,7,14]d, BTCUSDT 基准"""
    defaults = dict(
        market="crypto",
        computation_freq="D",
        forward_horizons=list(_CRYPTO_HORIZONS),
        n_quantiles=5,
        benchmark_symbol="BTCUSDT",
    )
    defaults.update(overrides)
    return FactorStudyConfig(**defaults)
