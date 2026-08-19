from __future__ import annotations

from pathlib import Path

import pandas as pd

from backtest.event_study.protocol import EventStudyConfig, ReportSplitConfig
from backtest.event_study.runner import EventStudyRunner
from backtest.event_study.studies import RVOLStudyAdapter, RVOLStudyParams
from backtest.event_study.universe import EventUniverseAudit


def test_read_symbols_accepts_bare_list_and_dict(tmp_path):
    from backtest.event_study.runner import _read_symbols

    bare = tmp_path / "bare.json"
    wrapped = tmp_path / "wrapped.json"
    bare.write_text('["aapl", "MSFT", "AAPL"]', encoding="utf-8")
    wrapped.write_text('{"symbols": ["nvda", "AAPL"]}', encoding="utf-8")

    assert _read_symbols(bare) == ["AAPL", "MSFT"]
    assert _read_symbols(wrapped) == ["AAPL", "NVDA"]


def _frame(rows: list[tuple[str, float, float, float]]) -> pd.DataFrame:
    return pd.DataFrame(rows, columns=["date", "open", "close", "volume"])


class _FakeAdapter:
    def __init__(self, symbols=None, universe=None):
        self._symbols = symbols
        self._universe = universe

    def load_all(self):
        if self._symbols == ["SPY"]:
            return {
                "SPY": _frame(
                    [
                        ("2024-01-01", 20, 20, 100),
                        ("2024-01-02", 20, 20, 100),
                        ("2024-01-03", 20, 20, 100),
                        ("2024-01-04", 20, 21, 100),
                        ("2024-01-05", 21, 22, 100),
                        ("2024-01-08", 22, 23, 100),
                        ("2024-01-09", 23, 24, 100),
                    ]
                )
            }
        return {
            "AAPL": _frame(
                [
                    ("2024-01-01", 10, 10, 10),
                    ("2024-01-02", 10, 11, 11),
                    ("2024-01-03", 11, 10, 9),
                    ("2024-01-04", 10, 10, 10),
                    ("2024-01-05", 10, 10, 30),
                    ("2024-01-08", 10, 11, 12),
                    ("2024-01-09", 11, 12, 11),
                ]
            )
        }

    def get_trading_dates(self):
        return [
            "2024-01-01",
            "2024-01-02",
            "2024-01-03",
            "2024-01-04",
            "2024-01-05",
            "2024-01-08",
            "2024-01-09",
        ]


class _FakeGate:
    def __init__(self, config, candidate_symbols=None, market_db_path=None):
        self._candidate_symbols = candidate_symbols or ["AAPL"]

    def build_eligibility_matrix(self, computation_dates):
        df = pd.DataFrame(True, index=computation_dates, columns=self._candidate_symbols)
        df.index.name = "date"
        return df

    def build_universe_audit(self, eligibility, loaded_symbol_count=None, json_universe_count=None):
        by_date = pd.DataFrame(
            {
                "date": list(eligibility.index),
                "year": [date[:4] for date in eligibility.index],
                "candidate_count": [len(eligibility.columns)] * len(eligibility.index),
                "eligible_count": eligibility.sum(axis=1).astype(int).tolist(),
            }
        )
        by_year = pd.DataFrame(
            {
                "year": ["2024"],
                "n_dates": [len(eligibility.index)],
                "first_date": [eligibility.index.min()],
                "last_date": [eligibility.index.max()],
                "candidate_count": [len(eligibility.columns)],
                "eligible_count_min": [int(eligibility.sum(axis=1).min())],
                "eligible_count_median": [float(eligibility.sum(axis=1).median())],
                "eligible_count_max": [int(eligibility.sum(axis=1).max())],
                "eligible_count_mean": [float(eligibility.sum(axis=1).mean())],
            }
        )
        return EventUniverseAudit(
            by_date=by_date,
            by_year=by_year,
            summary={
                "candidate_count": len(eligibility.columns),
                "json_universe_count": json_universe_count or len(eligibility.columns),
                "loaded_symbol_count": loaded_symbol_count or len(eligibility.columns),
                "eligible_count_median": float(eligibility.sum(axis=1).median()),
                "historical_market_cap_min_date": "2024-01-01",
                "historical_market_cap_max_date": "2024-01-09",
            },
        )


def test_runner_produces_fixed_artifacts(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr("backtest.event_study.runner.USStocksAdapter", _FakeAdapter)
    monkeypatch.setattr("backtest.event_study.runner.EventUniverseGate", _FakeGate)

    runner = EventStudyRunner(
        config=EventStudyConfig(
            study_name="rvol_up2",
            study_start_date="2024-01-01",
            study_end_date="2024-01-09",
            report_split=ReportSplitConfig(oos_start_date="2024-01-08"),
        ),
        study=RVOLStudyAdapter(RVOLStudyParams(rvol_lookback=3, rvol_threshold=1.0)),
    )

    outcome = runner.run(output_dir=tmp_path)

    assert outcome.status == "completed"
    assert outcome.summary_rows
    assert set(outcome.artifact_paths.keys()) == {
        "summary.csv",
        "event_level.csv",
        "universe_audit.csv",
        "report.md",
    }
    for path in outcome.artifact_paths.values():
        assert Path(path).exists()
    assert {row["window"] for row in outcome.summary_rows} >= {"Full", "IS"}

    event_level = pd.read_csv(outcome.artifact_paths["event_level.csv"])
    universe_audit = pd.read_csv(outcome.artifact_paths["universe_audit.csv"])
    assert "window" in event_level.columns
    assert "loaded_symbol_count" in set(
        universe_audit.loc[universe_audit["audit_type"] == "summary", "metric"]
    )
