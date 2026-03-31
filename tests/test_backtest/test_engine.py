"""
BacktestEngine 核心循环测试

使用合成数据 (5 只假股票 × 200 天) 验证:
1. 引擎正确运行
2. 防前视 — T+1 暴涨不会在 T 日买入
3. Sanity check — 全持有 ≈ 等权基准
"""

import pytest
import numpy as np
import pandas as pd
from unittest.mock import MagicMock, patch

from backtest.config import BacktestConfig
from backtest.engine import BacktestEngine
from backtest.metrics import BacktestMetrics


# ── 合成数据 ──────────────────────────────────────────

def _generate_prices(n_stocks=5, n_days=200, seed=42):
    """
    生成合成价格数据

    Returns:
        {symbol: DataFrame[date, close]}
    """
    rng = np.random.RandomState(seed)
    base_date = pd.Timestamp("2023-01-01")
    dates = pd.bdate_range(base_date, periods=n_days)

    price_dict = {}
    for i in range(n_stocks):
        symbol = f"SYN{i+1}"
        # 随机游走 + 轻微上升趋势
        returns = rng.normal(0.0005 * (i + 1), 0.02, n_days)
        prices = 100 * np.exp(np.cumsum(returns))
        df = pd.DataFrame({
            "date": [d.strftime("%Y-%m-%d") for d in dates],
            "close": prices,
        })
        price_dict[symbol] = df

    return price_dict


class MockAdapter:
    """模拟适配器 — 用合成数据"""

    def __init__(self, price_dict=None):
        self._data = price_dict or _generate_prices()
        self._loaded = False

    def load_all(self):
        self._loaded = True
        return self._data

    def get_trading_dates(self):
        all_dates = set()
        for df in self._data.values():
            all_dates.update(df["date"].tolist())
        return sorted(all_dates)

    def get_prices_at(self, date):
        prices = {}
        for sym, df in self._data.items():
            row = df[df["date"] == date]
            if not row.empty:
                prices[sym] = float(row.iloc[0]["close"])
        return prices

    def slice_to_date(self, date):
        sliced = {}
        for sym, df in self._data.items():
            cut = df[df["date"] <= date].reset_index(drop=True)
            if len(cut) >= 70:
                sliced[sym] = cut
        return sliced

    def get_benchmark_nav(self, symbol="SPY"):
        # 用第一只合成股票做基准
        first = list(self._data.values())[0]
        return list(zip(first["date"], first["close"].astype(float)))

    def get_rs_function(self, method):
        from src.indicators.rs_rating import compute_rs_rating_b, compute_rs_rating_c
        return compute_rs_rating_b if method == "B" else compute_rs_rating_c

    def get_index_prices(self, symbol="SPY"):
        """用第一只合成股票做 regime index"""
        first = list(self._data.values())[0]
        return pd.Series(
            first["close"].astype(float).values,
            index=first["date"].astype(str).values,
        )

    def get_date_range(self):
        dates = self.get_trading_dates()
        return (dates[0], dates[-1]) if dates else ("", "")


