from __future__ import annotations

import html
from pathlib import Path
from typing import Any, Dict


def build_report_markdown(spec_dict: Dict[str, Any], metrics: Dict[str, Any], warnings: list[str]) -> str:
    gates = metrics.get("gates", {})
    strategy = metrics.get("strategy", {})
    factor = metrics.get("factor", {})
    lines = [
        f"# Backtest Pipeline Report — {spec_dict['spec_id']}",
        "",
        "## Summary",
        f"- Benchmark: `{spec_dict['benchmark']}`",
        f"- Rebalance: `{spec_dict['portfolio']['rebalance']}`",
        f"- Factors: {', '.join(item['name'] for item in spec_dict['factors'])}",
        "- OOS capital reset: `True` (fresh capital; OOS does not inherit IS positions)",
        "",
        "## Key Gates",
    ]
    for key, value in gates.items():
        lines.append(f"- {key}: `{value}`")

    if warnings:
        lines.extend(["", "## Warnings"])
        lines.extend([f"- {warning}" for warning in warnings])

    if strategy:
        lines.extend(["", "## Strategy Metrics"])
        for phase in ("is", "oos"):
            phase_metrics = strategy.get(phase, {})
            if not phase_metrics:
                continue
            lines.extend([f"### {phase.upper()}"])
            for key in (
                "cagr",
                "annual_volatility",
                "sharpe_ratio",
                "max_drawdown",
                "annual_turnover",
                "excess_cagr",
                "ir",
            ):
                if key in phase_metrics:
                    lines.append(f"- {phase}.{key}: `{phase_metrics[key]}`")

    if factor:
        lines.extend(["", "## Factor Metrics"])
        for phase in ("is", "oos"):
            bundle = factor.get(phase, {})
            combo = bundle.get("combo", {})
            if not combo:
                continue
            lines.extend([f"### {phase.upper()} Combo"])
            for key in (
                "primary_horizon",
                "ic_mean",
                "ic_tstat",
                "top_bottom_spread",
                "top_decile_excess_return",
            ):
                if key in combo:
                    lines.append(f"- {phase}.combo.{key}: `{combo[key]}`")
            if "ic_decay" in combo:
                lines.append(f"- {phase}.combo.ic_decay: `{combo['ic_decay']}`")

    lines.extend(["", "## Spec", "```json", spec_dict if isinstance(spec_dict, str) else ""])
    if not isinstance(spec_dict, str):
        import json
        lines[-1] = json.dumps(spec_dict, ensure_ascii=False, indent=2, sort_keys=True)
    lines.extend(["```", ""])
    return "\n".join(lines)


def build_report_html(spec_dict: Dict[str, Any], metrics: Dict[str, Any], warnings: list[str]) -> str:
    markdown = build_report_markdown(spec_dict, metrics, warnings)
    return (
        "<html><head><meta charset='utf-8'><title>Backtest Pipeline Report</title>"
        "<style>body{font-family:-apple-system,BlinkMacSystemFont,sans-serif;max-width:960px;"
        "margin:40px auto;padding:0 20px;line-height:1.5} pre{background:#f5f5f5;padding:16px;"
        "overflow:auto;border-radius:8px}</style></head><body><pre>"
        + html.escape(markdown)
        + "</pre></body></html>"
    )


def write_report_bundle(
    artifact_dir: Path,
    spec_dict: Dict[str, Any],
    metrics: Dict[str, Any],
    warnings: list[str],
) -> Dict[str, Path]:
    md_path = artifact_dir / "report.md"
    html_path = artifact_dir / "report.html"
    md_path.write_text(build_report_markdown(spec_dict, metrics, warnings), encoding="utf-8")
    html_path.write_text(build_report_html(spec_dict, metrics, warnings), encoding="utf-8")
    return {"report_md": md_path, "report_html": html_path}
