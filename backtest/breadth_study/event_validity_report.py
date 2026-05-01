"""Markdown report for event-validity statistics."""
from __future__ import annotations

from typing import Any, Dict, List, Optional

import pandas as pd


def _render_top_summary(summary: pd.DataFrame, n: int = 12) -> str:
    cols = [
        "ma_window", "threshold", "event_type", "horizon",
        "event_n_min", "avg_mean_lift_pp", "min_mean_lift_pp",
        "avg_hit_lift_pp", "min_perm_p", "max_perm_p",
        "positive_mean_targets_count", "positive_mean_targets",
        "positive_hit_targets_count", "positive_hit_targets",
        "perm_sig_targets_count", "perm_sig_targets",
        "bootstrap_positive_targets_count", "bootstrap_positive_targets",
        "validity_score",
    ]
    cols = [c for c in cols if c in summary.columns]
    return summary.head(n)[cols].to_markdown(index=False, floatfmt=".4f")


def _render_focus_cells(table: pd.DataFrame, ma: int, threshold: float) -> str:
    focus = table[
        (table["ma_window"] == ma)
        & (table["threshold"].round(6) == round(float(threshold), 6))
    ].copy()
    if focus.empty:
        return "_No matching cells._"
    cols = [
        "event_type", "horizon", "target", "event_n",
        "event_mean_pct", "baseline_mean_pct", "mean_lift_pp",
        "event_hit_pct", "baseline_hit_pct", "hit_lift_pp",
        "perm_p", "bootstrap_mean_lift_ci_low_pp",
        "bootstrap_mean_lift_ci_high_pp", "bootstrap_share_nonpositive",
    ]
    cols = [c for c in cols if c in focus.columns]
    return focus.sort_values(["horizon", "target"])[cols].to_markdown(
        index=False, floatfmt=".4f"
    )


def _verdict(summary: pd.DataFrame) -> str:
    if summary.empty:
        return "NO_DATA"
    top = summary.iloc[0]
    if (
        int(top["perm_sig_targets_count"]) >= 1
        and int(top["positive_mean_targets_count"]) >= 2
    ):
        return "PROMISING_BUT_NEEDS_REPLICATION"
    if int(top["positive_mean_targets_count"]) >= 2:
        return "DIRECTIONAL_HINT_ONLY"
    return "NO_EVENT_EDGE"


def render_event_validity_report(
    *,
    manifest: Dict[str, Any],
    manifest_sha256: str,
    table: pd.DataFrame,
    summary: pd.DataFrame,
    universe_label: str = "$1B+ PIT, with_delisted_partial overlay",
    git_commit: Optional[str] = None,
    cli_command: Optional[str] = None,
) -> str:
    verdict = _verdict(summary)
    first_valid = table["first_valid_date"].min()
    last_date = table["last_date"].max()
    effective_years = float(table["effective_years"].min())
    p_max = float(manifest["hurdle_thresholds"]["h5_permutation_p_max"])

    parts: List[str] = []
    parts.append(f"# Breadth Event-Validity Report — {manifest['version']}\n")
    parts.append(f"**Manifest version**: {manifest['version']}")
    parts.append(f"**Manifest SHA256**: `{manifest_sha256}`")
    parts.append(f"**Frozen at**: {manifest['frozen_at']}")
    parts.append(f"**Signal mode**: {manifest.get('signal_mode', 'percentile')}")
    parts.append(f"**Data sample**: {manifest['from_date']} → {last_date}")
    parts.append(
        f"**Effective sample**: {first_valid} → {last_date} "
        f"({effective_years:.2f} effective years)"
    )
    parts.append(f"**Universe**: {universe_label}")
    parts.append(f"**Targets**: {', '.join(manifest['targets'])}")
    parts.append(
        f"**Question**: after an upcross event, are forward returns better than "
        f"same-window non-event dates? This report does not compare against "
        f"100% buy-and-hold.\n"
    )

    parts.append("## Verdict\n")
    parts.append(f"- **Verdict**: **{verdict}**")
    parts.append(f"- **Permutation threshold**: p < {p_max:g}")
    parts.append(
        "- **Reading**: mean_lift_pp = event average forward return minus "
        "non-event average; hit_lift_pp = event win-rate minus non-event win-rate; "
        "bootstrap_positive means the lower confidence bound is above zero.\n"
    )

    parts.append("## Top Event-Validity Candidates\n")
    parts.append(_render_top_summary(summary))
    parts.append("")

    parts.append("## Focus: MA20 Upcross 30%\n")
    parts.append(_render_focus_cells(table, ma=20, threshold=0.30))
    parts.append("")

    parts.append("## Full Summary\n")
    parts.append(summary.to_markdown(index=False, floatfmt=".4f"))
    parts.append("")

    parts.append("## Full Cell Table\n")
    parts.append("<details><summary>Click to expand</summary>\n")
    parts.append(table.to_markdown(index=False, floatfmt=".4f"))
    parts.append("\n</details>\n")

    parts.append("## Reproduction\n")
    parts.append(f"- Manifest version: {manifest['version']}")
    parts.append(f"- Manifest SHA256: `{manifest_sha256}`")
    if git_commit:
        parts.append(f"- Git commit: `{git_commit}`")
    if cli_command:
        parts.append(f"- CLI command: `{cli_command}`")
    parts.append("")
    return "\n".join(parts)


def write_event_validity_report(path, **kwargs) -> str:
    body = render_event_validity_report(**kwargs)
    from pathlib import Path
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(body)
    return body
