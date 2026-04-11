from pathlib import Path

from backtest.pipeline.runner import PipelineRunner
from tests.pipeline.helpers import seed_pipeline_dbs


def _write_spec(path: Path, body: str) -> Path:
    path.write_text(body.strip(), encoding="utf-8")
    return path


def test_e2e_single_factor_pipeline(tmp_path):
    market_db = tmp_path / "market.db"
    company_db = tmp_path / "company.db"
    seed_pipeline_dbs(market_db, company_db)

    spec_path = _write_spec(
        tmp_path / "single.yaml",
        """
spec_id: "single_factor_e2e"
benchmark: "SPY"
universe:
  market_cap_min_usd: 1000000000
  min_names: 2
factors:
  - name: "RS_Rating_B"
    params: {}
    transform: "raw"
combo:
  method: "single"
portfolio:
  selection: "top_n"
  top_n: 2
  rebalance: "weekly"
  weighting: "equal"
execution:
  timing: "next_open"
  transaction_cost_bps: 5.0
period:
  start: "2024-06-03"
  train_end: "2024-09-13"
  test_end: "2024-10-31"
        """,
    )
    runner = PipelineRunner(
        spec_path,
        artifact_root=tmp_path / "reports",
        market_db_path=market_db,
        company_db_path=company_db,
    )
    result = runner.run()

    for name in (
        "signals_is",
        "signals_oos",
        "nav_is",
        "nav_oos",
        "metrics_json",
        "report_md",
        "report_html",
    ):
        assert result.output_paths[name].exists()
    assert result.metrics["strategy"]["is"]["n_days"] > 0
    assert result.metrics["factor"]["is"]["combo"]["primary_horizon"] == 5


def test_e2e_three_factor_pipeline(tmp_path):
    market_db = tmp_path / "market.db"
    company_db = tmp_path / "company.db"
    seed_pipeline_dbs(market_db, company_db)

    spec_path = _write_spec(
        tmp_path / "combo.yaml",
        """
spec_id: "combo_factor_e2e"
benchmark: "SPY"
universe:
  market_cap_min_usd: 1000000000
  min_names: 2
factors:
  - name: "RS_Rating_B"
    params: {}
    transform: "rank_pct"
    weight: 0.4
  - name: "PMARP"
    params: {}
    transform: "rank_pct"
    weight: 0.4
  - name: "Attention_ZScore"
    params: {}
    transform: "rank_pct"
    weight: 0.2
combo:
  method: "weighted_sum"
portfolio:
  selection: "top_n"
  top_n: 2
  rebalance: "weekly"
  weighting: "inv_vol"
  vol_lookback_days: 60
execution:
  timing: "next_open"
  transaction_cost_bps: 5.0
period:
  start: "2024-09-02"
  train_end: "2024-10-03"
  test_end: "2024-10-31"
        """,
    )
    runner = PipelineRunner(
        spec_path,
        artifact_root=tmp_path / "reports",
        market_db_path=market_db,
        company_db_path=company_db,
    )
    result = runner.run()

    assert not result.signals_is.empty
    assert not result.signals_oos.empty
    assert result.metrics["factor"]["oos"]["combo"]["primary_horizon"] == 5
    assert "oos_vs_is_sharpe_ratio_gte_0_5" in result.metrics["gates"]
