"""
Market Momentum 指标测试
"""

import numpy as np
import pandas as pd
import pytest

from src.indicators.market_momentum import (
    MIN_DATA_DAYS,
    ROLLING_SUM_WINDOW,
    ZSCORE_WINDOW,
    _compute_momentum_series,
    compute_market_momentum,
    scan_market_momentum,
)


def _make_price_df(n_days: int, base_close: float = 100.0, base_volume: float = 1e6,
                   trend: float = 0.001) -> pd.DataFrame:
    """生成合成价格数据"""
    dates = pd.bdate_range("2020-01-01", periods=n_days)
    np.random.seed(42)
    noise = np.random.normal(0, 0.005, n_days)
    log_returns = trend + noise
    log_returns[0] = 0
    close = base_close * np.exp(np.cumsum(log_returns))
    volume = base_volume * (1 + 0.3 * np.random.randn(n_days)).clip(min=0.1)
    return pd.DataFrame({
        "date": dates,
        "close": close,
        "volume": volume,
    })


class TestComputeMomentumSeries:
    """_compute_momentum_series 内部函数测试"""

    def test_returns_none_for_empty_df(self):
        assert _compute_momentum_series(pd.DataFrame()) is None

    def test_returns_none_for_none(self):
        assert _compute_momentum_series(None) is None

    def test_returns_none_for_insufficient_data(self):
        df = _make_price_df(100)
        assert _compute_momentum_series(df) is None

    def test_returns_none_for_missing_columns(self):
        df = pd.DataFrame({"date": [1, 2], "price": [10, 11]})
        assert _compute_momentum_series(df) is None

    def test_returns_dataframe_with_expected_columns(self):
        df = _make_price_df(250)
        result = _compute_momentum_series(df)
        assert result is not None
        expected_cols = {"date", "close", "volume", "log_return", "daily_momentum",
                         "momentum_21d", "zscore"}
        assert expected_cols.issubset(set(result.columns))

    def test_log_return_first_row_is_nan(self):
        df = _make_price_df(250)
        result = _compute_momentum_series(df)
        assert np.isnan(result["log_return"].iloc[0])

    def test_log_return_correctness(self):
        df = _make_price_df(250)
        result = _compute_momentum_series(df)
        # 验证第 5 行的 log return
        expected = np.log(df["close"].iloc[5] / df["close"].iloc[4])
        assert abs(result["log_return"].iloc[5] - expected) < 1e-10

    def test_daily_momentum_formula(self):
        df = _make_price_df(250)
        result = _compute_momentum_series(df)
        idx = 10
        expected = df["close"].iloc[idx] * df["volume"].iloc[idx] * result["log_return"].iloc[idx]
        assert abs(result["daily_momentum"].iloc[idx] - expected) < 1e-4

    def test_zscore_has_valid_values_at_end(self):
        df = _make_price_df(250)
        result = _compute_momentum_series(df)
        # 最后一行 zscore 应该有值
        assert not np.isnan(result["zscore"].iloc[-1])


class TestComputeMarketMomentum:
    """compute_market_momentum 单股票计算测试"""

    def test_returns_none_for_insufficient_data(self):
        df = _make_price_df(100)
        assert compute_market_momentum(df) is None

    def test_returns_dict_with_expected_keys(self):
        df = _make_price_df(250)
        result = compute_market_momentum(df)
        assert result is not None
        assert set(result.keys()) == {"zscore", "raw_momentum_21d", "mean_150d", "std_150d"}

    def test_zscore_in_reasonable_range(self):
        df = _make_price_df(300)
        result = compute_market_momentum(df)
        assert result is not None
        # z-score 通常在 -5 到 5 之间
        assert -10 < result["zscore"] < 10

    def test_positive_trend_yields_positive_momentum(self):
        """强上涨趋势应产生正的 raw_momentum_21d"""
        df = _make_price_df(250, trend=0.01)  # 日均 1% 涨幅
        result = compute_market_momentum(df)
        assert result is not None
        assert result["raw_momentum_21d"] > 0

    def test_negative_trend_yields_negative_momentum(self):
        """下跌趋势应产生负的 raw_momentum_21d"""
        df = _make_price_df(250, trend=-0.01)  # 日均 1% 跌幅
        result = compute_market_momentum(df)
        assert result is not None
        assert result["raw_momentum_21d"] < 0

    def test_returns_none_for_empty(self):
        assert compute_market_momentum(pd.DataFrame()) is None

    def test_zero_volume_handling(self):
        """全零成交量应返回 None（zscore 全为 NaN）"""
        df = _make_price_df(250)
        df["volume"] = 0.0
        result = compute_market_momentum(df)
        # 全零 volume → daily_momentum 全为 0 → momentum_21d 全为 0
        # → std_150d = 0 → zscore = NaN → 返回 None
        assert result is None


