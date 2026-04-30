"""Tests for upcross event detector (Task 3)."""
from __future__ import annotations

import pandas as pd

from backtest.breadth_study.percentile_events import detect_upcross_events


def test_upcross_basic_detection():
    """Series crosses K=0.10 from below at idx 4 (0.06 -> 0.12)."""
    signal = pd.Series([0.5, 0.5, 0.04, 0.06, 0.12, 0.20])
    events = detect_upcross_events(signal, threshold=0.10, cooldown_days=20)
    assert len(events) == 1
    assert events[0]["index"] == 4


def test_cooldown_blocks_repeat():
    """Repeated up/down crossings — short cooldown emits more events."""
    signal = pd.Series([0.05, 0.15, 0.05, 0.15] * 10)  # 40 entries
    events_short = detect_upcross_events(signal, threshold=0.10, cooldown_days=5)
    events_long = detect_upcross_events(signal, threshold=0.10, cooldown_days=20)
    assert len(events_short) > len(events_long)


def test_no_event_if_starts_above_threshold():
    """Signal stays above threshold; never crosses up from below."""
    signal = pd.Series([0.5, 0.6, 0.7])
    events = detect_upcross_events(signal, threshold=0.10, cooldown_days=20)
    assert events == []


def test_adaptive_cooldown_per_horizon_group():
    """~30-day gap between two upcrosses: short=20d catches both, long=60d only one."""
    # idx 0: 0.05 (below)
    # idx 1: 0.15 (above) -> upcross at idx 1
    # idx 2..31: stays at 0.5 (above)
    # idx 32: 0.05 (drops below)
    # idx 33: 0.15 -> upcross at idx 33 (gap = 32 days)
    signal = pd.Series([0.05, 0.15] + [0.5] * 30 + [0.05, 0.15])
    short = detect_upcross_events(signal, threshold=0.10, cooldown_days=20)
    long_ = detect_upcross_events(signal, threshold=0.10, cooldown_days=60)
    assert len(short) == 2
    assert len(long_) == 1