class TestBacktestEngine:
    """引擎核心测试"""

    def test_basic_run(self):
        """引擎能正常跑完"""
        config = BacktestConfig(
            market="us_stocks", rs_method="B", top_n=3,
            sell_buffer=1, rebalance_freq="M",
            transaction_cost_bps=5.0, initial_capital=1_000_000,
        )
        adapter = MockAdapter()
        engine = BacktestEngine(config, adapter=adapter)
        metrics = engine.run()

        assert isinstance(metrics, BacktestMetrics)
        assert metrics.n_days > 0
        assert metrics.n_trades > 0
        assert len(engine.portfolio.snapshots) > 0

    def test_weekly_rebalance(self):
        """周频换仓"""
        config = BacktestConfig(
            market="us_stocks", rs_method="B", top_n=2,
            rebalance_freq="W", initial_capital=500_000,
        )
        adapter = MockAdapter()
        engine = BacktestEngine(config, adapter=adapter)
        metrics = engine.run()
        assert metrics.n_days > 0

    def test_method_c(self):
        """Method C 正常运行"""
        config = BacktestConfig(
            market="us_stocks", rs_method="C", top_n=3,
            rebalance_freq="M", initial_capital=1_000_000,
        )
        adapter = MockAdapter()
        engine = BacktestEngine(config, adapter=adapter)
        metrics = engine.run()
        assert metrics.n_days > 0

    def test_no_lookahead(self):
        """
        防前视测试: T+1 暴涨的股票不会在 T 日买入

        注入一只"未来暴涨"的股票，验证引擎在暴涨前不会选入。
        """
        # 生成正常数据
        prices = _generate_prices(n_stocks=4, n_days=200)

        # 添加一只在第 150 天暴涨的股票 (前 149 天表现平平)
        dates = list(prices.values())[0]["date"].tolist()
        rocket_prices = np.ones(200) * 50.0  # 前 149 天平稳
        rocket_prices[150:] = 500.0  # 第 150 天突然 10x

        prices["ROCKET"] = pd.DataFrame({
            "date": dates,
            "close": rocket_prices,
        })

        adapter = MockAdapter(prices)
        config = BacktestConfig(
            market="us_stocks", rs_method="B", top_n=2,
            sell_buffer=0, rebalance_freq="M",
            initial_capital=1_000_000,
        )
        engine = BacktestEngine(config, adapter=adapter)

        # monkey-patch slice_to_date 来验证只传历史数据
        original_slice = adapter.slice_to_date
        sliced_dates = []

        def tracking_slice(date):
            result = original_slice(date)
            for sym, df in result.items():
                max_date = df["date"].max()
                assert max_date <= date, f"前视偏差! {sym} 数据包含 {max_date} > {date}"
                sliced_dates.append(date)
            return result

        adapter.slice_to_date = tracking_slice
        metrics = engine.run()

        assert len(sliced_dates) > 0  # 确实调用了 slice

    def test_build_rebalance_set(self):
        """换仓日期集合构建"""
        config = BacktestConfig(
            market="us_stocks", rs_method="B", top_n=3,
            rebalance_freq="M",  # 21 天
        )
        adapter = MockAdapter()
        engine = BacktestEngine(config, adapter=adapter)
        dates = adapter.get_trading_dates()
        rb_set = engine._build_rebalance_set(dates)
        assert len(rb_set) > 0
        assert dates[0] in rb_set  # 第一天总是 rebalance

    def test_date_filter(self):
        """日期过滤"""
        config = BacktestConfig(
            market="us_stocks", rs_method="B", top_n=3,
            rebalance_freq="M",
            start_date="2023-06-01",
            end_date="2023-09-01",
        )
        adapter = MockAdapter()
        engine = BacktestEngine(config, adapter=adapter)
        metrics = engine.run()
        # 应该只有 ~3 个月的数据
        assert metrics.n_days < 100

    def test_zero_cost(self):
        """零成本回测"""
        config = BacktestConfig(
            market="us_stocks", rs_method="B", top_n=3,
            rebalance_freq="M", transaction_cost_bps=0.0,
        )
        adapter = MockAdapter()
        engine = BacktestEngine(config, adapter=adapter)
        metrics = engine.run()
        assert metrics.total_costs == 0.0

    def test_rebalance_held_true_more_turnover(self):
        """rebalance_held=True 产生更多换手 (调整已有持仓权重)"""
        common = dict(
            market="us_stocks", rs_method="B", top_n=3,
            sell_buffer=1, rebalance_freq="W",
            initial_capital=1_000_000, transaction_cost_bps=0,
        )

        config_true = BacktestConfig(**common, rebalance_held=True)
        engine_true = BacktestEngine(config_true, adapter=MockAdapter())
        metrics_true = engine_true.run()

        config_false = BacktestConfig(**common, rebalance_held=False)
        engine_false = BacktestEngine(config_false, adapter=MockAdapter())
        metrics_false = engine_false.run()

        # 真等权模式每次 rebalance 都调整已有持仓 → 更多交易
        assert metrics_true.n_trades > metrics_false.n_trades

    def test_rebalance_held_false_preserves_drift(self):
        """rebalance_held=False: 已有持仓保持价格漂移, 行为与原始逻辑一致"""
        config = BacktestConfig(
            market="us_stocks", rs_method="B", top_n=3,
            sell_buffer=1, rebalance_freq="M",
            initial_capital=1_000_000, rebalance_held=False,
        )
        adapter = MockAdapter()
        engine = BacktestEngine(config, adapter=adapter)
        metrics = engine.run()

        assert isinstance(metrics, BacktestMetrics)
        assert metrics.n_days > 0
        assert metrics.n_trades > 0

    def test_label_includes_rebalance_mode(self):
        """label() 区分 rebalance_held 模式"""
        config_eqw = BacktestConfig(
            market="us_stocks", rs_method="B", top_n=10,
            rebalance_freq="M", sell_buffer=5, rebalance_held=True,
        )
        config_drift = BacktestConfig(
            market="us_stocks", rs_method="B", top_n=10,
            rebalance_freq="M", sell_buffer=5, rebalance_held=False,
        )
        assert config_eqw.label() != config_drift.label()
        assert "eqw" in config_eqw.label()
        assert "drift" in config_drift.label()

    def test_label_with_regime_and_invvol(self):
        """label() 包含 regime 和 inv_vol 信息"""
        config = BacktestConfig(
            market="us_stocks", rs_method="B", top_n=10,
            rebalance_freq="M", sell_buffer=5,
            weighting="inv_vol", vol_lookback=60,
            regime_symbol="SPY", regime_ma_period=200, regime_mode="cash",
        )
        label = config.label()
        assert "inv_vol60" in label
        assert "regime200_cash" in label


