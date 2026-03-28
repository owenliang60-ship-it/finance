"""Forge control plane for iterative strategy optimization."""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from forge import common
from forge.evaluator import WindowResult, evaluate


_IMMUTABLE_GUARD_HASHES: dict[Path, str] = {}


@dataclass
class RoundResult:
    """Single optimization round output."""

    round_num: int
    level: str
    strategy_name: str
    hypothesis: str
    status: str
    visible_score: float
    best_visible_score: float
    accepted: bool
    strategy_hash: str
    reason: str = ""
    window_results: list[WindowResult] = field(default_factory=list)
    holdout_excess_cagr: Optional[float] = None
    holdout_mdd: Optional[float] = None


def run_campaign(
    strategy_name: str = "helen",
    campaign_path: Optional[Path] = None,
    max_rounds: Optional[int] = None,
) -> None:
    """Run the Forge optimization loop."""
    global _IMMUTABLE_GUARD_HASHES

    campaign_path = common.resolve_campaign_path(campaign_path)
    campaign = common.load_json(campaign_path)
    strategy_name = strategy_name or campaign["strategy_name"]
    paths = common.get_strategy_paths(strategy_name)
    forge_md = (common.FORGE_ROOT / "forge.md").read_text(encoding="utf-8")
    campaign_id = campaign["campaign_id"]
    public_log = common.FORGE_ROOT / "logs" / campaign_id / "experiments_public.tsv"
    private_log = common.FORGE_ROOT / "logs" / campaign_id / "experiments_private.jsonl"
    private_buffer: list[dict] = []  # holdout data stays in memory until campaign ends
    effective_max_rounds = int(max_rounds or campaign["max_rounds"])

    champion_result = evaluate(
        campaign_path,
        paths.champion_path,
        params_path=paths.champion_params_path if paths.champion_params_path.exists() else None,
    )
    if champion_result.status != "PASS" or champion_result.holdout is None:
        raise RuntimeError(
            "Champion baseline must pass before starting Forge: "
            f"{champion_result.status} {champion_result.error_message}"
        )

    best_score = champion_result.visible_score
    initial_holdout_baseline = champion_result.holdout.excess_cagr
    stale_count = 0
    error_count = 0

    for round_num in range(1, effective_max_rounds + 1):
        level = _determine_level(campaign, stale_count, error_count)
        _copy_champion_to_candidate(strategy_name)
        _IMMUTABLE_GUARD_HASHES = _capture_immutable_hashes(campaign, campaign_path, paths)

        workspace_baseline = _capture_workspace_state()
        public_log_tail = common.read_last_n_lines(public_log, 20)
        champion_code = paths.champion_path.read_text(encoding="utf-8")
        try:
            agent_output = _invoke_agent(
                strategy_name=strategy_name,
                forge_md=forge_md,
                champion_code=champion_code,
                public_log_tail=public_log_tail,
                best_score=best_score,
                current_level=level,
            )
            hypothesis = _extract_hypothesis(agent_output)
        except Exception as exc:
            hypothesis = "agent_invocation_failed"
            round_result = RoundResult(
                round_num=round_num,
                level=level,
                strategy_name=strategy_name,
                hypothesis=hypothesis,
                status="ERROR",
                visible_score=0.0,
                best_visible_score=best_score,
                accepted=False,
                strategy_hash="",
                reason=str(exc),
            )
            _write_public_log(round_result, public_log)
            private_buffer.append(_make_private_record(round_result, campaign))
            _discard_candidate(strategy_name)
            error_count += 1
            continue

        # Check workspace isolation: agent must only modify candidate files
        ws_ok, ws_reason = _check_workspace_isolation(workspace_baseline, strategy_name)
        if not ws_ok:
            round_result = RoundResult(
                round_num=round_num,
                level=level,
                strategy_name=strategy_name,
                hypothesis=hypothesis,
                status="FAIL_GUARD",
                visible_score=0.0,
                best_visible_score=best_score,
                accepted=False,
                strategy_hash="",
                reason=ws_reason,
            )
            _write_public_log(round_result, public_log)
            private_buffer.append(_make_private_record(round_result, campaign))
            _discard_candidate(strategy_name)
            error_count += 1
            continue

        passed, reason = _mutation_guard(strategy_name, campaign, level)
        if not passed:
            round_result = RoundResult(
                round_num=round_num,
                level=level,
                strategy_name=strategy_name,
                hypothesis=hypothesis,
                status="FAIL_GUARD",
                visible_score=0.0,
                best_visible_score=best_score,
                accepted=False,
                strategy_hash=common.hash_files(
                    [
                        paths.candidate_path,
                        paths.candidate_params_path,
                    ]
                ),
                reason=reason,
            )
            _write_public_log(round_result, public_log)
            private_buffer.append(_make_private_record(round_result, campaign))
            _discard_candidate(strategy_name)
            error_count += 1
            if error_count >= 5:
                print(f"WARNING: {error_count} consecutive failures")
            continue

        params_path = paths.candidate_params_path
        candidate_result = evaluate(campaign_path, paths.candidate_path, params_path=params_path)
        if candidate_result.status == "ERROR":
            round_result = RoundResult(
                round_num=round_num,
                level=level,
                strategy_name=strategy_name,
                hypothesis=hypothesis,
                status="ERROR",
                visible_score=candidate_result.visible_score,
                best_visible_score=best_score,
                accepted=False,
                strategy_hash=candidate_result.strategy_hash,
                reason=candidate_result.error_message,
                window_results=candidate_result.visible_windows,
                holdout_excess_cagr=(
                    candidate_result.holdout.excess_cagr if candidate_result.holdout else None
                ),
                holdout_mdd=candidate_result.holdout.max_drawdown if candidate_result.holdout else None,
            )
            _write_public_log(round_result, public_log)
            private_buffer.append(_make_private_record(round_result, campaign))
            _discard_candidate(strategy_name)
            error_count += 1
            continue

        error_count = 0
        accepted = candidate_result.status == "PASS" and candidate_result.visible_score > best_score
        visible_score = candidate_result.visible_score if candidate_result.status == "PASS" else 0.0
        round_result = RoundResult(
            round_num=round_num,
            level=level,
            strategy_name=strategy_name,
            hypothesis=hypothesis,
            status=candidate_result.status,
            visible_score=visible_score,
            best_visible_score=best_score,
            accepted=accepted,
            strategy_hash=candidate_result.strategy_hash,
            reason=candidate_result.error_message,
            window_results=candidate_result.visible_windows,
            holdout_excess_cagr=(
                candidate_result.holdout.excess_cagr if candidate_result.holdout else None
            ),
            holdout_mdd=candidate_result.holdout.max_drawdown if candidate_result.holdout else None,
        )

        if accepted:
            _promote_candidate(strategy_name)
            best_score = candidate_result.visible_score
            stale_count = 0
        else:
            _discard_candidate(strategy_name)
            stale_count += 1

        _write_public_log(round_result, public_log)
        private_buffer.append(_make_private_record(round_result, campaign))

        should_stop, stop_reason = _check_stop_rules(
            campaign=campaign,
            round_num=round_num,
            stale_count=stale_count,
            initial_holdout_baseline=initial_holdout_baseline,
            current_holdout=round_result.holdout_excess_cagr if accepted else None,
        )
        if should_stop:
            _flush_private_log(private_buffer, private_log)
            print(
                json.dumps(
                    {
                        "status": "STOPPED",
                        "round_num": round_num,
                        "best_visible_score": best_score,
                        "stop_reason": stop_reason,
                    },
                    ensure_ascii=False,
                    indent=2,
                )
            )
            return

    _flush_private_log(private_buffer, private_log)
    print(
        json.dumps(
            {
                "status": "COMPLETED",
                "rounds": effective_max_rounds,
                "best_visible_score": best_score,
            },
            ensure_ascii=False,
            indent=2,
        )
    )


