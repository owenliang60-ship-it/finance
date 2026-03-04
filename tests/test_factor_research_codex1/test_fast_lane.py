"""
Tests for fast-lane candidate evaluation.
"""

from typing import Dict

import numpy as np
import pandas as pd

from factor_research_codex1.evaluation.fast_lane import FastLaneConfig, FastLaneEvaluator
from factor_research_codex1.miner.generator import CandidateFactor


def _generate_price_dict(n_symbols=12, n_days=220, seed=42) -> Dict[str, pd.DataFrame]:
    rng = np.random.RandomState(seed)
    dates = pd.bdate_range("2022-01-03", periods=n_days)
    out: Dict[str, pd.DataFrame] = {}

    for i in range(n_symbols):
        sym = f"S{i:02d}"
        drift = 0.0008 if i < n_symbols // 2 else -0.0003
        noise = rng.normal(drift, 0.018, n_days)
        close = 80 * np.exp(np.cumsum(noise))
        high = close * (1 + rng.uniform(0.001, 0.01, n_days))
        low = close * (1 - rng.uniform(0.001, 0.01, n_days))
        open_ = close * (1 + rng.normal(0, 0.002, n_days))
        volume = rng.randint(500_000, 5_000_000, n_days)
        out[sym] = pd.DataFrame(
            {
                "date": [d.strftime("%Y-%m-%d") for d in dates],
                "open": open_,
                "high": high,
                "low": low,
                "close": close,
                "volume": volume,
            }
        )
    return out


class _MockUSAdapter:
    def __init__(self, data):
        self._data = data

    def load_all(self):
        return self._data

    def get_trading_dates(self):
        all_dates = set()
        for df in self._data.values():
            all_dates.update(df["date"].astype(str).tolist())
        return sorted(all_dates)

    def slice_to_date(self, date):
        out = {}
        for sym, df in self._data.items():
            cut = df[df["date"].astype(str) <= date].reset_index(drop=True)
            if len(cut) >= 40:
                out[sym] = cut
        return out


class TestFastLaneEvaluator:
    def test_evaluate_single_candidate(self):
        data = _generate_price_dict()
        adapter = _MockUSAdapter(data)
        evaluator = FastLaneEvaluator(
            adapter=adapter,
            config=FastLaneConfig(
                computation_freq="W",
                forward_horizons=[5, 10],
                n_quantiles=5,
                min_score_symbols=8,
                min_score_dates=10,
            ),
        )

        candidates = [
            CandidateFactor(
                candidate_id="C001",
                expression="ret_n(close, 20)",
                normalized_expression="ret_n(close, 20)",
                source="unit",
                hypothesis_id="trend_following",
            )
        ]
        metrics = evaluator.evaluate_candidates(candidates)
        assert len(metrics) == 1
        m = metrics[0]
        assert m.candidate_id == "C001"
        assert m.n_dates >= 10
        assert m.n_symbols >= 8
        assert np.isfinite(m.quality_score)

    def test_evaluate_multiple_candidates_sorted(self):
        data = _generate_price_dict()
        adapter = _MockUSAdapter(data)
        evaluator = FastLaneEvaluator(
            adapter=adapter,
            config=FastLaneConfig(
                computation_freq="W",
                forward_horizons=[5],
                n_quantiles=5,
                min_score_symbols=8,
                min_score_dates=10,
            ),
        )
        candidates = [
            CandidateFactor(
                candidate_id="C001",
                expression="ret_n(close, 20)",
                normalized_expression="ret_n(close, 20)",
                source="unit",
                hypothesis_id="trend",
            ),
            CandidateFactor(
                candidate_id="C002",
                expression="sub(50, rsi(close, 14))",
                normalized_expression="sub(50, rsi(close, 14))",
                source="unit",
                hypothesis_id="mean_reversion",
            ),
            CandidateFactor(
                candidate_id="C003",
                expression="mul(ret_n(close, 5), zscore(volume, 20))",
                normalized_expression="mul(ret_n(close, 5), zscore(volume, 20))",
                source="unit",
                hypothesis_id="volume_price_interaction",
            ),
        ]
        metrics = evaluator.evaluate_candidates(candidates)
        assert len(metrics) == 3
        scores = [m.quality_score for m in metrics]
        assert scores == sorted(scores, reverse=True)

