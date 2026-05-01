"""Integration smoke test for breadth pctile verification CLI (Task 13).

Uses synthetic data for determinism — no market.db dependency. Verifies
pipeline shape, schema completeness, and byte-identical reproducibility
across two runs with the same inputs.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from backtest.breadth_study.percentile_manifest import (
    load_manifest,
    manifest_sha256,
)
import sys

# Make scripts/ importable so we can call run_pipeline directly
PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))
from run_breadth_pctile_verification import _read_target_prices, run_pipeline  # noqa: E402


MANIFEST_PATH = PROJECT_ROOT / "backtest/breadth_study/manifests/breadth_pctile_v1.json"


def _build_synthetic(n_days=1500, seed=11):
    rng = np.random.default_rng(seed)
    dates = pd.bdate_range("2021-02-01", periods=n_days)
    z20 = np.cumsum(rng.normal(0, 0.05, n_days))
    z50 = np.cumsum(rng.normal(0, 0.04, n_days))
    breadth_20 = 1.0 / (1.0 + np.exp(-z20))
    breadth_50 = 1.0 / (1.0 + np.exp(-z50))
    daily_breadth = pd.DataFrame({
        "date": dates,
        "breadth_20": breadth_20,
        "breadth_50": breadth_50,
    })

    targets = ["SPY", "QQQ", "SOXX", "IWM", "XLK"]
    horizons = [5, 10, 20, 60]
    target_prices_dict = {}
    target_returns_data = {"date": dates}
    for tgt in targets:
        rets = rng.normal(0.0003, 0.012, n_days)
        closes = 100.0 * np.cumprod(1 + rets)
        opens = np.concatenate([[100.0], closes[:-1]])
        target_prices_dict[tgt] = pd.DataFrame({
            "date": dates, "open": opens, "close": closes,
        })
        for h in horizons:
            shifted_close = pd.Series(closes).shift(-h)
            entry_open = pd.Series(opens).shift(-1)
            target_returns_data[f"{tgt}_fwd_{h}d"] = (
                shifted_close / entry_open - 1
            ).to_numpy()
    target_returns = pd.DataFrame(target_returns_data)
    return daily_breadth, target_prices_dict, target_returns


@pytest.fixture(scope="module")
def manifest():
    return load_manifest(MANIFEST_PATH)


@pytest.fixture(scope="module")
def manifest_sha():
    return manifest_sha256(MANIFEST_PATH)


def _run(tmp_path, manifest, manifest_sha):
    daily_breadth, target_prices_dict, target_returns = _build_synthetic()
    output_dir = tmp_path / "out"
    report_path = tmp_path / "out" / "report.md"
    paths = run_pipeline(
        manifest=manifest,
        manifest_sha=manifest_sha,
        daily_breadth=daily_breadth,
        target_prices_dict=target_prices_dict,
        target_returns=target_returns,
        output_dir=output_dir,
        report_path=report_path,
        git_commit="testcommit",
        cli_command="pytest synthetic",
    )
    return paths


def test_pipeline_writes_three_csvs_and_a_report(tmp_path, manifest, manifest_sha):
    paths = _run(tmp_path, manifest, manifest_sha)
    for key in ("param_summary", "param_summary_qqq10d", "verification_table", "report"):
        assert paths[key].exists(), f"missing artifact: {key}"


def test_param_summary_csv_has_12_rows(tmp_path, manifest, manifest_sha):
    paths = _run(tmp_path, manifest, manifest_sha)
    df = pd.read_csv(paths["param_summary"])
    assert len(df) == 12
    assert (df["primary_cell"] == "SPY_10d").all()


def test_sensitivity_csv_has_12_rows_qqq_cell(tmp_path, manifest, manifest_sha):
    paths = _run(tmp_path, manifest, manifest_sha)
    df = pd.read_csv(paths["param_summary_qqq10d"])
    assert len(df) == 12
    assert (df["primary_cell"] == "QQQ_10d").all()


def test_verification_table_has_240_rows(tmp_path, manifest, manifest_sha):
    paths = _run(tmp_path, manifest, manifest_sha)
    df = pd.read_csv(paths["verification_table"])
    assert len(df) == 240
    assert {"ma_window", "threshold", "event_type", "target", "horizon"}.issubset(
        set(df.columns)
    )


def test_report_header_contains_manifest_sha(tmp_path, manifest, manifest_sha):
    paths = _run(tmp_path, manifest, manifest_sha)
    body = paths["report"].read_text()
    assert manifest_sha in body
    assert manifest["frozen_at"] in body


def test_report_effective_sample_years_present_and_positive(
    tmp_path, manifest, manifest_sha
):
    paths = _run(tmp_path, manifest, manifest_sha)
    df = pd.read_csv(paths["param_summary"])
    assert df["effective_years"].notna().all()
    assert (df["effective_years"] > 0).all()


def test_critical_columns_no_nan_explosion(tmp_path, manifest, manifest_sha):
    paths = _run(tmp_path, manifest, manifest_sha)
    df = pd.read_csv(paths["param_summary"])
    for col in ("event_n_short", "event_n_long", "effective_years"):
        assert df[col].notna().all(), f"{col} has NaN"


def test_read_target_prices_falls_back_when_market_db_missing_symbol(tmp_path, monkeypatch):
    db_path = tmp_path / "market.db"
    import sqlite3

    conn = sqlite3.connect(db_path)
    conn.execute("CREATE TABLE daily_price (symbol TEXT, date TEXT, open REAL, close REAL)")
    conn.execute(
        "INSERT INTO daily_price VALUES ('SPY', '2024-01-02', 100.0, 101.0)"
    )
    conn.commit()
    conn.close()

    fallback = pd.DataFrame(
        {
            "date": pd.to_datetime(["2024-01-02", "2024-01-03"]),
            "open": [200.0, 201.0],
            "close": [202.0, 203.0],
        }
    )

    import run_breadth_pctile_verification as cli

    monkeypatch.setattr(cli, "_fetch_yfinance_target_prices", lambda sym, start: fallback)
    monkeypatch.setattr(cli.time, "sleep", lambda _: None)

    out = _read_target_prices(db_path, ["SPY", "IWM"], "2024-01-01")

    assert len(out["SPY"]) == 1
    assert len(out["IWM"]) == 2
    assert out["IWM"]["close"].iloc[-1] == 203.0


def test_byte_identical_reproducibility(tmp_path, manifest, manifest_sha):
    out1 = tmp_path / "run1"
    out2 = tmp_path / "run2"
    daily_breadth, target_prices_dict, target_returns = _build_synthetic()
    paths1 = run_pipeline(
        manifest=manifest, manifest_sha=manifest_sha,
        daily_breadth=daily_breadth, target_prices_dict=target_prices_dict,
        target_returns=target_returns, output_dir=out1, report_path=out1 / "r.md",
        git_commit="testcommit", cli_command="pytest",
    )
    daily_breadth2, target_prices_dict2, target_returns2 = _build_synthetic()
    paths2 = run_pipeline(
        manifest=manifest, manifest_sha=manifest_sha,
        daily_breadth=daily_breadth2, target_prices_dict=target_prices_dict2,
        target_returns=target_returns2, output_dir=out2, report_path=out2 / "r.md",
        git_commit="testcommit", cli_command="pytest",
    )
    assert (
        paths1["param_summary"].read_bytes() == paths2["param_summary"].read_bytes()
    )
    assert (
        paths1["param_summary_qqq10d"].read_bytes()
        == paths2["param_summary_qqq10d"].read_bytes()
    )
    assert (
        paths1["verification_table"].read_bytes()
        == paths2["verification_table"].read_bytes()
    )
    assert paths1["report"].read_bytes() == paths2["report"].read_bytes()
