"""
绩效指标计算测试
"""

import pytest
import numpy as np
from backtest.metrics import compute_metrics, _max_drawdown, BacktestMetrics


class TestMaxDrawdown:
    """最大回撤计算"""

    def test_no_drawdown(self):
        navs = np.array([100, 101, 102, 103, 104])
        dd, dur = _max_drawdown(navs)
        assert dd == 0.0
        assert dur == 0

    def test_simple_drawdown(self):
        navs = np.array([100, 110, 90, 95, 100])
        dd, dur = _max_drawdown(navs)
        # peak=110, trough=90, dd = (90-110)/110 = -18.18%
        assert dd == pytest.approx(-20 / 110, rel=1e-4)
        assert dur > 0

    def test_flat_line(self):
        navs = np.array([100, 100, 100])
        dd, dur = _max_drawdown(navs)
        assert dd == 0.0


class TestComputeMetrics:
    """完整指标计算"""

    def _make_nav(self, returns, start=1_000_000):
        """从日收益率生成 NAV 序列"""
        navs = [start]
        for r in returns:
            navs.append(navs[-1] * (1 + r))
        dates = [f"2024-01-{i+1:02d}" for i in range(len(navs))]
        return list(zip(dates, navs))

    def test_empty_series(self):
        m = compute_metrics([], n_trades=0)
        assert m.total_return == 0.0
        assert m.sharpe_ratio == 0.0

    def test_single_point(self):
        m = compute_metrics([("2024-01-01", 1_000_000)])
        assert m.total_return == 0.0

    def test_positive_returns(self):
        # 50 天带波动的正收益
        rng = np.random.RandomState(42)
        returns = rng.normal(0.005, 0.01, 50)  # 正均值 + 波动
        nav = self._make_nav(returns.tolist())
        m = compute_metrics(nav)
        assert m.total_return > 0
        assert m.cagr > 0
        assert m.sharpe_ratio > 0

    def test_negative_returns(self):
        nav = self._make_nav([-0.01] * 10)
        m = compute_metrics(nav)
        assert m.total_return < 0
        assert m.cagr < 0
        assert m.max_drawdown < 0

    def test_volatile_returns(self):
        returns = [0.02, -0.03, 0.01, -0.02, 0.04, -0.01, 0.03, -0.02, 0.01, -0.01]
        nav = self._make_nav(returns)
        m = compute_metrics(nav)
        assert m.annual_volatility > 0
        assert m.max_drawdown < 0
        assert 0 <= m.win_rate <= 1

    def test_sortino_with_no_downside(self):
        nav = self._make_nav([0.01] * 10)
        m = compute_metrics(nav)
        # 无下行 → sortino = 0 (无下行标准差)
        assert m.sortino_ratio == 0.0

    def test_with_benchmark(self):
        strat_nav = self._make_nav([0.01] * 50)
        bench_nav = self._make_nav([0.005] * 50)
        m = compute_metrics(strat_nav, benchmark_nav=bench_nav)
        # 策略跑赢基准
        assert m.alpha > 0 or m.information_ratio > 0

    def test_trade_stats(self):
        nav = self._make_nav([0.01] * 10)
        m = compute_metrics(nav, total_costs=500.0, n_trades=20, annual_turnover=2.5)
        assert m.total_costs == 500.0
        assert m.n_trades == 20
        assert m.annual_turnover == 2.5

    def test_calmar_ratio(self):
        # 制造一次回撤
        returns = [0.01] * 5 + [-0.05] + [0.01] * 5
        nav = self._make_nav(returns)
        m = compute_metrics(nav)
        assert m.calmar_ratio != 0.0
        assert m.max_drawdown < 0

    def test_win_rate(self):
        returns = [0.01, -0.01, 0.01, 0.01, -0.01]
        nav = self._make_nav(returns)
        m = compute_metrics(nav)
        assert m.win_rate == pytest.approx(3 / 5, rel=0.01)
