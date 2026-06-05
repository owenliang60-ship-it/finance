"""Tests for terminal/morning_html_report.py — Task D1."""
from terminal.morning_html_report import dicts_to_html_table


def test_dicts_to_html_table_escapes_and_wraps():
    html = dicts_to_html_table(
        [{"标的": "A&B <Corp>", "市值": "$3.0T"}], columns=["标的", "市值"])
    assert "table-wrap" in html and "portfolio-table" in html
    assert "<th>标的</th>" in html
    assert "A&amp;B &lt;Corp&gt;" in html          # 已转义
    assert "<Corp>" not in html                     # 原始尖括号不得泄漏
