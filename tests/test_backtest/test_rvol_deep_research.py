import pandas as pd

from backtest.research.rvol_deep_research import (
    build_pmarp_rvol_lift_cohorts,
    build_strong_state_rvol_cohorts,
    comparison_pairs_from_cohorts,
    run_conditional_lift_comparisons,
)
from backtest.research.rvol_signal_stats import RVOLSignalStatsConfig
from backtest.research.rvol_signal_stats import (
    _close_location_bucket,
    _close_location_series,
    _rolling_recent_signal,
)


def test_close_location_bucket_boundaries():
    assert _close_location_bucket(0.20) == "near_low"
    assert _close_location_bucket(0.25) == "middle"
    assert _close_location_bucket(0.75) == "middle"
    assert _close_location_bucket(0.80) == "near_high"
    assert _close_location_bucket(float("nan")) is None


def test_close_location_series_returns_nan_when_high_equals_low():
    frame = pd.DataFrame(
        {
            "high": [110.0, 100.0],
            "low": [100.0, 100.0],
            "close": [108.0, 100.0],
        }
    )

    location = _close_location_series(frame)

    assert location.iloc[0] == 0.8
    assert pd.isna(location.iloc[1])


def test_rolling_recent_signal_uses_past_and_current_only():
    signal = pd.Series([True, False, False, False, True, False])

    recent_3d = _rolling_recent_signal(signal, window=3)
    recent_5d = _rolling_recent_signal(signal, window=5)

    assert recent_3d.tolist() == [True, True, True, False, True, True]
    assert recent_5d.tolist() == [True, True, True, True, True, True]


def test_build_pmarp_rvol_lift_cohorts_splits_accepted_and_rejected():
    config = RVOLSignalStatsConfig(study_start_date="2024-01-01")
    frame = pd.DataFrame(
        {
            "date": [
                "2024-01-01",
                "2024-01-02",
                "2024-01-03",
                "2024-01-04",
                "2024-01-05",
                "2024-01-06",
            ],
            "pmarp_up2": [False, False, True, False, True, True],
            "rvol_up2": [False, True, False, False, True, False],
            "rvol_recent_3d": [False, True, True, True, True, True],
            "rvol_recent_5d": [False, True, True, True, True, True],
        }
    )

    cohorts = build_pmarp_rvol_lift_cohorts({"AAA": frame}, config)

    assert cohorts["pmarp_up2_base"]["AAA"] == [
        "2024-01-03",
        "2024-01-05",
        "2024-01-06",
    ]
    assert cohorts["pmarp_up2_reject_rvol_same_day"]["AAA"] == [
        "2024-01-03",
        "2024-01-06",
    ]
    assert cohorts["pmarp_up2_accept_rvol_same_day"]["AAA"] == ["2024-01-05"]
    assert cohorts["pmarp_up2_accept_rvol_recent3"]["AAA"] == [
        "2024-01-03",
        "2024-01-05",
        "2024-01-06",
    ]


def test_build_strong_state_rvol_cohorts_uses_price_structure():
    config = RVOLSignalStatsConfig(study_start_date="2024-01-01")
    frame = pd.DataFrame(
        {
            "date": ["2024-01-01", "2024-01-02", "2024-01-03", "2024-01-04"],
            "rvol_up2": [True, True, True, True],
            "pmarp_bucket": ["pmarp_gte60", "pmarp_gte60", "pmarp_lt30", "pmarp_lt30"],
            "event_day_sign": ["sign_pos", "sign_flat", "sign_neg", "sign_pos"],
            "close_location_bucket": ["near_high", "near_high", "near_low", "near_low"],
        }
    )

    cohorts = build_strong_state_rvol_cohorts({"AAA": frame}, config)

    assert cohorts["rvol_up2_pmarp_gte60"]["AAA"] == ["2024-01-01", "2024-01-02"]
    assert cohorts["rvol_up2_pmarp_gte60_sign_pos"]["AAA"] == ["2024-01-01"]
    assert cohorts["rvol_up2_pmarp_gte60_close_near_high"]["AAA"] == [
        "2024-01-01",
        "2024-01-02",
    ]
    assert cohorts["rvol_up2_pmarp_gte60_sign_pos_close_near_high"]["AAA"] == [
        "2024-01-01"
    ]
    assert cohorts["rvol_up2_pmarp_lt30_sign_neg"]["AAA"] == ["2024-01-03"]
    assert cohorts["rvol_up2_pmarp_lt30_close_near_low"]["AAA"] == [
        "2024-01-03",
        "2024-01-04",
    ]


def test_run_conditional_lift_comparisons_compares_accepted_vs_rejected():
    cohorts = {
        "pmarp_up2_accept_rvol_recent3": {"AAA": ["2024-01-01"]},
        "pmarp_up2_reject_rvol_recent3": {"BBB": ["2024-01-01"]},
    }
    pairs = comparison_pairs_from_cohorts(cohorts)
    return_matrices = {
        1: pd.DataFrame(
            {"AAA": [0.10], "BBB": [0.02]},
            index=["2024-01-01"],
        )
    }
    symbol_date_index = {
        "AAA": {"2024-01-01": 0},
        "BBB": {"2024-01-01": 0},
    }

    result = run_conditional_lift_comparisons(
        {"recent3": pairs["pmarp_lift_rvol_recent3"]},
        return_matrices,
        symbol_date_index,
    )[0]

    assert result.accepted_events_scored == 1
    assert result.rejected_events_scored == 1
    assert result.accepted_mean == 0.10
    assert result.rejected_mean == 0.02
    assert result.mean_diff == 0.08