class TestRegimeFilter:
    """Regime filter 测试"""

    def test_regime_cash_no_holdings_when_off(self):
        """regime_mode=cash: regime off 期间持仓为零"""
        adapter = MockAdapter()
        dates = adapter.get_trading_dates()
        mid = len(dates) // 2

        # 构造 regime index: 前半高 (on), 后半低 (off)
        index_vals = [200.0] * mid + [50.0] * (len(dates) - mid)
        index_series = pd.Series(index_vals, index=dates)
        adapter.get_index_prices = lambda sym="SPY": index_series

        config = BacktestConfig(
            market="us_stocks", rs_method="B", top_n=3,
            rebalance_freq="M", initial_capital=1_000_000,
            regime_symbol="SPY", regime_ma_period=50,  # 短周期方便测试
            regime_mode="cash",
        )
        engine = BacktestEngine(config, adapter=adapter)
        metrics = engine.run()

        assert metrics.n_days > 0
        # 后半段 regime off → 应该有清仓操作
        late_snapshots = [s for s in engine.portfolio.snapshots
                         if s.date >= dates[mid + 60]]  # MA 需要回看期
        if late_snapshots:
            for snap in late_snapshots[-5:]:
                assert snap.n_holdings == 0, (
                    f"{snap.date}: 应该清仓但有 {snap.n_holdings} 只持仓"
                )

    def test_regime_scale_reduces_exposure(self):
        """regime_mode=scale: regime off 期间仓位缩减"""
        adapter = MockAdapter()
        dates = adapter.get_trading_dates()

        # regime 全程 off (index 远低于 MA)
        index_series = pd.Series([30.0] * len(dates), index=dates)
        adapter.get_index_prices = lambda sym="SPY": index_series

        config = BacktestConfig(
            market="us_stocks", rs_method="B", top_n=3,
            rebalance_freq="M", initial_capital=1_000_000,
            regime_symbol="SPY", regime_ma_period=10,
            regime_mode="scale", regime_scale_factor=0.5,
        )
        engine = BacktestEngine(config, adapter=adapter)
        metrics = engine.run()

        assert metrics.n_days > 0
        # 应该只用 ~50% 资金
        late = engine.portfolio.snapshots[-5:]
        for snap in late:
            cash_pct = snap.cash / snap.nav if snap.nav > 0 else 0
            assert cash_pct > 0.3, (
                f"{snap.date}: scale 0.5 但 cash 只有 {cash_pct:.1%}"
            )

    def test_regime_scale_with_drift_still_reduces(self):
        """P2 fix: regime_mode=scale + rebalance_held=False 仍然缩减已有持仓"""
        adapter = MockAdapter()
        dates = adapter.get_trading_dates()

        # regime 全程 off
        index_series = pd.Series([30.0] * len(dates), index=dates)
        adapter.get_index_prices = lambda sym="SPY": index_series

        config = BacktestConfig(
            market="us_stocks", rs_method="B", top_n=3,
            rebalance_freq="M", initial_capital=1_000_000,
            regime_symbol="SPY", regime_ma_period=10,
            regime_mode="scale", regime_scale_factor=0.5,
            rebalance_held=False,  # drift mode — the P2 edge case
        )
        engine = BacktestEngine(config, adapter=adapter)
        metrics = engine.run()

        assert metrics.n_days > 0
        late = engine.portfolio.snapshots[-5:]
        for snap in late:
            cash_pct = snap.cash / snap.nav if snap.nav > 0 else 0
            assert cash_pct > 0.3, (
                f"{snap.date}: scale 0.5 但 cash 只有 {cash_pct:.1%}, "
                f"drift mode 下 to_hold 没被调整"
            )

    def test_regime_disabled_by_default(self):
        """不传 regime_symbol → 行为不变"""
        config = BacktestConfig(
            market="us_stocks", rs_method="B", top_n=3,
            rebalance_freq="M", initial_capital=1_000_000,
        )
        adapter = MockAdapter()
        engine = BacktestEngine(config, adapter=adapter)
        metrics = engine.run()
        assert metrics.n_days > 0
        assert metrics.n_trades > 0

    def test_regime_stats(self):
        """regime_stats 属性输出正确"""
        adapter = MockAdapter()
        dates = adapter.get_trading_dates()
        mid = len(dates) // 2
        index_vals = [200.0] * mid + [50.0] * (len(dates) - mid)
        index_series = pd.Series(index_vals, index=dates)
        adapter.get_index_prices = lambda sym="SPY": index_series

        config = BacktestConfig(
            market="us_stocks", rs_method="B", top_n=3,
            rebalance_freq="M", initial_capital=1_000_000,
            regime_symbol="SPY", regime_ma_period=50,
            regime_mode="cash",
        )
        engine = BacktestEngine(config, adapter=adapter)
        engine.run()

        stats = engine.regime_stats
        assert 0 <= stats["regime_on_pct"] <= 1.0
        assert stats["n_switches"] >= 0
        assert stats["n_rebalances_on"] + stats["n_rebalances_off"] > 0


