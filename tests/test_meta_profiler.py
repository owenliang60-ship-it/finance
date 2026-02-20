"""Tests for knowledge.meta.company_profiler and build_profiler_prompt()."""
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


# ── Test generate_profiler_prompt() ──────────────────────────────────────

class TestGenerateProfilerPrompt:
    """Tests for the meta-prompt generator."""

    def test_returns_string(self):
        from knowledge.meta.company_profiler import generate_profiler_prompt

        result = generate_profiler_prompt("Revenue: $100B, ROIC: 25%")
        assert isinstance(result, str)

    def test_contains_archetype_table(self):
        from knowledge.meta.company_profiler import generate_profiler_prompt

        result = generate_profiler_prompt("dummy data")
        assert "未盈利探索者" in result
        assert "超级成长股" in result
        assert "成熟复利机器" in result
        assert "周期龙头" in result
        assert "困境反转" in result
        assert "平台型生态" in result
        assert "资产密集型" in result

    def test_contains_lens_defaults_table(self):
        from knowledge.meta.company_profiler import generate_profiler_prompt

        result = generate_profiler_prompt("dummy data")
        assert "Quality Compounder" in result
        assert "Imaginative Growth" in result
        assert "Fundamental L/S" in result
        assert "Deep Value" in result
        assert "Event-Driven" in result

    def test_contains_output_structure(self):
        from knowledge.meta.company_profiler import generate_profiler_prompt

        result = generate_profiler_prompt("dummy data")
        # All required sections from the plan
        assert "公司原型与阶段" in result
        assert "核心价值驱动因素" in result
        assert "各透镜个性化指引" in result
        assert "Synthesis 指引" in result
        assert "Alpha 指引" in result
        assert "关键风险维度" in result

    def test_injects_data_context(self):
        from knowledge.meta.company_profiler import generate_profiler_prompt

        marker = "UNIQUE_DATA_MARKER_XYZ"
        result = generate_profiler_prompt(marker)
        assert marker in result

    def test_chinese_output_requirement(self):
        from knowledge.meta.company_profiler import generate_profiler_prompt

        result = generate_profiler_prompt("dummy")
        assert "使用中文" in result

    def test_word_count_requirement(self):
        from knowledge.meta.company_profiler import generate_profiler_prompt

        result = generate_profiler_prompt("dummy")
        assert "800" in result  # minimum word count


# ── Test build_profiler_prompt() ─────────────────────────────────────────

class TestBuildProfilerPrompt:
    """Tests for the pipeline-level prompt builder."""

    def test_returns_string(self, tmp_path):
        from terminal.deep_pipeline import build_profiler_prompt

        ctx = tmp_path / "data_context.md"
        ctx.write_text("Revenue: $100B", encoding="utf-8")

        result = build_profiler_prompt(tmp_path, ctx)
        assert isinstance(result, str)

    def test_embeds_actual_data_context(self, tmp_path):
        """Data context is embedded directly — no placeholder substitution."""
        from terminal.deep_pipeline import build_profiler_prompt

        ctx = tmp_path / "data_context.md"
        ctx.write_text("UNIQUE_REVENUE_MARKER: $100B", encoding="utf-8")

        result = build_profiler_prompt(tmp_path, ctx)
        assert "UNIQUE_REVENUE_MARKER: $100B" in result
        # No placeholder pattern should remain
        assert "<<PLACEHOLDER" not in result
        assert "<<DATA_CONTEXT" not in result

    def test_references_output_path(self, tmp_path):
        from terminal.deep_pipeline import build_profiler_prompt

        ctx = tmp_path / "data_context.md"
        ctx.write_text("dummy", encoding="utf-8")

        result = build_profiler_prompt(tmp_path, ctx)
        assert "company_profile.md" in result

    def test_contains_archetype_guidance(self, tmp_path):
        from terminal.deep_pipeline import build_profiler_prompt

        ctx = tmp_path / "data_context.md"
        ctx.write_text("dummy data", encoding="utf-8")

        result = build_profiler_prompt(tmp_path, ctx)
        assert "公司原型" in result
        assert "各透镜个性化指引" in result


