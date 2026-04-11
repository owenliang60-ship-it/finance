import pandas as pd

from backtest.pipeline.primitives.evaluation import EvaluationEngine, newey_west_tstat
from backtest.pipeline.primitives.execution import ExecutionEngine
from backtest.pipeline.primitives.pit_data import PitData
from backtest.pipeline.spec import StrategySpec
from tests.pipeline.helpers import seed_pipeline_dbs


def test_newey_west_tstat_returns_finite_values():
    t_stat, p_value = newey_west_tstat([0.01, 0.02, 0.015, 0.03, 0.01], lag=2)
    assert t_stat != 0.0
    assert 0.0 <= p_value <= 1.0


def test_evaluation_engine_returns_factor_and_strategy_metrics(tmp_path):
    market_db = tmp_path / "market.db"
    company_db = tmp_path / "company.db"
    seed_pipeline_dbs(market_db, company_db)

    pit = PitData(market_db, company_db)
    execution = ExecutionEngine(pit, initial_capital=100_000.0)

    target_is = pd.DataFrame(
        [{"AAA": 0.5, "BBB": 0.5}],
        index=["2024-08-12"],
    )
    target_oos = pd.DataFrame(
        [{"AAA": 0.5, "BBB": 0.5}],
        index=["2024-09-16"],
    )
    run_is = execution.run(target_is, "SPY", StrategySpec.from_dict({
        "spec_id": "tmp",
        "benchmark": "SPY",
        "universe": {"market_cap_min_usd": 1000000000, "min_names": 2},
        "factors": [{"name": "RS_Rating_B", "params": {}}],
        "combo": {"method": "single"},
        "portfolio": {"selection": "top_n", "top_n": 2, "rebalance": "weekly", "weighting": "equal"},
        "execution": {"timing": "next_open"},
        "period": {"start": "2024-08-01", "train_end": "2024-09-13", "test_end": "2024-10-31"},
    }).execution, "2024-08-01", "2024-09-13")
    run_oos = execution.run(target_oos, "SPY", StrategySpec.from_dict({
        "spec_id": "tmp",
        "benchmark": "SPY",
        "universe": {"market_cap_min_usd": 1000000000, "min_names": 2},
        "factors": [{"name": "RS_Rating_B", "params": {}}],
        "combo": {"method": "single"},
        "portfolio": {"selection": "top_n", "top_n": 2, "rebalance": "weekly", "weighting": "equal"},
        "execution": {"timing": "next_open"},
        "period": {"start": "2024-08-01", "train_end": "2024-09-13", "test_end": "2024-10-31"},
    }).execution, "2024-09-16", "2024-10-31")

    spec = StrategySpec.from_dict(
        {
            "spec_id": "eval_case",
            "benchmark": "SPY",
            "universe": {"market_cap_min_usd": 1000000000, "min_names": 2},
            "factors": [{"name": "RS_Rating_B", "params": {}}],
            "combo": {"method": "single"},
            "portfolio": {"selection": "top_n", "top_n": 2, "rebalance": "weekly", "weighting": "equal"},
            "execution": {"timing": "next_open"},
            "evaluation": {"newey_west_lag_days": 3},
            "period": {"start": "2024-08-01", "train_end": "2024-09-13", "test_end": "2024-10-31"},
        }
    )
    dates_is = ["2024-08-12", "2024-08-19", "2024-08-26", "2024-09-02", "2024-09-09"]
    dates_oos = ["2024-09-16", "2024-09-23", "2024-09-30", "2024-10-07", "2024-10-14"]
    score_rows = [{"AAA": 3.0, "BBB": 2.0, "CCC": 1.0} for _ in range(len(dates_is) + len(dates_oos))]
    combo_frame = pd.DataFrame(score_rows, index=dates_is + dates_oos)

    evaluation = EvaluationEngine(pit)
    output = evaluation.evaluate(
        spec=spec,
        factor_frames={"RS_Rating_B": combo_frame},
        combo_frame=combo_frame,
        run_is=run_is,
        run_oos=run_oos,
        warnings=[],
    )

    assert "factor" in output.metrics
    assert "strategy" in output.metrics
    assert output.metrics["factor"]["is"]["combo"]["primary_horizon"] == 5
    assert output.metrics["factor"]["is"]["combo"]["ic_mean"] > 0
    assert "annual_turnover_within_limit" in output.metrics["gates"]
    assert "excess_cagr" in output.metrics["strategy"]["oos"]


def test_gate_treats_positive_oos_as_pass_when_is_sharpe_is_negative():
    evaluation = EvaluationEngine(pit_data=None)
    gates = evaluation._build_gates(
        strategy_is={"sharpe_ratio": -0.1},
        strategy_oos={"sharpe_ratio": 0.8, "annual_turnover": 1.0},
        factor_oos={"ic_mean": 0.02},
        max_annual_turnover=2.0,
    )

    assert gates["oos_vs_is_sharpe_ratio_gte_0_5"]["pass"] is True
