"""
单资产择时信号回测引擎

验证经典技术指标（MACD/RSI/MA交叉）作为择时信号是否有效：
严格按信号进出，与 buy-and-hold 对比。
"""

from backtest.timing.signals import (
    macd_signals,
    rsi_signals,
    ma_cross_signals,
    new_high_signals,
    vix_ma_signals,
    vix_spike_signals,
    vix_percentile_signals,
    vix_rsi_signals,
    vix_spike_hold_signals,
    vix_spike_revert_signals,
    SIGNAL_REGISTRY,
)
from backtest.timing.engine import TimingResult, run_timing_backtest
from backtest.timing.continuous_engine import (
    ContinuousTimingResult,
    run_continuous_backtest,
    trim_continuous_result_window,
    window_slice,
)
from backtest.timing.dual_engine_backtest import (
    DualEngineBacktestResult,
    run_dual_engine_backtest,
)
from backtest.timing.runner import (
    TimingStudyConfig,
    AggregateResult,
    run_timing_study,
)

__all__ = [
    "macd_signals",
    "rsi_signals",
    "ma_cross_signals",
    "new_high_signals",
    "vix_ma_signals",
    "vix_spike_signals",
    "vix_percentile_signals",
    "vix_rsi_signals",
    "vix_spike_hold_signals",
    "vix_spike_revert_signals",
    "SIGNAL_REGISTRY",
    "TimingResult",
    "run_timing_backtest",
    "ContinuousTimingResult",
    "run_continuous_backtest",
    "DualEngineBacktestResult",
    "run_dual_engine_backtest",
    "trim_continuous_result_window",
    "window_slice",
    "TimingStudyConfig",
    "AggregateResult",
    "run_timing_study",
]
