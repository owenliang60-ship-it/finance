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
        return (
            f"### Company: {self.info['companyName']} ({self.symbol})\n"
            f"- Sector: Technology\n- Market Cap: $500B\n\n"
            f"### Key Fundamentals\n- P/E: 25.0\n- Gross Margin: 70%"
        )


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
        assert "Software" in queries["competitive"] or "Technology" in queries["competitive"]


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
        # macro_briefing.md no longer listed (macro briefing moved to /macro skill)
        assert "macro_briefing.md" not in prompt

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
        assert "使用中文撰写" in prompt
        assert "500-700" in prompt

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
            "lens_quality_compounder.md": "## Quality Compounder\nFour moats identified. ROIC 30%. Rating: 4/5 stars.",
            "lens_imaginative_growth.md": "## Imaginative Growth\nTAM $200B. Revenue CAGR 25%. Rating: 5/5 stars.",
            "lens_fundamental_long_short.md": "## Fundamental L/S\nLong thesis strong. Short risk: valuation. Rating: 3/5 stars.",
            "lens_deep_value.md": "## Deep Value\nDCF suggests 20% upside. Margin of safety thin. Rating: 3/5 stars.",
            "lens_event_driven.md": "## Event-Driven\nEarnings catalyst in 60 days. Rating: 4/5 stars.",
            "debate.md": "## Debate\n3 tensions identified.\n\n### 总裁决\nBUY — 高信心。Bull wins with caveats.",
            "memo.md": "## Investment Memo\n\n### 执行摘要\nBUY with 12% position. Key risk: competition.\n\n### 变异观点\nMarket underrates AI monetization.",
            "oprms.md": "## OPRMS\nDNA: S | Timing: B (0.55) | Position: 11.7%",
            "alpha_red_team.md": "## Red Team\nCisco analog attack. Thesis survives but weakened.",
            "alpha_cycle.md": "## Cycle\nSentiment 7/10. Late expansion. Early majority adoption.",
            "alpha_bet.md": "## Asymmetric Bet\nBarbell structure. R:R 1:4.7. TAKE IT.",
        }
        for name, content in files.items():
            (research_dir / name).write_text(content)
        return files

    def test_returns_path_string(self, tmp_path):
        """compile_deep_report now returns path string, not report content."""
        with patch("terminal.deep_pipeline._COMPANIES_DIR", tmp_path):
            from terminal.deep_pipeline import get_research_dir, compile_deep_report

            research_dir = get_research_dir("TEST")
            self._populate_research_dir(research_dir)
            result = compile_deep_report("TEST", research_dir)

            # Returns a path string
            assert isinstance(result, str)
            assert Path(result).exists()
            assert "full_report_" in result

    def test_compiles_all_sections(self, tmp_path):
        with patch("terminal.deep_pipeline._COMPANIES_DIR", tmp_path):
            from terminal.deep_pipeline import get_research_dir, compile_deep_report

            research_dir = get_research_dir("TEST")
            self._populate_research_dir(research_dir)
            report_path = compile_deep_report("TEST", research_dir)
            report = Path(report_path).read_text(encoding="utf-8")

            assert "TEST 深度研究报告" in report
            assert "质量复利" in report
            assert "想象力成长" in report
            assert "红队试炼" in report
            assert "非对称赌注" in report

    def test_writes_full_report_file(self, tmp_path):
        with patch("terminal.deep_pipeline._COMPANIES_DIR", tmp_path):
            from terminal.deep_pipeline import get_research_dir, compile_deep_report

            research_dir = get_research_dir("TEST")
            self._populate_research_dir(research_dir)
            report_path = compile_deep_report("TEST", research_dir)

            # Find the dated report file
            report_files = list(research_dir.glob("full_report_*.md"))
            assert len(report_files) == 1
            assert str(report_files[0]) == report_path

    def test_writes_report_summary(self, tmp_path):
        """compile_deep_report should generate report_summary.md."""
        with patch("terminal.deep_pipeline._COMPANIES_DIR", tmp_path):
            from terminal.deep_pipeline import get_research_dir, compile_deep_report

            research_dir = get_research_dir("TEST")
            self._populate_research_dir(research_dir)
            compile_deep_report("TEST", research_dir)

            summary_path = research_dir / "report_summary.md"
            assert summary_path.exists()
            summary = summary_path.read_text(encoding="utf-8")
            assert "TEST 深度分析摘要" in summary
            assert "OPRMS" in summary

    def test_report_summary_contains_verdict(self, tmp_path):
        """report_summary.md should extract debate verdict."""
        with patch("terminal.deep_pipeline._COMPANIES_DIR", tmp_path):
            from terminal.deep_pipeline import get_research_dir, compile_deep_report

            research_dir = get_research_dir("TEST")
            self._populate_research_dir(research_dir)
            compile_deep_report("TEST", research_dir)

            summary = (research_dir / "report_summary.md").read_text(encoding="utf-8")
            assert "辩论总裁决" in summary
            assert "BUY" in summary

    def test_handles_missing_optional_files(self, tmp_path):
        with patch("terminal.deep_pipeline._COMPANIES_DIR", tmp_path):
            from terminal.deep_pipeline import get_research_dir, compile_deep_report

            research_dir = get_research_dir("TEST")
            # Only write required files, skip gemini_contrarian + research files
            required = {
                "data_context.md": "### Company: TEST",
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

            report_path = compile_deep_report("TEST", research_dir)
            report = Path(report_path).read_text(encoding="utf-8")
            # Should compile without error
            assert "TEST" in report
            # Gemini section should be gracefully absent
            assert "Contrarian" not in report

    def test_report_structure_order(self, tmp_path):
        """Verify report sections appear in correct order."""
        with patch("terminal.deep_pipeline._COMPANIES_DIR", tmp_path):
            from terminal.deep_pipeline import get_research_dir, compile_deep_report

            research_dir = get_research_dir("TEST")
            self._populate_research_dir(research_dir)
            report_path = compile_deep_report("TEST", research_dir)
            report = Path(report_path).read_text(encoding="utf-8")

            # Check section ordering (Chinese headers, no Research Context, no Macro)
            lens_idx = report.index("五维透镜分析")
            debate_idx = report.index("核心辩论")
            memo_idx = report.index("投资备忘录")
            oprms_idx = report.index("OPRMS 评级与仓位")
            alpha_idx = report.index("求导思维")

            assert lens_idx < debate_idx < memo_idx < oprms_idx < alpha_idx
            # Research Context and Macro should NOT be in the report
            assert "Research Context" not in report
            assert "宏观环境" not in report


class TestWriteAgentPrompts:
    """Tests for write_agent_prompts()."""

    def test_writes_all_prompt_files(self, tmp_path):
        from terminal.deep_pipeline import write_agent_prompts

        lens_prompts = [
            {"lens_name": "Quality Compounder", "agent_prompt": "QC prompt text", "output_path": "/out/qc.md"},
            {"lens_name": "Deep Value", "agent_prompt": "DV prompt text", "output_path": "/out/dv.md"},
        ]
        result = write_agent_prompts(
            research_dir=tmp_path,
            lens_agent_prompts=lens_prompts,
            gemini_prompt="Gemini prompt text",
            synthesis_prompt="Synthesis prompt text",
            alpha_prompt="Alpha prompt text",
        )

        # All paths exist
        assert Path(result["gemini_prompt_path"]).exists()
        assert Path(result["synthesis_prompt_path"]).exists()
        assert Path(result["alpha_prompt_path"]).exists()
        for lp in result["lens_prompt_paths"]:
            assert Path(lp["prompt_path"]).exists()
            assert "lens_name" in lp
            assert "output_path" in lp

    def test_prompt_content_preserved(self, tmp_path):
        from terminal.deep_pipeline import write_agent_prompts

        result = write_agent_prompts(
            research_dir=tmp_path,
            lens_agent_prompts=[
                {"lens_name": "Test Lens", "agent_prompt": "EXACT_CONTENT_123", "output_path": "/out/test.md"},
            ],
            gemini_prompt="GEMINI_EXACT",
            synthesis_prompt="SYNTH_EXACT",
            alpha_prompt="ALPHA_EXACT",
        )

        assert Path(result["gemini_prompt_path"]).read_text() == "GEMINI_EXACT"
        assert Path(result["synthesis_prompt_path"]).read_text() == "SYNTH_EXACT"
        assert Path(result["alpha_prompt_path"]).read_text() == "ALPHA_EXACT"
        assert Path(result["lens_prompt_paths"][0]["prompt_path"]).read_text() == "EXACT_CONTENT_123"

    def test_creates_prompts_subdirectory(self, tmp_path):
        from terminal.deep_pipeline import write_agent_prompts

        write_agent_prompts(
            research_dir=tmp_path,
            lens_agent_prompts=[],
            gemini_prompt="g",
            synthesis_prompt="s",
            alpha_prompt="a",
        )

        assert (tmp_path / "prompts").is_dir()


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
        # Prompt paths instead of prompt strings
        assert "lens_prompt_paths" in result
        assert "gemini_prompt_path" in result
        assert "synthesis_prompt_path" in result
        assert "alpha_prompt_path" in result
        # context_summary no longer returned (use data_context.md file)
        assert "context_summary" not in result
        # Old keys should NOT be present
        assert "lens_agent_prompts" not in result
        assert "gemini_prompt" not in result
        assert "synthesis_agent_prompt" not in result
        assert "alpha_agent_prompt" not in result

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
    def test_lens_prompt_paths_have_files(self, mock_lenses, mock_collect):
        mock_pkg = MockDataPackage("MSFT")
        mock_collect.return_value = mock_pkg
        mock_lenses.return_value = [
            {"lens_name": "Quality Compounder", "horizon": "20y", "core_metric": "ROIC", "prompt": "Analyze QC."},
            {"lens_name": "Deep Value", "horizon": "3-5y", "core_metric": "Book", "prompt": "Analyze DV."},
        ]

        from terminal.commands import deep_analyze_ticker

        result = deep_analyze_ticker("MSFT")
        for lp in result["lens_prompt_paths"]:
            assert "lens_name" in lp
            assert "prompt_path" in lp
            assert "output_path" in lp
            assert Path(lp["prompt_path"]).exists()
            content = Path(lp["prompt_path"]).read_text()
            assert "data_context.md" in content

    @patch("terminal.commands.collect_data")
    @patch("terminal.commands.prepare_lens_prompts")
    def test_research_queries_present(self, mock_lenses, mock_collect):
        mock_pkg = MockDataPackage("NVDA")
        mock_pkg.info["companyName"] = "NVIDIA Corporation"
        mock_pkg.info["industry"] = "Semiconductors"
        mock_collect.return_value = mock_pkg
        mock_lenses.return_value = []

        from terminal.commands import deep_analyze_ticker

        result = deep_analyze_ticker("NVDA")
        queries = result["research_queries"]
        assert "earnings" in queries
        assert "competitive" in queries
        assert "street" in queries
        assert "NVIDIA" in queries["earnings"] or "NVDA" in queries["earnings"]

    @patch("terminal.commands.collect_data")
    @patch("terminal.commands.prepare_lens_prompts")
    def test_gemini_prompt_file_contains_data(self, mock_lenses, mock_collect):
        mock_pkg = MockDataPackage("MSFT")
        mock_collect.return_value = mock_pkg
        mock_lenses.return_value = []

        from terminal.commands import deep_analyze_ticker

        result = deep_analyze_ticker("MSFT")
        gemini_path = Path(result["gemini_prompt_path"])
        assert gemini_path.exists()
        content = gemini_path.read_text()
        assert "contrarian" in content.lower()
        assert "MSFT" in content

    @patch("terminal.commands.collect_data")
    @patch("terminal.commands.prepare_lens_prompts")
    def test_synthesis_prompt_file_present(self, mock_lenses, mock_collect):
        mock_pkg = MockDataPackage("AAPL")
        mock_collect.return_value = mock_pkg
        mock_lenses.return_value = []

        from terminal.commands import deep_analyze_ticker

        result = deep_analyze_ticker("AAPL")
        synth_path = Path(result["synthesis_prompt_path"])
        assert synth_path.exists()
        content = synth_path.read_text()
        assert "AAPL" in content

    @patch("terminal.commands.collect_data")
    @patch("terminal.commands.prepare_lens_prompts")
    def test_alpha_prompt_file_present(self, mock_lenses, mock_collect):
        mock_pkg = MockDataPackage("AAPL")
        mock_collect.return_value = mock_pkg
        mock_lenses.return_value = []

        from terminal.commands import deep_analyze_ticker

        result = deep_analyze_ticker("AAPL")
        alpha_path = Path(result["alpha_prompt_path"])
        assert alpha_path.exists()
        content = alpha_path.read_text()
        assert "AAPL" in content


class TestBuildSynthesisAgentPrompt:
    """Tests for build_synthesis_agent_prompt()."""

    def test_contains_all_input_file_references(self, tmp_path):
        from terminal.deep_pipeline import build_synthesis_agent_prompt

        prompt = build_synthesis_agent_prompt(tmp_path, "TEST")
        assert "data_context.md" in prompt
        assert "lens_quality_compounder.md" in prompt
        assert "lens_imaginative_growth.md" in prompt
        assert "lens_fundamental_long_short.md" in prompt
        assert "lens_deep_value.md" in prompt
        assert "lens_event_driven.md" in prompt
        assert "earnings.md" in prompt
        assert "competitive.md" in prompt
        assert "street.md" in prompt

    def test_contains_oprms_framework(self, tmp_path):
        from terminal.deep_pipeline import build_synthesis_agent_prompt

        prompt = build_synthesis_agent_prompt(tmp_path, "MSFT")
        # OPRMS framework elements
        assert "圣杯" in prompt
        assert "猛将" in prompt
        assert "黑马" in prompt
        assert "跟班" in prompt
        assert "千载难逢" in prompt
        assert "DNA" in prompt
        assert "Timing" in prompt
        assert "25%" in prompt  # S-tier position cap

    def test_contains_output_format(self, tmp_path):
        from terminal.deep_pipeline import build_synthesis_agent_prompt

        prompt = build_synthesis_agent_prompt(tmp_path, "NVDA")
        # Output file instructions
        assert "debate.md" in prompt
        assert "memo.md" in prompt
        assert "oprms.md" in prompt
        # Chinese output requirement
        assert "中文" in prompt

    def test_contains_debate_structure(self, tmp_path):
        from terminal.deep_pipeline import build_synthesis_agent_prompt

        prompt = build_synthesis_agent_prompt(tmp_path, "TEST")
        assert "张力" in prompt or "tension" in prompt.lower()
        assert "BUY" in prompt
        assert "HOLD" in prompt
        assert "SELL" in prompt
        assert "500+" in prompt  # minimum word count

    def test_contains_memo_structure(self, tmp_path):
        from terminal.deep_pipeline import build_synthesis_agent_prompt

        prompt = build_synthesis_agent_prompt(tmp_path, "TEST")
        assert "Executive Summary" in prompt or "执行摘要" in prompt
        assert "Variant View" in prompt or "变异观点" in prompt
        assert "DCF" in prompt
        assert "800+" in prompt  # minimum word count

    def test_symbol_uppercased(self, tmp_path):
        from terminal.deep_pipeline import build_synthesis_agent_prompt

        prompt = build_synthesis_agent_prompt(tmp_path, "msft")
        assert "MSFT" in prompt


class TestBuildAlphaAgentPrompt:
    """Tests for build_alpha_agent_prompt()."""

    def test_contains_three_framework_sections(self, tmp_path):
        from terminal.deep_pipeline import build_alpha_agent_prompt

        prompt = build_alpha_agent_prompt(
            tmp_path, "TEST", "Technology", 150.0, None
        )
        # All three alpha frameworks embedded
        assert "红队试炼" in prompt
        assert "周期钟摆" in prompt
        assert "非对称赌注" in prompt

    def test_contains_placeholder_markers(self, tmp_path):
        from terminal.deep_pipeline import build_alpha_agent_prompt

        prompt = build_alpha_agent_prompt(
            tmp_path, "TEST", "Technology", 150.0, None
        )
        assert "<<PLACEHOLDER:" in prompt

    def test_contains_input_file_references(self, tmp_path):
        from terminal.deep_pipeline import build_alpha_agent_prompt

        prompt = build_alpha_agent_prompt(
            tmp_path, "MSFT", "Technology", 350.0, None
        )
        assert "data_context.md" in prompt
        assert "debate.md" in prompt
        assert "memo.md" in prompt
        assert "oprms.md" in prompt
        assert "gemini_contrarian.md" in prompt

    def test_contains_output_file_references(self, tmp_path):
        from terminal.deep_pipeline import build_alpha_agent_prompt

        prompt = build_alpha_agent_prompt(
            tmp_path, "MSFT", "Technology", 350.0, None
        )
        assert "alpha_red_team.md" in prompt
        assert "alpha_cycle.md" in prompt
        assert "alpha_bet.md" in prompt

    def test_with_oprms_context(self, tmp_path):
        from terminal.deep_pipeline import build_alpha_agent_prompt

        oprms = {
            "dna": "S",
            "timing": "B",
            "timing_coeff": 0.55,
            "investment_bucket": "Long-term Compounder",
        }
        prompt = build_alpha_agent_prompt(
            tmp_path, "MSFT", "Technology", 350.0, oprms
        )
        assert "DNA=S" in prompt
        assert "Timing=B" in prompt
        assert "0.55" in prompt

    def test_without_oprms_context(self, tmp_path):
        from terminal.deep_pipeline import build_alpha_agent_prompt

        prompt = build_alpha_agent_prompt(
            tmp_path, "MSFT", "Technology", 350.0, None
        )
        assert "首次分析" in prompt or "无现有" in prompt

    def test_conviction_modifier_update_instruction(self, tmp_path):
        from terminal.deep_pipeline import build_alpha_agent_prompt

        prompt = build_alpha_agent_prompt(
            tmp_path, "TEST", "Technology", 100.0, None
        )
        assert "conviction_modifier" in prompt
        assert "oprms.md" in prompt

    def test_symbol_uppercased(self, tmp_path):
        from terminal.deep_pipeline import build_alpha_agent_prompt

        prompt = build_alpha_agent_prompt(
            tmp_path, "msft", "Technology", 350.0, None
        )
        assert "MSFT" in prompt
