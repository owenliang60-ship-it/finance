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
    vix_ma_signals,
    vix_spike_signals,
    vix_percentile_signals,
    vix_rsi_signals,
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


def _make_vix_df(values, start_date="2024-01-01"):
    """从 VIX 值列表构建 aux_data DataFrame"""
    dates = pd.date_range(start=start_date, periods=len(values), freq="B")
    return pd.DataFrame({
        "date": dates.strftime("%Y-%m-%d"),
        "close": values,
    })


class TestVIXMASignals:
    def test_basic_buy_signal(self):
        """VIX 从高位下穿 MA → BUY"""
        # VIX 高位 30 天，然后快速下降
        vix_vals = [25] * 30 + [24, 23, 22, 20, 18, 16, 14, 13, 12, 11]
        vix_df = _make_vix_df(vix_vals)
        price_df = _make_price_df([100] * len(vix_vals))

        signals = vix_ma_signals(price_df, vix_ma_period=20, aux_data=vix_df)

        buy_signals = [s for s in signals if s[1] == "BUY"]
        assert len(buy_signals) > 0, "Should detect VIX crossing below MA"

    def test_basic_sell_signal(self):
        """VIX 从低位上穿 MA → SELL"""
        # VIX 低位 30 天，然后快速上升
        vix_vals = [15] * 30 + [16, 18, 20, 22, 25, 28, 30, 32, 35, 38]
        vix_df = _make_vix_df(vix_vals)
        price_df = _make_price_df([100] * len(vix_vals))

        signals = vix_ma_signals(price_df, vix_ma_period=20, aux_data=vix_df)

        sell_signals = [s for s in signals if s[1] == "SELL"]
        assert len(sell_signals) > 0, "Should detect VIX crossing above MA"

    def test_none_aux_data(self):
        """aux_data=None → 空列表"""
        price_df = _make_price_df([100] * 50)
        signals = vix_ma_signals(price_df, aux_data=None)
        assert signals == []

    def test_warmup(self):
        """信号不应在预热期内出现"""
        vix_vals = _make_oscillating(n=100, center=20, amplitude=10, period=30)
        vix_df = _make_vix_df(vix_vals)
        price_df = _make_price_df([100] * len(vix_vals))

        signals = vix_ma_signals(price_df, vix_ma_period=20, aux_data=vix_df)

        warmup_dates = set(vix_df["date"].iloc[:20].tolist())
        for date, _ in signals:
            assert date not in warmup_dates

    def test_date_alignment(self):
        """信号日期应在目标资产的日期集合内"""
        vix_vals = _make_oscillating(n=100, center=20, amplitude=10, period=30)
        vix_df = _make_vix_df(vix_vals)
        price_df = _make_price_df([100] * len(vix_vals))
        target_dates = set(price_df["date"].astype(str))

        signals = vix_ma_signals(price_df, vix_ma_period=20, aux_data=vix_df)

        for date, _ in signals:
            assert date in target_dates

    def test_short_price_window_preserves_vix_history(self):
        """P1 回归: 10 天价格窗口 + 30 天 VIX → MA(20) 应正常计算
        旧代码: 先裁到 10 天 → 10 < 21 → return []
        新代码: 在 30 天 VIX 上算 MA → 检测到下穿 → BUY
        """
        # 30 天 VIX: 20 天稳定高位 + 10 天快速下降 (穿 MA → BUY)
        vix_vals = [25.0] * 20 + [24, 22, 20, 18, 16, 14, 12, 10, 10, 10]
        vix_df = _make_vix_df(vix_vals, start_date="2024-01-01")

        # price_df 只覆盖后 10 天，日期与 VIX 对齐
        price_dates = vix_df["date"].iloc[20:].tolist()
        price_df = pd.DataFrame({"date": price_dates, "close": [100.0] * 10})

        signals = vix_ma_signals(price_df, vix_ma_period=20, aux_data=vix_df)

        buy_signals = [s for s in signals if s[1] == "BUY"]
        assert len(buy_signals) > 0, (
            "Short price window should not prevent VIX MA from using full history"
        )


