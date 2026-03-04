"""
Evaluation layer for Factor Research Codex 1.0.
"""

from factor_research_codex1.evaluation.expression_engine import DSLExpressionEngine, ExpressionEvalResult
from factor_research_codex1.evaluation.fast_lane import (
    CandidateFastMetrics,
    FastLaneConfig,
    FastLaneEvaluator,
)
from factor_research_codex1.evaluation.slow_lane import (
    BenchmarkComparison,
    CandidateSlowMetrics,
    SlowLaneConfig,
    SlowLaneEvaluator,
)

__all__ = [
    "DSLExpressionEngine",
    "ExpressionEvalResult",
    "CandidateFastMetrics",
    "FastLaneConfig",
    "FastLaneEvaluator",
    "BenchmarkComparison",
    "CandidateSlowMetrics",
    "SlowLaneConfig",
    "SlowLaneEvaluator",
]
