"""
Tests for slow-lane evaluator.
"""

from typing import Dict, List, Tuple

import numpy as np
import pandas as pd

from factor_research_codex1.evaluation.slow_lane import SlowLaneConfig, SlowLaneEvaluator
from factor_research_codex1.miner.generator import CandidateFactor


def _generate_data(n_symbols=16, n_days=260, seed=7) -> Dict[str, pd.DataFrame]:
    rng = np.random.RandomState(seed)
    dates = pd.bdate_range("2023-01-02", periods=n_days)
    out: Dict[str, pd.DataFrame] = {}

    for i in range(n_symbols):
        sym = f"S{i:02d}"
        drift = 0.0008 if i < n_symbols // 2 else -0.0002
        ret = rng.normal(drift, 0.02, n_days)
        close = 100 * np.exp(np.cumsum(ret))
        high = close * (1 + rng.uniform(0.001, 0.01, n_days))
        low = close * (1 - rng.uniform(0.001, 0.01, n_days))
        open_ = close * (1 + rng.normal(0, 0.002, n_days))
        volume = rng.randint(800_000, 8_000_000, n_days)
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
    def __init__(self, data: Dict[str, pd.DataFrame]):
        self._data = data

    def load_all(self):
        return self._data

    def get_trading_dates(self) -> List[str]:
        all_dates = set()
        for df in self._data.values():
            all_dates.update(df["date"].astype(str).tolist())
        return sorted(all_dates)

    def slice_to_date(self, date: str):
        out = {}
        for sym, df in self._data.items():
            cut = df[df["date"].astype(str) <= date].reset_index(drop=True)
            if len(cut) >= 40:
                out[sym] = cut
        return out

    def get_prices_at(self, date: str):
        p = {}
        for sym, df in self._data.items():
            row = df[df["date"].astype(str) == date]
            if not row.empty:
                p[sym] = float(row.iloc[-1]["close"])
        return p

    def get_benchmark_nav(self, symbol: str = "SPY") -> List[Tuple[str, float]]:
        dates = self.get_trading_dates()
        # synthetic benchmark trend
        if symbol == "SPY":
            vals = [100 * (1.00035 ** i) for i in range(len(dates))]
        elif symbol == "QQQ":
            vals = [100 * (1.00045 ** i) for i in range(len(dates))]
        else:
            vals = [100 * (1.00030 ** i) for i in range(len(dates))]
        return list(zip(dates, vals))


class TestSlowLaneEvaluator:
    def test_single_candidate(self):
        data = _generate_data()
        adapter = _MockUSAdapter(data)
        evaluator = SlowLaneEvaluator(
            adapter=adapter,
            config=SlowLaneConfig(
                top_n=8,
                rebalance_freq="W",
                weighting="equal",
                transaction_cost_bps=5.0,
            ),
        )
        candidate = CandidateFactor(
            candidate_id="C001",
            expression="ret_n(close, 20)",
            normalized_expression="ret_n(close, 20)",
            source="unit",
            hypothesis_id="trend",
        )
        metrics = evaluator.evaluate_candidates([candidate])
        assert len(metrics) == 1
        m = metrics[0]
        assert m.n_days > 50
        assert m.n_rebalances > 0
        assert m.n_trades > 0
        assert m.first_signal_date != ""
        assert m.first_activation_date != ""
        assert m.first_activation_date > m.first_signal_date
        assert len(m.benchmark_comparisons) == 3

    def test_multiple_candidates_sorted(self):
        data = _generate_data()
        adapter = _MockUSAdapter(data)
        evaluator = SlowLaneEvaluator(
            adapter=adapter,
            config=SlowLaneConfig(top_n=10, rebalance_freq="W", weighting="score_weighted"),
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
                hypothesis_id="vol_price",
            ),
        ]
        metrics = evaluator.evaluate_candidates(candidates)
        assert len(metrics) == 3
        scores = [m.quality_score for m in metrics]
        assert scores == sorted(scores, reverse=True)

