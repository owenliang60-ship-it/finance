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

    return f"""你是一位投资分析师，正在执行 **{lens_dict["lens_name"]}** 分析。

## 第一步：阅读上下文文件

阅读以下文件了解公司和市场背景：
- `{research_dir}/data_context.md` — 财务数据、比率、技术指标、宏观环境
- `{research_dir}/earnings.md` — 最新财报要点、管理层评论、指引
- `{research_dir}/competitive.md` — 竞争格局、同行对比
- `{research_dir}/street.md` — 分析师共识、目标价、多空争论

文件缺失或为空则跳过，用已有数据继续。

## 第二步：执行分析

{lens_dict["prompt"]}

## 第三步：写入输出

将完整分析写入：`{output_path}`

## 输出要求

- **使用中文撰写**（金融术语可用英文括注，如「护城河 (moat)」）
- **500-700 字**，追求信息密度而非篇幅
- **禁止重复研究文件中的原始数据** — 研究文件已提供事实，你只需提供本透镜独有的分析判断
- 引用数据时一句话概括（如「ROIC 连续 5 年 >25%」），不要复制整段
- 输出结构：
  1. **核心论点**（一句话可证伪的判断）
  2. **独特视角**（只有本透镜能看到的洞察，300-400 字）
  3. **评级**：星级 (1-5) + BUY/HOLD/PASS 判定 + 目标 IRR
  4. **触杀条件**：2-3 个本透镜视角下的可观测触发条件
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
    a structured markdown report. Writes to research_dir/full_report_{date}.md.

    Args:
        symbol: Stock ticker
        research_dir: Path containing all intermediate analysis files

    Returns:
        Complete report as markdown string
    """
    symbol = symbol.upper()
    date = datetime.now().strftime("%Y-%m-%d")

    # Read all sections (research files excluded from final report — they serve as lens input only)
    gemini = _read_research_file(research_dir, "gemini_contrarian.md")
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
        f"# {symbol} 深度研究报告",
        f"",
        f"**日期**: {date} | **分析师**: 未来资本 AI Trading Desk",
        f"",
        f"---",
        f"",
    ]

    # I. 五维透镜分析
    sections.append("## I. 五维透镜分析")
    sections.append("")
    if lens_qc:
        sections.append("### 1. 质量复利")
        sections.append(lens_qc)
        sections.append("")
    if lens_ig:
        sections.append("### 2. 想象力成长")
        sections.append(lens_ig)
        sections.append("")
    if lens_fls:
        sections.append("### 3. 基本面多空")
        sections.append(lens_fls)
        sections.append("")
    if lens_dv:
        sections.append("### 4. 深度价值")
        sections.append(lens_dv)
        sections.append("")
    if lens_ed:
        sections.append("### 5. 事件驱动")
        sections.append(lens_ed)
        sections.append("")

    # II. 核心辩论
    if debate:
        sections.append("## II. 核心辩论")
        sections.append(debate)
        sections.append("")

    # III. 投资备忘录
    if memo:
        sections.append("## III. 投资备忘录")
        sections.append(memo)
        sections.append("")

    # IV. OPRMS 评级与仓位
    if oprms:
        sections.append("## IV. OPRMS 评级与仓位")
        sections.append(oprms)
        sections.append("")

    # V. 第二层 — 求导思维
    if alpha_rt or alpha_cy or alpha_bet:
        sections.append("## V. 第二层 — 求导思维")
        sections.append("")
        if alpha_rt:
            sections.append("### 红队试炼")
            sections.append(alpha_rt)
            sections.append("")
        if gemini:
            sections.append("### Gemini 对立观点")
            sections.append(gemini)
            sections.append("")
        if alpha_cy:
            sections.append("### 周期钟摆")
            sections.append(alpha_cy)
            sections.append("")
        if alpha_bet:
            sections.append("### 非对称赌注")
            sections.append(alpha_bet)
            sections.append("")

    sections.append("---")
    sections.append(f"*Generated by 未来资本 AI Trading Desk — {date}*")

    report = "\n".join(sections)

    # Write to file (dated filename for version tracking)
    output_path = research_dir / f"full_report_{date}.md"
    output_path.write_text(report, encoding="utf-8")
    logger.info(f"Compiled deep report: {output_path} ({len(report)} chars)")

    # Generate HTML version
    try:
        from terminal.html_report import compile_html_report
        html_path = compile_html_report(symbol, research_dir, date=date)
        logger.info(f"Compiled HTML report: {html_path}")
    except Exception as e:
        logger.warning(f"HTML report generation failed: {e}")

    return report
