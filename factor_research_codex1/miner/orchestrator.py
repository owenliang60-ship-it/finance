"""
Week 1 orchestrator for autonomous candidate mining.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, List

from factor_research_codex1.miner.generator import CandidateFactor, FactorCandidateGenerator
from factor_research_codex1.miner.novelty_filter import NoveltyFilter


@dataclass
class MiningRunConfig:
    """Controls a single mining run."""

    max_grid_candidates: int = 250
    max_random_candidates: int = 350
    random_max_depth: int = 3
    novelty_threshold: float = 0.92


class MiningOrchestrator:
    """Run generation and novelty filtering in one step."""

    def __init__(self, generator: FactorCandidateGenerator | None = None):
        self.generator = generator or FactorCandidateGenerator()

    def run(
        self,
        config: MiningRunConfig | None = None,
        existing_normalized: Iterable[str] = (),
    ) -> List[CandidateFactor]:
        cfg = config or MiningRunConfig()

        grid = self.generator.generate_grid(max_candidates=cfg.max_grid_candidates)
        rand = self.generator.generate_random(
            max_candidates=cfg.max_random_candidates,
            max_depth=cfg.random_max_depth,
        )

        # Keep deterministic candidates first, then random.
        merged = grid + rand
        novelty = NoveltyFilter(similarity_threshold=cfg.novelty_threshold)
        return novelty.filter(merged, existing_normalized=existing_normalized)

