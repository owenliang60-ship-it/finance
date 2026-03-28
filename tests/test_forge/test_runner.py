import json
from pathlib import Path

from forge import common, runner


STRATEGY_CODE = """
from dataclasses import dataclass


@dataclass(frozen=True)
class StrategyConfig:
    fast_period: int = 20
    slow_period: int = 50


def run_backtest(symbol, price_4h_df=None, price_daily_df=None, config=None, **kwargs):
    config = config or StrategyConfig()
    return None
"""


def _build_temp_forge_tree(tmp_path: Path):
    forge_root = tmp_path / "forge"
    (forge_root / "strategies").mkdir(parents=True)
    (forge_root / "manifests").mkdir(parents=True)
    (forge_root / "logs").mkdir(parents=True)

    for rel_path, content in {
        "runner.py": "# placeholder\n",
        "evaluator.py": "# placeholder\n",
        "forge.md": "# prompt\n",
        "campaign.lock.json": json.dumps(
            {
                "campaign_id": "test",
                "parameter_surface_manifest": "manifests/test_surface.json",
            }
        ),
        "manifests/test_surface.json": json.dumps(
            {
                "parameters": {
                    "fast_period": {"type": "int", "range": [5, 100], "step": 5, "default": 20},
                    "slow_period": {"type": "int", "range": [20, 500], "step": 10, "default": 50},
                }
            }
        ),
        "strategies/test_champion.py": STRATEGY_CODE,
        "strategies/test_candidate.py": STRATEGY_CODE,
        "strategies/test_champion_params.json": json.dumps(
            {"fast_period": 20, "slow_period": 50}
        ),
        "strategies/test_candidate_params.json": json.dumps(
            {"fast_period": 20, "slow_period": 50}
        ),
    }.items():
        target = forge_root / rel_path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")

    return forge_root


def test_extract_hypothesis_reads_first_tagged_line():
    output = "note\nHYPOTHESIS: raise ema to reduce whipsaw\nmore"
    assert runner._extract_hypothesis(output) == "raise ema to reduce whipsaw"


def test_parse_claude_cli_payload_reads_json_result():
    payload = runner._parse_claude_cli_payload(
        '{"type":"result","is_error":false,"result":"HYPOTHESIS: test"}'
    )

    assert payload is not None
    assert payload["result"] == "HYPOTHESIS: test"


def test_extract_claude_error_prefers_json_result():
    payload = {
        "type": "result",
        "is_error": True,
        "result": "Failed to authenticate. OAuth token has expired.",
    }

    message = runner._extract_claude_error(payload, "")

    assert message == "Failed to authenticate. OAuth token has expired."


def test_mutation_guard_accepts_json_only_param_changes(tmp_path, monkeypatch):
    forge_root = _build_temp_forge_tree(tmp_path)
    monkeypatch.setattr(common, "FORGE_ROOT", forge_root)

    campaign_path = forge_root / "campaign.lock.json"
    campaign = common.load_json(campaign_path)
    paths = common.get_strategy_paths("test")

    candidate_params = common.load_json(paths.candidate_params_path)
    candidate_params["fast_period"] = 30
    paths.candidate_params_path.write_text(json.dumps(candidate_params), encoding="utf-8")

    runner._IMMUTABLE_GUARD_HASHES = runner._capture_immutable_hashes(campaign, campaign_path, paths)
    passed, reason = runner._mutation_guard("test", campaign, "parameter")

    assert passed is True
    assert reason == "ok"


def test_mutation_guard_rejects_python_change_in_parameter_mode(tmp_path, monkeypatch):
    forge_root = _build_temp_forge_tree(tmp_path)
    monkeypatch.setattr(common, "FORGE_ROOT", forge_root)

    campaign_path = forge_root / "campaign.lock.json"
    campaign = common.load_json(campaign_path)
    paths = common.get_strategy_paths("test")

    runner._IMMUTABLE_GUARD_HASHES = runner._capture_immutable_hashes(campaign, campaign_path, paths)
    paths.candidate_path.write_text(STRATEGY_CODE + "\n# changed\n", encoding="utf-8")

    passed, reason = runner._mutation_guard("test", campaign, "parameter")

    assert passed is False
    assert "byte-identical" in reason


def test_mutation_guard_structural_allows_code_change(tmp_path, monkeypatch):
    """Structural mode allows candidate.py changes if exports preserved."""
    forge_root = _build_temp_forge_tree(tmp_path)
    monkeypatch.setattr(common, "FORGE_ROOT", forge_root)

    campaign_path = forge_root / "campaign.lock.json"
    campaign = common.load_json(campaign_path)
    paths = common.get_strategy_paths("test")

    runner._IMMUTABLE_GUARD_HASHES = runner._capture_immutable_hashes(campaign, campaign_path, paths)
    # Modify candidate code but keep exports
    paths.candidate_path.write_text(STRATEGY_CODE + "\n# structural change\n", encoding="utf-8")

    passed, reason = runner._mutation_guard("test", campaign, "structural")

    assert passed is True
    assert reason == "ok"


def test_mutation_guard_structural_rejects_missing_exports(tmp_path, monkeypatch):
    """Structural mode rejects candidate missing required exports."""
    forge_root = _build_temp_forge_tree(tmp_path)
    monkeypatch.setattr(common, "FORGE_ROOT", forge_root)

    campaign_path = forge_root / "campaign.lock.json"
    campaign = common.load_json(campaign_path)
    paths = common.get_strategy_paths("test")

    runner._IMMUTABLE_GUARD_HASHES = runner._capture_immutable_hashes(campaign, campaign_path, paths)
    # Write candidate missing run_backtest
    paths.candidate_path.write_text(
        "from dataclasses import dataclass\n\n@dataclass(frozen=True)\nclass StrategyConfig:\n    x: int = 1\n",
        encoding="utf-8",
    )

    passed, reason = runner._mutation_guard("test", campaign, "structural")

    assert passed is False
    assert "run_backtest" in reason


def test_mutation_guard_structural_rejects_params_change(tmp_path, monkeypatch):
    """Structural mode rejects changes to candidate_params.json."""
    forge_root = _build_temp_forge_tree(tmp_path)
    monkeypatch.setattr(common, "FORGE_ROOT", forge_root)

    campaign_path = forge_root / "campaign.lock.json"
    campaign = common.load_json(campaign_path)
    paths = common.get_strategy_paths("test")

    runner._IMMUTABLE_GUARD_HASHES = runner._capture_immutable_hashes(campaign, campaign_path, paths)
    # Modify params in structural mode — should be rejected
    paths.candidate_params_path.write_text(
        json.dumps({"fast_period": 99, "slow_period": 50}), encoding="utf-8"
    )

    passed, reason = runner._mutation_guard("test", campaign, "structural")

    assert passed is False
    assert "structural mode requires candidate_params.json to match champion" in reason


def test_check_stop_rules_ignores_rejected_candidate_holdout():
    campaign = {
        "max_rounds": 50,
        "stale_stop_rounds": 20,
        "holdout_meltdown_threshold": -0.15,
    }

    should_stop, reason = runner._check_stop_rules(
        campaign=campaign,
        round_num=3,
        stale_count=0,
        initial_holdout_baseline=0.2,
        current_holdout=None,
    )

    assert should_stop is False
    assert reason == ""
