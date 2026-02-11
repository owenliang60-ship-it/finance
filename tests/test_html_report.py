"""Tests for terminal/html_report.py — HTML report builder."""
import re
import textwrap
from pathlib import Path

import pytest

from terminal.html_report import (
    CSS,
    LENS_COLORS,
    LENS_LABELS,
    _build_table,
    _extract_conviction_modifier,
    _extract_debate_verdict,
    _extract_meta_line,
    _extract_one_liner,
    _extract_oprms_dna,
    _extract_oprms_position,
    _extract_oprms_timing,
    _extract_oprms_verdict,
    _extract_rating_line,
    _extract_regime,
    _inline,
    _stars_html,
    _strip_first_heading,
    _verdict_tag,
    build_alpha_section,
    build_debate_section,
    build_header,
    build_lenses_section,
    build_macro_section,
    build_memo_section,
    build_oprms_section,
    build_toc,
    compile_html_report,
    md_to_html,
)


# ===================================================================
# md_to_html unit tests
# ===================================================================


class TestInline:
    def test_bold(self):
        assert "<strong>bold</strong>" in _inline("**bold**")

    def test_italic(self):
        assert "<em>italic</em>" in _inline("*italic*")

    def test_code(self):
        assert "<code>code</code>" in _inline("`code`")

    def test_mixed(self):
        result = _inline("**bold** and *italic* with `code`")
        assert "<strong>bold</strong>" in result
        assert "<em>italic</em>" in result
        assert "<code>code</code>" in result

    def test_html_escape(self):
        result = _inline("<script>alert('xss')</script>")
        assert "<script>" not in result
        assert "&lt;script&gt;" in result

    def test_plain(self):
        assert _inline("hello") == "hello"


class TestMdToHtml:
    def test_empty(self):
        assert md_to_html("") == ""
        assert md_to_html("   ") == ""

    def test_paragraph(self):
        result = md_to_html("Hello world")
        assert "<p>" in result
        assert "Hello world" in result

    def test_multi_paragraph(self):
        result = md_to_html("Para 1\n\nPara 2")
        assert result.count("<p>") == 2

    def test_heading_h2(self):
        result = md_to_html("## Section Title")
        assert "<h2>Section Title</h2>" in result

    def test_heading_h3(self):
        result = md_to_html("### Sub Title")
        assert "<h3>Sub Title</h3>" in result

    def test_heading_h1_skipped(self):
        result = md_to_html("# Top Title\nSome text")
        assert "<h1>" not in result
        assert "Some text" in result

    def test_horizontal_rule(self):
        result = md_to_html("---")
        assert "<hr>" in result

    def test_blockquote(self):
        result = md_to_html("> This is a quote")
        assert "<blockquote>" in result
        assert "This is a quote" in result

    def test_multi_line_blockquote(self):
        result = md_to_html("> Line 1\n> Line 2")
        assert "<blockquote>" in result
        assert "Line 1" in result
        assert "Line 2" in result

    def test_unordered_list(self):
        result = md_to_html("- Item 1\n- Item 2\n- Item 3")
        assert "<ul>" in result
        assert result.count("<li>") == 3
        assert "Item 2" in result

    def test_unordered_list_asterisk(self):
        result = md_to_html("* Item 1\n* Item 2")
        assert "<ul>" in result
        assert result.count("<li>") == 2

    def test_ordered_list(self):
        result = md_to_html("1. First\n2. Second\n3. Third")
        assert "<ol>" in result
        assert result.count("<li>") == 3

    def test_table(self):
        md = "| A | B |\n|---|---|\n| 1 | 2 |"
        result = md_to_html(md)
        assert '<table class="portfolio-table">' in result
        assert "<th>" in result
        assert "<td>" in result

    def test_inline_in_paragraph(self):
        result = md_to_html("This is **bold** text")
        assert "<strong>bold</strong>" in result

    def test_mixed_content(self):
        md = textwrap.dedent("""\
        ## Title

        Some **bold** paragraph.

        - Item 1
        - Item 2

        > A quote

        ---

        Another paragraph.""")
        result = md_to_html(md)
        assert "<h2>" in result
        assert "<strong>bold</strong>" in result
        assert "<ul>" in result
        assert "<blockquote>" in result
        assert "<hr>" in result


class TestBuildTable:
    def test_basic_table(self):
        lines = ["| A | B |", "|---|---|", "| 1 | 2 |"]
        result = _build_table(lines)
        assert "<table" in result
        assert "<th>" in result
        assert "<td>" in result
        assert "1" in result

    def test_no_separator(self):
        lines = ["| A | B |", "| 1 | 2 |"]
        result = _build_table(lines)
        assert "<table" in result

    def test_empty(self):
        assert _build_table([]) == ""
        assert _build_table(["| A |"]) == ""


