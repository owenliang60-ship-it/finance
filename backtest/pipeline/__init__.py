"""
Backtest Pipeline V3.

Focused factor-validation pipeline for US equity cross-sectional strategies.
"""

from backtest.pipeline.runner import PipelineRunner
from backtest.pipeline.spec import StrategySpec

__all__ = ["PipelineRunner", "StrategySpec"]
