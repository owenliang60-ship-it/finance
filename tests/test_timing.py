"""
择时信号回测引擎 — 测试套件
"""

import math

import pandas as pd
import pytest

from backtest.timing.signals import (
    macd_signals,
    rsi_signals,
    ma_cross_signals,
    SIGNAL_REGISTRY,
)
from backtest.timing.engine import TimingResult, run_timing_backtest
from backtest.timing.runner import (
    TimingStudyConfig,
    AggregateResult,
    _aggregate,
)


# ── 辅助函数 ──────────────────────────────────────────


def _make_price_df(prices, start_date="2024-01-01"):
    """从价格列表构建 price_df"""
    dates = pd.date_range(start=start_date, periods=len(prices), freq="B")
    return pd.DataFrame({
        "date": dates.strftime("%Y-%m-%d"),
        "close": prices,
    })


def _make_trend_up_then_down(n_up=100, n_down=50, start=100):
    """构造先涨后跌的价格序列"""
    prices = [start]
    for _ in range(n_up - 1):
        prices.append(prices[-1] * 1.005)
    for _ in range(n_down):
        prices.append(prices[-1] * 0.995)
    return prices


def _make_oscillating(n=200, center=100, amplitude=20, period=40):
    """构造周期性波动的价格序列 (适合均线交叉)"""
    import numpy as np
    t = np.arange(n)
    prices = center + amplitude * np.sin(2 * np.pi * t / period)
    # 确保价格为正
    prices = prices.clip(min=10)
    return prices.tolist()


# ══════════════════════════════════════════════════════════
# 信号生成器测试
# ══════════════════════════════════════════════════════════


class TestMACDSignals:
    def test_golden_cross(self):
        """先跌后涨的价格序列应该产生至少一个 BUY 信号"""
        # 先跌 60 天，再涨 100 天 → 应该有 MACD 金叉
        prices = [100]
        for _ in range(59):
            prices.append(prices[-1] * 0.995)
        for _ in range(100):
            prices.append(prices[-1] * 1.01)

        df = _make_price_df(prices)
        signals = macd_signals(df)

        buy_signals = [s for s in signals if s[1] == "BUY"]
        assert len(buy_signals) > 0, "Should detect at least one golden cross"

    def test_death_cross(self):
        """先涨后跌的价格序列应该产生至少一个 SELL 信号"""
        prices = _make_trend_up_then_down(n_up=80, n_down=80)
        df = _make_price_df(prices)
        signals = macd_signals(df)

        sell_signals = [s for s in signals if s[1] == "SELL"]
        assert len(sell_signals) > 0, "Should detect at least one death cross"

    def test_warmup_period(self):
        """信号不应在预热期（slow + signal 天）内出现"""
        prices = _make_oscillating(n=200)
        df = _make_price_df(prices)
        signals = macd_signals(df, fast=12, slow=26, signal=9)

        warmup = 26 + 9
        warmup_dates = set(df["date"].iloc[:warmup].tolist())

        for date, _ in signals:
            assert date not in warmup_dates, (
                "Signal at %s is within warmup period" % date
            )

    def test_alternating_signals(self):
        """信号应交替出现 BUY/SELL"""
        prices = _make_oscillating(n=300, period=60)
        df = _make_price_df(prices)
        signals = macd_signals(df)

        if len(signals) >= 2:
            for i in range(1, len(signals)):
                # 不应连续同类型
                assert signals[i][1] != signals[i - 1][1], (
                    "Consecutive same signals at %s and %s"
                    % (signals[i - 1][0], signals[i][0])
                )

    def test_custom_params(self):
        """自定义参数不应报错"""
        prices = _make_oscillating(n=200)
        df = _make_price_df(prices)
        signals = macd_signals(df, fast=8, slow=21, signal=5)
        assert isinstance(signals, list)


