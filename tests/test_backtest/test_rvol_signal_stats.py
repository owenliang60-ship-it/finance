import pandas as pd

from backtest.research.rvol_signal_stats import (
    RVOLSignalStatsConfig,
    _pmarp_bucket,
    build_rvol_feature_frames,
    build_rvol_signal_buckets,
    build_symbol_date_index,
    run_bucket_event_stats,
)


def test_build_rvol_feature_frames_marks_cross_up_and_context_columns():
    config = RVOLSignalStatsConfig(
        study_start_date="2024-01-01",
        rvol_lookback=2,
        rvol_threshold=2.0,
        pmarp_ema_period=2,
        pmarp_lookback=2,
        flat_move_threshold=0.01,
        pmarp_low_cutoff=30.0,
        pmarp_high_cutoff=60.0,
    )
    stock = pd.DataFrame(
        {
            "date": ["2024-01-01", "2024-01-02", "2024-01-03", "2024-01-04"],
            "open": [10, 10.5, 11.5, 10.0],
            "close": [10.0, 11.0, 10.0, 10.1],
            "volume": [100, 200, 240, 320],
        }
    )

    frames = build_rvol_feature_frames({"AAA": stock}, config)
    frame = frames["AAA"]
    assert frame["rvol_up2"].tolist() == [False, False, False, True]
    assert "move_bucket" in frame.columns
    assert "pmarp_bucket" in frame.columns


def test_build_rvol_signal_buckets_assigns_diagnostic_buckets():
    config = RVOLSignalStatsConfig(study_start_date="2024-01-01")
    frame = pd.DataFrame(
        {
            "date": ["2024-01-01", "2024-01-02", "2024-01-03"],
            "rvol_up2": [True, True, True],
            "move_bucket": ["sign_neg", "sign_flat", "sign_pos"],
            "pmarp_bucket": ["pmarp_lt30", "pmarp_30_60", "pmarp_gte60"],
        }
    )

    buckets = build_rvol_signal_buckets({"AAA": frame}, config)
    assert buckets["rvol_up2_sign_neg"]["AAA"] == ["2024-01-01"]
    assert buckets["rvol_up2_sign_flat"]["AAA"] == ["2024-01-02"]
    assert buckets["rvol_up2_sign_pos"]["AAA"] == ["2024-01-03"]
    assert buckets["rvol_up2_pmarp_lt30"]["AAA"] == ["2024-01-01"]
    assert buckets["rvol_up2_pmarp_30_60"]["AAA"] == ["2024-01-02"]
    assert buckets["rvol_up2_pmarp_gte60"]["AAA"] == ["2024-01-03"]
    assert buckets["rvol_up2_panic_proxy"]["AAA"] == ["2024-01-01"]
    assert buckets["rvol_up2_base_proxy"]["AAA"] == ["2024-01-02"]
    assert buckets["rvol_up2_churn_proxy"]["AAA"] == ["2024-01-03"]


def test_pmarp_bucket_uses_30_60_boundaries():
    assert _pmarp_bucket(29.99, low_cutoff=30.0, high_cutoff=60.0) == "pmarp_lt30"
    assert _pmarp_bucket(30.0, low_cutoff=30.0, high_cutoff=60.0) == "pmarp_30_60"
    assert _pmarp_bucket(59.99, low_cutoff=30.0, high_cutoff=60.0) == "pmarp_30_60"
    assert _pmarp_bucket(60.0, low_cutoff=30.0, high_cutoff=60.0) == "pmarp_gte60"


def test_run_bucket_event_stats_deoverlaps_same_symbol_with_horizon():
    feature_frames = {
        "AAA": pd.DataFrame({"date": ["2024-01-01", "2024-01-02", "2024-01-03", "2024-01-04"]})
    }
    symbol_date_index = build_symbol_date_index(feature_frames)
    events = {"AAA": ["2024-01-01", "2024-01-02", "2024-01-04"]}
    return_matrices = {
        2: pd.DataFrame(
            {"AAA": [0.10, 0.20, 0.00, 0.30]},
            index=["2024-01-01", "2024-01-02", "2024-01-03", "2024-01-04"],
        )
    }

    result = run_bucket_event_stats("rvol_up2_all", events, return_matrices, symbol_date_index)[0]
    assert result.n_events_raw == 3
    assert result.n_events_dedup == 2
    assert result.n_events_scored == 2
    assert result.n_effective == 2
    assert result.mean_event_return == 0.20


def test_run_bucket_event_stats_clusters_same_day_cross_section():
    feature_frames = {
        "AAA": pd.DataFrame({"date": ["2024-01-01", "2024-01-02"]}),
        "BBB": pd.DataFrame({"date": ["2024-01-01", "2024-01-02"]}),
    }
    symbol_date_index = build_symbol_date_index(feature_frames)
    events = {"AAA": ["2024-01-01"], "BBB": ["2024-01-01"]}
    return_matrices = {
        1: pd.DataFrame(
            {"AAA": [0.10], "BBB": [0.30]},
            index=["2024-01-01"],
        )
    }

    result = run_bucket_event_stats("rvol_up2_all", events, return_matrices, symbol_date_index)[0]
    assert result.n_events_scored == 2
    assert result.n_effective == 1
    assert result.mean_cluster_return == 0.20
