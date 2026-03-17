"""
单资产择时信号回测引擎

验证经典技术指标（MACD/RSI/MA交叉）作为择时信号是否有效：
严格按信号进出，与 buy-and-hold 对比。
"""

from backtest.timing.signals import (
    macd_signals,
    rsi_signals,
    ma_cross_signals,
    SIGNAL_REGISTRY,
)
from backtest.timing.engine import TimingResult, run_timing_backtest
from backtest.timing.runner import (
    TimingStudyConfig,
    AggregateResult,
    run_timing_study,
)

__all__ = [
    "macd_signals",
    "rsi_signals",
    "ma_cross_signals",
    "SIGNAL_REGISTRY",
    "TimingResult",
    "run_timing_backtest",
    "TimingStudyConfig",
    "AggregateResult",
    "run_timing_study",
]
