from backtest.research.rvol_event_explainers import summarize_event_explainers


def test_summarize_event_explainers_reports_coverage_and_spikes():
    cohorts = {
        "test": {
            "AAA": ["2024-01-02", "2025-12-15"],
            "BBB": ["2025-12-15"],
        }
    }
    earnings = {"AAA": ["2024-01-03"], "BBB": ["2025-12-20"]}
    social = {
        ("AAA", "2025-12-15"): 2.5,
        ("BBB", "2025-12-15"): 0.5,
    }

    result = summarize_event_explainers(
        cohorts,
        earnings_dates=earnings,
        social_scores=social,
        social_start_date="2025-12-13",
    )[0]

    assert result.events_total == 3
    assert result.events_with_earnings_info == 3
    assert result.events_near_earnings == 1
    assert result.events_with_social_info == 2
    assert result.events_social_spike == 1
    assert result.events_after_social_start == 2
    assert result.events_with_social_info_after_social_start == 2
    assert result.earnings_status == "usable"
    assert result.social_status == "usable_post_start"


def test_summarize_event_explainers_marks_low_social_coverage_exploratory():
    cohorts = {"test": {"AAA": ["2025-12-15"], "BBB": ["2025-12-16"]}}
    social = {("AAA", "2025-12-15"): 2.5}

    result = summarize_event_explainers(
        cohorts,
        social_scores=social,
        social_start_date="2025-12-13",
    )[0]

    assert result.social_post_start_coverage == 0.5
    assert result.social_status == "exploratory"
