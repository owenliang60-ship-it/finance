"""
Unit tests for candidate generation.
"""

from factor_research_codex1.miner.generator import FactorCandidateGenerator
from factor_research_codex1.miner.validator import FactorDSLValidator


class TestFactorCandidateGenerator:
    def test_generate_grid_returns_valid_candidates(self):
        validator = FactorDSLValidator()
        gen = FactorCandidateGenerator(validator=validator, seed=11)
        candidates = gen.generate_grid(max_candidates=60)

        assert len(candidates) > 0
        assert len(candidates) <= 60

        # All grid candidates should be valid and normalized.
        normalized = set()
        for c in candidates:
            result = validator.validate(c.expression)
            assert result.is_valid
            assert c.normalized_expression
            normalized.add(c.normalized_expression)
            assert c.source == "grid"
        assert len(normalized) == len(candidates)

    def test_generate_random_returns_valid_candidates(self):
        validator = FactorDSLValidator(max_depth=4)
        gen = FactorCandidateGenerator(validator=validator, seed=13)
        candidates = gen.generate_random(max_candidates=80, max_depth=3)

        assert len(candidates) > 0
        assert len(candidates) <= 80

        seen = set()
        for c in candidates:
            result = validator.validate(c.expression)
            assert result.is_valid
            assert c.normalized_expression == result.normalized_expression
            assert c.normalized_expression not in seen
            seen.add(c.normalized_expression)
            assert c.source == "random"

    def test_candidate_ids_are_unique(self):
        gen = FactorCandidateGenerator(seed=5)
        grid = gen.generate_grid(max_candidates=25)
        rand = gen.generate_random(max_candidates=25, max_depth=3)
        ids = [c.candidate_id for c in grid + rand]
        assert len(ids) == len(set(ids))

    def test_quality_gate_rejects_raw_level_expression(self):
        assert not FactorCandidateGenerator._passes_quality_gate("close")
        assert not FactorCandidateGenerator._passes_quality_gate("volume")
        assert FactorCandidateGenerator._passes_quality_gate("ret_n(close, 20)")
