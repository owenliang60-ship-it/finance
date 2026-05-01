from __future__ import annotations

import math

import pandas as pd

from backtest.breadth_study.recovery_sweep import (
    RecoverySweepConfig,
    detect_recovery_events,
    iter_event_specs,
)


def test_cross_up_detects_threshold_cross_with_cooldown():
    df = pd.DataFrame(
        {
            "date": pd.bdate_range("2024-01-01", periods=8),
            "signal": [0.18, 0.19, 0.21, 0.19, 0.22, 0.18, 0.19, 0.23],
        }
    )

    events = detect_recovery_events(
        df,
        signal_col="signal",
        event_family="cross_up",
        trigger_threshold=0.20,
        low_threshold=None,
        cooldown_days=3,
    )

    assert [event["date"] for event in events] == [df.loc[2, "date"], df.loc[7, "date"]]


def test_low_to_trigger_requires_prior_low_before_trigger():
    df = pd.DataFrame(
        {
            "date": pd.bdate_range("2024-01-01", periods=7),
            "signal": [0.31, 0.34, 0.41, 0.26, 0.29, 0.37, 0.42],
        }
    )

    events = detect_recovery_events(
        df,
        signal_col="signal",
        event_family="low_to_trigger",
        trigger_threshold=0.40,
        low_threshold=0.30,
        cooldown_days=1,
    )

    assert [event["date"] for event in events] == [df.loc[6, "date"]]


def test_iter_event_specs_skips_invalid_low_trigger_pairs():
    config = RecoverySweepConfig(
        trigger_thresholds=[0.20, 0.30],
        low_thresholds=[0.20, 0.25],
    )

    specs = list(iter_event_specs(config))
    cross_up = [spec for spec in specs if spec[0] == "cross_up"]

    assert len(cross_up) == 2
    assert math.isnan(cross_up[0][1])
    assert ("low_to_trigger", 0.20, 0.20) not in specs
    assert ("low_to_trigger", 0.25, 0.20) not in specs
    assert ("low_to_trigger", 0.20, 0.30) in specs
    assert ("low_to_trigger", 0.25, 0.30) in specs
