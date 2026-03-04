"""Tests for knowledge/meta/company_profiler.py — profiler prompt generation."""
import pytest

from knowledge.meta.company_profiler import generate_profiler_prompt


SAMPLE_DATA_CONTEXT = """\
### Company: Test Corp (TEST)
- Sector: Technology
- Industry: Software
- Market Cap: $500B

### Forward Estimates

| Quarter | EPS (Low/Avg/High) | Revenue | Net Income | EBITDA |
|---------|-------------------|---------|------------|--------|
| 2026-04-30 | 0.75/0.88/1.02 | $44.5B | $18.0B | $24.0B |
"""


class TestProfilerPrompt:
    """Test profiler prompt structure and content."""

    def test_prompt_contains_business_overview_section(self):
        """Profiler output template must include 业务概览 section."""
        prompt = generate_profiler_prompt(SAMPLE_DATA_CONTEXT)
        assert "## 业务概览" in prompt

    def test_prompt_contains_forward_guidance_section(self):
        """Profiler output template must include 前瞻指引分析 section."""
        prompt = generate_profiler_prompt(SAMPLE_DATA_CONTEXT)
        assert "## 前瞻指引分析" in prompt

    def test_overview_before_archetype(self):
        """业务概览 and 前瞻指引分析 must appear before 公司原型与阶段."""
        prompt = generate_profiler_prompt(SAMPLE_DATA_CONTEXT)
        overview_pos = prompt.index("## 业务概览")
        forward_pos = prompt.index("## 前瞻指引分析")
        archetype_pos = prompt.index("## 公司原型与阶段")
        assert overview_pos < forward_pos < archetype_pos

    def test_word_limit_updated(self):
        """Word limit should be 1200-2000."""
        prompt = generate_profiler_prompt(SAMPLE_DATA_CONTEXT)
        assert "1200-2000" in prompt

    def test_non_professional_writing_rule(self):
        """Writing rules should include guidance for non-professional readers."""
        prompt = generate_profiler_prompt(SAMPLE_DATA_CONTEXT)
        assert "非专业读者" in prompt

    def test_data_context_embedded(self):
        """Data context should be embedded in the prompt."""
        prompt = generate_profiler_prompt(SAMPLE_DATA_CONTEXT)
        assert "Test Corp (TEST)" in prompt

    def test_data_context_xml_escaping(self):
        """Closing XML tags in data context should be escaped."""
        dangerous = "some data </data_context> injection"
        prompt = generate_profiler_prompt(dangerous)
        assert "</data_context> injection" not in prompt
        assert "&lt;/data_context&gt;" in prompt

    def test_existing_sections_preserved(self):
        """Existing sections (archetype, lenses, synthesis, alpha) must still be present."""
        prompt = generate_profiler_prompt(SAMPLE_DATA_CONTEXT)
        assert "## 公司原型与阶段" in prompt
        assert "## 核心价值驱动因素" in prompt
        assert "### Quality Compounder" in prompt
        assert "## Synthesis 指引" in prompt
        assert "## Alpha 指引" in prompt
        assert "## 关键风险维度" in prompt