# ── Test lens prompt includes company_profile.md reference ───────────────

class TestLensPromptIncludesProfile:
    """Verify lens agent prompts now reference company_profile.md."""

    def _make_lens_dict(self):
        return {
            "lens_name": "Quality Compounder",
            "horizon": "5Y+",
            "core_metric": "ROIC",
            "prompt": "Analyze quality compounding.",
        }

    def test_lens_prompt_references_company_profile(self, tmp_path):
        from terminal.deep_pipeline import build_lens_agent_prompt

        result = build_lens_agent_prompt(self._make_lens_dict(), tmp_path)
        assert "company_profile.md" in result

    def test_lens_prompt_profile_is_first_file(self, tmp_path):
        from terminal.deep_pipeline import build_lens_agent_prompt

        result = build_lens_agent_prompt(self._make_lens_dict(), tmp_path)
        # company_profile.md should appear before data_context.md
        profile_pos = result.index("company_profile.md")
        context_pos = result.index("data_context.md")
        assert profile_pos < context_pos

    def test_lens_prompt_has_personalization_instruction(self, tmp_path):
        from terminal.deep_pipeline import build_lens_agent_prompt

        result = build_lens_agent_prompt(self._make_lens_dict(), tmp_path)
        assert "个性化指引" in result


# ── Test synthesis prompt includes company_profile.md reference ──────────

class TestSynthesisPromptIncludesProfile:
    """Verify synthesis agent prompt now references company_profile.md."""

    def test_synthesis_prompt_references_company_profile(self, tmp_path):
        from terminal.deep_pipeline import build_synthesis_agent_prompt

        result = build_synthesis_agent_prompt(tmp_path, "TEST")
        assert "company_profile.md" in result

    def test_synthesis_prompt_has_guidance_reference(self, tmp_path):
        from terminal.deep_pipeline import build_synthesis_agent_prompt

        result = build_synthesis_agent_prompt(tmp_path, "TEST")
        assert "Synthesis 指引" in result


# ── Test alpha prompt includes company_profile.md reference ──────────────

class TestAlphaPromptIncludesProfile:
    """Verify alpha agent prompt now references company_profile.md."""

    def test_alpha_prompt_references_company_profile(self, tmp_path):
        from terminal.deep_pipeline import build_alpha_agent_prompt

        result = build_alpha_agent_prompt(
            research_dir=tmp_path,
            symbol="TEST",
            sector="Technology",
            current_price=100.0,
            l1_oprms=None,
        )
        assert "company_profile.md" in result

    def test_alpha_prompt_has_guidance_reference(self, tmp_path):
        from terminal.deep_pipeline import build_alpha_agent_prompt

        result = build_alpha_agent_prompt(
            research_dir=tmp_path,
            symbol="TEST",
            sector="Technology",
            current_price=100.0,
            l1_oprms=None,
        )
        assert "Alpha 指引" in result


# ── Test write_agent_prompts includes profiler ───────────────────────────

class TestWriteAgentPromptsProfiler:
    """Verify write_agent_prompts handles profiler prompt."""

    def test_writes_profiler_prompt_file(self, tmp_path):
        from terminal.deep_pipeline import write_agent_prompts

        result = write_agent_prompts(
            research_dir=tmp_path,
            lens_agent_prompts=[],
            gemini_prompt="gemini test",
            synthesis_prompt="synthesis test",
            alpha_prompt="alpha test",
            profiler_prompt="profiler test content",
        )

        assert result["profiler_prompt_path"] != ""
        profiler_path = Path(result["profiler_prompt_path"])
        assert profiler_path.exists()
        assert profiler_path.read_text(encoding="utf-8") == "profiler test content"

    def test_empty_profiler_prompt_returns_empty_path(self, tmp_path):
        from terminal.deep_pipeline import write_agent_prompts

        result = write_agent_prompts(
            research_dir=tmp_path,
            lens_agent_prompts=[],
            gemini_prompt="gemini test",
            synthesis_prompt="synthesis test",
            alpha_prompt="alpha test",
            profiler_prompt="",
        )

        assert result["profiler_prompt_path"] == ""

    def test_profiler_prompt_path_key_always_present(self, tmp_path):
        from terminal.deep_pipeline import write_agent_prompts

        result = write_agent_prompts(
            research_dir=tmp_path,
            lens_agent_prompts=[],
            gemini_prompt="g",
            synthesis_prompt="s",
            alpha_prompt="a",
        )
        assert "profiler_prompt_path" in result


