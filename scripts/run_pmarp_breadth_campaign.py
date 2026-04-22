#!/usr/bin/env python3
from __future__ import annotations

import copy
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
FINANCE_ROOT = PROJECT_ROOT.parents[1]
PYTHON_BIN = FINANCE_ROOT / ".venv" / "bin" / "python"
PIPELINE_ENTRY = PROJECT_ROOT / "scripts" / "run_pipeline.py"
BASE_SPEC_PATH = PROJECT_ROOT / "backtest" / "specs" / "pipeline_pmarp_breadth_campaign_base.yaml"
STALE_STOP_ROUNDS = 6
HOLDOUT_MELTDOWN_THRESHOLD = -0.15

if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backtest.pipeline.spec import _load_spec_mapping


def build_candidates() -> list[dict[str, Any]]:
    return [
        {
            "id": "benchmark_t2_soft05_h20_top10_novol",
            "hypothesis": "Use the known soft05 10B benchmark EMA control as the first sanity baseline.",
            "params": {"trigger_threshold": 2.0, "holding_window_days": 20, "top_n": 10, "regime_mode": "benchmark_ema", "confirm_mode": "soft", "confirm_floor": 0.5, "max_trailing_volatility": None},
        },
        {
            "id": "breadth_t1_soft05_h20_top10_vol20",
            "hypothesis": "1% trigger + breadth 50% + soft confirmation is the clean base case.",
            "params": {"trigger_threshold": 1.0, "holding_window_days": 20, "top_n": 10, "regime_mode": "universe_breadth", "regime_breadth_threshold": 0.50, "confirm_mode": "soft", "confirm_floor": 0.5, "max_trailing_volatility": 0.20},
        },
        {
            "id": "breadth_t1_soft05_h20_top20_vol20",
            "hypothesis": "Breadth may support more breadth in holdings too; test top 20 names.",
            "params": {"trigger_threshold": 1.0, "holding_window_days": 20, "top_n": 20, "regime_mode": "universe_breadth", "regime_breadth_threshold": 0.50, "confirm_mode": "soft", "confirm_floor": 0.5, "max_trailing_volatility": 0.20},
        },
        {
            "id": "breadth_t1_soft05_h40_top10_vol20",
            "hypothesis": "The edge may still need longer carry even inside breadth-on regimes.",
            "params": {"trigger_threshold": 1.0, "holding_window_days": 40, "top_n": 10, "regime_mode": "universe_breadth", "regime_breadth_threshold": 0.50, "confirm_mode": "soft", "confirm_floor": 0.5, "max_trailing_volatility": 0.20},
        },
        {
            "id": "breadth_t1_soft05_h60_top10_vol20",
            "hypothesis": "Stress the 60-day carry thesis under breadth gating.",
            "params": {"trigger_threshold": 1.0, "holding_window_days": 60, "top_n": 10, "regime_mode": "universe_breadth", "regime_breadth_threshold": 0.50, "confirm_mode": "soft", "confirm_floor": 0.5, "max_trailing_volatility": 0.20},
        },
        {
            "id": "breadth_t1_soft00_h20_top10_vol20",
            "hypothesis": "If RVOL is mostly noise, removing the soft floor may help breadth do the filtering.",
            "params": {"trigger_threshold": 1.0, "holding_window_days": 20, "top_n": 10, "regime_mode": "universe_breadth", "regime_breadth_threshold": 0.50, "confirm_mode": "soft", "confirm_floor": 0.0, "max_trailing_volatility": 0.20},
        },
        {
            "id": "breadth_t1_recent5_h20_top10_vol20",
            "hypothesis": "Recent peak RVOL may work better than same-day RVOL inside breadth-on conditions.",
            "params": {"trigger_threshold": 1.0, "holding_window_days": 20, "top_n": 10, "regime_mode": "universe_breadth", "regime_breadth_threshold": 0.50, "confirm_mode": "recent_peak_soft", "confirm_floor": 0.5, "recent_peak_window": 5, "recent_peak_threshold": 2.0, "max_trailing_volatility": 0.20},
        },
        {
            "id": "breadth_t1_recent7_h20_top10_vol20",
            "hypothesis": "Extend the recent RVOL memory to 7 days for slower repair patterns.",
            "params": {"trigger_threshold": 1.0, "holding_window_days": 20, "top_n": 10, "regime_mode": "universe_breadth", "regime_breadth_threshold": 0.50, "confirm_mode": "recent_peak_soft", "confirm_floor": 0.5, "recent_peak_window": 7, "recent_peak_threshold": 2.0, "max_trailing_volatility": 0.20},
        },
        {
            "id": "breadth_t1_soft05_h20_top10_novolcap",
            "hypothesis": "Breadth gating may already suppress the junk names, making vol cap unnecessary.",
            "params": {"trigger_threshold": 1.0, "holding_window_days": 20, "top_n": 10, "regime_mode": "universe_breadth", "regime_breadth_threshold": 0.50, "confirm_mode": "soft", "confirm_floor": 0.5, "max_trailing_volatility": None},
        },
        {
            "id": "breadth_t1_soft05_h20_top10_vol25",
            "hypothesis": "Slightly looser vol cap may recover alpha lost to over-filtering.",
            "params": {"trigger_threshold": 1.0, "holding_window_days": 20, "top_n": 10, "regime_mode": "universe_breadth", "regime_breadth_threshold": 0.50, "confirm_mode": "soft", "confirm_floor": 0.5, "max_trailing_volatility": 0.25},
        },
        {
            "id": "breadth_t1_soft05_h20_top10_vol30",
            "hypothesis": "Let more high-vol names in and see if breadth can contain the damage.",
            "params": {"trigger_threshold": 1.0, "holding_window_days": 20, "top_n": 10, "regime_mode": "universe_breadth", "regime_breadth_threshold": 0.50, "confirm_mode": "soft", "confirm_floor": 0.5, "max_trailing_volatility": 0.30},
        },
        {
            "id": "breadth_t1_soft05_h20_top10_b55",
            "hypothesis": "Tighten breadth threshold to 55% and demand a healthier market tape.",
            "params": {"trigger_threshold": 1.0, "holding_window_days": 20, "top_n": 10, "regime_mode": "universe_breadth", "regime_breadth_threshold": 0.55, "confirm_mode": "soft", "confirm_floor": 0.5, "max_trailing_volatility": 0.20},
        },
        {
            "id": "breadth_t1_soft05_h20_top10_b60",
            "hypothesis": "Push breadth to 60% and see whether quality beats sample size.",
            "params": {"trigger_threshold": 1.0, "holding_window_days": 20, "top_n": 10, "regime_mode": "universe_breadth", "regime_breadth_threshold": 0.60, "confirm_mode": "soft", "confirm_floor": 0.5, "max_trailing_volatility": 0.20},
        },
        {
            "id": "benchmark_t1_soft05_h20_top10_vol20",
            "hypothesis": "Keep a benchmark EMA control to verify whether breadth really adds value.",
            "params": {"trigger_threshold": 1.0, "holding_window_days": 20, "top_n": 10, "regime_mode": "benchmark_ema", "confirm_mode": "soft", "confirm_floor": 0.5, "max_trailing_volatility": 0.20},
        },
        {
            "id": "benchmark_t1_recent5_h20_top10_vol20",
            "hypothesis": "Control run: benchmark EMA plus recent peak confirmation.",
            "params": {"trigger_threshold": 1.0, "holding_window_days": 20, "top_n": 10, "regime_mode": "benchmark_ema", "confirm_mode": "recent_peak_soft", "confirm_floor": 0.5, "recent_peak_window": 5, "recent_peak_threshold": 2.0, "max_trailing_volatility": 0.20},
        },
        {
            "id": "breadth_t2_soft05_h20_top10_vol20",
            "hypothesis": "Check whether 2% trigger becomes better once breadth cleans the regime.",
            "params": {"trigger_threshold": 2.0, "holding_window_days": 20, "top_n": 10, "regime_mode": "universe_breadth", "regime_breadth_threshold": 0.50, "confirm_mode": "soft", "confirm_floor": 0.5, "max_trailing_volatility": 0.20},
        },
        {
            "id": "breadth_t3_soft05_h60_top10_vol20",
            "hypothesis": "3% trigger may be the slower but cleaner variant under breadth gating.",
            "params": {"trigger_threshold": 3.0, "holding_window_days": 60, "top_n": 10, "regime_mode": "universe_breadth", "regime_breadth_threshold": 0.50, "confirm_mode": "soft", "confirm_floor": 0.5, "max_trailing_volatility": 0.20},
        },
    ]


