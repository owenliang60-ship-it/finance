"""
Tests for candidate registry state machine and persistence.
"""

from pathlib import Path

from factor_research_codex1.evaluation.slow_lane import BenchmarkComparison, CandidateSlowMetrics
from factor_research_codex1.registry import CandidateRegistry, RegistryRules


def _make_metric(
    cid: str,
    excess_cagr: float,
    avg_ir: float,
    mdd: float,
) -> CandidateSlowMetrics:
    return CandidateSlowMetrics(
        candidate_id=cid,
        expression="ret_n(close, 20)",
        normalized_expression="ret_n(close, 20)",
        hypothesis_id="trend",
        source="unit",
        n_days=252,
        n_rebalances=20,
        n_trades=120,
        first_signal_date="2024-01-02",
        first_activation_date="2024-01-03",
        strategy_total_return=0.25,
        strategy_cagr=0.22,
        strategy_sharpe=1.4,
        strategy_max_drawdown=mdd,
        strategy_annual_turnover=2.1,
        strategy_total_costs=12000.0,
        benchmark_comparisons=[
            BenchmarkComparison(
                benchmark="SPY",
                benchmark_total_return=0.12,
                benchmark_cagr=0.11,
                excess_total_return=0.13,
                excess_cagr=excess_cagr,
                alpha=0.05,
                beta=0.9,
                information_ratio=avg_ir,
                tracking_error=0.1,
            )
        ],
        avg_excess_cagr=excess_cagr,
        avg_information_ratio=avg_ir,
        quality_score=1.0,
    )


class TestCandidateRegistry:
    def test_classification_and_counts(self, tmp_path):
        reg_path = tmp_path / "registry.json"
        registry = CandidateRegistry(path=reg_path)
        rules = RegistryRules(
            min_approved_excess_cagr=0.03,
            min_approved_ir=0.4,
            max_approved_drawdown=0.35,
            min_watchlist_excess_cagr=0.0,
            min_watchlist_ir=0.1,
        )

        metrics = [
            _make_metric("A", excess_cagr=0.08, avg_ir=0.7, mdd=-0.20),   # approved
            _make_metric("B", excess_cagr=0.01, avg_ir=0.2, mdd=-0.40),   # watchlist
            _make_metric("C", excess_cagr=-0.02, avg_ir=-0.1, mdd=-0.30), # rejected
        ]
        registry.upsert_from_slow_metrics(metrics, rules=rules)
        counts = registry.counts()
        assert counts["approved"] == 1
        assert counts["watchlist"] == 1
        assert counts["rejected"] == 1

    def test_persistence_reload(self, tmp_path):
        reg_path = tmp_path / "registry.json"
        registry = CandidateRegistry(path=reg_path)
        registry.upsert_from_slow_metrics([_make_metric("A", 0.08, 0.7, -0.2)])
        path = registry.save()
        assert path.exists()

        reloaded = CandidateRegistry(path=reg_path)
        records = reloaded.records()
        assert len(records) == 1
        assert records[0].candidate_id == "A"
        assert records[0].status in {"approved", "watchlist", "rejected"}

