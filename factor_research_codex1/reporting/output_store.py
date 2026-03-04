"""
Run output storage for Codex1.0 mining pipelines.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Dict, List, Optional, Sequence

import pandas as pd

from factor_research_codex1.evaluation.slow_lane import CandidateSlowMetrics


def _timestamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%SZ")


@dataclass
class MiningRunArtifacts:
    run_id: str
    run_dir: Path
    fast_csv: Path
    slow_csv: Path
    metadata_json: Path
    top_json: Path


class MiningOutputStore:
    """Persist fast/slow run outputs in timestamped folders."""

    def __init__(self, base_dir: Optional[Path] = None):
        if base_dir is None:
            project_root = Path(__file__).resolve().parents[2]
            base_dir = project_root / "data" / "factor_research_codex1" / "runs"
        self.base_dir = base_dir
        self.base_dir.mkdir(parents=True, exist_ok=True)

    def save_run(
        self,
        fast_df: pd.DataFrame,
        slow_df: pd.DataFrame,
        slow_metrics: Sequence[CandidateSlowMetrics],
        metadata: Optional[Dict[str, object]] = None,
        top_n_json: int = 20,
    ) -> MiningRunArtifacts:
        run_id = _timestamp()
        run_dir = self.base_dir / run_id
        run_dir.mkdir(parents=True, exist_ok=True)

        fast_path = run_dir / "fast_lane.csv"
        slow_path = run_dir / "slow_lane.csv"
        meta_path = run_dir / "metadata.json"
        top_path = run_dir / "top_candidates.json"

        fast_df.to_csv(fast_path, index=False)
        slow_df.to_csv(slow_path, index=False)

        payload = {
            "run_id": run_id,
            "generated_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
            "fast_rows": int(len(fast_df)),
            "slow_rows": int(len(slow_df)),
            "metadata": metadata or {},
        }
        meta_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

        top_payload: List[Dict[str, object]] = []
        for m in list(slow_metrics)[:top_n_json]:
            top_payload.append(
                {
                    "candidate_id": m.candidate_id,
                    "expression": m.expression,
                    "normalized_expression": m.normalized_expression,
                    "hypothesis_id": m.hypothesis_id,
                    "source": m.source,
                    "quality_score": float(m.quality_score),
                    "strategy_cagr": float(m.strategy_cagr),
                    "strategy_sharpe": float(m.strategy_sharpe),
                    "strategy_max_drawdown": float(m.strategy_max_drawdown),
                    "avg_excess_cagr": float(m.avg_excess_cagr),
                    "avg_information_ratio": float(m.avg_information_ratio),
                    "benchmarks": [asdict(x) for x in m.benchmark_comparisons],
                }
            )
        top_path.write_text(json.dumps(top_payload, ensure_ascii=False, indent=2), encoding="utf-8")

        return MiningRunArtifacts(
            run_id=run_id,
            run_dir=run_dir,
            fast_csv=fast_path,
            slow_csv=slow_path,
            metadata_json=meta_path,
            top_json=top_path,
        )

