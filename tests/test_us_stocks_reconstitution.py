"""universe reconstitution 过滤测试"""
import pytest
from unittest.mock import patch
import pandas as pd
from backtest.adapters.us_stocks import USStocksAdapter


def _make_price_df(dates, closes):
    return pd.DataFrame({
        "date": dates,
        "open": closes, "high": closes, "low": closes,
        "close": closes, "volume": [1e6] * len(dates),
        "change": [0.0] * len(dates), "change_pct": [0.0] * len(dates),
    })


def _make_adapter(symbols, mcap_threshold=None):
    adapter = USStocksAdapter(symbols=symbols, mcap_threshold=mcap_threshold)
    dates = [f"2024-01-{d:02d}" for d in range(2, 72)]  # 70 天
    adapter._price_cache = {
        sym: _make_price_df(dates, [100.0] * 70) for sym in symbols
    }
    return adapter


def test_slice_to_date_filters_by_mcap():
    """SMALL 在该日市值 < 10B 应被过滤掉"""
    adapter = _make_adapter(["BIG", "SMALL"], mcap_threshold=10_000_000_000)
    mock_caps = {"BIG": 50_000_000_000, "SMALL": 5_000_000_000}

    with patch("backtest.adapters.us_stocks._get_bulk_mcaps", return_value=mock_caps):
        sliced = adapter.slice_to_date("2024-03-01")

    assert "BIG" in sliced
    assert "SMALL" not in sliced


def test_missing_mcap_data_keeps_stock():
    """无 mcap 数据的 symbol 应被保留（不踢出），避免数据缺失污染结果"""
    # 10 symbols, 9 有 mcap 数据 (90% 覆盖率通过), 1 个缺失 → 应保留
    symbols = [f"S{i}" for i in range(9)] + ["NO_DATA"]
    adapter = _make_adapter(symbols, mcap_threshold=10_000_000_000)
    mock_caps = {f"S{i}": 50_000_000_000 for i in range(9)}  # NO_DATA not in dict

    with patch("backtest.adapters.us_stocks._get_bulk_mcaps", return_value=mock_caps):
        sliced = adapter.slice_to_date("2024-03-01")

    assert "S0" in sliced
    assert "NO_DATA" in sliced  # 保留，不踢出


def test_coverage_gate_raises_on_low_coverage():
    """覆盖率 < 90% 时应 raise，拒绝出结果"""
    adapter = _make_adapter(
        [f"S{i}" for i in range(10)], mcap_threshold=10_000_000_000
    )
    # 只有 5/10 有 mcap 数据 = 50% 覆盖率
    mock_caps = {f"S{i}": 50_000_000_000 for i in range(5)}

    with patch("backtest.adapters.us_stocks._get_bulk_mcaps", return_value=mock_caps):
        with pytest.raises(ValueError, match="覆盖率"):
            adapter.slice_to_date("2024-03-01")


def test_coverage_gate_fires_every_rebalance():
    """覆盖率门卫不是只检查一次 — 第二次 rebalance 覆盖率下降也应 raise"""
    adapter = _make_adapter(
        [f"S{i}" for i in range(10)], mcap_threshold=10_000_000_000
    )
    good_caps = {f"S{i}": 50_000_000_000 for i in range(10)}  # 100%
    bad_caps = {f"S{i}": 50_000_000_000 for i in range(5)}   # 50%

    # 第一次通过
    with patch("backtest.adapters.us_stocks._get_bulk_mcaps", return_value=good_caps):
        sliced = adapter.slice_to_date("2024-02-15")
    assert len(sliced) == 10

    # 第二次覆盖率下降 → 应该 raise，不能放行
    with patch("backtest.adapters.us_stocks._get_bulk_mcaps", return_value=bad_caps):
        with pytest.raises(ValueError, match="覆盖率"):
            adapter.slice_to_date("2024-03-01")


def test_no_filter_without_threshold():
    """不设 mcap_threshold 时，所有股票都通过"""
    adapter = _make_adapter(["A", "B"])
    sliced = adapter.slice_to_date("2024-03-01")
    assert "A" in sliced
    assert "B" in sliced
