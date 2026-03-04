"""
Fast-lane evaluator for autonomous factor candidates.

Uses:
- DSL execution engine
- Existing factor-study IC framework
- Unified raw metric aggregation
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np
import pandas as pd
import warnings

from backtest.config import FREQ_DAYS
from backtest.factor_study.forward_returns import build_return_matrix
from backtest.factor_study.ic_analysis import ICResult, analyze_ic
from backtest.factor_study.protocol import FactorMeta
from factor_research_codex1.evaluation.expression_engine import DSLExpressionEngine
from factor_research_codex1.miner.generator import CandidateFactor


@dataclass
class FastLaneConfig:
    """Config for fast-lane evaluation."""

    computation_freq: str = "W"
    forward_horizons: List[int] = field(default_factory=lambda: [5, 10, 20])
    n_quantiles: int = 5
    min_score_symbols: int = 10
    min_score_dates: int = 12


@dataclass
class CandidateFastMetrics:
    """Evaluation summary for a candidate."""

    candidate_id: str
    expression: str
    normalized_expression: str
    hypothesis_id: str
    source: str
    n_dates: int
    n_symbols: int
    mean_ic: float
    mean_ic_ir: float
    mean_spread: float
    stability: float
    quality_score: float = 0.0


class FastLaneEvaluator:
    """
    Evaluate generated candidates with IC/quantile-style metrics.

    Adapter interface (expected):
    - load_all()
    - get_trading_dates()
    - slice_to_date(date)
    """

    def __init__(
        self,
        adapter,
        config: Optional[FastLaneConfig] = None,
        engine: Optional[DSLExpressionEngine] = None,
    ):
        self.adapter = adapter
        self.config = config or FastLaneConfig()
        self.engine = engine or DSLExpressionEngine()

    def evaluate_candidates(
        self,
        candidates: Sequence[CandidateFactor],
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        max_candidates: Optional[int] = None,
    ) -> List[CandidateFastMetrics]:
        """
        Evaluate candidates and return ranked list by quality score.
        """
        if not candidates:
            return []

        full_data = self.adapter.load_all()
        all_dates = self.adapter.get_trading_dates()
        if start_date:
            all_dates = [d for d in all_dates if d >= start_date]
        if end_date:
            all_dates = [d for d in all_dates if d <= end_date]
        if not all_dates:
            return []

        freq_days = FREQ_DAYS.get(self.config.computation_freq, 5)
        computation_dates = all_dates[::freq_days]
        if len(computation_dates) < 3:
            return []

        return_matrices = build_return_matrix(
            full_data,
            computation_dates,
            self.config.forward_horizons,
        )

        metrics: List[CandidateFastMetrics] = []
        target = candidates[:max_candidates] if max_candidates is not None else candidates

        for candidate in target:
            score_history, n_dates, n_symbols = self._build_score_history(
                candidate.normalized_expression,
                computation_dates,
            )

            # quick guardrail
            if n_dates < self.config.min_score_dates or n_symbols < self.config.min_score_symbols:
                metrics.append(
                    CandidateFastMetrics(
                        candidate_id=candidate.candidate_id,
                        expression=candidate.expression,
                        normalized_expression=candidate.normalized_expression,
                        hypothesis_id=candidate.hypothesis_id,
                        source=candidate.source,
                        n_dates=n_dates,
                        n_symbols=n_symbols,
                        mean_ic=0.0,
                        mean_ic_ir=0.0,
                        mean_spread=0.0,
                        stability=0.0,
                    )
                )
                continue

            ic_results = self._evaluate_ic(score_history, computation_dates, return_matrices, candidate.candidate_id)
            mean_ic, mean_ic_ir, mean_spread, stability = self._aggregate_ic(ic_results)

            metrics.append(
                CandidateFastMetrics(
                    candidate_id=candidate.candidate_id,
                    expression=candidate.expression,
                    normalized_expression=candidate.normalized_expression,
                    hypothesis_id=candidate.hypothesis_id,
                    source=candidate.source,
                    n_dates=n_dates,
                    n_symbols=n_symbols,
                    mean_ic=mean_ic,
                    mean_ic_ir=mean_ic_ir,
                    mean_spread=mean_spread,
                    stability=stability,
                )
            )

        self._attach_quality_scores(metrics)
        metrics.sort(key=lambda x: x.quality_score, reverse=True)
        return metrics

    def to_dataframe(self, metrics: Sequence[CandidateFastMetrics]) -> pd.DataFrame:
        """Convert metric list to DataFrame."""
        if not metrics:
            return pd.DataFrame()
        rows = [
            {
                "candidate_id": m.candidate_id,
                "expression": m.expression,
                "normalized_expression": m.normalized_expression,
                "hypothesis_id": m.hypothesis_id,
                "source": m.source,
                "n_dates": m.n_dates,
                "n_symbols": m.n_symbols,
                "mean_ic": m.mean_ic,
                "mean_ic_ir": m.mean_ic_ir,
                "mean_spread": m.mean_spread,
                "stability": m.stability,
                "quality_score": m.quality_score,
            }
            for m in metrics
        ]
        return pd.DataFrame(rows)

    def _build_score_history(
        self,
        expression: str,
        computation_dates: Sequence[str],
    ) -> Tuple[Dict[str, List[Tuple[str, float]]], int, int]:
        history: Dict[str, List[Tuple[str, float]]] = defaultdict(list)
        symbols_seen = set()
        dates_with_scores = 0

        for date in computation_dates:
            sliced = self.adapter.slice_to_date(date)
            if not sliced:
                continue
            result = self.engine.evaluate(expression, sliced)
            if not result.is_valid or not result.scores:
                continue

            n_for_date = 0
            for sym, score in result.scores.items():
                if not np.isfinite(score):
                    continue
                history[sym].append((date, float(score)))
                symbols_seen.add(sym)
                n_for_date += 1
            if n_for_date > 0:
                dates_with_scores += 1

        return dict(history), dates_with_scores, len(symbols_seen)

    def _evaluate_ic(
        self,
        score_history: Dict[str, List[Tuple[str, float]]],
        computation_dates: Sequence[str],
        return_matrices: Dict[int, pd.DataFrame],
        factor_name: str,
    ) -> List[ICResult]:
        meta = FactorMeta(
            name=factor_name,
            score_name="score",
            score_range=(-1e9, 1e9),
            higher_is_stronger=True,
            min_data_days=1,
        )
        with warnings.catch_warnings():
            # Some candidates are cross-sectionally constant on certain dates.
            # Those dates are ignored by analyze_ic when corr is NaN.
            warnings.filterwarnings(
                "ignore",
                message="An input array is constant; the correlation coefficient is not defined.",
            )
            ic_results, _ = analyze_ic(
                factor_meta=meta,
                score_history=score_history,
                return_matrices=return_matrices,
                computation_dates=list(computation_dates),
                n_quantiles=self.config.n_quantiles,
            )
        return ic_results

    @staticmethod
    def _aggregate_ic(ic_results: Sequence[ICResult]) -> Tuple[float, float, float, float]:
        if not ic_results:
            return 0.0, 0.0, 0.0, 0.0

        mean_ic = float(np.mean([x.mean_ic for x in ic_results]))
        mean_ic_ir = float(np.mean([x.ic_ir for x in ic_results]))
        mean_spread = float(np.mean([x.top_bottom_spread for x in ic_results]))
        vals = np.array([x.mean_ic for x in ic_results], dtype=float)

        # bounded stability score in [0, 1]:
        #   sign consistency * dispersion decay
        sign_consistency = float(abs(np.sum(np.sign(vals))) / len(vals))
        dispersion = float(np.std(vals, ddof=1)) if len(vals) > 1 else 0.0
        stability = float(sign_consistency * np.exp(-25.0 * dispersion))
        return mean_ic, mean_ic_ir, mean_spread, stability

    @staticmethod
    def _zscore(values: List[float]) -> List[float]:
        arr = np.array(values, dtype=float)
        mean = float(np.mean(arr))
        std = float(np.std(arr, ddof=0))
        if std <= 1e-12:
            return [0.0 for _ in values]
        return [float((x - mean) / std) for x in arr]

    def _attach_quality_scores(self, metrics: List[CandidateFastMetrics]) -> None:
        if not metrics:
            return
        z_ic = self._zscore([m.mean_ic for m in metrics])
        z_ir = self._zscore([m.mean_ic_ir for m in metrics])
        z_spread = self._zscore([m.mean_spread for m in metrics])
        z_stab = self._zscore([m.stability for m in metrics])

        for i, m in enumerate(metrics):
            m.quality_score = (
                0.40 * z_ic[i]
                + 0.30 * z_ir[i]
                + 0.20 * z_spread[i]
                + 0.10 * z_stab[i]
            )
