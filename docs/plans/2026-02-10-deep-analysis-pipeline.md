# Deep Analysis Pipeline v2 — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Build a file-driven multi-agent deep analysis system that produces 6000-8000 word institutional-grade research reports, upgrading from 60-point to 85-90 point quality.

**Architecture:** Python handles deterministic work (data collection, context formatting, report compilation). A Claude skill orchestrates agent dispatch in 2 waves: 3 research agents + Gemini in parallel, then 5 lens agents in parallel. All intermediate results pass through files in `data/companies/{SYM}/research/`, never through context window. Main Claude handles synthesis (debate, memo, alpha) by reading files sequentially.

**Tech Stack:** Python 3.13, existing terminal pipeline, Task tool for agent dispatch, Gemini MCP for contrarian view, WebSearch for research.

**Worktree:** `/Users/owen/CC workspace/.worktrees/finance-deep-analysis/`
**Python:** `/Users/owen/CC workspace/Finance/.venv/bin/python3` (shared from main)
**Tests:** `pytest tests/ -v` from worktree root

---

## File Map

```
NEW FILES:
  terminal/deep_pipeline.py          ~150 lines — helper functions
  tests/test_deep_pipeline.py        ~250 lines — unit tests
  ~/.claude/skills/deep-analysis/
    skill.md                          ~250 lines — orchestration skill

MODIFIED FILES:
  terminal/company_db.py             +1 line  (add "research" subdir)
  terminal/commands.py               +60 lines (deep_analyze_ticker entry point)
```

---

### Task 1: Add "research" subdir to company_db

**Files:**
- Modify: `terminal/company_db.py:28`
- Test: `tests/test_deep_pipeline.py` (new file, first test)

**Step 1: Write the failing test**

Create `tests/test_deep_pipeline.py`:

```python
"""Tests for terminal.deep_pipeline — Deep analysis file-based pipeline."""
import json
import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock
from dataclasses import dataclass, field
from typing import Optional, Any, List

import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


class TestResearchDir:
    """Test that company_db creates research subdir."""

    def test_get_company_dir_creates_research(self, tmp_path):
        with patch("terminal.company_db._COMPANIES_DIR", tmp_path):
            from terminal.company_db import get_company_dir

            d = get_company_dir("TEST")
            assert (d / "research").is_dir()
```

**Step 2: Run test to verify it fails**

Run: `/Users/owen/CC workspace/Finance/.venv/bin/python3 -m pytest tests/test_deep_pipeline.py::TestResearchDir::test_get_company_dir_creates_research -v`
Expected: FAIL — "research" dir does not exist

**Step 3: Add "research" to subdirs list**

In `terminal/company_db.py` line 28, change:

```python
# Before:
    for sub in ["memos", "analyses", "debates", "strategies", "trades"]:

# After:
    for sub in ["memos", "analyses", "debates", "strategies", "trades", "research"]:
```

**Step 4: Run test to verify it passes**

Run: `/Users/owen/CC workspace/Finance/.venv/bin/python3 -m pytest tests/test_deep_pipeline.py::TestResearchDir -v`
Expected: PASS

**Step 5: Run all existing tests to verify no regression**

Run: `/Users/owen/CC workspace/Finance/.venv/bin/python3 -m pytest tests/ -v --tb=short`
Expected: All 272+ tests PASS

**Step 6: Commit**

```bash
git add terminal/company_db.py tests/test_deep_pipeline.py
git commit -m "feat: add research subdir to company_db"
```

---

### Task 2: Create deep_pipeline.py — get_research_dir() + write_data_context()

**Files:**
- Create: `terminal/deep_pipeline.py`
- Test: `tests/test_deep_pipeline.py` (append)

**Step 1: Write the failing tests**

Append to `tests/test_deep_pipeline.py`:

