from pathlib import Path

import pandas as pd

from backtest.pipeline.runner import PipelineRunner


def _write_spec(path: Path) -> Path:
    path.write_text(
        """
spec_id: "runner_unit"
benchmark: "SPY"
universe:
  market_cap_min_usd: 1000000000
  min_names: 2
factors:
  - name: "RS_Rating_B"
    params: {}
combo:
  method: "single"
portfolio:
  selection: "top_n"
  top_n: 2
  rebalance: "weekly"
  weighting: "equal"
execution:
  timing: "next_open"
period:
  start: "2024-06-03"
  train_end: "2024-09-13"
  test_end: "2024-10-31"
        """.strip(),
        encoding="utf-8",
    )
    return path


def test_compute_spec_hash_is_reproducible(tmp_path):
    spec_path = _write_spec(tmp_path / "spec.yaml")
    runner_a = PipelineRunner(spec_path, artifact_root=tmp_path / "reports_a")
    runner_b = PipelineRunner(spec_path, artifact_root=tmp_path / "reports_b")

    assert runner_a.compute_spec_hash() == runner_b.compute_spec_hash()


def test_slice_frame_after_date_exclusive_end_date_inclusive(tmp_path):
    spec_path = _write_spec(tmp_path / "spec.yaml")
    runner = PipelineRunner(spec_path, artifact_root=tmp_path / "reports")
    frame = pd.DataFrame(
        [{"AAA": 1.0}, {"AAA": 2.0}, {"AAA": 3.0}],
        index=["2024-09-13", "2024-09-20", "2024-09-27"],
    )

    sliced = runner._slice_frame(
        frame,
        after_date="2024-09-13",
        end_date="2024-09-20",
    )

    assert sliced.index.tolist() == ["2024-09-20"]
