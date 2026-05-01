"""Run the broad-breadth buy-quality hardening study."""
from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from backtest.breadth_study.percentile_events import detect_upcross_events
from backtest.breadth_study.percentile_verifier import _build_signal_for_manifest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DAILY_BREADTH_PATH = PROJECT_ROOT / "data/breadth_study_1b/daily_breadth.csv"
MANIFEST_PATH = PROJECT_ROOT / "backtest/breadth_study/manifests/breadth_absolute_v1.json"

SAMPLE_START = "2021-02-01"
SAMPLE_END = "2026-04-28"

SIGNAL_COLUMNS = {
    ("S1", "active"): ("breadth_50_active", 0.25, 20),
    ("S1", "with_delisted_partial"): ("breadth_50", 0.25, 20),
    ("S2", "active"): ("breadth_20_active", 0.30, 60),
    ("S2", "with_delisted_partial"): ("breadth_20", 0.30, 60),
}

SIGNALS = ["S1", "S2"]
UNIVERSES = ["active", "with_delisted_partial"]
TARGETS = ["SPY", "QQQ", "SOXX"]
WINDOWS = [5, 20, 60, 120, 180]


def load_event_dates(signal: str, universe: str) -> list[pd.Timestamp]:
    """Return raw breadth upcross triggers after frozen cooldown de-duplication."""
    column, threshold, cooldown = SIGNAL_COLUMNS[(signal, universe)]
    daily = pd.read_csv(DAILY_BREADTH_PATH, parse_dates=["date"])
    daily = daily[
        (daily["date"] >= pd.Timestamp(SAMPLE_START))
        & (daily["date"] <= pd.Timestamp(SAMPLE_END))
    ].reset_index(drop=True)
    manifest = json.loads(MANIFEST_PATH.read_text())

    signal_series = _build_signal_for_manifest(manifest, daily, column)
    signal_series.index = pd.DatetimeIndex(daily["date"])
    events = detect_upcross_events(
        signal_series,
        threshold=threshold,
        cooldown_days=cooldown,
    )
    return [pd.Timestamp(ev["label"]) for ev in events]
