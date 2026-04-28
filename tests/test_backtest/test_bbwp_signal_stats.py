import pandas as pd

from backtest.research.bbwp_signal_stats import (
    BBWPSignalStatsConfig,
    build_bbwp_feature_frames,
    build_bbwp_signal_buckets,
    compare_trend_buckets,
    run_reversal_score_stats,
)


def test_build_bbwp_feature_frames_classifies_trend_bucket_from_middle_band():
    config = BBWPSignalStatsConfig(bb_period=2, bb_std=2.0, bbwp_lookback=2)
    stock = pd.DataFrame(
        {
            "date": ["2024-01-01", "2024-01-02", "2024-01-03", "2024-01-04"],
            "open": [10, 10, 10, 10],
            "close": [10, 12, 11, 9],
        }
    )

    frames = build_bbwp_feature_frames({"AAA": stock}, config)
    frame = frames["AAA"]
    assert "bb_middle" in frame.columns
    assert set(frame["trend_bucket"].dropna().unique()).issubset({"above_mid", "below_mid", "on_mid"})


def test_build_bbwp_signal_buckets_splits_above_and_below_mid():
    config = BBWPSignalStatsConfig(study_start_date="2024-01-01")
    frame = pd.DataFrame(
        {
            "date": ["2024-01-01", "2024-01-02", "2024-01-03"],
            "bbwp_down98": [True, True, True],
            "trend_bucket": ["above_mid", "below_mid", "on_mid"],
        }
    )

    buckets = build_bbwp_signal_buckets({"AAA": frame}, config)
    assert buckets["bbwp_down98_above_mid"]["AAA"] == ["2024-01-01"]
    assert buckets["bbwp_down98_below_mid"]["AAA"] == ["2024-01-02"]
    assert buckets["bbwp_down98_on_mid"]["AAA"] == ["2024-01-03"]


def test_compare_trend_buckets_prefers_below_when_below_mean_is_higher():
    above_events = {"AAA": ["2024-01-01", "2024-01-02"]}
    below_events = {"AAA": ["2024-01-03", "2024-01-04"]}
    return_matrices = {
        3: pd.DataFrame(
            {"AAA": [-0.03, -0.01, 0.04, 0.05]},
            index=["2024-01-01", "2024-01-02", "2024-01-03", "2024-01-04"],
        )
    }

    result = compare_trend_buckets(above_events, below_events, return_matrices)[0]
    assert result.below_mean_return > result.above_mean_return
    assert result.diff_below_minus_above > 0


def test_run_reversal_score_stats_is_positive_when_returns_reverse_trend():
    above_events = {"AAA": ["2024-01-01", "2024-01-02"]}
    below_events = {"AAA": ["2024-01-03", "2024-01-04"]}
    return_matrices = {
        3: pd.DataFrame(
            {"AAA": [-0.02, -0.01, 0.03, 0.02]},
            index=["2024-01-01", "2024-01-02", "2024-01-03", "2024-01-04"],
        )
    }

    result = run_reversal_score_stats(above_events, below_events, return_matrices)[0]
    assert result.mean_score > 0