def _copy_champion_to_candidate(strategy_name: str) -> None:
    """Copy champion artifacts into candidate artifacts."""
    paths = common.get_strategy_paths(strategy_name)
    paths.candidate_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(paths.champion_path, paths.candidate_path)
    if paths.champion_params_path.exists():
        shutil.copy2(paths.champion_params_path, paths.candidate_params_path)


def _invoke_agent(
    strategy_name: str,
    forge_md: str,
    champion_code: str,
    public_log_tail: str,
    best_score: float,
    current_level: str,
) -> str:
    """Invoke `claude -p` and return the raw stdout."""
    paths = common.get_strategy_paths(strategy_name)
    level_rule = (
        "Modify only candidate_params.json. Do not edit candidate.py."
        if current_level == "parameter"
        else (
            "You may edit candidate.py, but you must preserve "
            "StrategyConfig / run_backtest exports."
        )
    )
    prompt = f"""{forge_md}

Current level: {current_level}
Current best visible_score: {best_score:.6f}

Files:
- Champion strategy: {paths.champion_path}
- Candidate strategy: {paths.candidate_path}
- Champion params: {paths.champion_params_path}
- Candidate params: {paths.candidate_params_path}

Rules:
1. First stdout line must be exactly: HYPOTHESIS: <one sentence>
2. {level_rule}
3. Do not modify forge/common.py, forge/runner.py, forge/evaluator.py, forge/campaign.lock.json, forge/forge.md, manifest files, or logs.
4. Optimize only for visible_score shown by evaluator output. Holdout is hidden.

Recent public log tail:
{public_log_tail or "(empty)"}

Champion code:
```python
{champion_code}
```
"""
    completed = subprocess.run(
        ["claude", "-p", prompt, "--output-format", "json"],
        cwd=PROJECT_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    payload = _parse_claude_cli_payload(completed.stdout)
    if completed.returncode != 0:
        message = _extract_claude_error(payload, completed.stderr)
        raise RuntimeError(message)
    if payload is not None and payload.get("is_error"):
        raise RuntimeError(_extract_claude_error(payload, completed.stderr))
    if payload is not None and isinstance(payload.get("result"), str):
        return payload["result"]
    return completed.stdout


def _capture_workspace_state() -> set[str]:
    """Snapshot modified/untracked file paths before agent invocation."""
    result = subprocess.run(
        ["git", "status", "--porcelain", "-u"],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
    )
    files = set()
    for line in result.stdout.strip().splitlines():
        if len(line) > 3:
            # git status --porcelain: "XY filename" or "XY orig -> renamed"
            path = line[3:].split(" -> ")[-1].strip()
            files.add(path)
    return files


def _check_workspace_isolation(
    baseline: set[str],
    strategy_name: str,
) -> tuple[bool, str]:
    """Verify agent only modified candidate files. Revert unauthorized changes."""
    current = _capture_workspace_state()
    new_changes = current - baseline

    if not new_changes:
        return True, "ok"

    paths = common.get_strategy_paths(strategy_name)
    try:
        allowed = {
            str(paths.candidate_path.relative_to(PROJECT_ROOT)),
            str(paths.candidate_params_path.relative_to(PROJECT_ROOT)),
        }
    except ValueError:
        allowed = set()

    unauthorized = new_changes - allowed
    if not unauthorized:
        return True, "ok"

    # Revert unauthorized tracked-file changes
    for file_path in unauthorized:
        full_path = PROJECT_ROOT / file_path
        subprocess.run(
            ["git", "checkout", "--", file_path],
            cwd=PROJECT_ROOT,
            capture_output=True,
        )
        # Remove any new untracked files the agent created
        if full_path.exists() and file_path not in baseline:
            full_path.unlink(missing_ok=True)

    return False, f"agent modified unauthorized files: {', '.join(sorted(unauthorized))}"


def _mutation_guard(
    strategy_name: str,
    campaign: dict,
    current_level: str,
) -> tuple[bool, str]:
    """Validate candidate edits against the current forge level contract."""
    paths = common.get_strategy_paths(strategy_name)
    try:
        candidate_mod = common.dynamic_import(paths.candidate_path)
    except Exception as exc:
        return False, f"candidate import failed: {exc}"

    required_exports = (
        "StrategyConfig",
        "run_backtest",
    )
    missing = [name for name in required_exports if not hasattr(candidate_mod, name)]
    if missing:
        return False, f"candidate missing exports: {', '.join(missing)}"

    for path, expected_hash in _IMMUTABLE_GUARD_HASHES.items():
        if common.hash_file(path) != expected_hash:
            try:
                display_path = path.relative_to(PROJECT_ROOT)
            except ValueError:
                display_path = path
            return False, f"forbidden file modified: {display_path}"

    if current_level == "parameter":
        if common.hash_file(paths.candidate_path) != common.hash_file(paths.champion_path):
            return False, "parameter mode requires candidate.py to remain byte-identical"
        ok, reason = _validate_parameter_file(paths, campaign)
        return ok, reason

    champion_params = common.load_json(paths.champion_params_path)
    if paths.candidate_params_path.exists():
        candidate_params = common.load_json(paths.candidate_params_path)
        if candidate_params != champion_params:
            return False, "structural mode requires candidate_params.json to match champion"
    return True, "ok"


def _extract_hypothesis(agent_output: str) -> str:
    """Extract the first `HYPOTHESIS:` line from agent stdout."""
    for line in agent_output.splitlines():
        if line.startswith("HYPOTHESIS:"):
            value = line.split(":", 1)[1].strip()
            return value or "missing_hypothesis_body"
    return "missing_hypothesis"


def _parse_claude_cli_payload(stdout: str) -> dict | None:
    raw = stdout.strip()
    if not raw:
        return None
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return None
    return payload if isinstance(payload, dict) else None


def _extract_claude_error(payload: dict | None, stderr: str) -> str:
    if payload:
        message = payload.get("result")
        if isinstance(message, str) and message.strip():
            return message.strip()
    stderr = stderr.strip()
    if stderr:
        return stderr
    return "claude command failed"


def _write_public_log(round_result: RoundResult, log_path: Path) -> None:
    """Append a visible-only line to the public TSV log."""
    header = (
        "timestamp\tround\tlevel\tstrategy_name\thypothesis\tstatus\tvisible_score\tbest_visible_score\t"
        "accepted\tstrategy_hash\treason\tvisible_windows_json\n"
    )
    row = [
        datetime.now(timezone.utc).isoformat(),
        str(round_result.round_num),
        round_result.level,
        round_result.strategy_name,
        _sanitize_tsv_cell(round_result.hypothesis),
        round_result.status,
        f"{round_result.visible_score:.6f}",
        f"{round_result.best_visible_score:.6f}",
        "1" if round_result.accepted else "0",
        round_result.strategy_hash,
        _sanitize_tsv_cell(round_result.reason),
        _sanitize_tsv_cell(
            json.dumps(
                [asdict(window) for window in round_result.window_results],
                ensure_ascii=False,
                separators=(",", ":"),
            )
        ),
    ]
    _append_tsv(log_path, header, row)


def _make_private_record(round_result: RoundResult, campaign: dict) -> dict:
    """Build a private log record (kept in memory until campaign ends)."""
    return {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "campaign_id": campaign["campaign_id"],
        **asdict(round_result),
    }


def _flush_private_log(buffer: list[dict], log_path: Path) -> None:
    """Write all accumulated private records to disk at campaign end.

    Holdout data stays in memory during the campaign so the optimizing
    agent cannot read it from the filesystem.
    """
    if not buffer:
        return
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("a", encoding="utf-8") as handle:
        for record in buffer:
            handle.write(json.dumps(record, ensure_ascii=False, default=_json_default) + "\n")


def _json_default(obj):
    """Handle numpy types and other non-serializable objects."""
    import numpy as np

    if isinstance(obj, (np.bool_,)):
        return bool(obj)
    if isinstance(obj, (np.integer,)):
        return int(obj)
    if isinstance(obj, (np.floating,)):
        return float(obj)
    if isinstance(obj, (np.ndarray,)):
        return obj.tolist()
    if isinstance(obj, Path):
        return str(obj)
    raise TypeError(f"Object of type {type(obj).__name__} is not JSON serializable")


def _promote_candidate(strategy_name: str) -> None:
    """Promote candidate artifacts into champion artifacts."""
    paths = common.get_strategy_paths(strategy_name)
    shutil.copy2(paths.candidate_path, paths.champion_path)
    if paths.candidate_params_path.exists():
        shutil.copy2(paths.candidate_params_path, paths.champion_params_path)


def _discard_candidate(strategy_name: str) -> None:
    """Discard candidate artifacts after a failed or rejected round."""
    paths = common.get_strategy_paths(strategy_name)
    for path in (paths.candidate_path, paths.candidate_params_path):
        if path.exists():
            path.unlink()


def _check_stop_rules(
    campaign: dict,
    round_num: int,
    stale_count: int,
    initial_holdout_baseline: float,
    current_holdout: Optional[float],
) -> tuple[bool, str]:
    """Evaluate campaign stop conditions."""
    if round_num >= int(campaign["max_rounds"]):
        return True, "max_rounds_reached"
    if stale_count >= int(campaign["stale_stop_rounds"]):
        return True, "stale_stop_triggered"
    if current_holdout is not None:
        meltdown_floor = initial_holdout_baseline + float(campaign["holdout_meltdown_threshold"])
        if current_holdout < meltdown_floor:
            return True, "holdout_meltdown"
    return False, ""


def _determine_level(
    campaign: dict,
    stale_count: int,
    error_count: int,
) -> str:
    """Switch to structural mode only after enough valid but stale rounds."""
    del error_count
    if stale_count >= int(campaign["structural_unlock_after_stale"]):
        return "structural"
    return "parameter"


def _validate_parameter_file(paths, campaign: dict) -> tuple[bool, str]:
    if not paths.candidate_params_path.exists():
        return False, "candidate_params.json missing in parameter mode"

    manifest_path = (common.FORGE_ROOT / campaign["parameter_surface_manifest"]).resolve()
    surface = common.load_surface_manifest(manifest_path)
    champion_params = common.load_json(paths.champion_params_path)
    candidate_params = common.load_json(paths.candidate_params_path)

    expected_keys = set(surface)
    if set(candidate_params) != expected_keys or set(champion_params) != expected_keys:
        return False, "params files must exactly match manifest key set"

    for key, value in candidate_params.items():
        spec = surface.get(key)
        if spec is None:
            return False, f"parameter not in manifest: {key}"
        ok, reason = _validate_param_value(key, value, spec)
        if not ok:
            return False, reason
    return True, "ok"


def _validate_param_value(name: str, value, spec: dict) -> tuple[bool, str]:
    expected_type = spec["type"]
    if expected_type == "int":
        if not isinstance(value, int) or isinstance(value, bool):
            return False, f"{name} must be int"
    elif expected_type == "float":
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            return False, f"{name} must be float"
        value = float(value)
    else:
        return False, f"unsupported type in manifest: {expected_type}"

    lower, upper = spec["range"]
    numeric_value = float(value)
    if numeric_value < float(lower) - 1e-12 or numeric_value > float(upper) + 1e-12:
        return False, f"{name} out of range"

    return True, "ok"


def _capture_immutable_hashes(campaign: dict, campaign_path: Path, paths) -> dict[Path, str]:
    manifest_path = common.resolve_manifest_path(campaign, campaign_path)
    tracked = [
        common.FORGE_ROOT / "common.py",
        common.FORGE_ROOT / "runner.py",
        common.FORGE_ROOT / "evaluator.py",
        campaign_path,
        common.FORGE_ROOT / "forge.md",
        manifest_path,
        paths.champion_path,
        paths.champion_params_path,
    ]
    # Track public log if it exists (private log is deferred to campaign end)
    campaign_id = campaign.get("campaign_id", "")
    if campaign_id:
        public_log_path = common.FORGE_ROOT / "logs" / campaign_id / "experiments_public.tsv"
        if public_log_path.exists():
            tracked.append(public_log_path)
    return {path.resolve(): common.hash_file(path.resolve()) for path in tracked}


def _sanitize_tsv_cell(value: str) -> str:
    return value.replace("\t", " ").replace("\n", " ").strip()


def _append_tsv(path: Path, header: str, row: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        path.write_text(header, encoding="utf-8")
    with path.open("a", encoding="utf-8") as handle:
        handle.write("\t".join(row) + "\n")


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the Forge optimization loop.")
    parser.add_argument("--strategy", default=None)
    parser.add_argument(
        "--campaign",
        default=str(common.resolve_campaign_path()),
        help="Path to campaign.lock.json",
    )
    parser.add_argument("--rounds", type=int, help="Optional max rounds override")
    return parser.parse_args()


def main() -> None:
    args = _parse_args()
    run_campaign(
        strategy_name=args.strategy,
        campaign_path=Path(args.campaign).resolve(),
        max_rounds=args.rounds,
    )


if __name__ == "__main__":
    main()
