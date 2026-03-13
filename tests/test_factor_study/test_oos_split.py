"""
IS/OOS 分割测试 — R3: In-Sample / Out-of-Sample Time Split
"""

import numpy as np
import pandas as pd
import pytest

from backtest.config import FactorStudyConfig
from backtest.factor_study.protocol import Factor, FactorMeta
from backtest.factor_study.runner import FactorStudyRunner, _filter_score_history
from backtest.factor_study.signals import SignalDefinition, SignalType
from typing import Dict, List, Tuple


# ── 合成数据 + 适配器 ──────────────────────────────────

def _generate_prices(n_stocks=10, n_days=300, seed=42):
    """生成 300 天数据，足以满足 OOS 最小门槛"""
    rng = np.random.RandomState(seed)
    base_date = pd.Timestamp("2023-01-01")
    dates = pd.bdate_range(base_date, periods=n_days)

    price_dict = {}
    for i in range(n_stocks):
        symbol = f"SYN{i+1:02d}"
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
    def __init__(self, price_dict=None):
        self._data = price_dict or _generate_prices()

    def load_all(self):
        return self._data

    def get_trading_dates(self):
        all_dates = set()
        for df in self._data.values():
            all_dates.update(df["date"].astype(str).tolist())
        return sorted(all_dates)

    def get_benchmark_nav(self, symbol="SPY"):
        all_dates = self.get_trading_dates()
        rng = np.random.RandomState(99)
        prices = 100 * np.exp(np.cumsum(rng.normal(0.0003, 0.01, len(all_dates))))
        return list(zip(all_dates, prices.tolist()))

    def slice_to_date(self, date):
        sliced = {}
        for sym, df in self._data.items():
            cut = df[df["date"].astype(str) <= date].reset_index(drop=True)
            if len(cut) >= 10:
                sliced[sym] = cut
        return sliced


class RankFactor(Factor):
    @property
    def meta(self) -> FactorMeta:
        return FactorMeta(
            name="TestRank", score_name="rank",
            score_range=(0, 99), higher_is_stronger=True,
            min_data_days=10,
        )

    def compute(self, price_dict, date):
        scores = {}
        for sym, df in price_dict.items():
            if len(df) < 2:
                continue
            ret = df["close"].iloc[-1] / df["close"].iloc[0] - 1
            scores[sym] = ret
        if not scores:
            return {}
        vals = sorted(scores.values())
        n = len(vals)
        return {
            sym: min(99, int((vals.index(val) + 1) / n * 100))
            for sym, val in scores.items()
        }


# ── 测试 ──────────────────────────────────────────────

class TestOOSSplit:
    def test_has_oos_with_enough_data(self):
        """300 天周频 ≈ 60 计算日, 30% OOS = 18 日 < 50 门槛 → 跳过.
        降低门槛到 10 → 有 OOS."""
        config = FactorStudyConfig(
            market="us_stocks",
            computation_freq="W",
            forward_horizons=[5, 10],
            min_oos_dates=10,
        )
        adapter = MockAdapter()
        runner = FactorStudyRunner(config, adapter)
        runner.add_factor(RankFactor())

        results = runner.run()
        r = results[0]

        assert not r.oos_skipped
        assert r.oos_ic_results is not None
        assert len(r.oos_ic_results) >= 1
        assert r.oos_event_results is not None

    def test_oos_skipped_with_insufficient_data(self):
        """300 天周频 ≈ 60 计算日, 30% = 18 日 < 50 门槛 → 跳过."""
        config = FactorStudyConfig(
            market="us_stocks",
            computation_freq="W",
            forward_horizons=[5],
            min_oos_dates=50,  # 门槛很高
        )
        adapter = MockAdapter()
        runner = FactorStudyRunner(config, adapter)
        runner.add_factor(RankFactor())

        results = runner.run()
        r = results[0]

        assert r.oos_skipped
        assert r.oos_ic_results is None
        assert r.oos_event_results is None

    def test_is_dates_before_oos_dates(self):
        """IS 日期在 OOS 日期之前."""
        config = FactorStudyConfig(
            market="us_stocks",
            computation_freq="W",
            forward_horizons=[5],
            min_oos_dates=5,
        )
        adapter = MockAdapter()
        runner = FactorStudyRunner(config, adapter)
        runner.add_factor(RankFactor())

        results = runner.run()
        r = results[0]

        assert r.is_dates
        assert r.oos_dates
        # IS 的最后一天 < OOS 的第一天
        assert r.is_dates[-1] < r.oos_dates[0]

    def test_is_oos_cover_all_dates(self):
        """IS + OOS = 全部计算日期."""
        config = FactorStudyConfig(
            market="us_stocks",
            computation_freq="W",
            forward_horizons=[5],
            min_oos_dates=5,
        )
        adapter = MockAdapter()
        runner = FactorStudyRunner(config, adapter)
        runner.add_factor(RankFactor())

        results = runner.run()
        r = results[0]

        assert len(r.is_dates) + len(r.oos_dates) == r.n_computation_dates

    def test_is_results_always_present(self):
        """IS 结果始终存在."""
        config = FactorStudyConfig(
            market="us_stocks",
            computation_freq="W",
            forward_horizons=[5],
        )
        adapter = MockAdapter()
        runner = FactorStudyRunner(config, adapter)
        runner.add_factor(RankFactor())

        results = runner.run()
        r = results[0]

        assert r.ic_results is not None
        assert len(r.ic_results) >= 1


class TestFilterScoreHistory:
    def test_filters_by_date_set(self):
        history = {
            "AAPL": [("2024-01-01", 90), ("2024-01-08", 85), ("2024-01-15", 92)],
            "MSFT": [("2024-01-01", 75), ("2024-01-08", 80)],
        }
        dates_set = {"2024-01-01", "2024-01-15"}

        filtered = _filter_score_history(history, dates_set)

        assert "AAPL" in filtered
        assert len(filtered["AAPL"]) == 2
        assert filtered["AAPL"][0] == ("2024-01-01", 90)
        assert filtered["AAPL"][1] == ("2024-01-15", 92)
        assert "MSFT" in filtered
        assert len(filtered["MSFT"]) == 1

    def test_empty_after_filter_excluded(self):
        history = {
            "AAPL": [("2024-01-01", 90)],
        }
        dates_set = {"2024-02-01"}

        filtered = _filter_score_history(history, dates_set)
        assert "AAPL" not in filtered