```python
# --- Mock DataPackage for testing ---

class MockDataPackage:
    """Lightweight mock of pipeline.DataPackage for testing."""

    def __init__(self, symbol="TEST"):
        self.symbol = symbol
        self.info = {
            "companyName": "Test Corp",
            "marketCap": 500_000_000_000,
            "sector": "Technology",
            "industry": "Software",
            "exchange": "NASDAQ",
        }
        self.profile = {"description": "A test company.", "ceo": "Test CEO"}
        self.fundamentals = {"pe": 25.0, "grossMargin": 0.7}
        self.ratios = []
        self.income = []
        self.macro = None
        self.macro_briefing = None
        self.macro_signals = []
        self.company_record = None
        self.price = {"close": 100.0}
        self.indicators = {"symbol": symbol, "signals": [], "pmarp": {"current": 65.0}}
        self.analyst_estimates = None
        self.earnings_calendar = None
        self.insider_trades = []
        self.news = []

    @property
    def has_financials(self):
        return True

    @property
    def latest_price(self):
        return 100.0

    def format_context(self):
        return f"### Company: {self.info['companyName']} ({self.symbol})\n- Sector: Technology\n- Market Cap: $500B\n\n### Key Fundamentals\n- P/E: 25.0\n- Gross Margin: 70%"


class TestGetResearchDir:
    """Tests for get_research_dir()."""

    def test_creates_dir(self, tmp_path):
        with patch("terminal.deep_pipeline._COMPANIES_DIR", tmp_path):
            from terminal.deep_pipeline import get_research_dir

            d = get_research_dir("MSFT")
            assert d.is_dir()
            assert d == tmp_path / "MSFT" / "research"

    def test_idempotent(self, tmp_path):
        with patch("terminal.deep_pipeline._COMPANIES_DIR", tmp_path):
            from terminal.deep_pipeline import get_research_dir

            d1 = get_research_dir("MSFT")
            d2 = get_research_dir("MSFT")
            assert d1 == d2

    def test_uppercases_symbol(self, tmp_path):
        with patch("terminal.deep_pipeline._COMPANIES_DIR", tmp_path):
            from terminal.deep_pipeline import get_research_dir

            d = get_research_dir("msft")
            assert "MSFT" in str(d)


class TestWriteDataContext:
    """Tests for write_data_context()."""

    def test_writes_file(self, tmp_path):
        with patch("terminal.deep_pipeline._COMPANIES_DIR", tmp_path):
            from terminal.deep_pipeline import write_data_context, get_research_dir

            research_dir = get_research_dir("TEST")
            pkg = MockDataPackage()
            path = write_data_context(pkg, research_dir)

            assert path.exists()
            assert path.name == "data_context.md"
            content = path.read_text()
            assert "Test Corp" in content
            assert "Technology" in content

    def test_overwrites_existing(self, tmp_path):
        with patch("terminal.deep_pipeline._COMPANIES_DIR", tmp_path):
            from terminal.deep_pipeline import write_data_context, get_research_dir

            research_dir = get_research_dir("TEST")
            pkg = MockDataPackage()
            write_data_context(pkg, research_dir)
            path = write_data_context(pkg, research_dir)
            # Should not raise, file overwritten
            assert path.exists()
```

**Step 2: Run tests to verify they fail**

Run: `/Users/owen/CC workspace/Finance/.venv/bin/python3 -m pytest tests/test_deep_pipeline.py::TestGetResearchDir -v`
Expected: FAIL — module not found

**Step 3: Create deep_pipeline.py with initial functions**

Create `terminal/deep_pipeline.py`:

```python
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
```

**Step 4: Run tests to verify they pass**

Run: `/Users/owen/CC workspace/Finance/.venv/bin/python3 -m pytest tests/test_deep_pipeline.py::TestGetResearchDir tests/test_deep_pipeline.py::TestWriteDataContext -v`
Expected: All PASS

**Step 5: Commit**

```bash
git add terminal/deep_pipeline.py tests/test_deep_pipeline.py
git commit -m "feat: add deep_pipeline with get_research_dir and write_data_context"
```

---

### Task 3: Add prepare_research_queries() + build_lens_agent_prompt()

**Files:**
- Modify: `terminal/deep_pipeline.py`
- Test: `tests/test_deep_pipeline.py` (append)

**Step 1: Write the failing tests**

Append to `tests/test_deep_pipeline.py`:

