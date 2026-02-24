"""
Alpha Debate — 索罗斯 vs 马克斯终极辩论 prompt generator.

Phase 4 of the Alpha Layer: After red team, cycle, and bet analyses,
two personas exchange arguments over N rounds, then a referee synthesizes
the final verdict.

Structure (adapted from AIgora debate protocol):
  - N exchanges (each: Soros states → Marks responds)
  - 裁判综合: core claims → consensus → disagreements → final verdict

Soros (行动派): Reflexivity, time windows, asymmetric opportunity
Marks (耐心派): Cycle position, crowd behavior, option value of waiting
"""
import logging
from pathlib import Path

logger = logging.getLogger(__name__)


def generate_alpha_debate_prompt(
    symbol: str,
    research_dir_str: str,
    rounds: int = 2,
    past_experiences: str = "",
) -> str:
    """Generate the Alpha Debate prompt for LLM execution.

    The agent reads all research files, runs a structured N-round debate
    between Soros and Marks personas, then synthesizes a final verdict.

    Args:
        symbol: Stock ticker (uppercased)
        research_dir_str: Path string to research directory
        rounds: Number of debate exchanges (default 3)
        past_experiences: Formatted past experiences block (from memory)

    Returns:
        Complete prompt string for a Task agent
    """
    symbol = symbol.upper()
    rd = research_dir_str

    # Build past experiences section
    memory_block = ""
    if past_experiences:
        memory_block = (
            "\n## 历史经验 (Agent Memory)\n\n"
            "以下是对该标的过往分析的记忆。参考历史判断，注意哪些被验证、"
            "哪些被推翻，以及市场环境的变化。\n\n"
            + past_experiences
            + "\n"
        )

    # Build per-exchange instructions
    exchange_blocks = []
    for r in range(1, rounds + 1):
        if r == 1:
            soros_task = (
                "基于 `alpha_bet.md` 的核心赌注结构，陈述为什么**现在**必须行动：\n"
                "- 反身性循环正在形成的具体证据\n"
                "- 时间窗口为什么正在关闭（结构性，不是情绪性）\n"
                "- 赌注结构如何覆盖 `alpha_red_team.md` 的主要攻击\n"
                "- 等待的真实机会成本"
            )
            marks_task = (
                "基于 `alpha_cycle.md` 的周期定位，反驳索罗斯的行动论：\n"
                "- 当前钟摆分数是否真的支持行动？引用具体数据\n"
                "- 历史上类似周期位置的行动结果\n"
                "- 等待的 option value：市场正在免费给你什么期权？\n"
                "- 索罗斯的「时间窗口」是真实紧迫还是 FOMO？"
            )
        elif r < rounds:
            soros_task = (
                "回应马克斯的周期担忧，深化你的论证：\n"
                "- 承认哪些周期风险是真实的（不要回避）\n"
                "- 解释为什么当前的非对称性足以覆盖这些风险\n"
                "- 提出具体的风险管理方案（仓位结构、止损、分批建仓）"
            )
            marks_task = (
                "回应索罗斯的风险管理方案，指出其不足：\n"
                "- 仓位结构真的能管理尾部风险吗？\n"
                "- 反身性是刀刃，也会向下反转——你如何区分信号与噪音？\n"
                "- 在你的视角下，什么样的信号才能让你点头？"
            )
        else:
            soros_task = (
                "最终陈词：用最简洁的语言给出你的核心论点和行动建议。\n"
                "- 如果只能说一件事，是什么？\n"
                "- 你愿意用多大仓位为这个判断下注？"
            )
            marks_task = (
                "最终陈词：给出你的最终风险评估和立场。\n"
                "- 索罗斯说服了你什么？哪些担忧仍然存在？\n"
                "- 如果必须做出行动建议，你的答案是什么？"
            )

        block = (
            f"─── 交换 {r}/{rounds} ───\n\n"
            f"**索罗斯**（行动派）:\n{soros_task}\n\n"
            f"**马克斯**（耐心派）:\n{marks_task}"
        )
        exchange_blocks.append(block)

    exchanges_text = "\n\n".join(exchange_blocks)

    prompt = (
        f"你是未来资本的终极辩论裁判，主持 **{symbol}** 的行动派 vs 耐心派对决。\n\n"
        "## 人格设定\n\n"
        "**索罗斯（行动派）**:\n"
        "「不确定性中才有 alpha，等确定了就没赔率了。」\n"
        "关注：反身性循环、非对称赌注结构、执行时间表、市场结构性错误定价。\n"
        "偏好：在不确定中行动，用仓位结构管理风险，而非回避风险。\n\n"
        "**马克斯（耐心派）**:\n"
        "「市场周期决定一切，90% 的时间不应该行动。」\n"
        "关注：周期位置、拥挤度、均值回归力量、等待的 option value。\n"
        "偏好：只在极端错误定价时行动，大部分时间保持耐心。\n\n"
        "## 第一步：阅读全部研究材料\n\n"
        f"**仔细阅读**以下文件（缺失则跳过）：\n"
        f"- `{rd}/data_context.md` — 财务数据、宏观环境\n"
        f"- `{rd}/debate.md` — L1 核心辩论\n"
        f"- `{rd}/memo.md` — 投资备忘录\n"
        f"- `{rd}/oprms.md` — OPRMS 评级\n"
        f"- `{rd}/alpha_red_team.md` — 红队试炼（索罗斯需要回应的攻击）\n"
        f"- `{rd}/alpha_cycle.md` — 周期钟摆（马克斯的核心武器）\n"
        f"- `{rd}/alpha_bet.md` — 非对称赌注（索罗斯的核心武器）\n"
        f"- `{rd}/gemini_contrarian.md` — Gemini 对立观点（如存在）\n"
        f"{memory_block}\n"
        f"## 第二步：{rounds} 轮辩论交换\n\n"
        "两位思想者基于各自的框架展开正面交锋。\n"
        "**要求**：每人每轮 200-350 字，必须具体引用研究文件中的数据和论点，"
        "不能泛泛而谈。\n\n"
        f"{exchanges_text}\n\n"
        "## 第三步：裁判综合\n\n"
        "你作为裁判，综合整场辩论，给出结构化的最终判决。\n\n"
        "### 各方核心论点\n\n"
        "- **索罗斯的核心论点**（一句话）：\n"
        "- **马克斯的核心论点**（一句话）：\n\n"
        "### 共识\n\n"
        "两人都认同的事实或前提（即使结论不同）：\n"
        "- ...\n\n"
        "### 分歧地图\n\n"
        "| # | 分歧点 | 索罗斯立场 | 马克斯立场 | 裁判评估 |\n"
        "|---|--------|-----------|-----------|----------|\n"
        "| 1 | [核心分歧] | [索罗斯论据] | [马克斯论据] | [哪方更有说服力/未解决] |\n"
        "| 2 | ... | ... | ... | ... |\n\n"
        "### 最终判决\n\n"
        "基于辩论结果，给出明确的行动指引：\n\n"
        "- **谁更有说服力**：索罗斯 / 马克斯 / 势均力敌（给出理由）\n"
        "- **final_conviction_modifier**：给出 0.5-1.5 的信念调整系数\n"
        "  - 0.5-0.7：马克斯完胜，大幅降低仓位\n"
        "  - 0.8-0.9：马克斯略胜，谨慎降低\n"
        "  - 1.0：势均力敌，维持原判\n"
        "  - 1.1-1.2：索罗斯略胜，适度加码\n"
        "  - 1.3-1.5：索罗斯完胜，大幅加码\n"
        "- **final_action**：执行 / 搁置 / 放弃\n"
        "  - 执行：立即按赌注结构建仓\n"
        "  - 搁置：等待具体触发条件（需说明是什么条件）\n"
        "  - 放弃：放弃这个标的（需说明原因）\n\n"
        "## 第四步：更新 OPRMS\n\n"
        f"读取 `{rd}/oprms.md`，在文件末尾追加：\n\n"
        "```\n"
        "**Alpha Debate 调整**:\n"
        "- debate_conviction_modifier: [你给出的值]\n"
        "- 调整后仓位: 原始仓位 × debate_conviction_modifier = [新仓位]%\n"
        "- 核心分歧: [一句话]\n"
        "- final_action: [执行/搁置/放弃]\n"
        "- 此 conviction_modifier 覆盖 alpha_bet 的值（辩论是最终裁决）\n"
        "```\n\n"
        f"将更新后的内容写回 `{rd}/oprms.md`。\n\n"
        "## 输出要求\n\n"
        f"- **全部辩论和综合内容写入**：`{rd}/alpha_debate.md`\n"
        "- 使用中文撰写（金融术语可用英文括注）\n"
        "- 辩论必须有血有肉——具体引用研究文件中的数据和论点，不允许空洞的套话\n"
        f"- 总篇幅 1500-3000 字（{rounds} 轮辩论 + 综合）\n"
    )

    return prompt
