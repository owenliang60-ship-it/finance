import pandas as pd

from backtest.research.pmarp_signal_stats import (
    PMARPSignalStatsConfig,
    build_pmarp_feature_frames,
    build_pmarp_signal_events,
    run_signal_event_stats,
)


def test_build_pmarp_feature_frames_marks_down98_cross():
    config = PMARPSignalStatsConfig(ema_period=2, pmarp_lookback=2)
    stock = pd.DataFrame(
        {
            "date": [
                "2024-01-01",
                "2024-01-02",
                "2024-01-03",
                "2024-01-04",
                "2024-01-05",
                "2024-01-06",
            ],
            "open": [10, 11, 12, 13, 12, 11],
            "close": [10, 11, 12, 13, 12, 11],
        }
    )

    frames = build_pmarp_feature_frames({"AAA": stock}, config)
    frame = frames["AAA"]
    assert "pmarp" in frame.columns
    assert "pmarp_down98" in frame.columns


def test_build_pmarp_signal_events_collects_signal_dates():
    config = PMARPSignalStatsConfig(study_start_date="2024-01-01")
    frame = pd.DataFrame(
        {
            "date": ["2024-01-01", "2024-01-02", "2024-01-03"],
            "pmarp_down98": [False, True, True],
        }
    )

    events = build_pmarp_signal_events({"AAA": frame}, config)
    assert events["pmarp_down98"]["AAA"] == ["2024-01-02", "2024-01-03"]


def test_run_signal_event_stats_computes_positive_rate_and_mean():
    events = {"AAA": ["2024-01-01", "2024-01-02", "2024-01-03"]}
    return_matrices = {
        7: pd.DataFrame(
            {"AAA": [-0.02, 0.01, 0.03]},
            index=["2024-01-01", "2024-01-02", "2024-01-03"],
        )
    }

    result = run_signal_event_stats("pmarp_down98", events, return_matrices)[0]
    assert result.n_events == 3
    assert result.positive_rate > 0