def load_yaml(path: Path) -> dict[str, Any]:
    return _load_spec_mapping(path)


def dump_yaml(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def calmar_like(excess_cagr: float, max_drawdown: float) -> float:
    drawdown = abs(float(max_drawdown))
    return float(excess_cagr) if drawdown < 1e-9 else float(excess_cagr) / drawdown


def compute_metrics_summary(metrics: dict[str, Any]) -> dict[str, float]:
    is_metrics = metrics["strategy"]["is"]
    oos_metrics = metrics["strategy"]["oos"]
    return {
        "is_excess_cagr": float(is_metrics["excess_cagr"]),
        "is_sharpe": float(is_metrics["sharpe_ratio"]),
        "is_mdd": float(is_metrics["max_drawdown"]),
        "is_visible_score": calmar_like(is_metrics["excess_cagr"], is_metrics["max_drawdown"]),
        "oos_excess_cagr": float(oos_metrics["excess_cagr"]),
        "oos_sharpe": float(oos_metrics["sharpe_ratio"]),
        "oos_mdd": float(oos_metrics["max_drawdown"]),
        "oos_hidden_score": calmar_like(oos_metrics["excess_cagr"], oos_metrics["max_drawdown"]),
        "oos_turnover": float(oos_metrics["annual_turnover"]),
    }


def load_state(state_path: Path) -> dict[str, Any]:
    if state_path.exists():
        return json.loads(state_path.read_text(encoding="utf-8"))
    return {
        "best_visible_score": -999.0,
        "best_candidate_id": None,
        "baseline_holdout_excess_cagr": None,
        "stale_rounds": 0,
        "accepted_rounds": 0,
        "seen_candidate_ids": [],
        "last_candidate_id": None,
        "rounds_completed": 0,
    }


def save_state(state_path: Path, payload: dict[str, Any]) -> None:
    state_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")


def select_candidate(candidates: list[dict[str, Any]], seen_ids: set[str]) -> dict[str, Any] | None:
    for candidate in candidates:
        if candidate["id"] not in seen_ids:
            return candidate
    return None


def apply_candidate(base_spec: dict[str, Any], candidate: dict[str, Any]) -> dict[str, Any]:
    spec = copy.deepcopy(base_spec)
    params = spec["factors"][0]["params"]
    params.update(
        {
            "trigger_threshold": candidate["params"]["trigger_threshold"],
            "holding_window_days": candidate["params"]["holding_window_days"],
            "regime_mode": candidate["params"]["regime_mode"],
            "confirm_mode": candidate["params"]["confirm_mode"],
            "confirm_floor": candidate["params"].get("confirm_floor", 0.5),
            "recent_peak_window": candidate["params"].get("recent_peak_window", 0),
            "recent_peak_threshold": candidate["params"].get("recent_peak_threshold", 2.0),
        }
    )
    if candidate["params"]["regime_mode"] == "universe_breadth":
        params["regime_breadth_threshold"] = candidate["params"].get("regime_breadth_threshold", 0.50)
    if candidate["params"].get("max_trailing_volatility") is None:
        params.pop("max_trailing_volatility", None)
    else:
        params["max_trailing_volatility"] = candidate["params"]["max_trailing_volatility"]
    spec["portfolio"]["top_n"] = candidate["params"]["top_n"]
    spec["spec_id"] = f"pmarp_breadth_campaign_{candidate['id']}"
    spec["notes"] = candidate["hypothesis"]
    return spec


def run_pipeline(spec_path: Path) -> tuple[Path, dict[str, Any], str]:
    cmd = [str(PYTHON_BIN), str(PIPELINE_ENTRY), str(spec_path)]
    completed = subprocess.run(
        cmd,
        cwd=str(PROJECT_ROOT),
        capture_output=True,
        text=True,
        timeout=1800,
        check=False,
    )
    combined_output = "\n".join(part for part in [completed.stdout, completed.stderr] if part).strip()
    if completed.returncode != 0:
        raise RuntimeError(f"pipeline failed ({completed.returncode}):\n{combined_output}")

    artifact_dir: Path | None = None
    for line in completed.stdout.splitlines():
        if line.startswith("artifact_dir="):
            artifact_dir = Path(line.split("=", 1)[1].strip())
            break
    if artifact_dir is None:
        raise RuntimeError(f"pipeline stdout missing artifact_dir:\n{combined_output}")

    metrics_path = artifact_dir / "metrics.json"
    metrics = json.loads(metrics_path.read_text(encoding="utf-8"))
    return artifact_dir, metrics, combined_output


def build_payload(
    candidate: dict[str, Any] | None,
    state: dict[str, Any],
    summary: str,
    result: str,
    next_plan: str,
    task_directive: str,
    round_status: str,
    visible_score: float,
    stop_reason: str,
    generated_files: list[str],
    private_log: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "status": "success",
        "summary": summary,
        "result": result,
        "nextPlan": next_plan,
        "taskDirective": task_directive,
        "generated_files": generated_files,
        "campaign": {
            "hypothesis": candidate["hypothesis"] if candidate else "candidate queue exhausted",
            "roundStatus": round_status,
            "visibleScore": round(visible_score, 6),
            "bestVisibleScore": round(float(state["best_visible_score"] if float(state["best_visible_score"]) > -900 else visible_score), 6),
            "accepted": round_status == "PROMOTED",
            "staleRounds": int(state["stale_rounds"]),
            "stopReason": stop_reason,
            "notes": candidate["id"] if candidate else "exhausted",
            "privateLog": private_log or {},
        },
    }


def main() -> int:
    workspace_path_raw = os.environ.get("LOOP_WORKSPACE_PATH")
    if not workspace_path_raw:
        raise SystemExit("missing LOOP_WORKSPACE_PATH")

    workspace_path = Path(workspace_path_raw)
    campaign_dir = workspace_path / ".codex-loop" / "campaign"
    generated_dir = campaign_dir / "generated_specs"
    generated_dir.mkdir(parents=True, exist_ok=True)
    state_path = campaign_dir / "pmarp_breadth_state.json"

    state = load_state(state_path)
    candidates = build_candidates()
    seen_ids = set(state["seen_candidate_ids"])
    candidate = select_candidate(candidates, seen_ids)

    if candidate is None:
        payload = build_payload(
            candidate=None,
            state=state,
            summary="候选池已经跑完，没有新的 breadth / PMARP 组合可评估。",
            result="当前 campaign 的候选列表已耗尽，控制面自动结束本次自动迭代。",
            next_plan="查看 public/private log，决定是否扩展候选空间或改 acceptance rule。",
            task_directive="complete",
            round_status="EXHAUSTED",
            visible_score=state["best_visible_score"],
            stop_reason="candidate_queue_exhausted",
            generated_files=[str(state_path)],
        )
        print(json.dumps(payload, ensure_ascii=False))
        return 0

    base_spec = load_yaml(BASE_SPEC_PATH)
    candidate_spec = apply_candidate(base_spec, candidate)
    spec_path = generated_dir / f"{candidate_spec['spec_id']}.yaml"
    dump_yaml(spec_path, candidate_spec)

    artifact_dir, metrics, combined_output = run_pipeline(spec_path)
    summary_metrics = compute_metrics_summary(metrics)

    holdout_guard = (
        summary_metrics["oos_excess_cagr"] > 0.0
        and summary_metrics["oos_sharpe"] > 0.0
        and (
            state["baseline_holdout_excess_cagr"] is None
            or summary_metrics["oos_excess_cagr"]
            >= float(state["baseline_holdout_excess_cagr"]) + HOLDOUT_MELTDOWN_THRESHOLD
        )
    )
    accepted = (
        summary_metrics["is_visible_score"] > float(state["best_visible_score"])
        and holdout_guard
    )

    state["rounds_completed"] = int(state["rounds_completed"]) + 1
    state["seen_candidate_ids"].append(candidate["id"])
    state["last_candidate_id"] = candidate["id"]

    if accepted:
        if state["baseline_holdout_excess_cagr"] is None:
            state["baseline_holdout_excess_cagr"] = summary_metrics["oos_excess_cagr"]
        state["best_visible_score"] = summary_metrics["is_visible_score"]
        state["best_candidate_id"] = candidate["id"]
        state["accepted_rounds"] = int(state["accepted_rounds"]) + 1
        state["stale_rounds"] = 0
        round_status = "PROMOTED"
    else:
        state["stale_rounds"] = int(state["stale_rounds"]) + 1
        round_status = "REJECTED"

    stop_reason = ""
    task_directive = "continue"
    if (
        state["baseline_holdout_excess_cagr"] is not None
        and summary_metrics["oos_excess_cagr"] < float(state["baseline_holdout_excess_cagr"]) + HOLDOUT_MELTDOWN_THRESHOLD
    ):
        stop_reason = "holdout_meltdown_guard"
        task_directive = "complete"
    elif int(state["stale_rounds"]) >= STALE_STOP_ROUNDS:
        stop_reason = "stale_stop_rounds_reached"
        task_directive = "complete"
    elif len(state["seen_candidate_ids"]) >= len(candidates):
        stop_reason = "candidate_queue_exhausted"
        task_directive = "complete"

    save_state(state_path, state)

    generated_files = [
        str(spec_path),
        str(artifact_dir / "metrics.json"),
        str(artifact_dir / "report.html"),
        str(state_path),
    ]
    private_log = {
        "candidate_id": candidate["id"],
        "artifact_dir": str(artifact_dir),
        "is_excess_cagr": summary_metrics["is_excess_cagr"],
        "is_sharpe": summary_metrics["is_sharpe"],
        "is_visible_score": summary_metrics["is_visible_score"],
        "oos_excess_cagr": summary_metrics["oos_excess_cagr"],
        "oos_sharpe": summary_metrics["oos_sharpe"],
        "oos_hidden_score": summary_metrics["oos_hidden_score"],
        "oos_max_drawdown": summary_metrics["oos_mdd"],
        "oos_turnover": summary_metrics["oos_turnover"],
        "baseline_holdout_excess_cagr": state["baseline_holdout_excess_cagr"],
        "best_candidate_id": state["best_candidate_id"],
        "runner_output_tail": combined_output.splitlines()[-10:],
    }

    summary = (
        f"候选 {candidate['id']} 已完成，IS visible score={summary_metrics['is_visible_score']:.3f}，"
        f"{'通过' if accepted else '未通过'} hidden OOS guard。"
    )
    result = (
        "accepted → champion advanced on visible score while hidden OOS guard stayed alive."
        if accepted
        else "rejected → candidate did not beat the current best visible score or hidden OOS guard failed."
    )
    next_plan = (
        "控制面将继续尝试下一个 breadth 变体。"
        if task_directive == "continue"
        else "控制面已触发停机规则，建议审计 private log 后再扩展候选池。"
    )

    payload = build_payload(
        candidate=candidate,
        state=state,
        summary=summary,
        result=result,
        next_plan=next_plan,
        task_directive=task_directive,
        round_status=round_status,
        visible_score=summary_metrics["is_visible_score"],
        stop_reason=stop_reason,
        generated_files=generated_files,
        private_log=private_log,
    )
    print(json.dumps(payload, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
