"""Tests for Alpha Debate — Phase 4 prompt generation."""
import pytest

from knowledge.alpha.debate import generate_alpha_debate_prompt


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _basic_prompt(**kwargs):
    """Generate a basic debate prompt with defaults."""
    defaults = {
        "symbol": "NVDA",
        "research_dir_str": "/tmp/research/NVDA",
        "rounds": 2,
        "past_experiences": "",
    }
    defaults.update(kwargs)
    return generate_alpha_debate_prompt(**defaults)


# ===========================================================================
# TestDebatePromptBasics
# ===========================================================================

class TestDebatePromptBasics:
    def test_contains_symbol(self):
        prompt = _basic_prompt(symbol="AAPL")
        assert "AAPL" in prompt

    def test_symbol_uppercased(self):
        prompt = _basic_prompt(symbol="aapl")
        assert "AAPL" in prompt

    def test_contains_research_dir(self):
        prompt = _basic_prompt(research_dir_str="/data/research/NVDA")
        assert "/data/research/NVDA" in prompt

    def test_returns_string(self):
        prompt = _basic_prompt()
        assert isinstance(prompt, str)
        assert len(prompt) > 100


# ===========================================================================
# TestDebatePromptPersonas
# ===========================================================================

class TestDebatePromptPersonas:
    def test_contains_soros_persona(self):
        prompt = _basic_prompt()
        assert "索罗斯" in prompt

    def test_contains_marks_persona(self):
        prompt = _basic_prompt()
        assert "马克斯" in prompt

    def test_soros_action_bias(self):
        """Soros persona emphasizes action and reflexivity."""
        prompt = _basic_prompt()
        assert "反身性" in prompt or "不确定性" in prompt

    def test_marks_patience_bias(self):
        """Marks persona emphasizes cycles and waiting."""
        prompt = _basic_prompt()
        assert "周期" in prompt


# ===========================================================================
# TestDebatePromptStructure
# ===========================================================================

class TestDebatePromptStructure:
    def test_file_reading_instructions(self):
        """Prompt instructs agent to read research files."""
        prompt = _basic_prompt()
        assert "data_context.md" in prompt
        assert "oprms.md" in prompt
        assert "alpha_red_team.md" in prompt
        assert "alpha_cycle.md" in prompt
        assert "alpha_bet.md" in prompt

    def test_default_two_rounds(self):
        prompt = _basic_prompt(rounds=2)
        assert "交换 1/2" in prompt
        assert "交换 2/2" in prompt

    def test_single_round(self):
        prompt = _basic_prompt(rounds=1)
        assert "交换 1/1" in prompt
        assert "交换 2/" not in prompt

    def test_three_rounds(self):
        prompt = _basic_prompt(rounds=3)
        assert "交换 1/3" in prompt
        assert "交换 2/3" in prompt
        assert "交换 3/3" in prompt

    def test_referee_synthesis(self):
        """Prompt includes referee synthesis section."""
        prompt = _basic_prompt()
        assert "裁判" in prompt
        assert "final_conviction_modifier" in prompt
        assert "final_action" in prompt

    def test_conviction_modifier_range(self):
        """Prompt specifies the 0.5-1.5 range."""
        prompt = _basic_prompt()
        assert "0.5" in prompt
        assert "1.5" in prompt

    def test_action_options(self):
        """Prompt specifies 执行/搁置/放弃."""
        prompt = _basic_prompt()
        assert "执行" in prompt
        assert "搁置" in prompt
        assert "放弃" in prompt

    def test_oprms_update_instructions(self):
        """Prompt tells agent to update oprms.md."""
        prompt = _basic_prompt()
        assert "oprms.md" in prompt
        assert "Alpha Debate 调整" in prompt

    def test_output_file_specified(self):
        """Prompt specifies alpha_debate.md as output."""
        prompt = _basic_prompt()
        assert "alpha_debate.md" in prompt

    def test_word_count_guidance(self):
        """Prompt gives word count guidance."""
        prompt = _basic_prompt()
        assert "1500" in prompt or "3000" in prompt


# ===========================================================================
# TestDebatePromptMemory
# ===========================================================================

class TestDebatePromptMemory:
    def test_no_memory_block_when_empty(self):
        """No memory section when past_experiences is empty."""
        prompt = _basic_prompt(past_experiences="")
        assert "历史经验 (Agent Memory)" not in prompt

    def test_memory_block_injected(self):
        """Memory block is injected when past_experiences provided."""
        memory = "### 历史分析 #1\n- 价格: $800\n- 行动: 执行"
        prompt = _basic_prompt(past_experiences=memory)
        assert "历史经验 (Agent Memory)" in prompt
        assert "$800" in prompt

    def test_memory_instructions(self):
        """Memory block includes guidance to reference history."""
        memory = "### 历史分析 #1\n- 结论: BUY"
        prompt = _basic_prompt(past_experiences=memory)
        assert "验证" in prompt or "推翻" in prompt
