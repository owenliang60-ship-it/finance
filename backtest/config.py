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
    weighting: Literal["equal", "rs_weighted", "inv_vol"] = "equal"
    rebalance_freq: Literal["D", "3D", "W", "2W", "M"] = "M"
    transaction_cost_bps: float = 5.0
    initial_capital: float = 1_000_000.0
    benchmark_symbol: Optional[str] = None
    start_date: Optional[str] = None
    end_date: Optional[str] = None
    rebalance_held: bool = True  # True=真等权, False=动量持有(原行为)
    # Regime filter
    regime_symbol: Optional[str] = None           # e.g. "SPY"; None = disabled
    regime_ma_period: int = 200                   # SMA period for regime check
    regime_mode: Literal["cash", "scale"] = "cash"  # cash=清仓, scale=缩放
    regime_scale_factor: float = 0.5              # scale 模式下的缩放系数
    # Vol-targeted sizing
    vol_lookback: int = 60                        # inv_vol 波动率回看天数
    mcap_threshold: Optional[float] = None  # 历史市值阈值 (e.g. 10e9), None=不过滤

    @property
    def cost_rate(self) -> float:
        """交易成本比率 (单边)"""
        return self.transaction_cost_bps / 10_000

    def label(self) -> str:
        """参数组合的可读标签"""
        rb = "eqw" if self.rebalance_held else "drift"
        base = (
            f"{self.market}_{self.rs_method}_top{self.top_n}"
            f"_{self.rebalance_freq}_buf{self.sell_buffer}_{rb}"
        )
        if self.weighting != "equal":
            base += f"_{self.weighting}"
            if self.weighting == "inv_vol":
                base += f"{self.vol_lookback}"
        if self.regime_symbol:
            base += f"_regime{self.regime_ma_period}_{self.regime_mode}"
        if self.mcap_threshold:
            base += f"_mcap{self.mcap_threshold:.0e}"
        return base


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
    benchmark_symbol: Optional[str] = None       # 向后兼容，迁移到 benchmark_symbols
    benchmark_symbols: List[str] = field(default_factory=list)
    start_date: Optional[str] = None
    end_date: Optional[str] = None
    oos_fraction: float = 0.3       # 最后 30% 的日期作为 OOS
    min_oos_dates: int = 50         # OOS 最少计算日数，不够则跳过 OOS

    def __post_init__(self):
        if not self.forward_horizons:
            if self.market == "us_stocks":
                self.forward_horizons = list(_US_HORIZONS)
            else:
                self.forward_horizons = list(_CRYPTO_HORIZONS)

        # 向后兼容: benchmark_symbol → benchmark_symbols
        if self.benchmark_symbol and not self.benchmark_symbols:
            self.benchmark_symbols = [self.benchmark_symbol]
        # 保持 benchmark_symbol 同步 (取第一个)
        if self.benchmark_symbols and not self.benchmark_symbol:
            self.benchmark_symbol = self.benchmark_symbols[0]

    def label(self) -> str:
        return f"{self.market}_{self.computation_freq}"


def us_factor_study(**overrides) -> FactorStudyConfig:
    """美股因子研究预设: 周频, [5,10,20,40,60]d, QQQ+POOL_AVG 基准"""
    defaults = dict(
        market="us_stocks",
        computation_freq="W",
        forward_horizons=list(_US_HORIZONS),
        n_quantiles=5,
    )
    # 只在调用方没指定任何基准时才设默认值
    if "benchmark_symbol" not in overrides and "benchmark_symbols" not in overrides:
        defaults["benchmark_symbols"] = ["QQQ", "POOL_AVG"]
    defaults.update(overrides)
    return FactorStudyConfig(**defaults)


def crypto_factor_study(**overrides) -> FactorStudyConfig:
    """币圈因子研究预设: 日频, [1,3,5,7,14]d, BTCUSDT 基准"""
    defaults = dict(
        market="crypto",
        computation_freq="D",
        forward_horizons=list(_CRYPTO_HORIZONS),
        n_quantiles=5,
    )
    if "benchmark_symbol" not in overrides and "benchmark_symbols" not in overrides:
        defaults["benchmark_symbols"] = ["BTCUSDT"]
    defaults.update(overrides)
    return FactorStudyConfig(**defaults)