class TestRSISignals:
    def test_oversold_exit(self):
        """暴跌后反弹应产生 RSI 上穿 oversold → BUY"""
        # 急跌 → RSI 低于 30，然后反弹 → RSI 回升穿 30
        prices = [100]
        for _ in range(20):
            prices.append(prices[-1] * 0.97)  # 急跌
        for _ in range(30):
            prices.append(prices[-1] * 1.03)  # 强力反弹

        df = _make_price_df(prices)
        signals = rsi_signals(df)

        buy_signals = [s for s in signals if s[1] == "BUY"]
        assert len(buy_signals) > 0, "Should detect RSI crossing above oversold"

    def test_overbought_exit(self):
        """连涨后回落应产生 RSI 下穿 overbought → SELL"""
        prices = [100]
        for _ in range(30):
            prices.append(prices[-1] * 1.03)  # 连涨
        for _ in range(20):
            prices.append(prices[-1] * 0.97)  # 回落

        df = _make_price_df(prices)
        signals = rsi_signals(df)

        sell_signals = [s for s in signals if s[1] == "SELL"]
        assert len(sell_signals) > 0, "Should detect RSI crossing below overbought"

    def test_warmup_period(self):
        """信号不应在预热期内出现"""
        prices = _make_oscillating(n=100)
        df = _make_price_df(prices)
        signals = rsi_signals(df, period=14)

        warmup = 14 + 1
        warmup_dates = set(df["date"].iloc[:warmup].tolist())

        for date, _ in signals:
            assert date not in warmup_dates

    def test_custom_thresholds(self):
        """自定义阈值"""
        prices = _make_oscillating(n=200)
        df = _make_price_df(prices)
        signals = rsi_signals(df, oversold=20, overbought=80)
        assert isinstance(signals, list)


class TestMACrossSignals:
    def test_golden_cross(self):
        """先跌后涨应该产生 MA 金叉"""
        prices = [100]
        for _ in range(80):
            prices.append(prices[-1] * 0.995)
        for _ in range(100):
            prices.append(prices[-1] * 1.008)

        df = _make_price_df(prices)
        signals = ma_cross_signals(df)

        buy_signals = [s for s in signals if s[1] == "BUY"]
        assert len(buy_signals) > 0

    def test_death_cross(self):
        """先涨后跌应该产生 MA 死叉"""
        prices = _make_trend_up_then_down(n_up=100, n_down=100)
        df = _make_price_df(prices)
        signals = ma_cross_signals(df)

        sell_signals = [s for s in signals if s[1] == "SELL"]
        assert len(sell_signals) > 0

    def test_warmup_period(self):
        """信号不应在预热期（long_window 天）内出现"""
        prices = _make_oscillating(n=200)
        df = _make_price_df(prices)
        signals = ma_cross_signals(df, short_window=20, long_window=60)

        warmup_dates = set(df["date"].iloc[:60].tolist())
        for date, _ in signals:
            assert date not in warmup_dates


class TestSignalRegistry:
    def test_all_signals_registered(self):
        """所有信号都在注册表中"""
        assert "MACD" in SIGNAL_REGISTRY
        assert "RSI" in SIGNAL_REGISTRY
        assert "MA_Cross" in SIGNAL_REGISTRY

    def test_registry_callables(self):
        """注册表中的函数可调用"""
        for name, (fn, params) in SIGNAL_REGISTRY.items():
            assert callable(fn), "%s is not callable" % name
            assert isinstance(params, dict), "%s default params is not dict" % name


# ══════════════════════════════════════════════════════════
# 引擎测试
# ══════════════════════════════════════════════════════════