```python
class TestPrepareResearchQueries:
    """Tests for prepare_research_queries()."""

    def test_returns_all_topics(self):
        from terminal.deep_pipeline import prepare_research_queries

        queries = prepare_research_queries("MSFT", "Microsoft Corporation", "Technology", "Software")
        assert "earnings" in queries
        assert "competitive" in queries
        assert "street" in queries
        # Each topic has a query string
        for topic, q in queries.items():
            assert isinstance(q, str)
            assert len(q) > 10

    def test_includes_symbol(self):
        from terminal.deep_pipeline import prepare_research_queries

        queries = prepare_research_queries("NVDA", "NVIDIA", "Technology", "Semiconductors")
        for topic, q in queries.items():
            assert "NVDA" in q or "NVIDIA" in q

    def test_includes_sector_context(self):
        from terminal.deep_pipeline import prepare_research_queries

        queries = prepare_research_queries("MSFT", "Microsoft", "Technology", "Software")
        # Competitive query should mention sector/industry
        assert "Software" in queries["competitive"] or "Technology" in queries["competitive"] or "cloud" in queries["competitive"].lower()


class TestBuildLensAgentPrompt:
    """Tests for build_lens_agent_prompt()."""

    def test_contains_file_read_instructions(self, tmp_path):
        from terminal.deep_pipeline import build_lens_agent_prompt

        lens_dict = {
            "lens_name": "Quality Compounder",
            "horizon": "20+ years",
            "core_metric": "ROIC",
            "prompt": "Analyze the company from a quality compounder perspective.",
        }
        prompt = build_lens_agent_prompt(lens_dict, tmp_path)
        assert "data_context.md" in prompt
        assert "earnings.md" in prompt
        assert "competitive.md" in prompt
        assert "macro_briefing.md" in prompt

    def test_contains_lens_prompt(self, tmp_path):
        from terminal.deep_pipeline import build_lens_agent_prompt

        lens_dict = {
            "lens_name": "Deep Value",
            "horizon": "3-5 years",
            "core_metric": "Replacement Cost",
            "prompt": "Find margin of safety and hidden assets.",
        }
        prompt = build_lens_agent_prompt(lens_dict, tmp_path)
        assert "Find margin of safety" in prompt

    def test_contains_output_instructions(self, tmp_path):
        from terminal.deep_pipeline import build_lens_agent_prompt

        lens_dict = {
            "lens_name": "Quality Compounder",
            "horizon": "20+ years",
            "core_metric": "ROIC",
            "prompt": "Analyze.",
        }
        prompt = build_lens_agent_prompt(lens_dict, tmp_path)
        assert "lens_quality_compounder.md" in prompt
        assert "800" in prompt  # min word count

    def test_slug_generation(self, tmp_path):
        from terminal.deep_pipeline import build_lens_agent_prompt

        lens_dict = {
            "lens_name": "Fundamental Long/Short",
            "horizon": "1-3 years",
            "core_metric": "Relative Value",
            "prompt": "Analyze.",
        }
        prompt = build_lens_agent_prompt(lens_dict, tmp_path)
        assert "lens_fundamental_long_short.md" in prompt
```

**Step 2: Run tests to verify they fail**

Run: `/Users/owen/CC workspace/Finance/.venv/bin/python3 -m pytest tests/test_deep_pipeline.py::TestPrepareResearchQueries tests/test_deep_pipeline.py::TestBuildLensAgentPrompt -v`
Expected: FAIL

**Step 3: Implement the functions**

Append to `terminal/deep_pipeline.py`:

```python
import re


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
```

**Step 4: Run tests to verify they pass**

Run: `/Users/owen/CC workspace/Finance/.venv/bin/python3 -m pytest tests/test_deep_pipeline.py::TestPrepareResearchQueries tests/test_deep_pipeline.py::TestBuildLensAgentPrompt -v`
Expected: All PASS

**Step 5: Commit**

```bash
git add terminal/deep_pipeline.py tests/test_deep_pipeline.py
git commit -m "feat: add prepare_research_queries and build_lens_agent_prompt"
```

---

### Task 4: Add compile_deep_report()

**Files:**
- Modify: `terminal/deep_pipeline.py`
- Test: `tests/test_deep_pipeline.py` (append)

**Step 1: Write the failing tests**

Append to `tests/test_deep_pipeline.py`:

