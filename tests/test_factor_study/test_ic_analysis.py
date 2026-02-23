"""
IC 分析测试 — Track 1
"""

import numpy as np
import pandas as pd
import pytest

from backtest.factor_study.protocol import FactorMeta
from backtest.factor_study.ic_analysis import analyze_ic, ICResult, ICDecayCurve


# ── 合成数据 ─────────────────────────────────────────────

def _make_perfect_data(n_dates=30, n_symbols=20):
    """
    构造完美正相关数据:
    - 因子分数 = 排名 (0..n_symbols-1)
    - 前向收益 = 因子分数 × 0.001 + noise
    → 预期 IC 接近 1.0
    """
    dates = [f"2024-{(i // 28) + 1:02d}-{(i % 28) + 1:02d}" for i in range(n_dates)]
    symbols = [f"SYM{i:02d}" for i in range(n_symbols)]
    rng = np.random.RandomState(42)

    score_history = {}
    for j, sym in enumerate(symbols):
        history = []
        for d in dates:
            score = float(j * 5)  # 0, 5, 10, ..., 95
            history.append((d, score))
        score_history[sym] = history

    # 前向收益: 高分 → 高收益
    horizons = [5, 10]
    return_matrices = {}
    for h in horizons:
        data = {}
        for sym in symbols:
            j = int(sym.replace("SYM", ""))
            rets = [j * 0.001 + rng.normal(0, 0.0005) for _ in dates]
            data[sym] = rets
        return_matrices[h] = pd.DataFrame(data, index=dates)

    meta = FactorMeta("TestFactor", "score", (0, 95), higher_is_stronger=True)
    return meta, score_history, return_matrices, dates, horizons


def _make_random_data(n_dates=30, n_symbols=20):
    """构造随机数据: IC 应接近 0"""
    dates = [f"2024-{(i // 28) + 1:02d}-{(i % 28) + 1:02d}" for i in range(n_dates)]
    symbols = [f"SYM{i:02d}" for i in range(n_symbols)]
    rng = np.random.RandomState(123)

    score_history = {}
    for sym in symbols:
        history = [(d, rng.uniform(0, 100)) for d in dates]
        score_history[sym] = history

    horizons = [5]
    return_matrices = {}
    for h in horizons:
        data = {sym: rng.normal(0, 0.02, n_dates).tolist() for sym in symbols}
        return_matrices[h] = pd.DataFrame(data, index=dates)

    meta = FactorMeta("RandomFactor", "score", (0, 100), higher_is_stronger=True)
    return meta, score_history, return_matrices, dates, horizons


# ── 测试 ─────────────────────────────────────────────────

class TestAnalyzeIC:
    def test_perfect_correlation(self):
        meta, scores, rets, dates, horizons = _make_perfect_data()
        ic_results, decay = analyze_ic(meta, scores, rets, dates, n_quantiles=5)

        assert len(ic_results) == 2  # 两个 horizon
        # IC 应接近 1.0 (完美正相关)
        for ic in ic_results:
            assert ic.mean_ic > 0.8
            assert ic.ic_ir > 1.0
            assert ic.ic_hit_rate > 0.9

    def test_quantile_monotonicity(self):
        """完美数据下，Q5 > Q1"""
        meta, scores, rets, dates, horizons = _make_perfect_data()
        ic_results, _ = analyze_ic(meta, scores, rets, dates, n_quantiles=5)

        for ic in ic_results:
            assert ic.quantile_returns[5] > ic.quantile_returns[1]
            assert ic.top_bottom_spread > 0

    def test_random_ic_near_zero(self):
        """随机数据下 IC 接近 0"""
        meta, scores, rets, dates, horizons = _make_random_data()
        ic_results, _ = analyze_ic(meta, scores, rets, dates, n_quantiles=5)

        assert len(ic_results) >= 1
        assert abs(ic_results[0].mean_ic) < 0.3  # 不应太大

    def test_decay_curve(self):
        meta, scores, rets, dates, horizons = _make_perfect_data()
        _, decay = analyze_ic(meta, scores, rets, dates)

        assert decay.factor_name == "TestFactor"
        assert len(decay.horizons) == 2
        assert len(decay.mean_ics) == 2

    def test_insufficient_data(self):
        """数据太少时应返回空"""
        meta = FactorMeta("X", "x", (0, 1), True)
        scores = {"A": [("2024-01-01", 1.0)]}
        rets = {5: pd.DataFrame({"A": [0.01]}, index=["2024-01-01"])}

        ic_results, decay = analyze_ic(meta, scores, rets, ["2024-01-01"])
        # 少于 5 个 symbols/dates → None 被过滤
        assert len(ic_results) == 0


class TestICResult:
    def test_dataclass_fields(self):
        ic = ICResult(
            factor_name="Test",
            horizon=5,
            mean_ic=0.05,
            std_ic=0.03,
            ic_ir=1.67,
            ic_hit_rate=0.65,
            quantile_returns={1: -0.01, 2: 0.0, 3: 0.005, 4: 0.01, 5: 0.02},
            top_bottom_spread=0.03,
        )
        assert ic.factor_name == "Test"
        assert ic.horizon == 5
        assert ic.ic_ir == 1.67