# ===================================================================
# Extraction helper tests
# ===================================================================


class TestExtractRegime:
    def test_risk_on(self):
        text = "**Regime**: RISK_ON | **Risk Bias**: TAILWIND"
        regime, color = _extract_regime(text)
        assert regime == "RISK_ON"
        assert color == "green"

    def test_risk_off(self):
        text = "**Regime**: RISK_OFF | **Risk Bias**: HEADWIND"
        regime, color = _extract_regime(text)
        assert regime == "RISK_OFF"
        assert color == "red"

    def test_neutral(self):
        text = "**Regime**: NEUTRAL"
        regime, color = _extract_regime(text)
        assert regime == "NEUTRAL"
        assert color == "amber"

    def test_missing(self):
        regime, color = _extract_regime("no regime here")
        assert regime == "NEUTRAL"
        assert color == "amber"


class TestExtractOneLiner:
    def test_basic(self):
        text = "**一句话**: Market is bullish."
        assert _extract_one_liner(text) == "Market is bullish."

    def test_missing(self):
        assert _extract_one_liner("no one-liner") is None


class TestExtractRatingLine:
    def test_full(self):
        text = "**星级：3.5 / 5** | **判定：HOLD** | **目标 IRR：12%**"
        result = _extract_rating_line(text)
        assert result is not None
        assert result["stars"] == "3.5"
        assert result["verdict"] == "HOLD"
        assert result["irr"] == "12%"

    def test_no_irr(self):
        text = "**星级：1.5 / 5** | **判定：PASS**"
        result = _extract_rating_line(text)
        assert result is not None
        assert result["stars"] == "1.5"
        assert result["verdict"] == "PASS"

    def test_missing(self):
        assert _extract_rating_line("no rating") is None

    def test_buy(self):
        text = "**星级：4 / 5** | **判定：BUY** | **目标 IRR：>15%**"
        result = _extract_rating_line(text)
        assert result["verdict"] == "BUY"


class TestExtractOprms:
    OPRMS_TEXT = textwrap.dedent("""\
    # OPRMS 评级：TSLA

    > 未来资本 | 2026-02-10 | 股价：$417

    ---

    ## 资产基因 (DNA Rating)

    **评级: B — 黑马**

    **理由**: 一些理由
    - Point 1
    - Point 2

    **DNA 仓位上限: 7%**

    ---

    ## 时机系数 (Timing Rating)

    **评级: C — 垃圾时间**

    **系数: 0.2**

    **理由**:
    - Reason 1

    ---

    ## 仓位计算

    | 参数 | 数值 |
    |------|------|
    | **最终仓位** | **7% × 0.2 × 1.0 × 0.7 = 0.98%** |

    **实际建议: 0%**

    ---

    ## 综合判定

    | 维度 | 评级 |
    |------|------|
    | Verdict | **HOLD — 观察等待催化剂确认** |
    """)

    def test_dna(self):
        dna = _extract_oprms_dna(self.OPRMS_TEXT)
        assert dna["grade"] == "B"
        assert "黑马" in dna["name"]
        assert dna["cap"] == "7%"

    def test_timing(self):
        timing = _extract_oprms_timing(self.OPRMS_TEXT)
        assert timing["grade"] == "C"
        assert "垃圾时间" in timing["name"]
        assert timing["coeff"] == "0.2"

    def test_position(self):
        val, formula = _extract_oprms_position(self.OPRMS_TEXT)
        assert val is not None
        assert "0.98%" in val
        assert formula is not None
        assert "7%" in formula

    def test_verdict(self):
        verdict = _extract_oprms_verdict(self.OPRMS_TEXT)
        assert verdict is not None
        assert "HOLD" in verdict


class TestExtractDebateVerdict:
    def test_basic(self):
        text = "## 总体判定\n\n**HOLD — 等待催化剂确认**\n\nSome text"
        verdict = _extract_debate_verdict(text)
        assert verdict is not None
        assert "HOLD" in verdict

    def test_missing(self):
        assert _extract_debate_verdict("no verdict") is None


class TestExtractConviction:
    def test_basic(self):
        text = "**Value**: 0.7\n**理由**: blah"
        assert _extract_conviction_modifier(text) == "0.7"

    def test_missing(self):
        assert _extract_conviction_modifier("no value") is None