```python
class TestCompileDeepReport:
    """Tests for compile_deep_report()."""

    def _populate_research_dir(self, research_dir):
        """Create all expected files in research_dir for compilation."""
        files = {
            "data_context.md": "### Company: Test Corp (TEST)\n- Sector: Technology\n- Market Cap: $500B",
            "earnings.md": "## Earnings\nRevenue beat by 3%. Management guided higher.",
            "competitive.md": "## Competitive\nTEST leads with 35% market share vs RIVAL 25%.",
            "street.md": "## Street\nConsensus BUY. Average PT $150. Range $120-$180.",
            "gemini_contrarian.md": "## Contrarian View\nMarket may be underpricing competitive risk.",
            "macro_briefing.md": "## Macro\nRISK_ON regime. Low VIX, positive curve.",
            "lens_quality_compounder.md": "## Quality Compounder\nFour moats identified. ROIC 30%. Rating: 4/5 stars.",
            "lens_imaginative_growth.md": "## Imaginative Growth\nTAM $200B. Revenue CAGR 25%. Rating: 5/5 stars.",
            "lens_fundamental_long_short.md": "## Fundamental L/S\nLong thesis strong. Short risk: valuation. Rating: 3/5 stars.",
            "lens_deep_value.md": "## Deep Value\nDCF suggests 20% upside. Margin of safety thin. Rating: 3/5 stars.",
            "lens_event_driven.md": "## Event-Driven\nEarnings catalyst in 60 days. Rating: 4/5 stars.",
            "debate.md": "## Debate\n3 tensions identified. Bull wins with caveats.",
            "memo.md": "## Investment Memo\nBUY with 12% position. Key risk: competition.",
            "oprms.md": "## OPRMS\nDNA: S | Timing: B (0.55) | Position: 11.7%",
            "alpha_red_team.md": "## Red Team\nCisco analog attack. Thesis survives but weakened.",
            "alpha_cycle.md": "## Cycle\nSentiment 7/10. Late expansion. Early majority adoption.",
            "alpha_bet.md": "## Asymmetric Bet\nBarbell structure. R:R 1:4.7. TAKE IT.",
        }
        for name, content in files.items():
            (research_dir / name).write_text(content)
        return files

    def test_compiles_all_sections(self, tmp_path):
        with patch("terminal.deep_pipeline._COMPANIES_DIR", tmp_path):
            from terminal.deep_pipeline import get_research_dir, compile_deep_report

            research_dir = get_research_dir("TEST")
            self._populate_research_dir(research_dir)
            report = compile_deep_report("TEST", research_dir)

            assert "Test Corp" in report
            assert "Quality Compounder" in report
            assert "Imaginative Growth" in report
            assert "Red Team" in report
            assert "Asymmetric Bet" in report

    def test_writes_full_report_file(self, tmp_path):
        with patch("terminal.deep_pipeline._COMPANIES_DIR", tmp_path):
            from terminal.deep_pipeline import get_research_dir, compile_deep_report

            research_dir = get_research_dir("TEST")
            self._populate_research_dir(research_dir)
            report = compile_deep_report("TEST", research_dir)

            output_path = research_dir / "full_report.md"
            assert output_path.exists()
            assert output_path.read_text() == report

    def test_handles_missing_optional_files(self, tmp_path):
        with patch("terminal.deep_pipeline._COMPANIES_DIR", tmp_path):
            from terminal.deep_pipeline import get_research_dir, compile_deep_report

            research_dir = get_research_dir("TEST")
            # Only write required files, skip gemini_contrarian
            required = {
                "data_context.md": "### Company: TEST",
                "macro_briefing.md": "## Macro",
                "lens_quality_compounder.md": "## QC",
                "lens_imaginative_growth.md": "## IG",
                "lens_fundamental_long_short.md": "## FLS",
                "lens_deep_value.md": "## DV",
                "lens_event_driven.md": "## ED",
                "debate.md": "## Debate",
                "memo.md": "## Memo",
                "oprms.md": "## OPRMS",
                "alpha_red_team.md": "## RT",
                "alpha_cycle.md": "## Cycle",
                "alpha_bet.md": "## Bet",
            }
            for name, content in required.items():
                (research_dir / name).write_text(content)

            report = compile_deep_report("TEST", research_dir)
            # Should compile without error
            assert "TEST" in report
            # Gemini section should be gracefully absent or noted
            assert "Contrarian" not in report or "not available" in report.lower()
```

**Step 2: Run tests to verify they fail**

Run: `/Users/owen/CC workspace/Finance/.venv/bin/python3 -m pytest tests/test_deep_pipeline.py::TestCompileDeepReport -v`
Expected: FAIL

**Step 3: Implement compile_deep_report()**

Append to `terminal/deep_pipeline.py`:

```python
from datetime import datetime


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
```

**Step 4: Run tests to verify they pass**

Run: `/Users/owen/CC workspace/Finance/.venv/bin/python3 -m pytest tests/test_deep_pipeline.py::TestCompileDeepReport -v`
Expected: All PASS

**Step 5: Commit**

```bash
git add terminal/deep_pipeline.py tests/test_deep_pipeline.py
git commit -m "feat: add compile_deep_report for final report assembly"
```

---

### Task 5: Add deep_analyze_ticker() entry point to commands.py

**Files:**
- Modify: `terminal/commands.py`
- Test: `tests/test_deep_pipeline.py` (append)

**Step 1: Write the failing test**

Append to `tests/test_deep_pipeline.py`:

```python
class TestDeepAnalyzeTicker:
    """Tests for commands.deep_analyze_ticker() setup phase."""

    @patch("terminal.commands.collect_data")
    @patch("terminal.commands.prepare_lens_prompts")
    def test_returns_expected_keys(self, mock_lenses, mock_collect):
        mock_pkg = MockDataPackage("MSFT")
        mock_collect.return_value = mock_pkg
        mock_lenses.return_value = [
            {"lens_name": "Quality Compounder", "horizon": "20y", "core_metric": "ROIC", "prompt": "Analyze."},
        ]

        from terminal.commands import deep_analyze_ticker

        result = deep_analyze_ticker("MSFT")

        assert "research_dir" in result
        assert "data_context_path" in result
        assert "research_queries" in result
        assert "lens_agent_prompts" in result
        assert "macro_briefing_prompt" in result
        assert "gemini_prompt" in result
        assert "context_summary" in result

    @patch("terminal.commands.collect_data")
    @patch("terminal.commands.prepare_lens_prompts")
    def test_data_context_file_written(self, mock_lenses, mock_collect, tmp_path):
        mock_pkg = MockDataPackage("TEST")
        mock_collect.return_value = mock_pkg
        mock_lenses.return_value = []

        with patch("terminal.deep_pipeline._COMPANIES_DIR", tmp_path):
            from terminal.commands import deep_analyze_ticker

            result = deep_analyze_ticker("TEST")
            assert Path(result["data_context_path"]).exists()

    @patch("terminal.commands.collect_data")
    @patch("terminal.commands.prepare_lens_prompts")
    def test_lens_agent_prompts_have_output_paths(self, mock_lenses, mock_collect):
        mock_pkg = MockDataPackage("MSFT")
        mock_collect.return_value = mock_pkg
        mock_lenses.return_value = [
            {"lens_name": "Quality Compounder", "horizon": "20y", "core_metric": "ROIC", "prompt": "Analyze QC."},
            {"lens_name": "Deep Value", "horizon": "3-5y", "core_metric": "Book", "prompt": "Analyze DV."},
        ]

        from terminal.commands import deep_analyze_ticker

        result = deep_analyze_ticker("MSFT")
        for lap in result["lens_agent_prompts"]:
            assert "lens_name" in lap
            assert "agent_prompt" in lap
            assert "output_path" in lap
```

