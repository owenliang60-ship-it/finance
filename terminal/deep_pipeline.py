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


def build_synthesis_agent_prompt(research_dir: Path, symbol: str) -> str:
    """Build a self-contained prompt for the Phase 2 Synthesis agent.

    The agent reads 8 input files and writes debate.md, memo.md, oprms.md.
    This offloads ~100KB of context from the main Claude process.

    Args:
        research_dir: Path to research directory with all Phase 0-1 outputs
        symbol: Stock ticker

    Returns:
        Complete prompt string for a Task agent
    """
    symbol = symbol.upper()
    rd = str(research_dir)

    return f"""你是未来资本的高级投资分析师，正在执行 **{symbol}** 的综合研判。

## 第一步：阅读基础材料

阅读以下文件（缺失则跳过）：

**数据上下文：**
- `{rd}/data_context.md` — 财务数据、比率、技术指标、宏观环境

**五维透镜分析：**
- `{rd}/lens_quality_compounder.md` — 质量复利透镜
- `{rd}/lens_imaginative_growth.md` — 想象力成长透镜
- `{rd}/lens_fundamental_long_short.md` — 基本面多空透镜
- `{rd}/lens_deep_value.md` — 深度价值透镜
- `{rd}/lens_event_driven.md` — 事件驱动透镜

**研究素材：**
- `{rd}/earnings.md` — 最新财报要点
- `{rd}/competitive.md` — 竞争格局
- `{rd}/street.md` — 华尔街共识

## 第二步：核心辩论 (debate.md)

基于五维透镜分析，识别 **3 个核心张力/分歧**，然后对每个张力展开辩论：

**辩论格式**：
- 张力 1: [A vs B]
  - 多头论点 (Bull): 具体论据 + 数据支撑
  - 空头论点 (Bear): 具体反驳 + 数据支撑
  - 裁决: 哪方更有说服力，为什么
- 张力 2: [同上格式]
- 张力 3: [同上格式]
- **总裁决**: BUY / HOLD / SELL + 信心水平 (高/中/低)

**要求**：500+ 字，使用中文，辩论必须有血有肉（不是泛泛的"机会与风险并存"）

**写入文件**: `{rd}/debate.md`

## 第三步：投资备忘录 (memo.md)

基于辩论结论，撰写完整投资备忘录：

**备忘录结构**：
1. **执行摘要** (Executive Summary): 一段话概括结论和核心逻辑
2. **变异观点** (Variant View): 我们与市场共识的关键分歧是什么？
3. **关键力量** (Key Forces): 3-5 个驱动股价的核心力量，每个给方向 (↑/↓) 和权重
4. **估值** (Valuation): DCF 三情景 (悲观/基准/乐观)，给出具体假设和目标价
5. **风险框架** (Risk Framework): 关键风险 + 对冲/缓释策略
6. **仓位建议** (Position Recommendation): 具体建议 + 触发条件

**要求**：800+ 字，使用中文，DCF 必须有具体数字（不能只写"合理估值"）

**写入文件**: `{rd}/memo.md`

## 第四步：OPRMS 评级 (oprms.md)

基于辩论和备忘录，给出 OPRMS 评级：

**OPRMS 框架**：

Y 轴 — 资产基因 (DNA):
| 等级 | 名称 | 仓位上限 | 特征 |
|------|------|---------|------|
| S | 圣杯 | 25% | 改变人类进程的超级核心资产 |
| A | 猛将 | 15% | 强周期龙头，细分赛道霸主 |
| B | 黑马 | 7% | 强叙事驱动，赔率高但不确定 |
| C | 跟班 | 2% | 补涨逻辑，基本不做 |

X 轴 — 时机系数 (Timing):
| 等级 | 名称 | 系数范围 | 特征 |
|------|------|---------|------|
| S | 千载难逢 | 1.0-1.5 | 历史性时刻，暴跌坑底/突破 |
| A | 趋势确立 | 0.8-1.0 | 主升浪确认，右侧突破 |
| B | 正常波动 | 0.4-0.6 | 回调支撑，震荡 |
| C | 垃圾时间 | 0.1-0.3 | 左侧磨底，无催化剂 |

核心公式: 最终仓位 = 总资产 × DNA上限 × Timing系数

证据门槛: 每个评级必须列出 3+ 条具体证据

**输出格式**：
```
### OPRMS 评级 — {symbol}

**资产基因 (DNA)**: [S/A/B/C] — [名称]
- 理由: [2-3 句]
- 仓位上限: [X]%

**时机系数 (Timing)**: [S/A/B/C] — [名称]
- 系数: [X.X]
- 理由: [2-3 句]

**证据清单**:
1. [证据1]
2. [证据2]
3. [证据3]
...

**投资桶**: [Long-term Compounder / Catalyst-Driven Long / Watch / Pass]

**最终仓位**: DNA上限 [X]% × 时机系数 [X.X] = [Y.Y]%
(conviction_modifier 暂设 1.0，待 Alpha Layer 调整)
```

**写入文件**: `{rd}/oprms.md`

## 信息效率规则

- **禁止重复原始数据** — 研究文件和透镜分析已提供事实，你只需综合判断
- 引用数据时一句话概括即可
- 辩论中的论点必须来自透镜分析的洞察，不是泛泛的风险提示
"""


