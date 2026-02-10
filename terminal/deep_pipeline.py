"""
Deep analysis pipeline — file-driven multi-agent orchestration helpers.

This module provides deterministic helper functions for the /deep-analysis skill.
All intermediate results pass through files in data/companies/{SYM}/research/.

Architecture:
  Python (this module) → deterministic data prep + report compilation
  Skill (deep-analysis) → agent dispatch + LLM synthesis

The skill calls these functions, dispatches agents, then calls compile_deep_report().
"""
import logging
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

_COMPANIES_DIR = Path(__file__).parent.parent / "data" / "companies"


def get_research_dir(symbol: str) -> Path:
    """Get (or create) the research subdirectory for a ticker."""
    symbol = symbol.upper()
    d = _COMPANIES_DIR / symbol / "research"
    d.mkdir(parents=True, exist_ok=True)
    return d


def write_data_context(data_package: Any, research_dir: Path) -> Path:
    """Write formatted data context to research/data_context.md.

    Args:
        data_package: pipeline.DataPackage instance
        research_dir: Path to research directory

    Returns:
        Path to written file
    """
    context = data_package.format_context()
    path = research_dir / "data_context.md"
    path.write_text(context, encoding="utf-8")
    logger.info(f"Wrote data context: {path} ({len(context)} chars)")
    return path


def prepare_research_queries(
    symbol: str,
    company_name: str,
    sector: str,
    industry: str,
) -> Dict[str, str]:
    """Generate web search queries for research agents.

    Returns dict with keys: earnings, competitive, street.
    Each value is a search query string.
    """
    return {
        "earnings": (
            f"{company_name} {symbol} latest quarterly earnings results "
            f"revenue guidance management commentary transcript highlights 2026"
        ),
        "competitive": (
            f"{company_name} {symbol} vs competitors market share "
            f"{industry} competitive landscape comparison 2026"
        ),
        "street": (
            f"{symbol} analyst ratings price targets upgrades downgrades "
            f"Wall Street consensus bull bear debate 2026"
        ),
    }


def _slugify(name: str) -> str:
    """Convert lens name to file-safe slug."""
    return re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")


def build_lens_agent_prompt(
    lens_dict: Dict[str, str],
    research_dir: Path,
) -> str:
    """Build a self-contained prompt for a lens analysis agent.

    The agent will:
    1. Read context files from research_dir
    2. Run the lens analysis
    3. Write output to research_dir/lens_{slug}.md

    Args:
        lens_dict: {lens_name, horizon, core_metric, prompt} from prepare_lens_prompts()
        research_dir: Path to research directory with context files

    Returns:
        Complete prompt string for a Task agent
    """
    slug = _slugify(lens_dict["lens_name"])
    output_path = research_dir / f"lens_{slug}.md"

    return f"""You are an investment analyst running a **{lens_dict["lens_name"]}** analysis.

## Step 1: Read Context Files

Read these files to understand the company and market context:
- `{research_dir}/data_context.md` — Financial data, ratios, indicators, macro environment
- `{research_dir}/earnings.md` — Latest earnings highlights, management quotes, guidance
- `{research_dir}/competitive.md` — Competitive landscape, peer comparison
- `{research_dir}/street.md` — Analyst consensus, price targets, key debates
- `{research_dir}/macro_briefing.md` — Macro environment narrative

If any file is missing or empty, note it but proceed with available data.

## Step 2: Run Your Analysis

{lens_dict["prompt"]}

## Step 3: Write Output

Write your complete analysis to: `{output_path}`

## Output Requirements

- **Minimum 800 words** of substantive analysis (this is critical — do NOT write less)
- Cite specific numbers from the data (revenue, margins, growth rates, P/E, etc.)
- Reference management quotes from earnings.md where relevant
- Compare to competitors using competitive.md data
- End with:
  1. A clear **star rating (1-5)** for this lens
  2. A single-paragraph **key takeaway**
  3. **2-3 kill conditions** specific to this lens perspective
"""


def _read_research_file(research_dir: Path, filename: str) -> str:
    """Read a research file, returning empty string if missing."""
    path = research_dir / filename
    if path.exists():
        return path.read_text(encoding="utf-8")
    return ""