class TestStarsHtml:
    def test_five(self):
        result = _stars_html("5")
        assert result.count("&#9733;") == 5

    def test_three_point_five(self):
        result = _stars_html("3.5")
        assert "&#9733;" in result

    def test_invalid(self):
        result = _stars_html("N/A")
        assert "N/A" in result


class TestVerdictTag:
    def test_buy(self):
        result = _verdict_tag("BUY")
        assert "tag-buy" in result

    def test_hold(self):
        result = _verdict_tag("HOLD")
        assert "tag-hold" in result

    def test_pass(self):
        result = _verdict_tag("PASS")
        assert "tag-pass" in result

    def test_pair(self):
        result = _verdict_tag("PAIR")
        assert "tag-pair" in result


class TestStripFirstHeading:
    def test_strips_h1_and_meta(self):
        text = "# Title\n\n> Date: 2026\n\n---\n\nContent here"
        result = _strip_first_heading(text)
        assert "Content here" in result
        assert "# Title" not in result

    def test_no_heading(self):
        text = "Just content"
        result = _strip_first_heading(text)
        assert "Just content" in result


# ===================================================================
# Section builder tests
# ===================================================================


class TestBuildHeader:
    def test_basic(self, tmp_path):
        result = build_header("TSLA", tmp_path)
        assert "TSLA" in result
        assert "header-badge" in result
        assert "CONFIDENTIAL" in result

    def test_with_context(self, tmp_path):
        ctx = tmp_path / "data_context.md"
        ctx.write_text("Latest: $417.00\nMarket Cap: $1.35T\n**Regime: RISK_ON** (confidence: high)\n")
        result = build_header("TSLA", tmp_path)
        assert "$417.00" in result
        assert "$1.35T" in result
        assert "RISK_ON" in result


class TestBuildToc:
    def test_has_all_sections(self):
        result = build_toc()
        assert "sec-macro" not in result  # macro removed from deep analysis
        assert "sec-lenses" in result
        assert "sec-debate" in result
        assert "sec-memo" in result
        assert "sec-oprms" in result
        assert "sec-alpha" in result


class TestBuildMacroSection:
    def test_empty(self):
        assert build_macro_section("") == ""

    def test_basic(self):
        text = textwrap.dedent("""\
        # 宏观晨会简报

        **一句话**: Market is stable.
        **Regime**: RISK_ON | **Risk Bias**: TAILWIND

        ---

        ## 叙事 1: Something

        Some narrative here.
        """)
        result = build_macro_section(text)
        assert "sec-macro" in result
        assert "RISK_ON" in result
        assert "Market is stable" in result


class TestBuildLensesSection:
    def test_empty_dir(self, tmp_path):
        result = build_lenses_section(tmp_path)
        assert result == ""

    def test_with_lens_file(self, tmp_path):
        lens = tmp_path / "lens_quality_compounder.md"
        lens.write_text(textwrap.dedent("""\
        # Quality Compounder: TEST

        > Date: 2026-02-10

        ---

        ## 1. 核心论点
        **Test thesis.**

        ## 3. 评级
        **星级：4 / 5** | **判定：BUY** | **目标 IRR：15%**
        """))
        result = build_lenses_section(tmp_path)
        assert "Quality Compounder" in result
        assert "LENS 1" in result
        assert "tag-buy" in result
        assert "&#9733;" in result


class TestBuildDebateSection:
    def test_empty(self):
        assert build_debate_section("") == ""

    def test_with_tensions(self):
        text = textwrap.dedent("""\
        # TSLA 五透镜辩论

        > 日期：2026-02-10

        ---

        ## 三大核心张力

        ### 张力 1: Bull vs Bear

        **多头（Imaginative Growth）**: Bull case here.

        **空头（Quality Compounder）**: Bear case here.

        **决议**: Resolution here.

        ---

        ## 总体判定

        **HOLD — 等待催化剂**

        **置信度**: 中等
        """)
        result = build_debate_section(text)
        assert "sec-debate" in result
        assert "tension-block" in result
        assert "bull" in result.lower()
        assert "bear" in result.lower()
        assert "resolution" in result.lower()
        assert "verdict-box" in result


class TestBuildMemoSection:
    def test_empty(self):
        assert build_memo_section("") == ""

    def test_basic(self):
        text = "# TSLA Memo\n\n> 2026\n\n---\n\nSome memo content."
        result = build_memo_section(text)
        assert "sec-memo" in result
        assert "Some memo content" in result


class TestBuildOprmsSection:
    def test_empty(self):
        assert build_oprms_section("") == ""

    def test_with_data(self):
        text = TestExtractOprms.OPRMS_TEXT
        result = build_oprms_section(text)
        assert "sec-oprms" in result
        assert "snap-card" in result
        assert "B" in result  # DNA grade
        assert "C" in result  # Timing grade