class TestVIXSpikeSignals:
    def test_spike_buy(self):
        """VIX > 30 → BUY"""
        vix_vals = [18] * 10 + [35] * 5 + [18] * 5
        vix_df = _make_vix_df(vix_vals)
        price_df = _make_price_df([100] * len(vix_vals))

        signals = vix_spike_signals(price_df, buy_threshold=30, sell_threshold=20, aux_data=vix_df)

        buy_signals = [s for s in signals if s[1] == "BUY"]
        assert len(buy_signals) > 0

    def test_spike_sell(self):
        """VIX drops below 20 after spike → SELL"""
        vix_vals = [18] * 5 + [35] * 5 + [15] * 10
        vix_df = _make_vix_df(vix_vals)
        price_df = _make_price_df([100] * len(vix_vals))

        signals = vix_spike_signals(price_df, buy_threshold=30, sell_threshold=20, aux_data=vix_df)

        sell_signals = [s for s in signals if s[1] == "SELL"]
        assert len(sell_signals) > 0

    def test_none_aux_data(self):
        """aux_data=None → 空列表"""
        price_df = _make_price_df([100] * 50)
        signals = vix_spike_signals(price_df, aux_data=None)
        assert signals == []

    def test_no_consecutive_buys(self):
        """不应连续发出 BUY（有状态跟踪）"""
        vix_vals = [18] * 5 + [35, 36, 37, 38, 35] + [18] * 5
        vix_df = _make_vix_df(vix_vals)
        price_df = _make_price_df([100] * len(vix_vals))

        signals = vix_spike_signals(price_df, buy_threshold=30, sell_threshold=20, aux_data=vix_df)

        buy_signals = [s for s in signals if s[1] == "BUY"]
        assert len(buy_signals) == 1, "Should only trigger one BUY per spike"

    def test_day_zero_spike(self):
        """P2 回归: VIX 第一天就 > threshold → 应立即 BUY"""
        vix_vals = [35, 36, 37, 34, 32, 28, 18]
        vix_df = _make_vix_df(vix_vals)
        price_df = _make_price_df([100] * len(vix_vals))

        signals = vix_spike_signals(price_df, buy_threshold=30, sell_threshold=20, aux_data=vix_df)

        assert len(signals) > 0
        assert signals[0][1] == "BUY"
        assert signals[0][0] == vix_df["date"].iloc[0], (
            "BUY should fire on day 0, got %s" % signals[0][0]
        )


class TestVIXPercentileSignals:
    def test_extreme_high_buy(self):
        """VIX 百分位 > 90% → BUY"""
        # 252 天低 VIX + 突然飙升
        vix_vals = [15] * 252 + [40, 42, 45]
        vix_df = _make_vix_df(vix_vals)
        price_df = _make_price_df([100] * len(vix_vals))

        signals = vix_percentile_signals(
            price_df, lookback=252, buy_pctile=90, sell_pctile=20, aux_data=vix_df,
        )

        buy_signals = [s for s in signals if s[1] == "BUY"]
        assert len(buy_signals) > 0

    def test_none_aux_data(self):
        """aux_data=None → 空列表"""
        price_df = _make_price_df([100] * 50)
        signals = vix_percentile_signals(price_df, aux_data=None)
        assert signals == []

    def test_too_short_data(self):
        """数据不足 lookback → 空列表"""
        vix_vals = [20] * 100
        vix_df = _make_vix_df(vix_vals)
        price_df = _make_price_df([100] * 100)

        signals = vix_percentile_signals(
            price_df, lookback=252, aux_data=vix_df,
        )
        assert signals == []

    def test_short_price_window_preserves_vix_history(self):
        """P1 回归: 300 天 VIX + 100 天价格窗口 → 百分位应正常工作"""
        # 300 天 VIX: 前 260 天低位，然后飙升
        vix_vals = [15] * 260 + [40, 42, 45] + [15] * 37
        vix_df = _make_vix_df(vix_vals, start_date="2023-01-01")
        # price_df 只覆盖后 100 天（包含飙升点）
        price_df = _make_price_df([100] * 100, start_date="2023-09-18")

        signals = vix_percentile_signals(
            price_df, lookback=252, buy_pctile=90, sell_pctile=20, aux_data=vix_df,
        )

        buy_signals = [s for s in signals if s[1] == "BUY"]
        assert len(buy_signals) > 0, (
            "300-day VIX history with 100-day price window should still detect percentile spike"
        )


