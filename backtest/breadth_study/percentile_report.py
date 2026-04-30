"""Verification report generator (Task 12).

Renders a markdown report with param_summary as the primary verdict surface,
sensitivity comparison vs QQQ 10d, cluster details, bootstrap CIs,
permutation diagnostics, and a collapsible 240-row diagnostic table.

The report is deterministic given the same (manifest, summary, sensitivity,
table) inputs — no timestamps in body content beyond ``frozen_at`` from the
manifest.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

import pandas as pd

from backtest.breadth_study.percentile_clusters import detect_cluster_patterns


VERDICT_PROMOTE = "PROMOTE_TO_HARDENING"
VERDICT_ISOLATED = "ISOLATED_HINT_ONLY"
VERDICT_REJECT = "REJECT_NO_SIGNAL"


def _label(ma: int, K: float, et: str) -> str:
    return f"MA{int(ma)} K={K:.2f} {et}"


def _verdict_from_clusters(
    primary_clusters: List[Dict[str, Any]],
    sensitivity_clusters: List[Dict[str, Any]],
) -> str:
    """Top-level verdict.

    PROMOTE_TO_HARDENING: at least one cluster passes on BOTH primary
    (SPY 10d) and sensitivity (QQQ 10d) at the same (ma, event_type, threshold-set).
    ISOLATED_HINT_ONLY: any primary cluster or isolated pass exists.
    REJECT_NO_SIGNAL: neither.
    """

    def _key(c: Dict[str, Any]) -> tuple:
        return (c["ma_window"], c["event_type"], tuple(c["thresholds"]))

    primary_keys = {_key(c) for c in primary_clusters}
    sensitivity_keys = {_key(c) for c in sensitivity_clusters}
    if primary_keys & sensitivity_keys:
        return VERDICT_PROMOTE
    if primary_clusters or sensitivity_clusters:
        return VERDICT_ISOLATED
    return VERDICT_REJECT


def _isolated_passes(
    summary: pd.DataFrame, clusters: List[Dict[str, Any]], pass_threshold: int
) -> pd.DataFrame:
    """Rows that pass the threshold but are not part of any cluster."""
    in_cluster = set()
    for c in clusters:
        for K in c["thresholds"]:
            in_cluster.add((int(c["ma_window"]), float(K), c["event_type"]))

    def _is_isolated(row: pd.Series) -> bool:
        if row["passes_count_param"] < pass_threshold:
            return False
        return (
            int(row["ma_window"]),
            float(row["threshold"]),
            row["event_type"],
        ) not in in_cluster

    mask = summary.apply(_is_isolated, axis=1)
    return summary[mask].copy()


def _render_param_summary(df: pd.DataFrame) -> str:
    cols = [
        "ma_window", "threshold", "event_type", "primary_cell",
        "event_n_short", "event_n_long", "effective_years", "events_per_year",
        "event_hit", "baseline_hit", "hit_lift_pp",
        "perm_p", "perm_sampling_method", "perm_success_rate",
        "strategy_cagr_pp", "bnh_cagr_pp", "excess_cagr_pp",
        "excess_cagr_ci_low", "excess_cagr_ci_high", "excess_cagr_share_negative",
        "n_trades", "exposure_pct",
        "target_same_sign_count", "target_same_sign_targets",
        "short_horizon_same_sign_count", "short_horizon_same_sign_horizons",
        "long_horizon_diff",
        "h1_freq_pass", "h2_hit_pass", "h3_target_pass",
        "h4_short_horizon_pass", "h5_perm_pass", "h6_strategy_pass",
        "passes_count_param",
    ]
    cols = [c for c in cols if c in df.columns]
    return df[cols].to_markdown(index=False, floatfmt=".4f")


def _render_sensitivity_comparison(
    primary: pd.DataFrame, sensitivity: pd.DataFrame
) -> str:
    join_cols = ["ma_window", "threshold", "event_type"]
    p = primary[join_cols + ["passes_count_param"]].rename(
        columns={"passes_count_param": "passes_spy_10d"}
    )
    s = sensitivity[join_cols + ["passes_count_param"]].rename(
        columns={"passes_count_param": "passes_qqq_10d"}
    )
    merged = p.merge(s, on=join_cols, how="outer").sort_values(join_cols)

    def _verdict(row: pd.Series) -> str:
        spy_pass = row["passes_spy_10d"] >= 4
        qqq_pass = row["passes_qqq_10d"] >= 4
        if spy_pass and qqq_pass:
            return "BOTH (strong)"
        if qqq_pass and not spy_pass:
            return "QQQ-only (warn: AI bull leg fit?)"
        if spy_pass and not qqq_pass:
            return "SPY-only (broad but no QQQ specificity)"
        return "Neither"

    merged["verdict"] = merged.apply(_verdict, axis=1)
    return merged.to_markdown(index=False)


def _render_cluster_detail(
    clusters: List[Dict[str, Any]], summary: pd.DataFrame
) -> str:
    if not clusters:
        return "_No clusters found._\n"
    parts: List[str] = []
    for c in clusters:
        parts.append(
            f"### Cluster: MA{c['ma_window']} {c['event_type']} "
            f"thresholds={c['thresholds']}\n"
        )
        for K in c["thresholds"]:
            row = summary[
                (summary["ma_window"] == c["ma_window"])
                & (summary["threshold"] == K)
                & (summary["event_type"] == c["event_type"])
            ]
            if row.empty:
                continue
            r = row.iloc[0]
            parts.append(
                f"- **{_label(r['ma_window'], r['threshold'], r['event_type'])}**: "
                f"passes={r['passes_count_param']}, "
                f"effective_years={r['effective_years']:.2f}, "
                f"events_short={r['event_n_short']}, events_long={r['event_n_long']}, "
                f"hit_lift_pp={r['hit_lift_pp']:.2f}, "
                f"perm_p={r['perm_p']:.4f} ({r['perm_sampling_method']}), "
                f"excess_cagr_pp={r['excess_cagr_pp']:.2f} "
                f"[{r['excess_cagr_ci_low']:.2f}, {r['excess_cagr_ci_high']:.2f}], "
                f"share_negative={r['excess_cagr_share_negative']:.2f}, "
                f"target_same_sign={r['target_same_sign_count']}/5, "
                f"short_horizon_same_sign={r['short_horizon_same_sign_count']}/3, "
                f"long_horizon_diff={r['long_horizon_diff']:.4f}"
            )
        parts.append("")
    return "\n".join(parts)


def _render_isolated_passes(isolated: pd.DataFrame) -> str:
    if isolated.empty:
        return "_No isolated passes._\n"
    cols = [
        "ma_window", "threshold", "event_type",
        "passes_count_param", "hit_lift_pp", "perm_p", "excess_cagr_pp",
    ]
    cols = [c for c in cols if c in isolated.columns]
    body = isolated[cols].to_markdown(index=False, floatfmt=".4f")
    note = (
        "\n> ⚠️ **High fluke risk.** Isolated passes are NOT independent evidence; "
        "they cannot be promoted on their own.\n"
    )
    return body + note


def _render_bootstrap_ci(summary: pd.DataFrame, h6_threshold_pp: float) -> str:
    cols = [
        "ma_window", "threshold", "event_type",
        "excess_cagr_pp", "excess_cagr_ci_low", "excess_cagr_ci_high",
        "excess_cagr_share_negative", "h6_strategy_pass",
    ]
    cols = [c for c in cols if c in summary.columns]
    body = summary[cols].to_markdown(index=False, floatfmt=".4f")

    borderline = summary[
        (~summary["h6_strategy_pass"].astype(bool))
        & (summary["excess_cagr_ci_high"] >= h6_threshold_pp)
    ]
    if borderline.empty:
        note = "\n_No borderline params (H6 fail but CI upper >= threshold)._\n"
    else:
        labels = ", ".join(
            _label(r["ma_window"], r["threshold"], r["event_type"])
            for _, r in borderline.iterrows()
        )
        note = (
            f"\n**Borderline (H6 fail but CI includes "
            f"{h6_threshold_pp:g}pp threshold):** {labels}\n"
        )
    return body + note


def _render_perm_diagnostics(summary: pd.DataFrame, warn_threshold: float) -> str:
    cols = [
        "ma_window", "threshold", "event_type",
        "perm_p", "perm_sampling_method", "perm_success_rate",
    ]
    cols = [c for c in cols if c in summary.columns]
    df = summary[cols].copy()
    df["warn"] = df["perm_success_rate"] < warn_threshold
    return df.to_markdown(index=False, floatfmt=".4f")


def render_report(
    *,
    manifest: Dict[str, Any],
    manifest_sha256: str,
    primary_summary: pd.DataFrame,
    sensitivity_summary: pd.DataFrame,
    verification_table: pd.DataFrame,
    universe_label: str = "$1B+ PIT, with_delisted_partial overlay",
    git_commit: Optional[str] = None,
    cli_command: Optional[str] = None,
) -> str:
    """Return a complete markdown report string. No file IO."""
    pass_threshold = int(manifest["pass_threshold"])
    h6_threshold_pp = float(manifest["hurdle_thresholds"]["h6_strategy_excess_cagr_pp"])
    warn_threshold = float(manifest["permutation"]["warning_threshold"])

    primary_clusters = detect_cluster_patterns(primary_summary, pass_threshold)
    sensitivity_clusters = detect_cluster_patterns(sensitivity_summary, pass_threshold)
    verdict = _verdict_from_clusters(primary_clusters, sensitivity_clusters)

    primary_isolated = _isolated_passes(primary_summary, primary_clusters, pass_threshold)

    # Effective-sample header value (use min/max from primary so window is honest)
    first_valid = primary_summary["first_valid_date"].min()
    last_date = primary_summary["last_date"].max()
    effective_years = float(primary_summary["effective_years"].iloc[0])

    primary_target = manifest["primary_target"]
    primary_horizon = manifest["primary_horizon"]
    sensitivity_target = manifest["sensitivity_target"]
    sensitivity_horizon = manifest["sensitivity_horizon"]
    one_way_bps = manifest["strategy_costs"]["one_way_bps"]
    round_trip_bps = manifest["strategy_costs"]["round_trip_bps"]

    parts: List[str] = []
    parts.append(f"# Breadth Percentile Upcross Verification — {manifest['version']}\n")
    parts.append(f"**Manifest version**: {manifest['version']}")
    parts.append(f"**Manifest SHA256**: `{manifest_sha256}`")
    parts.append(f"**Frozen at**: {manifest['frozen_at']}")
    parts.append(f"**Data sample**: {manifest['from_date']} → {last_date}")
    parts.append(
        f"**Effective sample**: {first_valid} → {last_date} "
        f"({effective_years:.2f} effective years)"
    )
    parts.append(f"**Universe**: {universe_label}")
    parts.append(f"**Primary cell (主)**: {primary_target} {primary_horizon}d")
    parts.append(f"**Sensitivity cell (副)**: {sensitivity_target} {sensitivity_horizon}d")
    parts.append(
        f"**Strategy costs**: {one_way_bps}bp one-way ({round_trip_bps}bp roundtrip)\n"
    )

    parts.append("## Top-Level Verdict\n")
    parts.append(f"- **Cluster passes (主表)**: {len(primary_clusters)}")
    parts.append(f"- **Cluster passes (副表 QQQ)**: {len(sensitivity_clusters)}")
    both_count = sum(
        1 for c in primary_clusters
        if (c["ma_window"], c["event_type"], tuple(c["thresholds"]))
        in {(s["ma_window"], s["event_type"], tuple(s["thresholds"]))
            for s in sensitivity_clusters}
    )
    parts.append(f"- **SPY+QQQ 双通过 cluster**: {both_count} ← 最强证据")
    parts.append(f"- **Isolated passes (主表)**: {len(primary_isolated)}")
    parts.append(f"- **Verdict**: **{verdict}**\n")

    parts.append("## Sensitivity Comparison (主 vs 副)\n")
    parts.append(_render_sensitivity_comparison(primary_summary, sensitivity_summary))
    parts.append("")
    parts.append(
        "Reading: 双过 = strong evidence; 仅 QQQ 过 = AI-bull-leg fit risk; "
        "仅 SPY 过 = breadth has broad effect but no QQQ specificity; 都不过 = "
        f"{effective_years:.2f}y sample cannot validate.\n"
    )

    parts.append(f"## Param Summary — Primary ({primary_target} {primary_horizon}d)\n")
    parts.append(_render_param_summary(primary_summary))
    parts.append("")

    parts.append(
        f"## Param Summary — Sensitivity ({sensitivity_target} {sensitivity_horizon}d)\n"
    )
    parts.append(_render_param_summary(sensitivity_summary))
    parts.append("")

    parts.append("## Cluster Pattern Detail — Primary\n")
    parts.append(_render_cluster_detail(primary_clusters, primary_summary))

    parts.append("## Cluster Pattern Detail — Sensitivity\n")
    parts.append(_render_cluster_detail(sensitivity_clusters, sensitivity_summary))

    parts.append("## Bootstrap CI for Excess CAGR (primary)\n")
    parts.append(_render_bootstrap_ci(primary_summary, h6_threshold_pp))
    parts.append("")

    parts.append("## Permutation Diagnostics (primary)\n")
    parts.append(_render_perm_diagnostics(primary_summary, warn_threshold))
    parts.append("")
    parts.append(
        f"_warn=True flags `perm_success_rate < {warn_threshold:g}`; "
        "such cells were re-sampled via sequential fallback or hit warning threshold._\n"
    )

    parts.append("## Isolated Pass Detail — Primary\n")
    parts.append(_render_isolated_passes(primary_isolated))

    parts.append("## Diagnostic: 240-row Verification Table\n")
    parts.append("<details><summary>Click to expand</summary>\n")
    parts.append(verification_table.to_markdown(index=False, floatfmt=".4f"))
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


def write_report(path, **kwargs) -> str:
    """Render report and write to ``path``. Returns the rendered markdown."""
    body = render_report(**kwargs)
    from pathlib import Path
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(body)
    return body