class TestScanMarketMomentum:
    """scan_market_momentum 批量扫描测试"""

    def test_empty_input(self):
        result = scan_market_momentum({})
        assert isinstance(result, pd.DataFrame)
        assert result.empty
        assert list(result.columns) == ["symbol", "zscore", "raw_momentum_21d", "signal"]

    def test_single_stock(self):
        df = _make_price_df(250)
        result = scan_market_momentum({"AAPL": df})
        assert len(result) == 1
        assert result["symbol"].iloc[0] == "AAPL"

    def test_multiple_stocks_sorted_by_zscore(self):
        # 强趋势 vs 弱趋势
        strong = _make_price_df(250, trend=0.01)
        weak = _make_price_df(250, trend=0.001)
        result = scan_market_momentum({"STRONG": strong, "WEAK": weak})
        assert len(result) == 2
        # 应按 zscore 降序排列
        assert result["zscore"].iloc[0] >= result["zscore"].iloc[1]

    def test_columns_completeness(self):
        df = _make_price_df(250)
        result = scan_market_momentum({"TEST": df})
        expected_cols = ["symbol", "zscore", "raw_momentum_21d", "signal"]
        assert list(result.columns) == expected_cols

    def test_signal_column_respects_threshold(self):
        df = _make_price_df(250, trend=0.01)
        result = scan_market_momentum({"AAPL": df}, threshold=999)
        # 极高阈值，不应触发信号
        assert not result["signal"].iloc[0]

    def test_skips_insufficient_data(self):
        short_df = _make_price_df(50)
        long_df = _make_price_df(250)
        result = scan_market_momentum({"SHORT": short_df, "LONG": long_df})
        assert len(result) == 1
        assert result["symbol"].iloc[0] == "LONG"


class TestFactorIntegration:
    """Factor 框架集成测试"""

    def test_market_momentum_factor_registered(self):
        from backtest.factor_study.factors import ALL_FACTORS
        assert "Market_Momentum" in ALL_FACTORS

    def test_get_factor(self):
        from backtest.factor_study.factors import get_factor
        factor = get_factor("Market_Momentum")
        assert factor.meta.name == "Market_Momentum"
        assert factor.meta.score_name == "zscore"
        assert factor.meta.score_range == (-5, 5)
        assert factor.meta.higher_is_stronger is True
        assert factor.meta.min_data_days == 172

    def test_factor_compute_returns_scores(self):
        from backtest.factor_study.factors import get_factor
        factor = get_factor("Market_Momentum")
        df = _make_price_df(250)
        scores = factor.compute({"AAPL": df}, date="2021-01-01")
        assert "AAPL" in scores
        assert isinstance(scores["AAPL"], float)

    def test_sweep_config_exists(self):
        from backtest.factor_study.sweep import get_default_sweep
        signals = get_default_sweep("Market_Momentum")
        assert len(signals) > 0

    def test_list_factors_includes_market_momentum(self):
        from backtest.factor_study.factors import list_factors
        assert "Market_Momentum" in list_factors()


class TestMinDataDays:
    """MIN_DATA_DAYS 常量一致性"""

    def test_min_data_days_value(self):
        assert MIN_DATA_DAYS == 172  # 1 + 21 + 150

    def test_exactly_min_data_days_works(self):
        df = _make_price_df(MIN_DATA_DAYS)
        result = compute_market_momentum(df)
        assert result is not None

    def test_one_less_than_min_returns_none(self):
        df = _make_price_df(MIN_DATA_DAYS - 1)
        assert compute_market_momentum(df) is None