class TestTimingEngine:
    def test_basic_round_trip(self):
        """BUY -> SELL -> BUY 完整流程"""
        prices = [100, 102, 105, 108, 103, 100, 98, 101, 105, 110]
        df = _make_price_df(prices)
        signals = [
            (df["date"].iloc[1], "BUY"),
            (df["date"].iloc[4], "SELL"),
            (df["date"].iloc[7], "BUY"),
        ]

        result = run_timing_backtest("TEST", "test_signal", df, signals)

        assert result.symbol == "TEST"
        assert result.n_trades == 2
        assert len(result.strategy_nav) == len(prices)
        assert len(result.buyhold_nav) == len(prices)
        assert result.time_in_market > 0

    def test_always_in_equals_buyhold(self):
        """只有 BUY 无 SELL → 等价于 buy-and-hold"""
        prices = [100, 105, 110, 108, 115, 120]
        df = _make_price_df(prices)
        signals = [(df["date"].iloc[0], "BUY")]

        result = run_timing_backtest("TEST", "test", df, signals)

        # 策略 NAV 应等于 buy-and-hold NAV
        for (_, s_nav), (_, bh_nav) in zip(result.strategy_nav, result.buyhold_nav):
            assert abs(s_nav - bh_nav) < 0.01, (
                "Strategy NAV (%.2f) != B&H NAV (%.2f)" % (s_nav, bh_nav)
            )

        assert abs(result.excess_cagr) < 0.01

    def test_always_out(self):
        """无 BUY 信号 → 初始资金不变"""
        prices = [100, 110, 120, 130, 140, 150]
        df = _make_price_df(prices)
        signals = []

        result = run_timing_backtest("TEST", "test", df, signals, initial_capital=100.0)

        # 策略 NAV 应始终为 100
        for _, nav in result.strategy_nav:
            assert nav == 100.0, "NAV should be 100 when always out, got %.2f" % nav

        assert result.n_trades == 0
        assert result.time_in_market == 0.0

    def test_duplicate_buy_ignored(self):
        """连续 2 个 BUY → 第二个忽略"""
        prices = [100, 102, 105, 108, 110, 112]
        df = _make_price_df(prices)
        signals = [
            (df["date"].iloc[1], "BUY"),
            (df["date"].iloc[3], "BUY"),  # 应被忽略
        ]

        result = run_timing_backtest("TEST", "test", df, signals)
        assert result.n_trades == 1  # 只有第一个 BUY 生效

    def test_duplicate_sell_ignored(self):
        """连续 2 个 SELL → 第二个忽略"""
        prices = [100, 102, 105, 108, 103, 100]
        df = _make_price_df(prices)
        signals = [
            (df["date"].iloc[1], "BUY"),
            (df["date"].iloc[3], "SELL"),
            (df["date"].iloc[4], "SELL"),  # 应被忽略 (已空仓)
        ]

        result = run_timing_backtest("TEST", "test", df, signals)
        assert result.n_trades == 1

    def test_nav_increases_in_market(self):
        """持仓时价格上涨 → NAV 增加"""
        prices = [100, 100, 110, 120, 130]
        df = _make_price_df(prices)
        signals = [(df["date"].iloc[1], "BUY")]

        result = run_timing_backtest("TEST", "test", df, signals, initial_capital=100.0)

        # 入场价 100, 最后 130 → NAV 应为 130
        last_nav = result.strategy_nav[-1][1]
        assert abs(last_nav - 130.0) < 0.01

    def test_nav_preserved_out_of_market(self):
        """空仓时 NAV 不变"""
        prices = [100, 105, 110, 115, 100, 80, 60]
        df = _make_price_df(prices)
        signals = [
            (df["date"].iloc[0], "BUY"),
            (df["date"].iloc[2], "SELL"),  # 卖在 110, NAV = 100 * 110/100 = 110
        ]

        result = run_timing_backtest("TEST", "test", df, signals, initial_capital=100.0)

        # SELL 后 NAV 应该锁定在 110
        sell_date_idx = 2
        for _, nav in result.strategy_nav[sell_date_idx + 1:]:
            assert abs(nav - 110.0) < 0.01, "NAV should be 110 when out, got %.2f" % nav

    def test_excess_cagr_positive_when_dodging_crash(self):
        """避开暴跌 → excess_cagr 应为正"""
        prices = [100]
        for _ in range(50):
            prices.append(prices[-1] * 1.005)
        crash_start = len(prices)
        for _ in range(30):
            prices.append(prices[-1] * 0.97)
        for _ in range(20):
            prices.append(prices[-1] * 1.005)

        df = _make_price_df(prices)
        # 在暴跌前卖出
        signals = [
            (df["date"].iloc[0], "BUY"),
            (df["date"].iloc[crash_start - 1], "SELL"),
        ]

        result = run_timing_backtest("TEST", "test", df, signals)
        assert result.excess_cagr > 0, (
            "Should have positive excess CAGR when dodging crash, got %.4f"
            % result.excess_cagr
        )

    def test_metrics_computed(self):
        """验证 metrics 对象被正确填充"""
        prices = list(range(100, 200))
        df = _make_price_df(prices)
        signals = [(df["date"].iloc[0], "BUY")]

        result = run_timing_backtest("TEST", "test", df, signals)

        assert result.strategy_metrics.n_days == len(prices)
        assert result.strategy_metrics.total_return > 0
        assert result.buyhold_metrics.total_return > 0


# ══════════════════════════════════════════════════════════
# 聚合器测试
# ══════════════════════════════════════════════════════════