class TestBuildAlphaSection:
    def test_empty(self):
        assert build_alpha_section("", "", "", "") == ""

    def test_red_team_only(self):
        text = "# Alpha Red Team\n\n> 2026\n\n---\n\nAttack vector."
        result = build_alpha_section(text, "", "", "")
        assert "sec-alpha" in result
        assert "risk-callout" in result
        assert "Red Team" in result

    def test_all_sections(self):
        rt = "# Red Team\n\n---\n\nAttack."
        gem = "# Gemini\n\n---\n\nContrarian."
        cy = "# Cycle\n\n---\n\nCycle analysis."
        bet = "# Bet\n\n---\n\n**Value**: 0.8\nBet analysis."
        result = build_alpha_section(rt, gem, cy, bet)
        assert "Red Team" in result
        assert "Contrarian" in result
        assert "Cycle" in result
        assert "Asymmetric Bet" in result
        assert "0.8" in result


# ===================================================================
# End-to-end compilation test
# ===================================================================


class TestCompileHtmlReport:
    def test_e2e_minimal(self, tmp_path):
        """Test with minimal content — should produce valid HTML."""
        memo = tmp_path / "memo.md"
        memo.write_text("# Test Memo\n\n---\n\nSome content.")
        result = compile_html_report("TEST", tmp_path)
        assert result.exists()
        content = result.read_text(encoding="utf-8")
        assert "<!DOCTYPE html>" in content
        assert "TEST" in content
        assert "</html>" in content

    def test_e2e_full(self, tmp_path):
        """Test with all sections present."""
        files = {
            "data_context.md": "### Company: TSLA\n**Regime: RISK_ON** (confidence: high)\n",
            "lens_quality_compounder.md": "# QC\n\n---\n\n**星级：4 / 5** | **判定：BUY** | **目标 IRR：15%**",
            "lens_deep_value.md": "# DV\n\n---\n\n**星级：2 / 5** | **判定：PASS**",
            "debate.md": "# Debate\n\n---\n\n## 总体判定\n\n**HOLD**\n\nText.",
            "memo.md": "# Memo\n\n---\n\nMemo content.",
            "oprms.md": TestExtractOprms.OPRMS_TEXT,
            "alpha_red_team.md": "# RT\n\n---\n\nAttack.",
            "gemini_contrarian.md": "# Gem\n\n---\n\nContrarian.",
            "alpha_cycle.md": "# Cycle\n\n---\n\nCycle.",
            "alpha_bet.md": "# Bet\n\n---\n\n**Value**: 0.7\nBet.",
        }
        for name, content in files.items():
            (tmp_path / name).write_text(content, encoding="utf-8")

        result = compile_html_report("TSLA", tmp_path)
        assert result.exists()
        content = result.read_text(encoding="utf-8")

        # Verify structure
        assert "<!DOCTYPE html>" in content
        assert "TSLA" in content
        assert "sec-macro" not in content  # macro removed from deep analysis
        assert "sec-lenses" in content
        assert "sec-debate" in content
        assert "sec-memo" in content
        assert "sec-oprms" in content
        assert "sec-alpha" in content
        assert "JetBrains Mono" in content
        assert "Crimson Pro" in content
        assert "</html>" in content

    def test_output_path(self, tmp_path):
        (tmp_path / "memo.md").write_text("# M\n\n---\n\nContent.")
        result = compile_html_report("AAPL", tmp_path, date="2026-02-10")
        assert result.name == "full_report_2026-02-10.html"
        assert result.parent == tmp_path

    def test_output_path_default_date(self, tmp_path):
        (tmp_path / "memo.md").write_text("# M\n\n---\n\nContent.")
        result = compile_html_report("AAPL", tmp_path)
        assert result.name.startswith("full_report_")
        assert result.name.endswith(".html")


class TestCssConstants:
    def test_css_has_variables(self):
        assert "--bg:" in CSS
        assert "--gold:" in CSS
        assert "--surface:" in CSS

    def test_lens_colors_complete(self):
        expected = {"quality_compounder", "imaginative_growth",
                    "fundamental_long_short", "deep_value", "event_driven"}
        assert set(LENS_COLORS.keys()) == expected

    def test_lens_labels_complete(self):
        expected = {"quality_compounder", "imaginative_growth",
                    "fundamental_long_short", "deep_value", "event_driven"}
        assert set(LENS_LABELS.keys()) == expected
