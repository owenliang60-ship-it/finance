"""Tests for terminal/morning_html_report.py — Task D1 + D2."""
from terminal.morning_html_report import dicts_to_html_table, compile_morning_html_report


def test_dicts_to_html_table_escapes_and_wraps():
    html = dicts_to_html_table(
        [{"标的": "A&B <Corp>", "市值": "$3.0T"}], columns=["标的", "市值"])
    assert "table-wrap" in html and "portfolio-table" in html
    assert "<th>标的</th>" in html
    assert "A&amp;B &lt;Corp&gt;" in html          # 已转义
    assert "<Corp>" not in html                     # 原始尖括号不得泄漏


def test_compile_full_html(tmp_path):
    payload = {"as_of": "2026-06-03", "blocks": [
        {"heading": "1. PMARP 信号"},
        {"heading": "上穿98% — 大盘(≥$100B)",
         "columns": ["标的", "概念", "市值"],
         "rows": [{"标的": "NVDA", "概念": "计算芯片/GPU加速器", "市值": "$3.0T"}]},
    ]}
    out = compile_morning_html_report(payload, "2026-06-03", out_dir=tmp_path)
    text = out.read_text(encoding="utf-8")
    assert out.suffix == ".html"
    assert "<!DOCTYPE html>" in text
    assert 'class="portfolio-table"' in text and "table-wrap" in text   # CSS + EXTRA_CSS 生效
    assert "NVDA" in text and "计算芯片/GPU加速器" in text
    assert "业务角色" not in text                                        # 2c 一致
