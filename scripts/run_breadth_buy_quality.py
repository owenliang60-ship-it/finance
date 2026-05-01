"""Run the broad-breadth buy-quality hardening study."""
from __future__ import annotations

import json
import sqlite3
import sys
from argparse import ArgumentParser
from pathlib import Path

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backtest.breadth_study.buy_quality import (
    compute_better_than_random_pct_simple,
    compute_better_than_random_pct_stratified,
    distance_to_future_min,
    forward_percentile_rank,
    max_drawdown_after_entry,
)
from backtest.breadth_study.percentile_events import detect_upcross_events
from backtest.breadth_study.percentile_verifier import _build_signal_for_manifest

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
METRICS = ["rank_pct", "max_dd", "dist_to_min"]


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


def load_target_closes(targets: list[str]) -> dict[str, pd.Series]:
    """Read target closes from market.db in read-only mode, clipped to sample."""
    conn = sqlite3.connect(
        f"file:{PROJECT_ROOT / 'data/market.db'}?mode=ro",
        uri=True,
    )
    try:
        out: dict[str, pd.Series] = {}
        for target in targets:
            df = pd.read_sql(
                "SELECT date, close FROM daily_price "
                "WHERE symbol = ? AND date BETWEEN ? AND ? ORDER BY date",
                conn,
                params=(target, SAMPLE_START, SAMPLE_END),
                parse_dates=["date"],
            ).set_index("date")
            out[target] = pd.to_numeric(df["close"], errors="coerce")
        return out
    finally:
        conn.close()


def _empty_metric_row(
    signal: str,
    universe: str,
    event_date: pd.Timestamp,
    target: str,
    window: int,
) -> dict[str, object]:
    return {
        "signal": signal,
        "universe": universe,
        "event_date": event_date,
        "target": target,
        "window_days": window,
        "signal_close": float("nan"),
        "rank_pct": float("nan"),
        "max_dd": float("nan"),
        "dist_to_min": float("nan"),
    }


def run_buy_quality_pipeline(output_dir: Path) -> pd.DataFrame:
    """Write events.csv with every raw trigger x target x window row retained."""
    rows: list[dict[str, object]] = []
    target_closes = load_target_closes(TARGETS)

    for signal in SIGNALS:
        for universe in UNIVERSES:
            events = load_event_dates(signal=signal, universe=universe)
            for event_date in events:
                for target in TARGETS:
                    closes = target_closes[target]
                    if event_date not in closes.index:
                        for window in WINDOWS:
                            rows.append(
                                _empty_metric_row(
                                    signal, universe, event_date, target, window
                                )
                            )
                        continue

                    signal_idx = int(closes.index.get_loc(event_date))
                    signal_close = float(closes.iloc[signal_idx])
                    for window in WINDOWS:
                        rows.append({
                            "signal": signal,
                            "universe": universe,
                            "event_date": event_date,
                            "target": target,
                            "window_days": window,
                            "signal_close": signal_close,
                            "rank_pct": forward_percentile_rank(closes, signal_idx, window),
                            "max_dd": max_drawdown_after_entry(closes, signal_idx, window),
                            "dist_to_min": distance_to_future_min(closes, signal_idx, window),
                        })

    df = pd.DataFrame(rows)
    output_dir.mkdir(parents=True, exist_ok=True)
    df.to_csv(output_dir / "events.csv", index=False)
    return df


def compute_all_days_baseline(
    targets: list[str] | None = None,
    windows: list[int] | None = None,
) -> pd.DataFrame:
    """Compute the same three metrics for every eligible target trading day."""
    targets = targets or TARGETS
    windows = windows or WINDOWS
    target_closes = load_target_closes(targets)
    rows: list[dict[str, object]] = []
    for target, closes in target_closes.items():
        for window in windows:
            for idx in range(len(closes)):
                if idx + window >= len(closes):
                    break
                rows.append({
                    "target": target,
                    "window_days": window,
                    "date": closes.index[idx],
                    "rank_pct": forward_percentile_rank(closes, idx, window),
                    "max_dd": max_drawdown_after_entry(closes, idx, window),
                    "dist_to_min": distance_to_future_min(closes, idx, window),
                })
    return pd.DataFrame(rows)