class TestInvVolEngine:
    """Inverse-vol weighting 引擎集成测试"""

    def test_invvol_runs(self):
        """inv_vol 回测能跑通"""
        config = BacktestConfig(
            market="us_stocks", rs_method="B", top_n=3,
            rebalance_freq="M", initial_capital=1_000_000,
            weighting="inv_vol", vol_lookback=60,
        )
        adapter = MockAdapter()
        engine = BacktestEngine(config, adapter=adapter)
        metrics = engine.run()

        assert metrics.n_days > 0
        assert metrics.n_trades > 0

    def test_invvol_vs_equal_different_nav(self):
        """inv_vol 和 equal weight 产生不同的 NAV 轨迹"""
        common = dict(
            market="us_stocks", rs_method="B", top_n=3,
            sell_buffer=1, rebalance_freq="M",
            initial_capital=1_000_000, transaction_cost_bps=0,
        )

        config_eq = BacktestConfig(**common, weighting="equal")
        engine_eq = BacktestEngine(config_eq, adapter=MockAdapter())
        metrics_eq = engine_eq.run()

        config_iv = BacktestConfig(**common, weighting="inv_vol", vol_lookback=60)
        engine_iv = BacktestEngine(config_iv, adapter=MockAdapter())
        metrics_iv = engine_iv.run()

        # NAV 应该不同
        assert metrics_eq.total_return != metrics_iv.total_return
