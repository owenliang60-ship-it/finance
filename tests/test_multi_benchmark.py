"""
多基准因子研究 — 测试

覆盖:
- POOL_AVG NAV 合成正确性
- 多基准结果数量 (2 基准 × 1 因子 = 2 结果)
- benchmark_label 填充
- 因子分数只算一次 (不乘以基准数)
- 向后兼容 (旧 config benchmark_symbol)
- POOL_AVG 哨兵在 adapter 中的处理
"""

import math
from collections import defaultdict
from typing import Dict, List, Tuple
from unittest.mock import MagicMock, patch

import numpy as np
import pandas as pd
import pytest

from backtest.config import FactorStudyConfig, us_factor_study
from backtest.factor_study.protocol import Factor, FactorMeta
from backtest.factor_study.runner import FactorStudyResults, FactorStudyRunner


# ── 测试数据 fixtures ──────────────────────────────────────

def _make_price_df(prices: List[float], start_date: str = "2023-01-02") -> pd.DataFrame:
    """生成价格 DataFrame"""
    dates = pd.bdate_range(start=start_date, periods=len(prices))
    return pd.DataFrame({
        "date": [d.strftime("%Y-%m-%d") for d in dates],
        "close": prices,
    })


def _make_mock_adapter(n_symbols: int = 5, n_days: int = 200):
    """创建 mock adapter，带确定性数据"""
    np.random.seed(42)
    symbols = [f"SYM{i}" for i in range(n_symbols)]
    start_date = "2023-01-02"
    dates = pd.bdate_range(start=start_date, periods=n_days)
    date_strs = [d.strftime("%Y-%m-%d") for d in dates]

    price_cache = {}
    for sym in symbols:
        # 每只股票从 100 开始，加上小随机游走
        base = 100.0
        returns = np.random.normal(0.001, 0.02, n_days)
        prices = base * np.cumprod(1 + returns)
        price_cache[sym] = pd.DataFrame({
            "date": date_strs,
            "close": prices,
        })

    # Benchmark 数据 (QQQ)
    qqq_returns = np.random.normal(0.0008, 0.015, n_days)
    qqq_prices = 350.0 * np.cumprod(1 + qqq_returns)
    qqq_df = pd.DataFrame({
        "date": date_strs,
        "close": qqq_prices,
    })

    adapter = MagicMock()
    adapter._price_cache = price_cache
    adapter.load_all.return_value = price_cache

    adapter.get_trading_dates.return_value = date_strs

    def slice_to_date(date):
        result = {}
        for sym, df in price_cache.items():
            mask = df["date"] <= date
            cut = df[mask]
            if len(cut) >= 70:
                result[sym] = cut.reset_index(drop=True)
        return result

    adapter.slice_to_date.side_effect = slice_to_date

    def get_benchmark_nav(symbol="SPY"):
        if symbol == "POOL_AVG":
            # 合成等权 NAV
            close_series = {}
            for sym, df in price_cache.items():
                s = df.set_index("date")["close"].astype(float)
                close_series[sym] = s
            close_df = pd.DataFrame(close_series).sort_index()
            daily_returns = close_df.pct_change()
            pool_avg_return = daily_returns.mean(axis=1)
            nav = (1 + pool_avg_return).cumprod() * 100
            nav.iloc[0] = 100.0
            return [(d, float(v)) for d, v in nav.items() if not np.isnan(v)]
        elif symbol == "QQQ":
            return list(zip(qqq_df["date"], qqq_df["close"].astype(float)))
        else:
            return []

    adapter.get_benchmark_nav.side_effect = get_benchmark_nav

    return adapter, price_cache


class _DummyFactor(Factor):
    """测试用因子: 按 symbol 名排序分配分数"""

    def __init__(self):
        self._compute_call_count = 0

    @property
    def meta(self) -> FactorMeta:
        return FactorMeta(
            name="Dummy",
            score_name="score",
            score_range=(0, 100),
            higher_is_stronger=True,
            min_data_days=70,
        )

    def compute(self, price_dict, date: str) -> Dict[str, float]:
        self._compute_call_count += 1
        symbols = sorted(price_dict.keys())
        n = len(symbols)
        return {sym: (i + 1) * 100.0 / n for i, sym in enumerate(symbols)}


# ── 测试用例 ──────────────────────────────────────────────

