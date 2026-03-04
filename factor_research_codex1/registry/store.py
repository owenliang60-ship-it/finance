"""
Candidate registry for Codex1.0 autonomous factor mining.

Tracks candidate lifecycle status:
- approved
- watchlist
- rejected
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence

from factor_research_codex1.evaluation.slow_lane import CandidateSlowMetrics


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


@dataclass
class RegistryRules:
    """Thresholds for candidate state classification."""

    min_approved_excess_cagr: float = 0.03
    min_approved_ir: float = 0.4
    max_approved_drawdown: float = 0.35
    min_watchlist_excess_cagr: float = 0.0
    min_watchlist_ir: float = 0.1


@dataclass
class CandidateRegistryRecord:
    """Persistent candidate record."""

    candidate_id: str
    normalized_expression: str
    expression: str
    hypothesis_id: str
    source: str
    status: str
    decision_reason: str
    created_at: str
    updated_at: str
    metrics_snapshot: Dict[str, float] = field(default_factory=dict)


class CandidateRegistry:
    """JSON-backed registry store."""

    def __init__(self, path: Optional[Path] = None):
        if path is None:
            project_root = Path(__file__).resolve().parents[2]
            path = project_root / "data" / "factor_research_codex1" / "candidate_registry.json"
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._records: Dict[str, CandidateRegistryRecord] = {}
        self._load()

    def upsert_from_slow_metrics(
        self,
        metrics: Sequence[CandidateSlowMetrics],
        rules: Optional[RegistryRules] = None,
    ) -> None:
        """Update registry from slow-lane metrics."""
        rule = rules or RegistryRules()
        now = _utc_now_iso()
        for m in metrics:
            status, reason = self._classify(m, rule)
            existing = self._records.get(m.candidate_id)
            created_at = existing.created_at if existing else now

            snapshot = {
                "quality_score": float(m.quality_score),
                "strategy_cagr": float(m.strategy_cagr),
                "strategy_sharpe": float(m.strategy_sharpe),
                "strategy_max_drawdown": float(m.strategy_max_drawdown),
                "avg_excess_cagr": float(m.avg_excess_cagr),
                "avg_information_ratio": float(m.avg_information_ratio),
                "n_days": float(m.n_days),
                "n_rebalances": float(m.n_rebalances),
                "n_trades": float(m.n_trades),
            }

            self._records[m.candidate_id] = CandidateRegistryRecord(
                candidate_id=m.candidate_id,
                normalized_expression=m.normalized_expression,
                expression=m.expression,
                hypothesis_id=m.hypothesis_id,
                source=m.source,
                status=status,
                decision_reason=reason,
                created_at=created_at,
                updated_at=now,
                metrics_snapshot=snapshot,
            )

    def counts(self) -> Dict[str, int]:
        """Return registry status counts."""
        out = {"approved": 0, "watchlist": 0, "rejected": 0}
        for r in self._records.values():
            out[r.status] = out.get(r.status, 0) + 1
        return out

    def records(self) -> List[CandidateRegistryRecord]:
        """Return records sorted by update time desc."""
        return sorted(
            self._records.values(),
            key=lambda x: x.updated_at,
            reverse=True,
        )

    def save(self) -> Path:
        """Persist registry to disk."""
        payload = {
            "version": "codex1.0",
            "updated_at": _utc_now_iso(),
            "count": len(self._records),
            "status_counts": self.counts(),
            "records": [asdict(x) for x in self.records()],
        }
        self.path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return self.path

    def _load(self) -> None:
        if not self.path.exists():
            self._records = {}
            return
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
            records = payload.get("records", [])
            self._records = {}
            for raw in records:
                rec = CandidateRegistryRecord(**raw)
                self._records[rec.candidate_id] = rec
        except Exception:
            # Soft-fail for corrupted file; start fresh instead of breaking runs.
            self._records = {}

    @staticmethod
    def _classify(m: CandidateSlowMetrics, rules: RegistryRules) -> tuple[str, str]:
        mdd = abs(float(m.strategy_max_drawdown))
        if (
            m.avg_excess_cagr >= rules.min_approved_excess_cagr
            and m.avg_information_ratio >= rules.min_approved_ir
            and mdd <= rules.max_approved_drawdown
        ):
            return "approved", "Pass approved thresholds on excess CAGR/IR/drawdown."

        if (
            m.avg_excess_cagr >= rules.min_watchlist_excess_cagr
            and m.avg_information_ratio >= rules.min_watchlist_ir
        ):
            return "watchlist", "Partial pass: monitor for stability and more OOS evidence."

        return "rejected", "Failed minimum thresholds on excess CAGR/IR."