**Step 2: Run tests to verify they fail**

Run: `/Users/owen/CC workspace/Finance/.venv/bin/python3 -m pytest tests/test_deep_pipeline.py::TestDeepAnalyzeTicker -v`
Expected: FAIL — deep_analyze_ticker not found

**Step 3: Add deep_analyze_ticker() to commands.py**

Add to `terminal/commands.py` after the existing `analyze_ticker()` function (after line 218):

```python
def deep_analyze_ticker(
    symbol: str,
    price_days: int = 120,
) -> Dict[str, Any]:
    """
    Setup phase for deep analysis — prepares all data and prompts.

    Returns a dict consumed by the /deep-analysis skill, which handles
    agent dispatch and LLM synthesis. All intermediate files go to
    data/companies/{SYMBOL}/research/.

    This function does NOT run any LLM analysis. It only:
    1. Collects data (FMP + FRED + indicators)
    2. Writes data_context.md to research dir
    3. Prepares research queries for web search agents
    4. Prepares lens agent prompts (with file read/write instructions)
    5. Prepares macro briefing prompt
    6. Prepares Gemini contrarian prompt
    """
    from terminal.deep_pipeline import (
        get_research_dir,
        write_data_context,
        prepare_research_queries,
        build_lens_agent_prompt,
    )

    symbol = symbol.upper()
    result: Dict[str, Any] = {"symbol": symbol}

    # 1. Collect data
    scratchpad = AnalysisScratchpad(symbol, "deep")
    data_pkg = collect_data(symbol, price_days=price_days, scratchpad=scratchpad)

    # 2. Research directory + data context
    research_dir = get_research_dir(symbol)
    ctx_path = write_data_context(data_pkg, research_dir)
    result["research_dir"] = str(research_dir)
    result["data_context_path"] = str(ctx_path)
    result["context_summary"] = data_pkg.format_context()

    # 3. Research queries
    info = data_pkg.info or {}
    result["research_queries"] = prepare_research_queries(
        symbol=symbol,
        company_name=info.get("companyName", symbol),
        sector=info.get("sector", ""),
        industry=info.get("industry", ""),
    )

    # 4. Macro briefing prompt
    if data_pkg.macro:
        try:
            from terminal.macro_briefing import generate_briefing_prompt, detect_signals
            signals = detect_signals(data_pkg.macro)
            result["macro_briefing_prompt"] = generate_briefing_prompt(data_pkg.macro, signals)
            result["macro_signals"] = [
                {"name": s.name, "label": s.label, "strength": s.strength}
                for s in signals if s.fired
            ]
        except Exception as e:
            logger.warning(f"Macro briefing generation failed: {e}")
            result["macro_briefing_prompt"] = ""
    else:
        result["macro_briefing_prompt"] = ""

    # 5. Lens agent prompts
    lens_prompts = prepare_lens_prompts(symbol, data_pkg)
    result["lens_agent_prompts"] = []
    for lp in lens_prompts:
        agent_prompt = build_lens_agent_prompt(lp, research_dir)
        slug = lp["lens_name"].lower().replace("/", "_").replace(" ", "_")
        slug = "".join(c for c in slug if c.isalnum() or c == "_")
        result["lens_agent_prompts"].append({
            "lens_name": lp["lens_name"],
            "agent_prompt": agent_prompt,
            "output_path": str(research_dir / f"lens_{slug}.md"),
        })

    # 6. Gemini contrarian prompt
    company_name = info.get("companyName", symbol)
    result["gemini_prompt"] = (
        f"You are a contrarian investment analyst. Given the following data about "
        f"{company_name} ({symbol}), provide a 500-word bearish counter-thesis. "
        f"Focus on risks the market is ignoring, historical analogs of similar "
        f"companies that failed, and structural weaknesses in the business model.\n\n"
        f"Key data:\n{data_pkg.format_context()[:3000]}"
    )

    # 7. Data summary for reference
    result["data"] = {
        "info": data_pkg.info,
        "latest_price": data_pkg.latest_price,
        "indicators": data_pkg.indicators,
        "has_financials": data_pkg.has_financials,
    }

    # 8. Alpha prompt args (for Phase 4 in skill)
    record = data_pkg.company_record
    result["alpha_prompt_args"] = {
        "symbol": symbol,
        "sector": info.get("sector", ""),
        "data_context_path": str(ctx_path),
        "l1_oprms": record.oprms if record and record.has_data else None,
        "current_price": data_pkg.latest_price,
    }

    result["scratchpad_path"] = str(scratchpad.log_path)
    return result
```