class TestPoolAvgNavComputation:
    """测试 POOL_AVG 等权 NAV 合成数学正确性"""

    def test_pool_avg_nav_computation(self):
        """等权 NAV = 各股票日收益率横截面均值的累乘"""
        from backtest.adapters.us_stocks import USStocksAdapter

        adapter = USStocksAdapter.__new__(USStocksAdapter)
        adapter._price_cache = {}
        adapter._symbols = None

        # 3 只股票，4 天数据
        adapter._price_cache = {
            "A": pd.DataFrame({
                "date": ["2023-01-02", "2023-01-03", "2023-01-04", "2023-01-05"],
                "close": [100.0, 110.0, 105.0, 115.0],
            }),
            "B": pd.DataFrame({
                "date": ["2023-01-02", "2023-01-03", "2023-01-04", "2023-01-05"],
                "close": [200.0, 190.0, 200.0, 210.0],
            }),
            "C": pd.DataFrame({
                "date": ["2023-01-02", "2023-01-03", "2023-01-04", "2023-01-05"],
                "close": [50.0, 55.0, 52.0, 54.0],
            }),
        }

        nav = adapter._compute_pool_avg_nav()
        assert len(nav) == 4

        # 手动计算
        # Day 1: NAV = 100
        assert nav[0][0] == "2023-01-02"
        assert nav[0][1] == 100.0

        # Day 2 returns: A=+10%, B=-5%, C=+10% → avg = +5%
        # NAV = 100 * 1.05 = 105
        assert nav[1][0] == "2023-01-03"
        assert abs(nav[1][1] - 105.0) < 0.01

        # Day 3 returns: A=-4.545%, B=+5.263%, C=-5.455% → avg = -1.579%
        # NAV = 105 * (1 - 0.01579) ≈ 103.342
        r_a = 105.0 / 110.0 - 1  # -0.04545
        r_b = 200.0 / 190.0 - 1  # +0.05263
        r_c = 52.0 / 55.0 - 1    # -0.05455
        avg_r = (r_a + r_b + r_c) / 3
        expected_nav2 = 105.0 * (1 + avg_r)
        assert abs(nav[2][1] - expected_nav2) < 0.01

    def test_pool_avg_returns_empty_on_no_data(self):
        """无数据时返回空列表"""
        from backtest.adapters.us_stocks import USStocksAdapter

        adapter = USStocksAdapter.__new__(USStocksAdapter)
        adapter._price_cache = {}
        adapter._symbols = None

        # Mock load_all to do nothing (cache already empty)
        adapter.load_all = MagicMock(return_value={})

        nav = adapter._compute_pool_avg_nav()
        assert nav == []


class TestPoolAvgSentinelInAdapter:
    """测试 POOL_AVG 哨兵值在 adapter 中的处理"""

    def test_pool_avg_sentinel_in_adapter(self):
        """get_benchmark_nav('POOL_AVG') 返回有效 NAV"""
        from backtest.adapters.us_stocks import USStocksAdapter

        adapter = USStocksAdapter.__new__(USStocksAdapter)
        adapter._price_cache = {
            "X": pd.DataFrame({
                "date": ["2023-01-02", "2023-01-03", "2023-01-04"],
                "close": [100.0, 105.0, 110.0],
            }),
            "Y": pd.DataFrame({
                "date": ["2023-01-02", "2023-01-03", "2023-01-04"],
                "close": [200.0, 210.0, 200.0],
            }),
        }
        adapter._symbols = None

        nav = adapter.get_benchmark_nav("POOL_AVG")
        assert len(nav) == 3
        assert nav[0][1] == 100.0  # 起始
        # 所有值应为正数
        for _, v in nav:
            assert v > 0


class TestMultiBenchmarkResultCount:
    """测试多基准产生正确的结果数量"""

    def test_multi_benchmark_produces_correct_result_count(self):
        """2 基准 × 1 因子 = 2 结果"""
        adapter, _ = _make_mock_adapter()

        config = FactorStudyConfig(
            market="us_stocks",
            computation_freq="W",
            forward_horizons=[5, 10],
            n_quantiles=5,
            benchmark_symbols=["QQQ", "POOL_AVG"],
            oos_fraction=0.0,  # 不做 OOS 简化测试
        )

        runner = FactorStudyRunner(config, adapter)
        runner.add_factor(_DummyFactor())

        results = runner.run()
        assert len(results) == 2  # 2 基准 × 1 因子

    def test_single_benchmark_produces_one_result(self):
        """1 基准 × 1 因子 = 1 结果"""
        adapter, _ = _make_mock_adapter()

        config = FactorStudyConfig(
            market="us_stocks",
            computation_freq="W",
            forward_horizons=[5, 10],
            n_quantiles=5,
            benchmark_symbols=["QQQ"],
            oos_fraction=0.0,
        )

        runner = FactorStudyRunner(config, adapter)
        runner.add_factor(_DummyFactor())

        results = runner.run()
        assert len(results) == 1

    def test_no_benchmark_produces_one_result(self):
        """无基准 × 1 因子 = 1 结果 (原始收益)"""
        adapter, _ = _make_mock_adapter()

        config = FactorStudyConfig(
            market="us_stocks",
            computation_freq="W",
            forward_horizons=[5, 10],
            n_quantiles=5,
            benchmark_symbols=[],
            oos_fraction=0.0,
        )

        runner = FactorStudyRunner(config, adapter)
        runner.add_factor(_DummyFactor())

        results = runner.run()
        assert len(results) == 1
        assert results[0].benchmark_label == ""