class TestVIXRSISignals:
    def test_vix_overbought_buy(self):
        """VIX RSI > 70（VIX 过热 = 市场超卖）→ BUY"""
        # VIX 急涨 → RSI > 70
        vix_vals = [15] * 20
        for _ in range(20):
            vix_vals.append(vix_vals[-1] * 1.05)
        # 然后回落
        for _ in range(10):
            vix_vals.append(vix_vals[-1] * 0.95)

        vix_df = _make_vix_df(vix_vals)
        price_df = _make_price_df([100] * len(vix_vals))

        signals = vix_rsi_signals(price_df, period=14, overbought=70, oversold=30, aux_data=vix_df)

        buy_signals = [s for s in signals if s[1] == "BUY"]
        assert len(buy_signals) > 0

    def test_none_aux_data(self):
        """aux_data=None → 空列表"""
        price_df = _make_price_df([100] * 50)
        signals = vix_rsi_signals(price_df, aux_data=None)
        assert signals == []

    def test_warmup(self):
        """信号不应在预热期内出现"""
        vix_vals = _make_oscillating(n=100, center=20, amplitude=10, period=30)
        vix_df = _make_vix_df(vix_vals)
        price_df = _make_price_df([100] * len(vix_vals))

        signals = vix_rsi_signals(price_df, period=14, aux_data=vix_df)

        warmup = 14 + 1
        warmup_dates = set(vix_df["date"].iloc[:warmup].tolist())
        for date, _ in signals:
            assert date not in warmup_dates

    def test_pure_rally_rsi_100(self):
        """P2 回归: VIX 纯涨时 RSI 应为 100，不是 50"""
        # 20 天横盘 + 30 天每天涨 5%
        vix_vals = [15.0] * 20
        for _ in range(30):
            vix_vals.append(vix_vals[-1] * 1.05)

        vix_df = _make_vix_df(vix_vals)
        price_df = _make_price_df([100] * len(vix_vals))

        signals = vix_rsi_signals(
            price_df, period=14, overbought=70, oversold=30, aux_data=vix_df,
        )

        buy_signals = [s for s in signals if s[1] == "BUY"]
        assert len(buy_signals) > 0, (
            "VIX pure rally should push RSI above 70 and trigger BUY"
        )


class TestSignalRegistry:
    def test_all_signals_registered(self):
        """所有信号都在注册表中"""
        assert "MACD" in SIGNAL_REGISTRY
        assert "RSI" in SIGNAL_REGISTRY
        assert "MA_Cross" in SIGNAL_REGISTRY
        assert "New_High" in SIGNAL_REGISTRY
        assert "VIX_MA" in SIGNAL_REGISTRY
        assert "VIX_Spike" in SIGNAL_REGISTRY
        assert "VIX_Percentile" in SIGNAL_REGISTRY
        assert "VIX_RSI" in SIGNAL_REGISTRY

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


# ══════════════════════════════════════════════════════════
# Runner VIX 注入测试
# ══════════════════════════════════════════════════════════


class TestRunnerVIXInjection:
    def test_vix_signal_set(self):
        """_VIX_SIGNALS 包含所有 VIX 信号"""
        from backtest.timing.runner import _VIX_SIGNALS
        assert "VIX_MA" in _VIX_SIGNALS
        assert "VIX_Spike" in _VIX_SIGNALS
        assert "VIX_Percentile" in _VIX_SIGNALS
        assert "VIX_RSI" in _VIX_SIGNALS

    def test_non_vix_signal_not_in_set(self):
        """非 VIX 信号不在 _VIX_SIGNALS 中"""
        from backtest.timing.runner import _VIX_SIGNALS
        assert "MACD" not in _VIX_SIGNALS
        assert "RSI" not in _VIX_SIGNALS
        assert "MA_Cross" not in _VIX_SIGNALS
        assert "New_High" not in _VIX_SIGNALS
