"""
Alpha Debate — 索罗斯 vs 马克斯终极辩论 prompt generator.

Phase 4 of the Alpha Layer: After red team, cycle, and bet analyses,
two personas debate whether to act now or wait.

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

    The agent reads all research files, then simulates a structured
    debate between Soros and Marks personas, concluding with a
    referee synthesis.

    Args:
        symbol: Stock ticker (uppercased)
        research_dir_str: Path string to research directory
        rounds: Number of debate rounds (default 2)
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

    # Build round instructions
    round_instructions = []
    for r in range(1, rounds + 1):
        if r == 1:
            round_instructions.append(
                "### Round %d\n\n"
                "**索罗斯开局**:\n"
                "基于 alpha_bet.md 的核心洞见和赌注结构，阐述为什么**现在**必须行动：\n"
                "- 反身性循环正在形成的证据是什么？\n"
                "- 时间窗口为什么正在关闭？\n"
                "- 赌注结构如何化解红队攻击的主要风险？\n"
                "- 等待的真实成本是什么？（错过的不是价格，是结构）\n\n"
                "**马克斯回应**:\n"
                "基于 alpha_cycle.md 的周期定位，反驳索罗斯的行动论：\n"
                "- 当前周期位置（钟摆分数）真的支持行动吗？\n"
                "- 历史上类似周期位置的行动结果如何？\n"
                "- 等待的 option value 有多大？（市场给你的免费看跌期权）\n"
                "- 索罗斯的「时间窗口」是真实紧迫还是 FOMO？\n"
                % r
            )
        else:
            round_instructions.append(
                "### Round %d\n\n"
                "**索罗斯反驳**:\n"
                "回应马克斯的周期担忧：\n"
                "- 承认哪些周期风险是真实的\n"
                "- 解释为什么时间窗口和非对称性足以覆盖这些风险\n"
                "- 提出具体的风险管理方案（不是忽视风险，而是管理风险）\n\n"
                "**马克斯最终立场**:\n"
                "给出最终风险评估：\n"
                "- 索罗斯说服了你什么？\n"
                "- 哪些担忧仍然存在？\n"
                "- 如果必须给建议，你的最终立场是什么？\n"
                % r
            )

    rounds_text = "\n".join(round_instructions)

    prompt = (
        "你是未来资本的终极辩论裁判，正在主持 **{symbol}** 的行动派 vs 耐心派终极对决。\n\n"
        "## 人格设定\n\n"
        "**索罗斯（行动派）**:\n"
        "「不确定性中才有 alpha，等确定了就没赔率了。」\n"
        "关注：反身性循环、非对称赌注结构、执行时间表、市场结构性错误定价。\n"
        "偏好：在不确定中行动，用仓位结构管理风险，而非回避风险。\n\n"
        "**马克斯（耐心派）**:\n"
        "「市场周期决定一切，90%% 的时间不应该行动。」\n"
        "关注：周期位置、拥挤度、均值回归力量、等待的 option value。\n"
        "偏好：只在极端错误定价时行动，大部分时间保持现金。\n\n"
        "## 第一步：阅读全部研究材料\n\n"
        "**仔细阅读**以下文件（缺失则跳过）：\n"
        "- `{rd}/data_context.md` — 财务数据、宏观环境\n"
        "- `{rd}/debate.md` — L1 核心辩论\n"
        "- `{rd}/memo.md` — 投资备忘录\n"
        "- `{rd}/oprms.md` — OPRMS 评级\n"
        "- `{rd}/alpha_red_team.md` — 红队试炼（索罗斯需要回应的攻击）\n"
        "- `{rd}/alpha_cycle.md` — 周期钟摆（马克斯的核心武器）\n"
        "- `{rd}/alpha_bet.md` — 非对称赌注（索罗斯的核心武器）\n"
        "- `{rd}/gemini_contrarian.md` — Gemini 对立观点（如存在）\n"
        "{memory_block}"
        "\n## 第二步：终极辩论\n\n"
        "基于上述材料，展开 {rounds} 轮辩论：\n\n"
        "{rounds_text}"
        "\n## 第三步：裁判综合\n\n"
        "作为裁判，你必须给出最终判决：\n\n"
        "1. **核心分歧**: 一句话概括索罗斯和马克斯的根本分歧\n"
        "2. **谁更有说服力**: 在这个具体案例中，谁的论证更有力？为什么？\n"
        "3. **final_conviction_modifier**: 给出 0.5-1.5 的信念调整系数\n"
        "   - 0.5-0.7: 马克斯完胜，大幅降低仓位\n"
        "   - 0.8-0.9: 马克斯略胜，谨慎降低\n"
        "   - 1.0: 平局，维持原判\n"
        "   - 1.1-1.2: 索罗斯略胜，适度加码\n"
        "   - 1.3-1.5: 索罗斯完胜，大幅加码\n"
        "4. **final_action**: 执行 / 搁置 / 放弃\n"
        "   - 执行: 立即按赌注结构建仓\n"
        "   - 搁置: 等待具体触发条件\n"
        "   - 放弃: 放弃这个标的\n"
        "5. **key_disagreement**: 核心分歧的一句话总结\n\n"
        "## 第四步：更新 OPRMS\n\n"
        "读取 `{rd}/oprms.md`，在文件末尾追加：\n\n"
        "```\n"
        "**Alpha Debate 调整**:\n"
        "- debate_conviction_modifier: [你给出的值]\n"
        "- 调整后仓位: 原始仓位 × debate_conviction_modifier = [新仓位]%%\n"
        "- 核心分歧: [一句话]\n"
        "- final_action: [执行/搁置/放弃]\n"
        "- 此 conviction_modifier 覆盖 alpha_bet 的值（辩论是最终裁决）\n"
        "```\n\n"
        "将更新后的内容写回 `{rd}/oprms.md`。\n\n"
        "## 输出要求\n\n"
        "- **全部输出写入**: `{rd}/alpha_debate.md`\n"
        "- 使用中文撰写（金融术语可用英文括注）\n"
        "- 辩论必须有血有肉 — 具体引用研究文件中的数据和论点\n"
        "- 每轮每人 200-400 字，裁判综合 300-500 字\n"
        "- 总篇幅 1500-3000 字\n"
    ).format(
        symbol=symbol,
        rd=rd,
        memory_block=memory_block,
        rounds=rounds,
        rounds_text=rounds_text,
    )

    return prompt
