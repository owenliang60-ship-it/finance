"""Structural mode tests: sentinel param assertion + mutation guard behavior."""

import json
from types import SimpleNamespace

from forge import common, evaluator


def test_structural_mode_uses_champion_params_not_defaults(tmp_path, monkeypatch):
    """Sentinel test: evaluator must use champion_params.json values, not StrategyConfig defaults.

    Strategy has StrategyConfig(fast_period=20) as default.
    champion_params.json sets fast_period=99 (sentinel).
    If evaluator uses defaults, fast_period=20. If it uses params file, fast_period=99.
    """
    strategy_code = '''
from dataclasses import dataclass

@dataclass(frozen=True)
class StrategyConfig:
    fast_period: int = 20
    slow_period: int = 50

def run_backtest(symbol, price_4h_df=None, price_daily_df=None, config=None, **kwargs):
    config = config or StrategyConfig()
    return None
'''
    strategy_path = tmp_path / "champion.py"
    strategy_path.write_text(strategy_code, encoding="utf-8")

    # Sentinel: params file has fast_period=99, different from default 20
    params_path = tmp_path / "champion_params.json"
    params_path.write_text(json.dumps({"fast_period": 99, "slow_period": 50}), encoding="utf-8")

    campaign_path = tmp_path / "campaign.lock.json"
    campaign_path.write_text(
        json.dumps(
            {
                "campaign_id": "sentinel_test",
                "strategy_name": "champion",
                "symbol": "BTCUSDT",
                "interval": "4h",
                "data_dir": "../data/crypto",
                "data_snapshot_hash": "",
                "visible_windows": [{"name": "A", "start": "2020-01-01", "end": "2020-12-31"}],
                "holdout_window": {"name": "holdout", "start": "2021-01-01", "end": "2021-12-31"},
                "transaction_cost_bps": 10.0,
                "rebalance_dead_zone_pct": 5.0,
                "days_per_year": 2190,
                "gate_max_mdd": -0.55,
                "gate_min_exposure": 0.01,
                "parameter_surface_manifest": "manifests/test.json",
            }
        ),
        encoding="utf-8",
    )

    # Capture the config that evaluator passes to run_backtest
    captured_configs = []
    original_dynamic_import = common.dynamic_import

    def capturing_import(path, **kwargs):
        mod = original_dynamic_import(path, **kwargs)
        original_run = mod.run_backtest

        def capturing_run(symbol, config=None, **kw):
            captured_configs.append(config)
            return object()  # non-None placeholder; trim is mocked

        mod.run_backtest = capturing_run
        return mod

    monkeypatch.setattr(evaluator, "compute_data_hash", lambda *a, **kw: "hash")
    monkeypatch.setattr(evaluator, "_load_price_frames", lambda *a, **kw: (None, None))
    monkeypatch.setattr(common, "dynamic_import", capturing_import)
    monkeypatch.setattr(
        evaluator,
        "trim_continuous_result_window",
        lambda *a, **kw: SimpleNamespace(
            strategy_metrics=SimpleNamespace(cagr=0.1, max_drawdown=-0.2, sharpe_ratio=0.5),
            buyhold_metrics=SimpleNamespace(cagr=0.05),
            excess_cagr=0.05,
            mean_exposure=0.3,
            n_rebalances=1,
        ),
    )

    # Evaluate with params_path — simulates what runner does in both parameter AND structural mode
    result = evaluator.evaluate(campaign_path, strategy_path, params_path=params_path)

    assert result.status != "ERROR", f"Unexpected error: {result.error_message}"
    # The sentinel assertion: config must have fast_period=99, not the default 20
    assert len(captured_configs) == 2  # visible + holdout
    for config in captured_configs:
        assert config.fast_period == 99, (
            f"Expected sentinel value 99, got {config.fast_period}. "
            "Evaluator is using StrategyConfig defaults instead of params file!"
        )
