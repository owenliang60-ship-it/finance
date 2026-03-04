"""
Tests for run artifact output store.
"""

import json

import pandas as pd

from factor_research_codex1.evaluation.slow_lane import BenchmarkComparison, CandidateSlowMetrics
from factor_research_codex1.reporting import MiningOutputStore


def _metric(cid: str) -> CandidateSlowMetrics:
    return CandidateSlowMetrics(
        candidate_id=cid,
        expression="ret_n(close, 20)",
        normalized_expression="ret_n(close, 20)",
        hypothesis_id="trend",
        source="unit",
        n_days=250,
        n_rebalances=20,
        n_trades=80,
        first_signal_date="2024-01-02",
        first_activation_date="2024-01-03",
        strategy_total_return=0.2,
        strategy_cagr=0.18,
        strategy_sharpe=1.3,
        strategy_max_drawdown=-0.2,
        strategy_annual_turnover=1.8,
        strategy_total_costs=5000.0,
        benchmark_comparisons=[
            BenchmarkComparison(
                benchmark="SPY",
                benchmark_total_return=0.1,
                benchmark_cagr=0.09,
                excess_total_return=0.1,
                excess_cagr=0.09,
                alpha=0.03,
                beta=0.95,
                information_ratio=0.8,
                tracking_error=0.12,
            )
        ],
        avg_excess_cagr=0.09,
        avg_information_ratio=0.8,
        quality_score=1.2,
    )


class TestMiningOutputStore:
    def test_save_run(self, tmp_path):
        store = MiningOutputStore(base_dir=tmp_path / "runs")
        fast_df = pd.DataFrame(
            [
                {"candidate_id": "A", "quality_score": 1.0},
                {"candidate_id": "B", "quality_score": 0.5},
            ]
        )
        slow_df = pd.DataFrame(
            [
                {"candidate_id": "A", "quality_score": 1.2},
            ]
        )
        metrics = [_metric("A"), _metric("B")]

        artifacts = store.save_run(
            fast_df=fast_df,
            slow_df=slow_df,
            slow_metrics=metrics,
            metadata={"stage": "test"},
            top_n_json=1,
        )

        assert artifacts.run_dir.exists()
        assert artifacts.fast_csv.exists()
        assert artifacts.slow_csv.exists()
        assert artifacts.metadata_json.exists()
        assert artifacts.top_json.exists()

        meta = json.loads(artifacts.metadata_json.read_text(encoding="utf-8"))
        assert meta["metadata"]["stage"] == "test"
        assert meta["fast_rows"] == 2
        assert meta["slow_rows"] == 1

        top = json.loads(artifacts.top_json.read_text(encoding="utf-8"))
        assert len(top) == 1
        assert top[0]["candidate_id"] == "A"
        assert "benchmarks" in top[0]

