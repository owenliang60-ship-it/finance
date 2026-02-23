"""
因子有效性研究框架 (Factor Study Framework)

双轨分析:
- Track 1: 连续 IC 分析 (因子整体预测力)
- Track 2: 离散事件研究 (信号有效性)

用法:
    from backtest.config import us_factor_study
    from backtest.adapters.us_stocks import USStocksAdapter
    from backtest.factor_study import FactorStudyRunner, get_factor

    config = us_factor_study()
    adapter = USStocksAdapter()
    runner = FactorStudyRunner(config, adapter)
    runner.add_factor(get_factor("RS_Rating_B"))
    results = runner.run()
"""

from backtest.factor_study.protocol import Factor, FactorMeta
from backtest.factor_study.factors import get_factor, list_factors, ALL_FACTORS
from backtest.factor_study.signals import SignalType, SignalDefinition, detect_signals
from backtest.factor_study.forward_returns import build_return_matrix
from backtest.factor_study.ic_analysis import ICResult, ICDecayCurve, analyze_ic
from backtest.factor_study.event_study import EventStudyResult, run_event_study
from backtest.factor_study.sweep import get_default_sweep, build_custom_sweep
from backtest.factor_study.runner import FactorStudyRunner, FactorStudyResults
from backtest.factor_study.report import (
    print_results,
    export_csv,
    generate_html_report,
    save_html_report,
)

__all__ = [
    # Protocol
    "Factor",
    "FactorMeta",
    # Factors
    "get_factor",
    "list_factors",
    "ALL_FACTORS",
    # Signals
    "SignalType",
    "SignalDefinition",
    "detect_signals",
    # Forward Returns
    "build_return_matrix",
    # IC Analysis
    "ICResult",
    "ICDecayCurve",
    "analyze_ic",
    # Event Study
    "EventStudyResult",
    "run_event_study",
    # Sweep
    "get_default_sweep",
    "build_custom_sweep",
    # Runner
    "FactorStudyRunner",
    "FactorStudyResults",
    # Report
    "print_results",
    "export_csv",
    "generate_html_report",
    "save_html_report",
]
