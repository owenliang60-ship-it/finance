"""
Novelty filtering utilities for generated factors.
"""

from __future__ import annotations

import re
from typing import Iterable, List, Sequence, Set

from factor_research_codex1.miner.generator import CandidateFactor


_TOKEN_RE = re.compile(r"[A-Za-z_]+|\d+")


def _token_set(expression: str) -> Set[str]:
    return set(_TOKEN_RE.findall(expression))


def _jaccard_similarity(a: Set[str], b: Set[str]) -> float:
    if not a and not b:
        return 1.0
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


class NoveltyFilter:
    """
    Filter duplicate/highly similar candidates by token-level similarity.

    This is a lightweight Week 1 implementation and can be replaced by
    return-correlation based clustering in later phases.
    """

    def __init__(self, similarity_threshold: float = 0.92):
        self.similarity_threshold = similarity_threshold

    def filter(
        self,
        candidates: Sequence[CandidateFactor],
        existing_normalized: Iterable[str] = (),
    ) -> List[CandidateFactor]:
        existing = set(existing_normalized)
        kept: List[CandidateFactor] = []
        kept_tokens: List[Set[str]] = []

        for candidate in candidates:
            expr = candidate.normalized_expression
            if expr in existing:
                continue

            tokens = _token_set(expr)
            is_similar = False
            for prev in kept_tokens:
                if _jaccard_similarity(tokens, prev) >= self.similarity_threshold:
                    is_similar = True
                    break

            if is_similar:
                continue

            kept.append(candidate)
            kept_tokens.append(tokens)
            existing.add(expr)

        return kept

