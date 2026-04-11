from pathlib import Path

import pytest

from backtest.pipeline.spec import SpecValidationError, StrategySpec


def _write_spec(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "spec.yaml"
    path.write_text(text, encoding="utf-8")
    return path


def test_single_factor_spec_parses(tmp_path):
    path = _write_spec(
        tmp_path,
        """
spec_id: "single_factor"
benchmark: "SPY"
universe:
  market_cap_min_usd: 10000000000
factors:
  - name: "RS_Rating_B"
    params: {}
    transform: "rank_pct"
combo:
  method: "single"
portfolio:
  selection: "top_n"
  top_n: 10
  rebalance: "weekly"
  weighting: "equal"
  max_position_weight: 0.2
execution:
  timing: "next_open"
  transaction_cost_bps: 5.0
period:
  start: "2020-01-01"
  train_end: "2023-12-31"
  test_end: "2024-12-31"
        """.strip(),
    )

    spec = StrategySpec.from_yaml(path)
    assert spec.spec_id == "single_factor"
    assert spec.combo.method == "single"
    assert len(spec.factors) == 1


def test_three_factor_spec_parses(tmp_path):
    path = _write_spec(
        tmp_path,
        """
spec_id: "three_factor"
benchmark: "SPY"
universe:
  market_cap_min_usd: 10000000000
  exclude_sectors: ["Energy"]
  min_names: 30
factors:
  - name: "RS_Rating_B"
    params: {}
    transform: "rank_pct"
    weight: 0.5
  - name: "PMARP"
    params: {"ema_period": 20, "lookback": 150}
    transform: "zscore"
    weight: 0.3
  - name: "Attention_ZScore"
    params: {}
    transform: "zscore"
    weight: 0.2
combo:
  method: "weighted_sum"
portfolio:
  selection: "top_n"
  top_n: 10
  rebalance: "monthly_first_trading_day"
  weighting: "inv_vol"
  vol_lookback_days: 60
  max_position_weight: 0.15
execution:
  timing: "next_open"
  transaction_cost_bps: 5.0
period:
  start: "2020-01-01"
  train_end: "2023-12-31"
  test_end: "2024-12-31"
        """.strip(),
    )

    spec = StrategySpec.from_yaml(path)
    assert len(spec.factors) == 3
    assert spec.portfolio.weighting == "inv_vol"
    assert spec.resolved_newey_west_lag_days() == 21


def test_single_combo_with_multiple_factors_rejected(tmp_path):
    path = _write_spec(
        tmp_path,
        """
spec_id: "bad_single"
benchmark: "SPY"
universe:
  market_cap_min_usd: 10000000000
factors:
  - name: "RS_Rating_B"
    params: {}
  - name: "PMARP"
    params: {}
combo:
  method: "single"
portfolio:
  selection: "top_n"
  top_n: 10
  rebalance: "weekly"
  weighting: "equal"
  max_position_weight: 0.2
execution:
  timing: "next_open"
  transaction_cost_bps: 5.0
period:
  start: "2020-01-01"
  train_end: "2023-12-31"
  test_end: "2024-12-31"
        """.strip(),
    )

    with pytest.raises(SpecValidationError):
        StrategySpec.from_yaml(path)


def test_inv_vol_requires_positive_lookback(tmp_path):
    path = _write_spec(
        tmp_path,
        """
spec_id: "bad_inv_vol"
benchmark: "SPY"
universe:
  market_cap_min_usd: 10000000000
factors:
  - name: "RS_Rating_B"
    params: {}
combo:
  method: "single"
portfolio:
  selection: "top_n"
  top_n: 10
  rebalance: "weekly"
  weighting: "inv_vol"
  vol_lookback_days: 0
  max_position_weight: 0.2
execution:
  timing: "next_open"
  transaction_cost_bps: 5.0
period:
  start: "2020-01-01"
  train_end: "2023-12-31"
  test_end: "2024-12-31"
        """.strip(),
    )

    with pytest.raises(SpecValidationError):
        StrategySpec.from_yaml(path)


def test_only_next_open_supported(tmp_path):
    path = _write_spec(
        tmp_path,
        """
spec_id: "bad_timing"
benchmark: "SPY"
universe:
  market_cap_min_usd: 10000000000
factors:
  - name: "RS_Rating_B"
    params: {}
combo:
  method: "single"
portfolio:
  selection: "top_n"
  top_n: 10
  rebalance: "weekly"
  weighting: "equal"
  max_position_weight: 0.2
execution:
  timing: "same_close"
  transaction_cost_bps: 5.0
period:
  start: "2020-01-01"
  train_end: "2023-12-31"
  test_end: "2024-12-31"
        """.strip(),
    )

    with pytest.raises(SpecValidationError):
        StrategySpec.from_yaml(path)