def build_alpha_agent_prompt(
    research_dir: Path,
    symbol: str,
    sector: str,
    current_price: float | None,
    l1_oprms: dict | None,
) -> str:
    """Build a self-contained prompt for the Phase 3 Alpha agent.

    The agent reads 5 input files and writes 3 alpha analysis files.
    Uses the actual framework text from the alpha generators as embedded prompts.

    Args:
        research_dir: Path to research directory with Phase 0-2 outputs
        symbol: Stock ticker
        sector: Company sector
        current_price: Latest stock price
        l1_oprms: Current OPRMS rating dict (or None)

    Returns:
        Complete prompt string for a Task agent
    """
    from knowledge.alpha.red_team import generate_red_team_prompt
    from knowledge.alpha.cycle_pendulum import generate_cycle_prompt
    from knowledge.alpha.asymmetric_bet import generate_bet_prompt

    symbol = symbol.upper()
    rd = str(research_dir)

    # Generate framework prompts with placeholder markers
    red_team_framework = generate_red_team_prompt(
        symbol=symbol,
        memo_summary="<<PLACEHOLDER: 从 memo.md 提取执行摘要段落>>",
        l1_verdict="<<PLACEHOLDER: 从 debate.md 提取最终 BUY/HOLD/SELL 判定>>",
        l1_key_forces="<<PLACEHOLDER: 从 memo.md 提取关键力量列表>>",
        data_context="<<PLACEHOLDER: 从 data_context.md 读取全部内容>>",
    )

    cycle_framework = generate_cycle_prompt(
        symbol=symbol,
        sector=sector,
        data_context="<<PLACEHOLDER: 从 data_context.md 读取全部内容>>",
        red_team_summary="<<PLACEHOLDER: 用你在第二步写的 alpha_red_team.md 内容>>",
        macro_briefing="<<PLACEHOLDER: 从 data_context.md 中提取宏观相关段落>>",
    )

    bet_framework = generate_bet_prompt(
        symbol=symbol,
        data_context="<<PLACEHOLDER: 从 data_context.md 读取全部内容>>",
        red_team_summary="<<PLACEHOLDER: 用你在第二步写的 alpha_red_team.md 内容>>",
        cycle_summary="<<PLACEHOLDER: 用你在第三步写的 alpha_cycle.md 内容>>",
        l1_oprms=l1_oprms,
        l1_verdict="<<PLACEHOLDER: 从 debate.md 提取最终 BUY/HOLD/SELL 判定>>",
        current_price=current_price,
    )

    # Format OPRMS context for reference
    if l1_oprms:
        oprms_ref = (
            f"当前 L1 OPRMS: DNA={l1_oprms.get('dna', 'N/A')}, "
            f"Timing={l1_oprms.get('timing', 'N/A')}, "
            f"Coeff={l1_oprms.get('timing_coeff', 'N/A')}"
        )
    else:
        oprms_ref = "无现有 OPRMS 评级（首次分析）"

    return f"""你是未来资本的 Layer 2 求导思维分析师，正在执行 **{symbol}** 的第二层深度分析。

{oprms_ref}

## 第一步：阅读基础材料

阅读以下文件：
- `{rd}/data_context.md` — 财务数据、宏观环境
- `{rd}/debate.md` — L1 核心辩论和最终判定
- `{rd}/memo.md` — L1 投资备忘录
- `{rd}/oprms.md` — L1 OPRMS 评级
- `{rd}/gemini_contrarian.md` — Gemini 对立观点（如存在）

从这些文件中提取：
- **l1_verdict**: debate.md 中的最终判定 (BUY/HOLD/SELL)
- **memo_summary**: memo.md 中的执行摘要段落
- **l1_key_forces**: memo.md 中的关键力量列表
- **data_context**: data_context.md 全文

## 第二步：红队试炼 (alpha_red_team.md)

以下是红队试炼的完整框架。将其中的 <<PLACEHOLDER: ...>> 替换为你从第一步文件中提取的实际内容，然后执行分析。

如果 gemini_contrarian.md 存在，将 Gemini 的对立观点融入你的攻击中（哪些地方 Gemini 的攻击更尖锐，采纳之）。

---
{red_team_framework}
---

**要求**：500+ 字，使用中文
**写入文件**: `{rd}/alpha_red_team.md`

## 第三步：周期钟摆 (alpha_cycle.md)

以下是周期钟摆的完整框架。将 <<PLACEHOLDER: ...>> 替换为实际内容（红队摘要用你第二步的输出）。

---
{cycle_framework}
---

**要求**：500+ 字，使用中文
**写入文件**: `{rd}/alpha_cycle.md`

## 第四步：非对称赌注 (alpha_bet.md)

以下是非对称赌注的完整框架。将 <<PLACEHOLDER: ...>> 替换为实际内容。

---
{bet_framework}
---

**要求**：500+ 字，使用中文
**写入文件**: `{rd}/alpha_bet.md`

## 第五步：更新 OPRMS conviction_modifier

在 alpha_bet.md 的最终判决中，你会给出一个 conviction_modifier (0.5-1.5)。
读取 `{rd}/oprms.md`，在文件末尾追加：

```
**Alpha Layer 调整**:
- conviction_modifier: [你给出的值]
- 调整后仓位: 原始仓位 × conviction_modifier = [新仓位]%
- 调整理由: [一句话]
```

将更新后的内容写回 `{rd}/oprms.md`。

## 输出要求

- **所有输出使用中文**（金融术语可用英文括注）
- **三个文件必须按顺序完成**（红队 → 周期 → 赌注），后一步依赖前一步的输出
- 每个文件 500+ 字，追求信息密度
"""


