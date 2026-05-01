"""Tests for broad-breadth buy-quality hardening."""
from __future__ import annotations

import pandas as pd
import pytest

from backtest.breadth_study.buy_quality import (
    forward_percentile_rank,
    max_drawdown_after_entry,
)


def test_forward_percentile_rank_signal_is_minimum():
    """信号日 close 就是未来 N 天最低 close -> 排位 = 0%."""
    closes = pd.Series(
        [100.0, 110, 115, 120, 130],
        index=pd.date_range("2025-01-01", periods=5),
    )

    rank = forward_percentile_rank(closes, signal_idx=0, window=4)

    assert rank == 0.0


def test_forward_percentile_rank_signal_is_maximum():
    """信号日 close 就是未来 N 天最高 close -> 排位 = 100%."""
    closes = pd.Series(
        [130.0, 110, 105, 100, 95],
        index=pd.date_range("2025-01-01", periods=5),
    )

    rank = forward_percentile_rank(closes, signal_idx=0, window=4)

    assert rank == 1.0


def test_forward_percentile_rank_window_truncated():
    """样本末端窗口不足 -> 返回 NaN."""
    closes = pd.Series(
        [100.0, 110],
        index=pd.date_range("2025-01-01", periods=2),
    )

    rank = forward_percentile_rank(closes, signal_idx=0, window=5)

    assert pd.isna(rank)


def test_max_drawdown_no_drop():
    """信号日后单调上涨 -> 回撤 = 0."""
    closes = pd.Series(
        [100.0, 105, 110, 120],
        index=pd.date_range("2025-01-01", periods=4),
    )

    dd = max_drawdown_after_entry(closes, signal_idx=0, window=3)

    assert dd == 0.0


def test_max_drawdown_simple():
    """信号日 close=100, 未来最低 90 -> 回撤 = -10%."""
    closes = pd.Series(
        [100.0, 95, 90, 105],
        index=pd.date_range("2025-01-01", periods=4),
    )

    dd = max_drawdown_after_entry(closes, signal_idx=0, window=3)

    assert dd == pytest.approx(-0.10)