Also add the import at the top of commands.py (after existing imports):

```python
# Add after line 31 (from pathlib import Path):
# (no new imports needed — deep_pipeline imports are inline in deep_analyze_ticker)
```

**Step 4: Run tests to verify they pass**

Run: `/Users/owen/CC workspace/Finance/.venv/bin/python3 -m pytest tests/test_deep_pipeline.py::TestDeepAnalyzeTicker -v`
Expected: All PASS

**Step 5: Run full test suite**

Run: `/Users/owen/CC workspace/Finance/.venv/bin/python3 -m pytest tests/ -v --tb=short`
Expected: All 272+ existing tests PASS + new tests PASS

**Step 6: Commit**

```bash
git add terminal/commands.py terminal/deep_pipeline.py tests/test_deep_pipeline.py
git commit -m "feat: add deep_analyze_ticker entry point for multi-agent analysis"
```

---

### Task 6: Create the /deep-analysis skill

**Files:**
- Create: `~/.claude/skills/deep-analysis/skill.md`

**Step 1: Create the skill file**

```markdown
# Deep Analysis — Multi-Agent Research Report

## When to Use

User says "深度分析", "deep analysis", "deep dive", or "全面分析" followed by a ticker symbol.

## What This Produces

A 6000-8000 word institutional-grade research report, saved to:
- `data/companies/{SYM}/research/full_report.md` — complete report
- Company DB (OPRMS, memo, alpha, kill conditions)
- Heptabase (note card + journal entry)

## Execution Protocol

**CRITICAL: Follow each phase exactly. Do NOT skip steps or compress outputs.**

---

### Phase 0: Data Collection + Research Dispatch

**Step 0a: Run Python setup**

```python
from terminal.commands import deep_analyze_ticker
setup = deep_analyze_ticker('{SYMBOL}')
```

Report the setup results to the user briefly (price, market cap, regime).

**Step 0b: Dispatch research agents (PARALLEL)**

Launch 3 Task agents simultaneously + 1 Gemini call. Each agent uses `subagent_type="general-purpose"` and `model="opus"`.

**Agent 1 — Earnings Research:**
```
Search the web for the latest quarterly earnings of {SYMBOL} using these queries:
{setup['research_queries']['earnings']}

Produce a structured summary (500-800 words) covering:
- Revenue, EPS, guidance vs expectations (exact numbers)
- Management quotes from earnings call (verbatim where possible)
- Key Q&A highlights from analyst questions
- Forward guidance details

Write your output to: {setup['research_dir']}/earnings.md
```

**Agent 2 — Competitive Intelligence:**
```
Search the web for competitive landscape analysis:
{setup['research_queries']['competitive']}

Produce a structured comparison (500-800 words) covering:
- Market share data (with sources)
- Recent competitive moves (product launches, pricing, partnerships)
- Comparative financial metrics table (revenue growth, margins, valuation)
- Competitive moat assessment

Write your output to: {setup['research_dir']}/competitive.md
```

**Agent 3 — Street Consensus:**
```
Search the web for analyst views:
{setup['research_queries']['street']}

Produce a structured summary (500-800 words) covering:
- Rating distribution (Buy/Hold/Sell counts)
- Price target range (low/median/high) with notable analyst names
- Key bull arguments (top 3)
- Key bear arguments (top 3)
- Recent rating changes (upgrades/downgrades in last 30 days)

