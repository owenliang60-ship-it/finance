import pandas as pd

from backtest.research.event_path_diagnostics import run_tail_diagnostics


def test_run_tail_diagnostics_uses_forward_high_low_for_mfe_mae():
    price = pd.DataFrame(
        {
            "date": ["2024-01-01", "2024-01-02", "2024-01-03", "2024-01-04"],
            "close": [100.0, 96.0, 105.0, 104.0],
            "high": [100.0, 102.0, 110.0, 106.0],
            "low": [100.0, 95.0, 99.0, 101.0],
        }
    )
    cohorts = {"test": {"AAA": ["2024-01-01"]}}

    result = run_tail_diagnostics(cohorts, {"AAA": price}, horizons=[3])[0]

    assert result.n_events == 1
    assert result.n_path_scored == 1
    assert round(result.mean, 6) == 0.04
    assert round(result.mfe_mean, 6) == 0.10
    assert round(result.mae_mean, 6) == -0.05
    assert result.mfe_to_mae_ratio == 2.0


def test_run_tail_diagnostics_skips_events_without_high_low():
    price = pd.DataFrame(
        {
            "date": ["2024-01-01", "2024-01-02"],
            "close": [100.0, 101.0],
        }
    )
    cohorts = {"test": {"AAA": ["2024-01-01"]}}

    result = run_tail_diagnostics(cohorts, {"AAA": price}, horizons=[1])[0]

    assert result.n_events == 1
    assert result.n_path_scored == 0
    assert result.mean == 0.0
