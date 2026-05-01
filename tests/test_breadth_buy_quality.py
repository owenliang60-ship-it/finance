"""Tests for broad-breadth buy-quality hardening."""
from __future__ import annotations

import pandas as pd
import numpy as np
import pytest

from backtest.breadth_study.buy_quality import (
    compute_better_than_random_pct_simple,
    distance_to_future_min,
    forward_percentile_rank,
    max_drawdown_after_entry,
    sample_dates_stratified_cooldown,
)
from scripts.run_breadth_buy_quality import (
    SAMPLE_END,
    SAMPLE_START,
    compute_all_days_baseline,
    load_event_dates,
    load_target_closes,
    run_buy_quality_pipeline,
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


def test_events_csv_columns_and_rows(tmp_path):
    """events.csv keeps every raw trigger x target x window row, including NaNs."""
    run_buy_quality_pipeline(output_dir=tmp_path)

    df = pd.read_csv(tmp_path / "events.csv")

    expected_cols = {
        "signal",
        "universe",
        "event_date",
        "target",
        "window_days",
        "signal_close",
        "rank_pct",
        "max_dd",
        "dist_to_min",
    }
    assert expected_cols.issubset(df.columns)
    assert 850 <= len(df) <= 1000
    assert len(df) == (14 + 14 + 16 + 17) * 3 * 5


def test_load_target_closes_respects_sample_window():
    """target close is clipped to the frozen sample window."""
    closes = load_target_closes(["SPY"])["SPY"]

    assert closes.index.min() >= pd.Timestamp(SAMPLE_START)
    assert closes.index.max() <= pd.Timestamp(SAMPLE_END)


def test_all_days_baseline_shape():
    baseline = compute_all_days_baseline(targets=["SPY"], windows=[60])

    assert "rank_pct" in baseline.columns
    assert 1200 <= len(baseline) <= 1300


def test_random_sample_simple_signal_clearly_better():
    all_days_metric = pd.Series(np.random.default_rng(42).uniform(0, 1, 1300))
    event_metric = pd.Series([0.05, 0.08, 0.12, 0.15, 0.10] * 3)

    p = compute_better_than_random_pct_simple(
        event_metric,
        all_days_metric,
        n_iter=10000,
        lower_is_better=True,
    )

    assert p >= 0.95


def test_random_sample_stratified_respects_per_year_cooldown():
    """Stratified sampling enforces positional cooldown within each year only."""
    rng = np.random.default_rng(42)
    all_days_dates = pd.DatetimeIndex(pd.date_range("2021-02-01", periods=1300, freq="B"))
    real_event_dates = [
        pd.Timestamp("2022-03-15"),
        pd.Timestamp("2022-09-01"),
        pd.Timestamp("2023-04-03"),
        pd.Timestamp("2023-10-16"),
    ]

    sampled = sample_dates_stratified_cooldown(
        all_days_dates,
        real_event_dates,
        cooldown=20,
        rng=rng,
    )

    years = sorted({date.year for date in sampled})
    for year in years:
        positions = sorted(
            all_days_dates.get_loc(date) for date in sampled if date.year == year
        )
        for pos_a, pos_b in zip(positions[:-1], positions[1:]):
            assert pos_b - pos_a >= 20, (
                f"year {year}: positional gap {pos_b - pos_a} < cooldown 20"
            )
