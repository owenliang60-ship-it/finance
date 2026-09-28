"""Deep-analysis wiring: Phase 0 freeze → prompts → MD / HTML / PDF report."""
from datetime import date
from pathlib import Path
from unittest.mock import patch

import pytest

from tests.test_deep_pipeline import MockDataPackage
from tests.test_financial_history import AS_OF, _std_estimates, make_db


@pytest.fixture
def frozen_research_dir(tmp_path):
    from terminal.financial_history import prepare_financial_history

    db = make_db(tmp_path, estimates=_std_estimates())
    rd = tmp_path / "research"
    out = prepare_financial_history("TST", rd, as_of=AS_OF, db_path=db, allow_live=False)
    assert out["status"] == "complete"
    for name, text in {
        "company_profile.md": "# Profile\n\n## 业务概览\n测试公司。",
        "lens_quality_compounder.md": "## QC\n评级: 4/5 BUY",
        "debate.md": "## Debate\n### 总裁决\nBUY",
        "memo.md": "## Memo\n### 执行摘要\nBUY",
        "oprms.md": "## OPRMS\nDNA: A | Timing: B (0.5)",
    }.items():
        (rd / name).write_text(text, encoding="utf-8")
    return rd


@patch("terminal.commands.collect_data")
@patch("terminal.commands.prepare_lens_prompts")
def test_analyze_ticker_freezes_history_before_prompts(mock_lenses, mock_collect, tmp_path):
    mock_collect.return_value = MockDataPackage("TEST")
    mock_lenses.return_value = [
        {"lens_name": "Deep Value", "horizon": "3-5y", "core_metric": "Book", "prompt": "Analyze."}]
    with patch("terminal.deep_pipeline._COMPANIES_DIR", tmp_path):
        from terminal.commands import analyze_ticker
        result = analyze_ticker("TEST")
    assert result["financial_history"]["status"] == "blocked"      # stub from test_deep_pipeline
    ctx = Path(result["data_context_path"]).read_text(encoding="utf-8")
    assert "五年季度业绩与未来四季共识" in ctx
    profiler = Path(result["profiler_prompt_path"]).read_text(encoding="utf-8")
    assert "五年季度业绩与未来四季共识" in profiler                  # embedded at build time
    lens = Path(result["lens_prompt_paths"][0]["prompt_path"]).read_text(encoding="utf-8")
    assert "financial_history.md" in lens and "不自行重算" in lens
    synthesis = Path(result["synthesis_prompt_path"]).read_text(encoding="utf-8")
    assert "financial_history.md" in synthesis


def test_markdown_report_embeds_frozen_chart(frozen_research_dir):
    from terminal.deep_pipeline import compile_deep_report

    with (
        patch("terminal.html_report.compile_html_report", return_value=None),
        patch("terminal.company_store.get_store"),
        patch("terminal.dashboard.generate_dashboard"),
        patch("terminal.memory.extract_situation_summary", return_value=None),
        patch("terminal.company_db.save_kill_conditions"),
        patch("terminal.company_db.save_alpha_package"),
        patch("terminal.financial_history.build_financial_history") as rebuild,
    ):
        report = Path(compile_deep_report("TST", frozen_research_dir)).read_text(encoding="utf-8")
    rebuild.assert_not_called()                                    # compile never re-fetches
    assert "## 0.5 股价与业绩" in report
    assert "![TST 五年股价与季度业绩](price_fundamentals_5y_4q.png)" in report
    assert report.index("## 0.5") < report.index("## I. 五维透镜分析")
    assert "口径切换" in report


def test_html_embeds_png_and_pdf_gets_landscape_page(frozen_research_dir):
    from terminal.html_report import compile_html_report

    html_path = compile_html_report("TST", frozen_research_dir, date="2026-09-28")
    doc = html_path.read_text(encoding="utf-8")
    assert 'id="sec-financials"' in doc and 'href="#sec-financials"' in doc
    assert "data:image/png;base64," in doc
    weasyprint = pytest.importorskip("weasyprint")
    pages = weasyprint.HTML(filename=str(html_path)).render().pages
    landscape = [p for p in pages if p.width > p.height]
    assert len(landscape) == 1


def test_toc_has_no_dead_financials_link_without_manifest(tmp_path):
    from terminal.html_report import compile_html_report

    (tmp_path / "memo.md").write_text("## Memo\n### 执行摘要\nBUY", encoding="utf-8")
    doc = compile_html_report("OLD", tmp_path, date="2026-09-28").read_text(encoding="utf-8")
    assert 'href="#sec-financials"' not in doc and 'id="sec-financials"' not in doc
