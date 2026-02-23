"""
数据适配器测试

使用临时目录的合成 CSV 测试加载逻辑。
"""

import pytest
import numpy as np
import pandas as pd
from pathlib import Path
import tempfile
import shutil

from backtest.adapters.crypto_rs import compute_crypto_rs_b, compute_crypto_rs_c


# ── crypto_rs 纯计算测试 ─────────────────────────────

class TestCryptoRsB:
    """币圈 Method B 测试"""

    def _make_prices(self, n_symbols=5, n_days=30, seed=42):
        rng = np.random.RandomState(seed)
        return {
            f"SYM{i}USDT": 100 * np.exp(
                np.cumsum(rng.normal(0.001 * (i + 1), 0.03, n_days))
            )
            for i in range(n_symbols)
        }

    def test_basic(self):
        prices = self._make_prices()
        df = compute_crypto_rs_b(prices)
        assert not df.empty
        assert "rs_rank" in df.columns
        assert "symbol" in df.columns
        assert len(df) == 5

    def test_rank_range(self):
        prices = self._make_prices(n_symbols=20)
        df = compute_crypto_rs_b(prices)
        assert df["rs_rank"].min() >= 0
        assert df["rs_rank"].max() <= 99

    def test_insufficient_data(self):
        prices = {"A": np.array([100, 101, 102])}
        df = compute_crypto_rs_b(prices)
        assert df.empty

    def test_single_symbol(self):
        prices = {"A": np.random.RandomState(42).normal(100, 5, 30).cumsum() + 1000}
        prices["A"] = np.abs(prices["A"])
        df = compute_crypto_rs_b(prices)
        if not df.empty:
            assert df.iloc[0]["rs_rank"] == 50  # 单只居中

    def test_empty_input(self):
        df = compute_crypto_rs_b({})
        assert df.empty


class TestCryptoRsC:
    """币圈 Method C 测试"""

    def _make_prices(self, n_symbols=5, n_days=30, seed=42):
        rng = np.random.RandomState(seed)
        return {
            f"SYM{i}USDT": 100 * np.exp(
                np.cumsum(rng.normal(0.001 * (i + 1), 0.03, n_days))
            )
            for i in range(n_symbols)
        }

    def test_basic(self):
        prices = self._make_prices()
        df = compute_crypto_rs_c(prices)
        assert not df.empty
        assert "composite" in df.columns
        assert len(df) == 5

    def test_empty_input(self):
        df = compute_crypto_rs_c({})
        assert df.empty


# ── 适配器集成测试 (临时目录) ────────────────────────

class TestCryptoAdapter:
    """CryptoAdapter 加载测试"""

    @pytest.fixture
    def temp_cache(self, tmp_path):
        """创建临时 CSV 缓存"""
        cache_dir = tmp_path / "daily_klines"
        cache_dir.mkdir()

        rng = np.random.RandomState(42)
        for sym in ["BTCUSDT", "ETHUSDT", "SOLUSDT"]:
            dates = pd.bdate_range("2024-01-01", periods=30)
            df = pd.DataFrame({
                "date": [d.strftime("%Y-%m-%d") for d in dates],
                "close": 100 * np.exp(np.cumsum(rng.normal(0.001, 0.02, 30))),
                "volume": rng.uniform(1e6, 1e7, 30),
            })
            df.to_csv(cache_dir / f"{sym}.csv", index=False)

        return cache_dir

    def test_load_all(self, temp_cache):
        from backtest.adapters.crypto import CryptoAdapter
        adapter = CryptoAdapter(cache_dir=temp_cache)
        data = adapter.load_all()
        assert len(data) == 3
        assert "BTCUSDT" in data

    def test_trading_dates(self, temp_cache):
        from backtest.adapters.crypto import CryptoAdapter
        adapter = CryptoAdapter(cache_dir=temp_cache)
        dates = adapter.get_trading_dates()
        assert len(dates) > 0
        assert dates == sorted(dates)

    def test_slice_to_date(self, temp_cache):
        from backtest.adapters.crypto import CryptoAdapter
        adapter = CryptoAdapter(cache_dir=temp_cache)
        adapter.load_all()
        dates = adapter.get_trading_dates()
        mid_date = dates[len(dates) // 2]
        sliced = adapter.slice_to_date(mid_date)
        for sym, arr in sliced.items():
            assert isinstance(arr, np.ndarray)

    def test_get_prices_at(self, temp_cache):
        from backtest.adapters.crypto import CryptoAdapter
        adapter = CryptoAdapter(cache_dir=temp_cache)
        adapter.load_all()
        dates = adapter.get_trading_dates()
        prices = adapter.get_prices_at(dates[0])
        assert len(prices) > 0
        for v in prices.values():
            assert v > 0

    def test_rs_function(self, temp_cache):
        from backtest.adapters.crypto import CryptoAdapter
        adapter = CryptoAdapter(cache_dir=temp_cache)
        fn_b = adapter.get_rs_function("B")
        fn_c = adapter.get_rs_function("C")
        assert callable(fn_b)
        assert callable(fn_c)
