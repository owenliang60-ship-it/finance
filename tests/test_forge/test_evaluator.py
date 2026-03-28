import json
from pathlib import Path
from types import SimpleNamespace

from forge import common
from forge import evaluator


def _make_fake_strategy_module(ema_period_tracker=None):
    """Create a mock strategy module implementing the generic contract."""

    class FakeConfig:
        def __init__(self, **kwargs):
            self.ema_period = kwargs.get("ema_period", 144)
            for k, v in kwargs.items():
                setattr(self, k, v)

    def fake_run_backtest(symbol, price_4h_df, price_daily_df, config=None, **kwargs):
        if ema_period_tracker is not None and config is not None:
            ema_period_tracker.append(config.ema_period)
        return object()  # will be replaced by trim mock

    mod = SimpleNamespace(
        StrategyConfig=FakeConfig,
        run_backtest=fake_run_backtest,
    )
    return mod


def _make_campaign(tmp_path, gate_max_mdd=-0.55):
    campaign_path = tmp_path / "campaign.lock.json"
    campaign_path.write_text(
        json.dumps(
            {
                "campaign_id": "test",
                "strategy_name": "test_strategy",
                "symbol": "BTCUSDT",
                "interval": "4h",
                "data_dir": "../data/crypto",
                "data_snapshot_hash": "",
                "visible_windows": [{"name": "A", "start": "2020-01-01", "end": "2020-12-31"}],
                "holdout_window": {"name": "holdout", "start": "2021-01-01", "end": "2021-12-31"},
                "transaction_cost_bps": 10.0,
                "rebalance_dead_zone_pct": 5.0,
                "days_per_year": 2190,
                "gate_max_mdd": gate_max_mdd,
                "gate_min_exposure": 0.2,
                "parameter_surface_manifest": "manifests/test_surface.json",
            }
        ),
        encoding="utf-8",
    )
    return campaign_path


def _mock_trim(*args, **kwargs):
    return SimpleNamespace(
        strategy_metrics=SimpleNamespace(cagr=0.2, max_drawdown=-0.3, sharpe_ratio=1.1),
        buyhold_metrics=SimpleNamespace(cagr=0.1),
        excess_cagr=0.1,
        mean_exposure=0.4,
        n_rebalances=2,
    )


def test_evaluator_uses_params_override_and_hashes_extra_artifacts(tmp_path, monkeypatch):
    strategy_path = common.get_strategy_paths("helen").champion_path
    params_path = tmp_path / "candidate_params.json"
    params_path.write_text(json.dumps({"ema_period": 200}), encoding="utf-8")
    campaign_path = _make_campaign(tmp_path)

    observed_ema_periods = []
    fake_mod = _make_fake_strategy_module(ema_period_tracker=observed_ema_periods)

    monkeypatch.setattr(evaluator, "compute_data_hash", lambda *args, **kwargs: "hash")
    monkeypatch.setattr(evaluator, "_load_price_frames", lambda *args, **kwargs: (None, None))
    monkeypatch.setattr(common, "dynamic_import", lambda *args, **kwargs: fake_mod)
    monkeypatch.setattr(evaluator, "trim_continuous_result_window", _mock_trim)

    result = evaluator.evaluate(campaign_path, strategy_path, params_path=params_path)

    assert result.status == "PASS"
    assert observed_ema_periods == [200, 200]
    # Calmar score = excess_cagr / |mdd| = 0.1 / 0.3
    assert abs(result.visible_score - 0.1 / 0.3) < 1e-9
    assert result.strategy_hash != common.hash_files([strategy_path])


def test_evaluator_zeroes_visible_score_on_fail_gate(tmp_path, monkeypatch):
    strategy_path = common.get_strategy_paths("helen").champion_path
    campaign_path = _make_campaign(tmp_path, gate_max_mdd=-0.2)

    fake_mod = _make_fake_strategy_module()

    monkeypatch.setattr(evaluator, "compute_data_hash", lambda *args, **kwargs: "hash")
    monkeypatch.setattr(evaluator, "_load_price_frames", lambda *args, **kwargs: (None, None))
    monkeypatch.setattr(common, "dynamic_import", lambda *args, **kwargs: fake_mod)
    monkeypatch.setattr(evaluator, "trim_continuous_result_window", _mock_trim)

    result = evaluator.evaluate(campaign_path, strategy_path)

    assert result.status == "FAIL_GATE"
    assert result.visible_score == 0.0


def test_validate_strategy_exports_rejects_missing():
    mod = SimpleNamespace(StrategyConfig=object)  # missing run_backtest
    try:
        evaluator._validate_strategy_exports(mod)
        assert False, "Should have raised"
    except AttributeError as exc:
        assert "run_backtest" in str(exc)


def test_validate_strategy_exports_accepts_valid():
    mod = SimpleNamespace(StrategyConfig=object, run_backtest=lambda: None)
    evaluator._validate_strategy_exports(mod)  # should not raise