def write_agent_prompts(
    research_dir: Path,
    lens_agent_prompts: List[Dict[str, str]],
    gemini_prompt: str,
    synthesis_prompt: str,
    alpha_prompt: str,
) -> Dict[str, Any]:
    """Write all agent prompts to files, return path references.

    This keeps large prompt strings on disk instead of in the main
    Claude context window (~29KB saved).

    Args:
        research_dir: Path to research directory
        lens_agent_prompts: List of {lens_name, agent_prompt, output_path}
        gemini_prompt: Gemini contrarian prompt string
        synthesis_prompt: Phase 2 synthesis agent prompt string
        alpha_prompt: Phase 3 alpha agent prompt string

    Returns:
        Dict with prompt file paths:
        - lens_prompt_paths: [{lens_name, prompt_path, output_path}]
        - gemini_prompt_path: str
        - synthesis_prompt_path: str
        - alpha_prompt_path: str
    """
    prompts_dir = research_dir / "prompts"
    prompts_dir.mkdir(exist_ok=True)

    # Lens prompts
    lens_paths = []
    for lp in lens_agent_prompts:
        slug = _slugify(lp["lens_name"])
        prompt_path = prompts_dir / f"lens_{slug}.txt"
        prompt_path.write_text(lp["agent_prompt"], encoding="utf-8")
        lens_paths.append({
            "lens_name": lp["lens_name"],
            "prompt_path": str(prompt_path),
            "output_path": lp["output_path"],
        })

    # Gemini prompt
    gemini_path = prompts_dir / "gemini.txt"
    gemini_path.write_text(gemini_prompt, encoding="utf-8")

    # Synthesis prompt
    synthesis_path = prompts_dir / "synthesis.txt"
    synthesis_path.write_text(synthesis_prompt, encoding="utf-8")

    # Alpha prompt
    alpha_path = prompts_dir / "alpha.txt"
    alpha_path.write_text(alpha_prompt, encoding="utf-8")

    logger.info(
        f"Wrote {len(lens_paths) + 3} prompt files to {prompts_dir}"
    )

    return {
        "lens_prompt_paths": lens_paths,
        "gemini_prompt_path": str(gemini_path),
        "synthesis_prompt_path": str(synthesis_path),
        "alpha_prompt_path": str(alpha_path),
    }


