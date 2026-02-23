"""
RS 动量回测引擎 — 横截面动量选股回测框架

支持美股和币安合约两个市场，复用现有 RS 计算核心，
提供参数扫描、稳健性分析和 Walk-Forward 验证。
"""

from backtest.config import BacktestConfig, us_preset, crypto_preset
from backtest.engine import BacktestEngine
from backtest.portfolio import PortfolioState
from backtest.metrics import compute_metrics
from backtest.rebalancer import Rebalancer
from backtest.sweep import ParameterSweep

__all__ = [
    "BacktestConfig",
    "us_preset",
    "crypto_preset",
    "BacktestEngine",
    "PortfolioState",
    "compute_metrics",
    "Rebalancer",
    "ParameterSweep",
]
