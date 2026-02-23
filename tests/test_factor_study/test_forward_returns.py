"""
前向收益矩阵测试
"""

import numpy as np
import pandas as pd
import pytest

from backtest.factor_study.forward_returns import build_return_matrix


# ── 合成数据 ─────────────────────────────────────────────

def _make_price_dict():
    """3 只股票 × 20 天, 固定价格方便验证"""
    dates = [f"2024-01-{d:02d}" for d in range(1, 21)]

    # AAPL: 每天涨 1%
    aapl_prices = [100 * 1.01 ** i for i in range(20)]
    # MSFT: 固定 200
    msft_prices = [200.0] * 20
    # GOOG: 每天跌 0.5%
    goog_prices = [150 * 0.995 ** i for i in range(20)]

    return {
        "AAPL": pd.DataFrame({"date": dates, "close": aapl_prices}),
        "MSFT": pd.DataFrame({"date": dates, "close": msft_prices}),
        "GOOG": pd.DataFrame({"date": dates, "close": goog_prices}),
    }


# ── 测试 ─────────────────────────────────────────────────

class TestBuildReturnMatrix:
    def test_basic_shape(self):
        price_dict = _make_price_dict()
        dates = ["2024-01-01", "2024-01-05", "2024-01-10"]
        horizons = [5, 10]

        result = build_return_matrix(price_dict, dates, horizons)

        assert 5 in result
        assert 10 in result
        assert result[5].shape == (3, 3)  # 3 dates × 3 symbols
        assert result[10].shape == (3, 3)

    def test_aapl_5d_return(self):
        """AAPL 每天涨 1%, 5 天 forward return ≈ 1.01^5 - 1"""
        price_dict = _make_price_dict()
        dates = ["2024-01-01"]
        horizons = [5]

        result = build_return_matrix(price_dict, dates, horizons)
        ret = result[5].loc["2024-01-01", "AAPL"]

        expected = 1.01 ** 5 - 1
        assert abs(ret - expected) < 1e-10

    def test_msft_flat(self):
        """MSFT 价格不变, forward return = 0"""
        price_dict = _make_price_dict()
        dates = ["2024-01-01"]
        horizons = [5]

        result = build_return_matrix(price_dict, dates, horizons)
        ret = result[5].loc["2024-01-01", "MSFT"]

        assert abs(ret) < 1e-10

    def test_nan_for_out_of_range(self):
        """日期超出范围应返回 NaN"""
        price_dict = _make_price_dict()
        dates = ["2024-01-15"]
        horizons = [10]  # 15 + 10 = 25 > 20 天数据

        result = build_return_matrix(price_dict, dates, horizons)
        ret = result[10].loc["2024-01-15", "AAPL"]

        assert np.isnan(ret)

    def test_nan_for_missing_date(self):
        """不在数据中的日期应返回 NaN"""
        price_dict = _make_price_dict()
        dates = ["2024-02-01"]  # 不存在
        horizons = [5]

        result = build_return_matrix(price_dict, dates, horizons)
        ret = result[5].loc["2024-02-01", "AAPL"]

        assert np.isnan(ret)

    def test_multiple_horizons(self):
        """多个 horizon 都正确"""
        price_dict = _make_price_dict()
        dates = ["2024-01-01"]
        horizons = [1, 5, 10]

        result = build_return_matrix(price_dict, dates, horizons)

        # AAPL 1d return
        ret_1 = result[1].loc["2024-01-01", "AAPL"]
        assert abs(ret_1 - 0.01) < 1e-10

        # GOOG 5d return (下跌)
        ret_5 = result[5].loc["2024-01-01", "GOOG"]
        expected = 0.995 ** 5 - 1
        assert abs(ret_5 - expected) < 1e-10

    def test_empty_price_dict(self):
        result = build_return_matrix({}, ["2024-01-01"], [5])
        assert 5 in result
        assert result[5].empty
