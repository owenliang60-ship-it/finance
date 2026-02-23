"""
FactorStudyRunner 集成测试

使用合成数据 + DummyFactor 验证完整流水线
"""

import numpy as np
import pandas as pd
import pytest
from typing import Dict, List, Tuple

from backtest.config import FactorStudyConfig
from backtest.factor_study.protocol import Factor, FactorMeta
from backtest.factor_study.runner import FactorStudyRunner
from backtest.factor_study.signals import SignalDefinition, SignalType


# ── 合成数据 ─────────────────────────────────────────────

def _generate_prices(n_stocks=10, n_days=100, seed=42):
    """
    生成合成价格数据, 前半股票有上升趋势 (模拟高 RS)
    """
    rng = np.random.RandomState(seed)
    base_date = pd.Timestamp("2023-01-01")
    dates = pd.bdate_range(base_date, periods=n_days)

    price_dict = {}
    for i in range(n_stocks):
        symbol = f"SYN{i+1:02d}"
        # 前半有正 drift, 后半负 drift
        drift = 0.001 * (n_stocks - i) if i < n_stocks // 2 else -0.0005
        returns = rng.normal(drift, 0.02, n_days)
        prices = 100 * np.exp(np.cumsum(returns))
        df = pd.DataFrame({
            "date": [d.strftime("%Y-%m-%d") for d in dates],
            "close": prices,
            "volume": rng.randint(1_000_000, 10_000_000, n_days),
        })
        price_dict[symbol] = df

    return price_dict


class MockAdapter:
    """模拟适配器"""

    def __init__(self, price_dict=None):
        self._data = price_dict or _generate_prices()

    def load_all(self):
        return self._data

    def get_trading_dates(self):
        all_dates = set()
        for df in self._data.values():
            all_dates.update(df["date"].astype(str).tolist())
        return sorted(all_dates)

    def slice_to_date(self, date: str):
        sliced = {}
        for sym, df in self._data.items():
            cut = df[df["date"].astype(str) <= date].reset_index(drop=True)
            if len(cut) >= 10:
                sliced[sym] = cut
        return sliced


class RankFactor(Factor):
    """
    测试用因子: 简单排名 (按累积收益排名)
    分数 = 累积收益的 percentile rank × 99
    """

    @property
    def meta(self) -> FactorMeta:
        return FactorMeta(
            name="TestRank",
            score_name="rank",
            score_range=(0, 99),
            higher_is_stronger=True,
            min_data_days=10,
        )

    def compute(
        self,
        price_dict: Dict[str, pd.DataFrame],
        date: str,
    ) -> Dict[str, float]:
        scores = {}
        for sym, df in price_dict.items():
            if len(df) < 2:
                continue
            ret = df["close"].iloc[-1] / df["close"].iloc[0] - 1
            scores[sym] = ret

        if not scores:
            return {}

        # 转为 0-99 rank
        vals = sorted(scores.values())
        n = len(vals)
        ranked = {}
        for sym, val in scores.items():
            pct = (vals.index(val) + 1) / n
            ranked[sym] = min(99, int(pct * 100))

        return ranked


# ── 测试 ─────────────────────────────────────────────────

class TestFactorStudyRunner:
    def test_basic_run(self):
        config = FactorStudyConfig(
            market="us_stocks",
            computation_freq="W",
            forward_horizons=[5, 10],
            n_quantiles=5,
        )
        adapter = MockAdapter()
        runner = FactorStudyRunner(config, adapter)
        runner.add_factor(RankFactor())

        results = runner.run()

        assert len(results) == 1
        r = results[0]
        assert r.factor_name == "TestRank"
        assert r.n_computation_dates > 0
        assert r.n_symbols > 0
        assert r.elapsed_seconds >= 0

    def test_ic_results_generated(self):
        config = FactorStudyConfig(
            market="us_stocks",
            computation_freq="W",
            forward_horizons=[5, 10],
            n_quantiles=5,
        )
        adapter = MockAdapter()
        runner = FactorStudyRunner(config, adapter)
        runner.add_factor(RankFactor())

        results = runner.run()
        r = results[0]

        # 应该有 IC 结果
        assert len(r.ic_results) >= 1

    def test_custom_sweep(self):
        config = FactorStudyConfig(
            market="us_stocks",
            computation_freq="W",
            forward_horizons=[5],
            n_quantiles=5,
        )
        adapter = MockAdapter()
        runner = FactorStudyRunner(config, adapter)
        runner.add_factor(RankFactor())

        # 自定义 sweep: 只有一个阈值
        custom_signals = [
            SignalDefinition(SignalType.THRESHOLD, 50),
        ]
        runner.set_sweep("TestRank", custom_signals)

        results = runner.run()
        r = results[0]

        # 应该有事件研究结果
        event_results = [e for e in r.event_results if e.signal_label == "threshold_50"]
        assert len(event_results) >= 1

    def test_no_factors_returns_empty(self):
        config = FactorStudyConfig(market="us_stocks")
        adapter = MockAdapter()
        runner = FactorStudyRunner(config, adapter)

        results = runner.run()
        assert results == []

    def test_date_range_filter(self):
        config = FactorStudyConfig(
            market="us_stocks",
            computation_freq="W",
            forward_horizons=[5],
            start_date="2023-03-01",
            end_date="2023-04-01",
        )
        adapter = MockAdapter()
        runner = FactorStudyRunner(config, adapter)
        runner.add_factor(RankFactor())

        results = runner.run()
        r = results[0]

        # 应该有更少的计算日期
        assert r.n_computation_dates < 10

    def test_multiple_factors(self):
        config = FactorStudyConfig(
            market="us_stocks",
            computation_freq="W",
            forward_horizons=[5],
        )
        adapter = MockAdapter()
        runner = FactorStudyRunner(config, adapter)
        runner.add_factor(RankFactor())
        runner.add_factor(RankFactor())  # 同一个因子加两次

        results = runner.run()
        assert len(results) == 2