def _read_research_file(research_dir: Path, filename: str) -> str:
    """Read a research file, returning empty string if missing."""
    path = research_dir / filename
    if path.exists():
        return path.read_text(encoding="utf-8")
    return ""


def _extract_summary_from_sections(
    oprms: str, memo: str, debate: str, alpha_bet: str,
) -> str:
    """Extract key metrics from analysis files for report_summary.md.

    Pulls OPRMS rating, verdict, and key conclusions into ~3KB summary.
    """
    lines = []

    # OPRMS rating
    if oprms:
        lines.append("## OPRMS 评级")
        lines.append("")
        # Extract just the rating block (first ~30 lines usually sufficient)
        oprms_lines = oprms.strip().split("\n")
        for line in oprms_lines[:40]:
            lines.append(line)
        lines.append("")

    # Debate verdict (last paragraph or "总裁决" section)
    if debate:
        lines.append("## 辩论总裁决")
        lines.append("")
        # Find verdict section
        verdict_found = False
        for i, line in enumerate(debate.split("\n")):
            if any(kw in line for kw in ["总裁决", "总体判定", "Overall Verdict", "最终判定"]):
                verdict_found = True
                # Include this line + next 5
                for vl in debate.split("\n")[i:i + 6]:
                    lines.append(vl)
                break
        if not verdict_found:
            # Fallback: last 5 lines
            for vl in debate.strip().split("\n")[-5:]:
                lines.append(vl)
        lines.append("")

    # Memo executive summary (first paragraph after header)
    if memo:
        lines.append("## 执行摘要")
        lines.append("")
        memo_lines = memo.strip().split("\n")
        in_summary = False
        summary_count = 0
        for line in memo_lines:
            if any(kw in line for kw in ["执行摘要", "Executive Summary"]):
                in_summary = True
                continue
            if in_summary:
                if line.startswith("##") or line.startswith("**") and summary_count > 2:
                    break
                lines.append(line)
                summary_count += 1
                if summary_count >= 8:
                    break
        lines.append("")

    # Alpha bet conclusion
    if alpha_bet:
        lines.append("## 非对称赌注结论")
        lines.append("")
        bet_lines = alpha_bet.strip().split("\n")
        # Last 8 lines typically contain the verdict
        for bl in bet_lines[-8:]:
            lines.append(bl)
        lines.append("")

    return "\n".join(lines)


def compile_deep_report(symbol: str, research_dir: Path) -> str:
    """Compile all research files into a single deep analysis report.

    Reads all files from research_dir and assembles them into
    a structured markdown report. Writes to research_dir/full_report_{date}.md.
    Also generates report_summary.md (~3KB) for lightweight consumption.

    Args:
        symbol: Stock ticker
        research_dir: Path containing all intermediate analysis files

    Returns:
        Path to the compiled report file (string)
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

    # Write report summary (~3KB) for Heptabase + user display
    summary = _extract_summary_from_sections(oprms, memo, debate, alpha_bet)
    summary_header = (
        f"# {symbol} 深度分析摘要\n\n"
        f"**日期**: {date} | **完整报告**: `{output_path.name}`\n\n---\n\n"
    )
    summary_path = research_dir / "report_summary.md"
    summary_path.write_text(summary_header + summary, encoding="utf-8")
    logger.info(f"Wrote report summary: {summary_path} ({len(summary)} chars)")

    # Generate HTML version
    try:
        from terminal.html_report import compile_html_report
        html_path = compile_html_report(symbol, research_dir, date=date)
        logger.info(f"Compiled HTML report: {html_path}")
    except Exception as e:
        logger.warning(f"HTML report generation failed: {e}")

    return str(output_path)
