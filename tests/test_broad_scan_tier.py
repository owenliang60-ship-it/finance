"""Tests for broad scan tiering and group filters."""

import pandas as pd

from scripts.broad_market_scan import classify_tier, compute_adv_20d, split_group_candidates


def test_classify_tier_high_by_mcap():
    assert classify_tier({"marketCap": 6e9, "consecutive_days": 1}) == "🔥"


def test_classify_tier_high_by_streak():
    assert classify_tier({"marketCap": 2e9, "consecutive_days": 3}) == "🔥"


def test_classify_tier_defaults_streak():
    assert classify_tier({"marketCap": 2e9}) == "📊"


def test_compute_adv_20d():
    frame = pd.DataFrame(
        {
            "close": [10.0] * 25,
            "volume": [1000.0] * 25,
        }
    )
    assert compute_adv_20d(frame) == 10_000.0


def test_split_group_candidates_filters_low_adv():
    frame = pd.DataFrame({"close": [10.0] * 25, "volume": [100.0] * 25})
    candidates = [{"symbol": "LOW", "marketCap": 2e9, "consecutive_days": 1}]

    group, log_only = split_group_candidates(candidates, {"LOW": frame})

    assert group == []
    assert len(log_only) == 1


def test_split_group_candidates_keeps_high_adv_and_mcap():
    frame = pd.DataFrame({"close": [100.0] * 25, "volume": [100000.0] * 25})
    candidates = [{"symbol": "GOOD", "marketCap": 6e9, "consecutive_days": 1}]

    group, log_only = split_group_candidates(candidates, {"GOOD": frame})

    assert len(group) == 1
    assert log_only == []