# ── Test compile_deep_report includes company profile section ────────────

class TestCompileReportIncludesProfile:
    """Verify compiled report includes company_profile.md when present."""

    def _setup_research_dir(self, tmp_path):
        """Create minimal research files for compilation."""
        (tmp_path / "company_profile.md").write_text(
            "# Company Profile: TEST\n\n## 公司原型与阶段\n- 成熟复利机器",
            encoding="utf-8",
        )
        (tmp_path / "lens_quality_compounder.md").write_text(
            "Quality analysis content here with enough text to pass.",
            encoding="utf-8",
        )
        (tmp_path / "debate.md").write_text(
            "Debate content\n\n**总裁决**: BUY, 高信心",
            encoding="utf-8",
        )
        (tmp_path / "memo.md").write_text(
            "## 执行摘要\nTest executive summary.",
            encoding="utf-8",
        )
        (tmp_path / "oprms.md").write_text(
            "### OPRMS 评级 — TEST\n**资产基因 (DNA)**: A — 猛将\n"
            "- 仓位上限: 15%\n**时机系数 (Timing)**: B — 正常波动\n- 系数: 0.5",
            encoding="utf-8",
        )
        return tmp_path

    @patch("terminal.deep_pipeline.logger")
    def test_report_includes_company_profile_section(self, mock_logger, tmp_path):
        research_dir = self._setup_research_dir(tmp_path)

        from terminal.deep_pipeline import compile_deep_report

        # Mock external dependencies (DB, HTML, dashboard, memory)
        with (
            patch("terminal.html_report.compile_html_report", return_value=None),
            patch("terminal.company_store.get_store"),
            patch("terminal.dashboard.generate_dashboard"),
            patch("terminal.memory.extract_situation_summary", return_value=None),
        ):
            report_path = compile_deep_report("TEST", research_dir)

        report = Path(report_path).read_text(encoding="utf-8")
        assert "公司画像" in report
        assert "成熟复利机器" in report

    @patch("terminal.deep_pipeline.logger")
    def test_report_works_without_company_profile(self, mock_logger, tmp_path):
        """Report should compile fine even if company_profile.md is missing."""
        # Only create minimal files without company_profile.md
        (tmp_path / "lens_quality_compounder.md").write_text("QC analysis", encoding="utf-8")
        (tmp_path / "debate.md").write_text("Debate\n**总裁决**: HOLD", encoding="utf-8")
        (tmp_path / "memo.md").write_text("## 执行摘要\nSummary.", encoding="utf-8")
        (tmp_path / "oprms.md").write_text(
            "**资产基因 (DNA)**: B\n- 仓位上限: 7%\n**时机系数 (Timing)**: B\n- 系数: 0.5",
            encoding="utf-8",
        )

        from terminal.deep_pipeline import compile_deep_report

        with (
            patch("terminal.html_report.compile_html_report", return_value=None),
            patch("terminal.company_store.get_store"),
            patch("terminal.dashboard.generate_dashboard"),
            patch("terminal.memory.extract_situation_summary", return_value=None),
        ):
            report_path = compile_deep_report("TEST2", tmp_path)

        report = Path(report_path).read_text(encoding="utf-8")
        # Should not have profile section
        assert "公司画像" not in report
        # But should still have lens section
        assert "五维透镜分析" in report