class TestBenchmarkLabelPopulated:
    """测试每个结果有正确的 benchmark_label"""

    def test_benchmark_label_populated(self):
        """多基准结果中 benchmark_label 正确"""
        adapter, _ = _make_mock_adapter()

        config = FactorStudyConfig(
            market="us_stocks",
            computation_freq="W",
            forward_horizons=[5, 10],
            n_quantiles=5,
            benchmark_symbols=["QQQ", "POOL_AVG"],
            oos_fraction=0.0,
        )

        runner = FactorStudyRunner(config, adapter)
        runner.add_factor(_DummyFactor())

        results = runner.run()
        labels = [r.benchmark_label for r in results]
        assert "QQQ" in labels
        assert "POOL_AVG" in labels


class TestScoresComputedOnce:
    """测试因子分数只算一次 (不随基准数翻倍)"""

    def test_scores_computed_once_per_factor(self):
        """mock compute()，验证调用次数不随基准数翻倍"""
        adapter, _ = _make_mock_adapter()

        # 先跑 1 基准，记录 compute 调用次数
        config_1 = FactorStudyConfig(
            market="us_stocks",
            computation_freq="W",
            forward_horizons=[5],
            n_quantiles=5,
            benchmark_symbols=["QQQ"],
            oos_fraction=0.0,
        )
        factor_1 = _DummyFactor()
        runner_1 = FactorStudyRunner(config_1, adapter)
        runner_1.add_factor(factor_1)
        runner_1.run()
        calls_with_1_bench = factor_1._compute_call_count

        # 再跑 2 基准
        config_2 = FactorStudyConfig(
            market="us_stocks",
            computation_freq="W",
            forward_horizons=[5],
            n_quantiles=5,
            benchmark_symbols=["QQQ", "POOL_AVG"],
            oos_fraction=0.0,
        )
        factor_2 = _DummyFactor()
        runner_2 = FactorStudyRunner(config_2, adapter)
        runner_2.add_factor(factor_2)
        results = runner_2.run()

        # 因子 compute 调用次数相同 (不随基准数翻倍)
        assert factor_2._compute_call_count == calls_with_1_bench
        # 但结果有 2 个
        assert len(results) == 2


class TestBackwardCompatSingleBenchmark:
    """测试旧 config benchmark_symbol 仍然工作"""

    def test_backward_compat_single_benchmark(self):
        """旧 config benchmark_symbol='QQQ' → 自动迁移到 benchmark_symbols"""
        config = FactorStudyConfig(
            market="us_stocks",
            benchmark_symbol="QQQ",
        )
        assert config.benchmark_symbols == ["QQQ"]
        assert config.benchmark_symbol == "QQQ"

    def test_benchmark_symbols_takes_precedence(self):
        """benchmark_symbols 有值时不被 benchmark_symbol 覆盖"""
        config = FactorStudyConfig(
            market="us_stocks",
            benchmark_symbol="SPY",
            benchmark_symbols=["QQQ", "POOL_AVG"],
        )
        # benchmark_symbols 已经有值，不被覆盖
        assert config.benchmark_symbols == ["QQQ", "POOL_AVG"]

    def test_factory_default_benchmarks(self):
        """us_factor_study() 默认 QQQ + POOL_AVG"""
        config = us_factor_study()
        assert config.benchmark_symbols == ["QQQ", "POOL_AVG"]
        assert config.benchmark_symbol == "QQQ"  # 第一个同步过来

    def test_factory_override_benchmarks(self):
        """us_factor_study(benchmark_symbols=["SPY"]) 覆盖默认"""
        config = us_factor_study(benchmark_symbols=["SPY"])
        assert config.benchmark_symbols == ["SPY"]

    def test_old_style_factory_override(self):
        """旧式 us_factor_study(benchmark_symbol="SPY") — 真实场景不传 benchmark_symbols"""
        config = us_factor_study(benchmark_symbol="SPY")
        # benchmark_symbol 指定 → 覆盖工厂默认，迁移到 benchmark_symbols
        assert config.benchmark_symbols == ["SPY"]
        assert config.benchmark_symbol == "SPY"

    def test_old_style_factory_override_with_empty_list(self):
        """旧式 + 显式空列表"""
        config = us_factor_study(benchmark_symbol="SPY", benchmark_symbols=[])
        assert config.benchmark_symbols == ["SPY"]
