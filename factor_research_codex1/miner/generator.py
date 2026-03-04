"""
Candidate factor generation (grid + random) for Week 1.
"""

from __future__ import annotations

import random
import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Set

from factor_research_codex1.miner.hypothesis import DEFAULT_HYPOTHESES, list_hypotheses
from factor_research_codex1.miner.validator import FactorDSLValidator


@dataclass(frozen=True)
class CandidateFactor:
    """Single candidate factor record."""

    candidate_id: str
    expression: str
    normalized_expression: str
    source: str
    hypothesis_id: str
    metadata: Dict[str, object] = field(default_factory=dict)


class FactorCandidateGenerator:
    """Generate candidate expressions and keep only syntactically valid ones."""

    def __init__(
        self,
        validator: Optional[FactorDSLValidator] = None,
        seed: int = 7,
    ):
        self.validator = validator or FactorDSLValidator()
        self._rng = random.Random(seed)
        self._counter = 0

    def generate_grid(self, max_candidates: int = 300) -> List[CandidateFactor]:
        """
        Deterministic template-driven generation.

        This provides broad baseline coverage and reproducibility.
        """
        expressions: List[str] = []

        # RSI family
        for w in (6, 9, 14, 21, 28):
            expressions.extend(
                [
                    f"rsi(close, {w})",
                    f"sub(rsi(close, {w}), 50)",
                    f"rank_cs(rsi(close, {w}))",
                ]
            )

        # MACD family
        for fast, slow, signal in ((8, 21, 9), (12, 26, 9), (16, 32, 9)):
            expressions.extend(
                [
                    f"macd(close, {fast}, {slow}, {signal})",
                    f"rank_cs(macd(close, {fast}, {slow}, {signal}))",
                ]
            )

        # Return/volatility families
        for ret_w in (3, 5, 10, 20, 60):
            expressions.append(f"ret_n(close, {ret_w})")
            expressions.append(f"rank_cs(ret_n(close, {ret_w}))")

        for vol_w in (10, 20, 60, 120):
            expressions.append(f"vol_n(close, {vol_w})")
            expressions.append(f"zscore(vol_n(close, {vol_w}), 60)")

        # Composite features
        for ret_w, z_w in ((5, 20), (10, 60), (20, 120)):
            expressions.extend(
                [
                    f"zscore(ret_n(close, {ret_w}), {z_w})",
                    f"clip(zscore(ret_n(close, {ret_w}), {z_w}), -3, 3)",
                    f"mul(ret_n(close, {ret_w}), zscore(volume, {z_w}))",
                ]
            )

        # Deduplicate while preserving insertion order.
        unique = list(dict.fromkeys(expressions))
        return self._materialize(
            unique[:max_candidates],
            source="grid",
            hypothesis_id="template_grid",
        )

    def generate_random(
        self,
        max_candidates: int = 300,
        max_depth: int = 3,
    ) -> List[CandidateFactor]:
        """
        Randomized expression generation in a constrained grammar space.
        """
        candidates: List[CandidateFactor] = []
        seen: Set[str] = set()
        attempts = max_candidates * 15

        hypotheses = list_hypotheses()
        if not hypotheses:
            hypotheses = list(DEFAULT_HYPOTHESES)

        for _ in range(attempts):
            if len(candidates) >= max_candidates:
                break

            hyp = self._rng.choice(hypotheses)
            expr = self._random_expr(max_depth=max_depth, current_depth=1)
            result = self.validator.validate(expr)
            if not result.is_valid:
                continue
            if not self._passes_quality_gate(result.normalized_expression):
                continue
            if result.normalized_expression in seen:
                continue

            seen.add(result.normalized_expression)
            candidates.append(
                self._build_candidate(
                    expression=expr,
                    normalized_expression=result.normalized_expression,
                    source="random",
                    hypothesis_id=hyp.hypothesis_id,
                )
            )

        return candidates

    def _materialize(
        self,
        expressions: Sequence[str],
        source: str,
        hypothesis_id: str,
    ) -> List[CandidateFactor]:
        out: List[CandidateFactor] = []
        for expr in expressions:
            result = self.validator.validate(expr)
            if not result.is_valid:
                continue
            if not self._passes_quality_gate(result.normalized_expression):
                continue
            out.append(
                self._build_candidate(
                    expression=expr,
                    normalized_expression=result.normalized_expression,
                    source=source,
                    hypothesis_id=hypothesis_id,
                )
            )
        return out

    def _build_candidate(
        self,
        expression: str,
        normalized_expression: str,
        source: str,
        hypothesis_id: str,
    ) -> CandidateFactor:
        self._counter += 1
        return CandidateFactor(
            candidate_id=f"AUTO_{self._counter:05d}",
            expression=expression,
            normalized_expression=normalized_expression,
            source=source,
            hypothesis_id=hypothesis_id,
            metadata={"version": "codex1.0-week1"},
        )

    def _random_expr(self, max_depth: int, current_depth: int) -> str:
        if current_depth >= max_depth or self._rng.random() < 0.35:
            return self._random_terminal()

        choice = self._rng.choice(("unary", "binary", "window", "clip"))
        if choice == "unary":
            fn = self._rng.choice(("abs", "normalize", "rank_cs"))
            return f"{fn}({self._random_expr(max_depth, current_depth + 1)})"

        if choice == "binary":
            fn = self._rng.choice(("add", "sub", "mul", "div"))
            lhs = self._random_expr(max_depth, current_depth + 1)
            rhs = self._random_expr(max_depth, current_depth + 1)
            return f"{fn}({lhs}, {rhs})"

        if choice == "window":
            fn = self._rng.choice(("ret_n", "vol_n", "rsi", "sma", "ema", "zscore", "ts_rank", "lag", "delta"))
            expr = self._random_expr(max_depth, current_depth + 1)
            window = self._rng.choice((2, 3, 5, 10, 14, 20, 30, 60, 120, 200))
            return f"{fn}({expr}, {window})"

        # clip
        expr = self._random_expr(max_depth, current_depth + 1)
        lo = self._rng.choice((-5, -4, -3, -2))
        hi = self._rng.choice((2, 3, 4, 5))
        return f"clip({expr}, {lo}, {hi})"

    def _random_terminal(self) -> str:
        terminals = [
            "close",
            "ret_n(close, 5)",
            "ret_n(close, 20)",
            "vol_n(close, 20)",
            "rsi(close, 14)",
            "macd(close, 12, 26, 9)",
            "atr(high, low, close, 14)",
            "zscore(volume, 20)",
        ]
        return self._rng.choice(terminals)

    @staticmethod
    def _passes_quality_gate(normalized_expression: str) -> bool:
        """
        Basic expression-level quality gate.

        Week 2 heuristic:
        - Reject bare raw price/volume fields.
        - Require at least one signal-like transform token.
        """
        expr = normalized_expression.strip()
        if expr in {"open", "high", "low", "close", "volume", "dollar_volume", "vwap"}:
            return False

        required_tokens = (
            "ret_n(",
            "vol_n(",
            "rsi(",
            "macd(",
            "zscore(",
            "rank_cs(",
            "normalize(",
            "delta(",
            "ts_rank(",
            "atr(",
        )
        if any(tok in expr for tok in required_tokens):
            return True

        tokens = set(re.findall(r"[A-Za-z_]+", expr))
        return bool(
            tokens.intersection(
                {"ret_n", "vol_n", "rsi", "macd", "zscore", "rank_cs", "normalize", "delta", "ts_rank", "atr"}
            )
        )