Write your output to: {setup['research_dir']}/street.md
```

**Gemini Contrarian View:**
Call `mcp__dual-llm__gemini_think` with:
- question: `{setup['gemini_prompt']}`
- system_prompt: "You are a contrarian short-seller. Find the weakest points in this investment thesis. Be specific with numbers and historical analogs."
- model: "gemini-2.0-flash"

Write the response to: `{setup['research_dir']}/gemini_contrarian.md`

**Wait for all 4 to complete before proceeding.**

---

### Phase 1: Macro Briefing (Main Claude)

Read the macro_briefing_prompt from setup:
```
{setup['macro_briefing_prompt']}
```

Write a **500+ word** macro narrative. This is NOT a summary — it's a full trading-desk morning briefing.

**Write output to:** `{setup['research_dir']}/macro_briefing.md`

---

### Phase 2: Five Lens Analysis (PARALLEL)

Dispatch 5 Task agents simultaneously. Each uses `subagent_type="general-purpose"` and `model="opus"`.

For each lens in `setup['lens_agent_prompts']`:

```
Task(
    prompt=lens['agent_prompt'],   # Contains file read/write instructions
    subagent_type="general-purpose",
    model="opus",
    name=f"lens-{lens['lens_name']}",
    description=f"Analyze {lens['lens_name']}",
)
```

**Wait for all 5 to complete before proceeding.**

**Quality gate:** After all agents return, read each lens output file. If any is under 500 words, re-dispatch that specific agent with an explicit note: "Your previous output was too short. Expand to minimum 800 words with more specific data citations."

---

### Phase 3: Debate + Memo + OPRMS (Main Claude)

**Step 3a: Read all 5 lens outputs.**

Read each file from research_dir:
- `lens_quality_compounder.md`
- `lens_imaginative_growth.md`
- `lens_fundamental_long_short.md`
- `lens_deep_value.md`
- `lens_event_driven.md`

**Step 3b: Identify 3 core tensions across the 5 perspectives.**

What fundamental disagreements or trade-offs emerge? Write them as:
- Tension 1: [A vs B]
- Tension 2: [C vs D]
- Tension 3: [E vs F]

**Step 3c: Run structured debate (500+ words).**

For each tension, write a bull argument and bear argument, then a resolution. End with an overall verdict: BUY / HOLD / SELL with confidence level.

**Write to:** `{research_dir}/debate.md`

**Step 3d: Write full investment memo (800+ words).**

Include: Executive Summary, Variant View, Key Forces, Valuation (DCF scenarios with sensitivity), Risk Framework, Position Recommendation.

**Write to:** `{research_dir}/memo.md`

**Step 3e: OPRMS rating.**

Apply the OPRMS framework:
- DNA rating (S/A/B/C) with rationale
- Timing rating (S/A/B/C) with coefficient
- Position calculation: DNA_cap × timing_coeff × regime_mult
- Conviction modifier from alpha layer (use 1.0 as placeholder, update after Phase 4)

**Write to:** `{research_dir}/oprms.md`

---

### Phase 4: Alpha Layer (Main Claude, SEQUENTIAL)

**Step 4a: Red Team (500+ words)**

Read `gemini_contrarian.md` for independent adversarial input.

Generate the red team prompt:
```python
from knowledge.alpha.red_team import generate_red_team_prompt
prompt = generate_red_team_prompt(
    symbol='{SYMBOL}',
    memo_summary='[from your memo executive summary]',
    l1_verdict='[BUY/HOLD/SELL from debate]',
    l1_key_forces='[3 key forces from memo]',
    data_context=open('{research_dir}/data_context.md').read(),
)
```

Run the prompt. Incorporate Gemini's contrarian points where they strengthen the attack.

**Write to:** `{research_dir}/alpha_red_team.md`

**Step 4b: Cycle & Pendulum (500+ words)**

```python
from knowledge.alpha.cycle_pendulum import generate_cycle_prompt
prompt = generate_cycle_prompt(
    symbol='{SYMBOL}',
    sector='{sector}',
    data_context=open('{research_dir}/data_context.md').read(),
    red_team_summary='[from your red team output]',
    macro_briefing=open('{research_dir}/macro_briefing.md').read(),
)
```

**Write to:** `{research_dir}/alpha_cycle.md`

**Step 4c: Asymmetric Bet (500+ words)**

```python
from knowledge.alpha.asymmetric_bet import generate_bet_prompt
prompt = generate_bet_prompt(
    symbol='{SYMBOL}',
    data_context=open('{research_dir}/data_context.md').read(),
    red_team_summary='[from your red team output]',
    cycle_summary='[from your cycle output]',
    l1_oprms=setup['alpha_prompt_args']['l1_oprms'],
    l1_verdict='[BUY/HOLD/SELL]',
    current_price=setup['alpha_prompt_args']['current_price'],
)
```

Update conviction_modifier in oprms.md based on bet output.

**Write to:** `{research_dir}/alpha_bet.md`

---

### Phase 5: Assembly + Storage

**Step 5a: Compile report**

```python
from terminal.deep_pipeline import compile_deep_report
report = compile_deep_report('{SYMBOL}', Path('{research_dir}'))
```

**Step 5b: Save to Company DB**

Save OPRMS, memo, alpha package, kill conditions using terminal.company_db functions.

**Step 5c: Sync to Heptabase**

1. Call `mcp__heptabase__save_to_note_card` with the full report content (or a condensed version if >5000 chars)
2. Call `mcp__heptabase__append_to_journal` with a brief analysis summary

**Step 5d: Report to user**

Present the full report to the user. Remind them to drag the Heptabase card to the 「未来资本」whiteboard.

---

## Error Recovery

- If a research agent fails: proceed without that research file. Note the gap.
- If a lens agent produces < 300 words: re-dispatch once. If still short, proceed with available output.
- If Gemini call fails: skip contrarian view. Note in red team that independent verification was unavailable.
- If any Python function throws: report the error and stop. Do not proceed with partial data.

## Quality Checklist (Before Phase 5)

Before compiling the final report, verify:
- [ ] All 5 lens files exist and are > 500 words each
- [ ] Macro briefing is > 300 words
- [ ] Debate identifies exactly 3 tensions
- [ ] Memo has explicit DCF range (bear/base/bull)
- [ ] OPRMS has explicit DNA + Timing + position %
- [ ] Alpha red team has ≥ 3 attack vectors
- [ ] Alpha bet has explicit R:R ratio and verdict
```

