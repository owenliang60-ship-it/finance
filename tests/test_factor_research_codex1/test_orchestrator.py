"""
Unit tests for mining orchestrator and novelty filtering.
"""

from factor_research_codex1.miner.orchestrator import MiningOrchestrator, MiningRunConfig


class TestMiningOrchestrator:
    def test_run_produces_candidates(self):
        orchestrator = MiningOrchestrator()
        config = MiningRunConfig(
            max_grid_candidates=40,
            max_random_candidates=60,
            random_max_depth=3,
            novelty_threshold=0.95,
        )
        result = orchestrator.run(config=config)

        assert len(result) > 0
        normalized = [c.normalized_expression for c in result]
        assert len(normalized) == len(set(normalized))

    def test_run_respects_existing_normalized(self):
        orchestrator = MiningOrchestrator()
        config = MiningRunConfig(
            max_grid_candidates=20,
            max_random_candidates=20,
            random_max_depth=3,
            novelty_threshold=1.0,  # exact dedupe only
        )

        first = orchestrator.run(config=config)
        assert len(first) > 0
        blocked = {first[0].normalized_expression}
        second = orchestrator.run(config=config, existing_normalized=blocked)

        assert all(c.normalized_expression not in blocked for c in second)

