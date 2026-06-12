"""compute_beta 单测 — 全部合成序列，不碰 DB。"""
import numpy as np
import pandas as pd

from src.indicators.beta import compute_beta, BETA_WINDOW, BETA_MIN_OBS


def _make_pair(n=200, beta=1.5, seed=42, noise_sd=0.001):
    """构造已知 beta 的个股/基准收盘价对（日简单收益率线性关系 + 噪声）。"""
    rng = np.random.default_rng(seed)
    dates = pd.bdate_range("2025-08-01", periods=n)
    bench_ret = rng.normal(0.0005, 0.01, n)
    stock_ret = beta * bench_ret + rng.normal(0.0, noise_sd, n)
    bench = pd.Series(100.0 * np.cumprod(1 + bench_ret), index=dates)
    stock = pd.Series(50.0 * np.cumprod(1 + stock_ret), index=dates)
    return stock, bench


def test_recovers_known_beta():
    stock, bench = _make_pair(beta=1.5)
    result = compute_beta(stock, bench)
    assert result is not None
    assert abs(result - 1.5) < 0.05          # spec §6.1


def test_recovers_low_beta():
    stock, bench = _make_pair(beta=0.4, seed=7)
    assert abs(compute_beta(stock, bench) - 0.4) < 0.05


def test_uses_tail_window_only():
    # 前段 beta=0.5、后 130 日 beta=2.0 的拼接序列：窗口只看末尾 → 接近 2.0
    s1, b1 = _make_pair(n=120, beta=0.5, seed=1)
    rng = np.random.default_rng(2)
    dates2 = pd.bdate_range(s1.index[-1] + pd.Timedelta(days=1), periods=130)
    bench_ret2 = rng.normal(0.0005, 0.01, 130)
    stock_ret2 = 2.0 * bench_ret2 + rng.normal(0.0, 0.001, 130)
    bench = pd.concat([b1, pd.Series(float(b1.iloc[-1]) * np.cumprod(1 + bench_ret2), index=dates2)])
    stock = pd.concat([s1, pd.Series(float(s1.iloc[-1]) * np.cumprod(1 + stock_ret2), index=dates2)])
    result = compute_beta(stock, bench, window=126)
    assert abs(result - 2.0) < 0.1


def test_insufficient_overlap_returns_none():
    stock, bench = _make_pair(n=40)          # 39 个收益率 < BETA_MIN_OBS=60
    assert compute_beta(stock, bench) is None


def test_misaligned_dates_inner_join():
    # 个股中段停牌 30 日：inner join 后仍 >= min_obs，正常出值且接近真值
    stock, bench = _make_pair(n=200, beta=1.2, seed=3)
    stock = stock.drop(stock.index[80:110])
    result = compute_beta(stock, bench)
    assert result is not None
    assert abs(result - 1.2) < 0.1


def test_constant_benchmark_returns_none():
    stock, _ = _make_pair()
    flat = pd.Series(100.0, index=stock.index)
    assert compute_beta(stock, flat) is None


def test_none_or_empty_inputs():
    stock, bench = _make_pair()
    assert compute_beta(None, bench) is None
    assert compute_beta(stock, None) is None
    assert compute_beta(pd.Series(dtype=float), bench) is None


def test_zero_close_bad_data_returns_finite_or_none():
    # 真实案例（STRF 2026-03-16 close=0.0）：0 价产生 inf 收益率，
    # cov 变 NaN 后污染返回值。坏行必须按缺失剔除，结果保持有限且接近真值。
    stock, bench = _make_pair(n=200, beta=1.5)
    stock.iloc[100] = 0.0
    result = compute_beta(stock, bench)
    assert result is not None
    assert np.isfinite(result)
    assert abs(result - 1.5) < 0.1


def test_defaults_match_spec():
    assert BETA_WINDOW == 126
    assert BETA_MIN_OBS == 60
