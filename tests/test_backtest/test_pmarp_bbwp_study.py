import pandas as pd

from backtest.research.pmarp_bbwp_study import (
    PMARPBBWPStudyConfig,
    build_cohorts_from_feature_frames,
    build_feature_frames,
    compare_event_groups,
    filter_events_by_date,
)


def test_build_cohorts_from_feature_frames_creates_recent_and_same_day_filters():
    config = PMARPBBWPStudyConfig(study_start_date="2024-01-01", recent_confirm_window=3)
    frame = pd.DataFrame(
        {
            "date": ["2024-01-01", "2024-01-02", "2024-01-03", "2024-01-04"],
            "pmarp_up2": [False, False, False, True],
            "bbwp_down98": [False, True, False, False],
            "bbwp_highturn": [False, False, True, False],
            "prior_downtrend": [False, True, True, True],
            "prior_uptrend": [False, False, False, False],
        }
    )

    cohorts = build_cohorts_from_feature_frames({"AAA": frame}, config)

    assert cohorts["bbwp_down98_after_downtrend"]["AAA"] == ["2024-01-02"]
    assert cohorts["bbwp_highturn_after_downtrend"]["AAA"] == ["2024-01-03"]
    assert cohorts["pmarp_up2_base"]["AAA"] == ["2024-01-04"]
    assert "AAA" not in cohorts.get("pmarp_up2_accept_down98_same_day", {})
    assert cohorts["pmarp_up2_accept_down98_recent3"]["AAA"] == ["2024-01-04"]
    assert cohorts["pmarp_up2_accept_highturn_recent3"]["AAA"] == ["2024-01-04"]


def test_filter_events_by_date():
    events = {"AAA": ["2024-01-01", "2024-02-01"], "BBB": ["2024-03-01"]}
    filtered = filter_events_by_date(events, start_date="2024-02-01", end_date="2024-03-01")
    assert filtered == {"AAA": ["2024-02-01"], "BBB": ["2024-03-01"]}


def test_compare_event_groups_runs_welch_test_on_cluster_means():
    accepted_events = {"AAA": ["2024-01-01", "2024-01-02"]}
    rejected_events = {"AAA": ["2024-01-03", "2024-01-04"]}
    return_matrices = {
        30: pd.DataFrame(
            {"AAA": [0.10, 0.12, 0.01, 0.02]},
            index=["2024-01-01", "2024-01-02", "2024-01-03", "2024-01-04"],
        )
    }

    results = compare_event_groups(
        label="test_filter",
        accepted_events=accepted_events,
        rejected_events=rejected_events,
        return_matrices=return_matrices,
        sample="Full",
    )
    result = results[0]
    assert result.accepted_n_events == 2
    assert result.rejected_n_events == 2
    assert result.accepted_mean_return > result.rejected_mean_return
    assert result.sample == "Full"


def test_build_feature_frames_keeps_prior_excess_values_aligned():
    config = PMARPBBWPStudyConfig(
        study_start_date="2024-01-01",
        trend_lookback_days=1,
        pmarp_lookback=2,
        bbwp_lookback=2,
        bbwp_period=2,
    )
    stock = pd.DataFrame(
        {
            "date": ["2024-01-01", "2024-01-02", "2024-01-03", "2024-01-04"],
            "open": [100, 101, 102, 103],
            "close": [100, 101, 99, 98],
        }
    )
    benchmark = pd.DataFrame(
        {
            "date": ["2024-01-01", "2024-01-02", "2024-01-03", "2024-01-04"],
            "open": [100, 100, 100, 100],
            "close": [100, 100, 100, 100],
        }
    )

    frames = build_feature_frames({"AAA": stock}, benchmark, config)
    frame = frames["AAA"]
    assert frame["prior_excess_20d"].notna().sum() >= 1
