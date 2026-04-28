"""Research-only helpers for offline event studies."""

from .daily_event_returns import (
    build_close_forward_return_matrices,
    build_prior_excess_return_matrix,
    build_t1open_excess_return_matrices,
)
from .rvol_signal_stats import (
    RVOLSignalStatResult,
    RVOLSignalStatsConfig,
    build_rvol_feature_frames,
    build_rvol_signal_buckets,
    build_symbol_date_index,
    run_bucket_event_stats,
)
from .rvol_deep_research import (
    ConditionalLiftResult,
    build_pmarp_rvol_lift_cohorts,
    build_strong_state_rvol_cohorts,
    comparison_pairs_from_cohorts,
    run_conditional_lift_comparisons,
)
from .event_path_diagnostics import (
    TailDiagnosticResult,
    run_tail_diagnostics,
)
from .rvol_event_explainers import (
    EventExplainerSummary,
    summarize_event_explainers,
)

__all__ = [
    "ConditionalLiftResult",
    "EventExplainerSummary",
    "RVOLSignalStatResult",
    "RVOLSignalStatsConfig",
    "TailDiagnosticResult",
    "build_close_forward_return_matrices",
    "build_pmarp_rvol_lift_cohorts",
    "build_prior_excess_return_matrix",
    "build_rvol_feature_frames",
    "build_rvol_signal_buckets",
    "build_strong_state_rvol_cohorts",
    "build_symbol_date_index",
    "build_t1open_excess_return_matrices",
    "comparison_pairs_from_cohorts",
    "run_bucket_event_stats",
    "run_conditional_lift_comparisons",
    "run_tail_diagnostics",
    "summarize_event_explainers",
]