class TestAggregateStatistics:
    def _make_mock_results(self, excess_cagrs, sharpe_diffs=None):
        """从 excess_cagr 列表创建 mock TimingResult 列表"""
        from backtest.metrics import BacktestMetrics

        if sharpe_diffs is None:
            sharpe_diffs = [0.0] * len(excess_cagrs)

        results = []
        for i, (ec, sd) in enumerate(zip(excess_cagrs, sharpe_diffs)):
            empty_metrics = BacktestMetrics(
                total_return=0.1, cagr=0.05 + ec, annual_volatility=0.2,
                max_drawdown=-0.15, max_dd_duration=30,
                sharpe_ratio=0.5 + sd, sortino_ratio=0.6, calmar_ratio=0.3,
                alpha=0.0, beta=1.0, information_ratio=0.0, tracking_error=0.0,
                annual_turnover=0.0, total_costs=0.0, win_rate=0.5,
                n_days=252, n_trades=10,
            )
            bh_metrics = BacktestMetrics(
                total_return=0.1, cagr=0.05, annual_volatility=0.2,
                max_drawdown=-0.15, max_dd_duration=30,
                sharpe_ratio=0.5, sortino_ratio=0.6, calmar_ratio=0.3,
                alpha=0.0, beta=1.0, information_ratio=0.0, tracking_error=0.0,
                annual_turnover=0.0, total_costs=0.0, win_rate=0.5,
                n_days=252, n_trades=0,
            )
            results.append(TimingResult(
                symbol="STOCK%d" % i,
                signal_name="TEST",
                strategy_nav=[],
                buyhold_nav=[],
                strategy_metrics=empty_metrics,
                buyhold_metrics=bh_metrics,
                excess_cagr=ec,
                sharpe_diff=sd,
                mdd_diff=0.0,
                n_trades=10,
                time_in_market=0.6,
            ))
        return results

    def test_positive_t_stat(self):
        """全部跑赢 → t-stat 应为正"""
        results = self._make_mock_results([0.05, 0.03, 0.04, 0.06, 0.02])
        config = TimingStudyConfig(signal_name="TEST")
        agg = _aggregate(config, {}, results, [])

        assert agg.t_stat > 0
        assert agg.hit_rate == 1.0
        assert agg.mean_excess_cagr > 0

    def test_negative_t_stat(self):
        """全部跑输 → t-stat 应为负"""
        results = self._make_mock_results([-0.05, -0.03, -0.04, -0.06, -0.02])
        config = TimingStudyConfig(signal_name="TEST")
        agg = _aggregate(config, {}, results, [])

        assert agg.t_stat < 0
        assert agg.hit_rate == 0.0

    def test_hit_rate_calculation(self):
        """3/5 跑赢 → hit_rate = 0.6"""
        results = self._make_mock_results([0.05, -0.03, 0.04, -0.06, 0.02])
        config = TimingStudyConfig(signal_name="TEST")
        agg = _aggregate(config, {}, results, [])

        assert abs(agg.hit_rate - 0.6) < 0.001

    def test_t_stat_formula(self):
        """手动验证 t-stat 公式"""
        excess = [0.10, 0.05, 0.08]
        results = self._make_mock_results(excess)
        config = TimingStudyConfig(signal_name="TEST")
        agg = _aggregate(config, {}, results, [])

        n = len(excess)
        mean = sum(excess) / n
        std = math.sqrt(sum((x - mean) ** 2 for x in excess) / (n - 1))
        expected_t = mean / (std / math.sqrt(n))

        assert abs(agg.t_stat - expected_t) < 0.001, (
            "t-stat %.4f != expected %.4f" % (agg.t_stat, expected_t)
        )

    def test_empty_results(self):
        """空结果 → 安全返回"""
        config = TimingStudyConfig(signal_name="TEST")
        agg = _aggregate(config, {}, [], [])

        assert agg.n_stocks == 0
        assert agg.t_stat == 0.0
        assert agg.p_value == 1.0
        assert agg.hit_rate == 0.0

    def test_mean_sharpe_diff(self):
        """验证 mean_sharpe_diff 计算"""
        sharpe_diffs = [0.1, -0.2, 0.3]
        results = self._make_mock_results(
            [0.01, 0.01, 0.01],
            sharpe_diffs=sharpe_diffs,
        )
        config = TimingStudyConfig(signal_name="TEST")
        agg = _aggregate(config, {}, results, [])

        expected = sum(sharpe_diffs) / len(sharpe_diffs)
        assert abs(agg.mean_sharpe_diff - expected) < 0.001

    def test_index_results_preserved(self):
        """指数结果应被保留在 index_results 中"""
        stock_results = self._make_mock_results([0.05])
        index_results = self._make_mock_results([0.02])
        config = TimingStudyConfig(signal_name="TEST")
        agg = _aggregate(config, {}, stock_results, index_results)

        assert len(agg.index_results) == 1
        assert len(agg.per_stock_results) == 1
