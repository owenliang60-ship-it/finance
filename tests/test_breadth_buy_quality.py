"""Tests for broad-breadth buy-quality hardening."""
from __future__ import annotations

import pandas as pd
import pytest

from backtest.breadth_study.buy_quality import (
    distance_to_future_min,
    forward_percentile_rank,
    max_drawdown_after_entry,
)
from scripts.run_breadth_buy_quality import load_event_dates


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


def test_distance_signal_is_min():
    """信号日就是未来最低 -> 距离 = 0."""
    closes = pd.Series(
        [100.0, 110, 120],
        index=pd.date_range("2025-01-01", periods=3),
    )

    distance = distance_to_future_min(closes, signal_idx=0, window=2)

    assert distance == 0.0


def test_distance_simple():
    """信号日 100, 未来最低 90 -> 距离 = (100-90)/90 = 11.11%."""
    closes = pd.Series(
        [100.0, 95, 90, 105],
        index=pd.date_range("2025-01-01", periods=4),
    )

    distance = distance_to_future_min(closes, signal_idx=0, window=3)

    assert distance == pytest.approx(10 / 90)


def test_load_event_dates_s1_active_snapshot():
    events = load_event_dates(signal="S1", universe="active")

    assert len(events) == 14
    assert events[:3] == [
        pd.Timestamp("2021-12-02"),
        pd.Timestamp("2022-01-24"),
        pd.Timestamp("2022-02-25"),
    ]
    assert events[-3:] == [
        pd.Timestamp("2025-03-14"),
        pd.Timestamp("2025-04-24"),
        pd.Timestamp("2026-03-24"),
    ]


def test_load_event_dates_s1_with_delisted_snapshot():
    events = load_event_dates(signal="S1", universe="with_delisted_partial")

    assert len(events) == 14
    assert events[:3] == [
        pd.Timestamp("2021-12-02"),
        pd.Timestamp("2022-01-24"),
        pd.Timestamp("2022-02-24"),
    ]
    assert events[-3:] == [
        pd.Timestamp("2025-03-14"),
        pd.Timestamp("2025-04-24"),
        pd.Timestamp("2026-03-24"),
    ]


def test_load_event_dates_s2_active_snapshot():
    events = load_event_dates(signal="S2", universe="active")

    assert len(events) == 16
    assert events[:3] == [
        pd.Timestamp("2021-06-21"),
        pd.Timestamp("2021-09-22"),
        pd.Timestamp("2021-12-21"),
    ]
    assert events[-3:] == [
        pd.Timestamp("2025-08-04"),
        pd.Timestamp("2025-11-21"),
        pd.Timestamp("2026-03-25"),
    ]


def test_load_event_dates_s2_with_delisted_snapshot():
    events = load_event_dates(signal="S2", universe="with_delisted_partial")

    assert len(events) == 17
    assert events[:3] == [
        pd.Timestamp("2021-03-10"),
        pd.Timestamp("2021-06-21"),
        pd.Timestamp("2021-09-22"),
    ]
    assert events[-3:] == [
        pd.Timestamp("2025-08-04"),
        pd.Timestamp("2025-11-21"),
        pd.Timestamp("2026-03-25"),
    ]