**Step 2: Verify skill loads**

The skill should be listed when running `/help` or checking `~/.claude/skills/deep-analysis/`.

**Step 3: Commit** (skill is outside the repo, no git needed for this file)

Save note: the skill file is at `~/.claude/skills/deep-analysis/skill.md` — this is a global Claude setting, not part of the repo.

---

### Task 7: End-to-end validation

**Step 1: Run full test suite from worktree**

```bash
cd "/Users/owen/CC workspace/.worktrees/finance-deep-analysis"
/Users/owen/CC\ workspace/Finance/.venv/bin/python3 -m pytest tests/ -v --tb=short
```

Expected: All previous 272 tests + new tests PASS.

**Step 2: Dry-run the Python setup**

```bash
cd "/Users/owen/CC workspace/.worktrees/finance-deep-analysis"
/Users/owen/CC\ workspace/Finance/.venv/bin/python3 -c "
from terminal.commands import deep_analyze_ticker
result = deep_analyze_ticker('AAPL')
print('Keys:', list(result.keys()))
print('Research dir:', result['research_dir'])
print('Lens count:', len(result['lens_agent_prompts']))
print('Has macro prompt:', bool(result['macro_briefing_prompt']))
print('Data context exists:', __import__('pathlib').Path(result['data_context_path']).exists())
"
```

Expected: All keys present, research dir created, 5 lens prompts, data_context.md written.

**Step 3: Manual skill test**

In a Claude Code session, say: "深度分析 AAPL"

Verify the skill triggers and follows the phase protocol. This is a manual validation step — the output quality is the real test.

---

### Task 8: Merge to main

**Step 1: Ensure clean state**

```bash
cd "/Users/owen/CC workspace/.worktrees/finance-deep-analysis"
git status
git log --oneline main..feature/deep-analysis
```

**Step 2: Merge**

```bash
cd "/Users/owen/CC workspace/Finance"
git merge feature/deep-analysis --no-ff -m "feat: add deep analysis pipeline v2 — file-driven multi-agent reports"
```

**Step 3: Clean up worktree**

```bash
git worktree remove "/Users/owen/CC workspace/.worktrees/finance-deep-analysis"
git branch -d feature/deep-analysis
```

**Step 4: Run final test suite from main**

```bash
/Users/owen/CC\ workspace/Finance/.venv/bin/python3 -m pytest tests/ -v --tb=short
```

Expected: All tests PASS.

---

## Architecture Summary

```
User: "深度分析 MSFT"
  │
  ▼
/deep-analysis skill triggers
  │
  ▼
Phase 0: deep_analyze_ticker('MSFT')
  │  ├── collect_data() → DataPackage
  │  ├── write_data_context() → research/data_context.md
  │  ├── prepare_research_queries() → {earnings, competitive, street}
  │  └── build_lens_agent_prompt() × 5 → agent prompts
  │
  ├── Task Agent: Earnings → research/earnings.md
  ├── Task Agent: Competitive → research/competitive.md
  ├── Task Agent: Street → research/street.md
  └── Gemini MCP → research/gemini_contrarian.md
  │
Phase 1: Main Claude → research/macro_briefing.md
  │
Phase 2 (parallel):
  ├── Task Agent: Quality Compounder → research/lens_quality_compounder.md
  ├── Task Agent: Imaginative Growth → research/lens_imaginative_growth.md
  ├── Task Agent: Fundamental L/S → research/lens_fundamental_long_short.md
  ├── Task Agent: Deep Value → research/lens_deep_value.md
  └── Task Agent: Event-Driven → research/lens_event_driven.md
  │
Phase 3: Main Claude:
  ├── Read 5 lens files → debate.md
  ├── Write memo.md
  └── Write oprms.md
  │
Phase 4: Main Claude (sequential):
  ├── Red Team + Gemini → alpha_red_team.md
  ├── Cycle → alpha_cycle.md
  └── Bet → alpha_bet.md
  │
Phase 5: compile_deep_report() → full_report.md
  ├── Company DB (save_oprms, save_memo, save_alpha)
  └── Heptabase MCP (card + journal)
```