def compile_deep_report(symbol: str, research_dir: Path) -> str:
    """Compile all research files into a single deep analysis report.

    Reads all files from research_dir and assembles them into
    a structured markdown report. Writes result to research_dir/full_report.md.

    Args:
        symbol: Stock ticker
        research_dir: Path containing all intermediate analysis files

    Returns:
        Complete report as markdown string
    """
    symbol = symbol.upper()
    date = datetime.now().strftime("%Y-%m-%d")

    # Read all sections
    data_ctx = _read_research_file(research_dir, "data_context.md")
    earnings = _read_research_file(research_dir, "earnings.md")
    competitive = _read_research_file(research_dir, "competitive.md")
    street = _read_research_file(research_dir, "street.md")
    gemini = _read_research_file(research_dir, "gemini_contrarian.md")
    macro = _read_research_file(research_dir, "macro_briefing.md")
    lens_qc = _read_research_file(research_dir, "lens_quality_compounder.md")
    lens_ig = _read_research_file(research_dir, "lens_imaginative_growth.md")
    lens_fls = _read_research_file(research_dir, "lens_fundamental_long_short.md")
    lens_dv = _read_research_file(research_dir, "lens_deep_value.md")
    lens_ed = _read_research_file(research_dir, "lens_event_driven.md")
    debate = _read_research_file(research_dir, "debate.md")
    memo = _read_research_file(research_dir, "memo.md")
    oprms = _read_research_file(research_dir, "oprms.md")
    alpha_rt = _read_research_file(research_dir, "alpha_red_team.md")
    alpha_cy = _read_research_file(research_dir, "alpha_cycle.md")
    alpha_bet = _read_research_file(research_dir, "alpha_bet.md")

    sections = [
        f"# {symbol} Deep Research Report",
        f"",
        f"**Date**: {date} | **Analyst**: 未来资本 AI Trading Desk",
        f"",
        f"---",
        f"",
    ]

    # Macro
    if macro:
        sections.append("## I. Macro Environment")
        sections.append(macro)
        sections.append("")

    # Earnings + Competitive + Street (research context)
    if earnings or competitive or street:
        sections.append("## II. Research Context")
        if earnings:
            sections.append(earnings)
            sections.append("")
        if competitive:
            sections.append(competitive)
            sections.append("")
        if street:
            sections.append(street)
            sections.append("")

    # Five lenses
    sections.append("## III. Five-Lens Analysis")
    sections.append("")
    if lens_qc:
        sections.append("### 1. Quality Compounder")
        sections.append(lens_qc)
        sections.append("")
    if lens_ig:
        sections.append("### 2. Imaginative Growth")
        sections.append(lens_ig)
        sections.append("")
    if lens_fls:
        sections.append("### 3. Fundamental Long/Short")
        sections.append(lens_fls)
        sections.append("")
    if lens_dv:
        sections.append("### 4. Deep Value")
        sections.append(lens_dv)
        sections.append("")
    if lens_ed:
        sections.append("### 5. Event-Driven")
        sections.append(lens_ed)
        sections.append("")

    # Debate
    if debate:
        sections.append("## IV. Core Debate")
        sections.append(debate)
        sections.append("")

    # Memo
    if memo:
        sections.append("## V. Investment Memo")
        sections.append(memo)
        sections.append("")

    # OPRMS
    if oprms:
        sections.append("## VI. OPRMS Rating & Position")
        sections.append(oprms)
        sections.append("")

    # Alpha layer
    if alpha_rt or alpha_cy or alpha_bet:
        sections.append("## VII. Layer 2 — Second-Order Thinking")
        sections.append("")
        if alpha_rt:
            sections.append("### Red Team")
            sections.append(alpha_rt)
            sections.append("")
        if gemini:
            sections.append("### Gemini Contrarian View")
            sections.append(gemini)
            sections.append("")
        if alpha_cy:
            sections.append("### Cycle & Pendulum")
            sections.append(alpha_cy)
            sections.append("")
        if alpha_bet:
            sections.append("### Asymmetric Bet")
            sections.append(alpha_bet)
            sections.append("")

    sections.append("---")
    sections.append(f"*Generated by 未来资本 AI Trading Desk — {date}*")

    report = "\n".join(sections)

    # Write to file
    output_path = research_dir / "full_report.md"
    output_path.write_text(report, encoding="utf-8")
    logger.info(f"Compiled deep report: {output_path} ({len(report)} chars)")

    return report