def build_summary(
    events_df: pd.DataFrame,
    baseline_df: pd.DataFrame,
    n_iter: int = 10000,
) -> pd.DataFrame:
    """Summarize event metrics against simple and stratified random baselines."""
    rows: list[dict[str, object]] = []
    events_df = events_df.copy()
    events_df["event_date"] = pd.to_datetime(events_df["event_date"])
    baseline_df = baseline_df.copy()
    baseline_df["date"] = pd.to_datetime(baseline_df["date"])

    group_cols = ["signal", "universe", "target", "window_days"]
    for (signal, universe, target, window), group in events_df.groupby(group_cols):
        cooldown = SIGNAL_COLUMNS[(signal, universe)][2]
        baseline_cell = baseline_df[
            (baseline_df["target"] == target)
            & (baseline_df["window_days"] == window)
        ].copy()
        for metric in METRICS:
            metric_values = pd.to_numeric(group[metric], errors="coerce")
            valid_mask = metric_values.notna()
            event_values = metric_values[valid_mask]
            event_dates = group.loc[valid_mask, "event_date"]
            baseline_metric = baseline_cell[["date", metric]].dropna().copy()
            lower_is_better = metric != "max_dd"

            p_simple = compute_better_than_random_pct_simple(
                event_values,
                baseline_metric[metric],
                n_iter=n_iter,
                lower_is_better=lower_is_better,
            )
            event_with_dates = pd.DataFrame({
                "date": event_dates.to_numpy(),
                "metric_value": event_values.to_numpy(),
            })
            baseline_with_dates = baseline_metric.rename(
                columns={metric: "metric_value"}
            )
            p_stratified = compute_better_than_random_pct_stratified(
                event_with_dates,
                baseline_with_dates,
                cooldown=cooldown,
                n_iter=n_iter,
                lower_is_better=lower_is_better,
            )

            rows.append({
                "signal": signal,
                "universe": universe,
                "target": target,
                "window_days": window,
                "metric": metric,
                "n_events": int(len(event_values)),
                "event_median": float(event_values.median())
                if len(event_values) else float("nan"),
                "event_p25": float(event_values.quantile(0.25))
                if len(event_values) else float("nan"),
                "event_p75": float(event_values.quantile(0.75))
                if len(event_values) else float("nan"),
                "event_worst": float(event_values.max() if lower_is_better else event_values.min())
                if len(event_values) else float("nan"),
                "all_days_median": float(baseline_metric[metric].median())
                if not baseline_metric.empty else float("nan"),
                "p_simple": p_simple,
                "p_stratified": p_stratified,
            })
    return pd.DataFrame(rows)


def plot_event_vs_baseline_box(
    events_df: pd.DataFrame,
    baseline_df: pd.DataFrame,
    signal: str,
    universe: str,
    target: str,
    window: int,
    metric: str,
    output_path: Path,
) -> None:
    """Save one event-vs-baseline boxplot cell."""
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    event_vals = events_df[
        (events_df["signal"] == signal)
        & (events_df["universe"] == universe)
        & (events_df["target"] == target)
        & (events_df["window_days"] == window)
    ][metric].dropna()
    baseline_vals = baseline_df[
        (baseline_df["target"] == target)
        & (baseline_df["window_days"] == window)
    ][metric].dropna()

    output_path.parent.mkdir(parents=True, exist_ok=True)
    fig, ax = plt.subplots(figsize=(7, 5))
    ax.boxplot(
        [baseline_vals, event_vals],
        labels=["All trading days", f"{signal} events (n={len(event_vals)})"],
    )
    ax.set_title(f"{signal} ({universe}) | {target} | {window}d | {metric}")
    ax.set_ylabel(metric)
    fig.tight_layout()
    fig.savefig(output_path, dpi=120)
    plt.close(fig)


def plot_all_charts(
    events_df: pd.DataFrame,
    baseline_df: pd.DataFrame,
    output_dir: Path,
) -> int:
    """Write all frozen event-vs-baseline boxplots."""
    chart_dir = output_dir / "charts"
    count = 0
    for signal in SIGNALS:
        for universe in UNIVERSES:
            for target in TARGETS:
                for window in WINDOWS:
                    for metric in METRICS:
                        path = (
                            chart_dir
                            / f"{signal}_{universe}_{target}_{window}d_{metric}.png"
                        )
                        plot_event_vs_baseline_box(
                            events_df,
                            baseline_df,
                            signal,
                            universe,
                            target,
                            window,
                            metric,
                            path,
                        )
                        count += 1
    return count


def run_full_pipeline(output_dir: Path, n_iter: int = 10000) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    events = run_buy_quality_pipeline(output_dir)
    baseline = compute_all_days_baseline(TARGETS, WINDOWS)
    baseline.to_csv(output_dir / "all_days_baseline.csv", index=False)
    summary = build_summary(events, baseline, n_iter=n_iter)
    summary.to_csv(output_dir / "summary.csv", index=False)
    plot_all_charts(events, baseline, output_dir)
    return events, baseline, summary


def main() -> None:
    parser = ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output",
        type=Path,
        default=PROJECT_ROOT / "data/breadth_buy_quality",
    )
    parser.add_argument("--iterations", type=int, default=10000)
    args = parser.parse_args()

    run_full_pipeline(args.output, n_iter=args.iterations)


if __name__ == "__main__":
    main()
