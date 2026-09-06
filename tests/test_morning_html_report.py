"""Tests for terminal/morning_html_report.py — Task D1 + D2 + Task 4 (volconc full chain)."""
from terminal.morning_html_report import dicts_to_html_table, compile_morning_html_report
from scripts.morning_report import build_html_payload


def test_dicts_to_html_table_escapes_and_wraps():
    html = dicts_to_html_table(
        [{"标的": "A&B <Corp>", "市值": "$3.0T"}], columns=["标的", "市值"])
    assert "table-wrap" in html and "portfolio-table" in html
    assert "<th>标的</th>" in html
    assert "A&amp;B &lt;Corp&gt;" in html          # 已转义
    assert "<Corp>" not in html                     # 原始尖括号不得泄漏


def test_premium_row_styles_only_first_cell_red_bold_and_keeps_escaping():
    output = dicts_to_html_table(
        [{"标的": "A&B <Corp>", "市值": "$3.0T", "_premium": True},
         {"标的": "NORMAL", "市值": "$2.0T"}],
        columns=["标的", "市值"],
    )
    assert '<tr class="premium-row">' in output
    assert "A&amp;B &lt;Corp&gt;" in output
    assert "<Corp>" not in output
    assert '<tr><td>NORMAL</td>' in output


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


def test_compile_html_renders_subtitle_and_alerts(tmp_path):
    # P1 review fix: section 0 needs a subtitle (criteria/as_of) + red alert lines.
    payload = {"as_of": "2026-04-24", "blocks": [
        {"heading": "0. 大盘择时因子", "subtitle": "PMARP 上穿2% | 信号日 2026-04-24",
         "alerts": ["🔴 大盘择时触发", "🔴 PMARP 2% UPCROSS: SPY 1.5→2.4"]},
    ]}
    out = compile_morning_html_report(payload, "2026-04-24", out_dir=tmp_path)
    text = out.read_text(encoding="utf-8")
    assert "PMARP 上穿2% | 信号日 2026-04-24" in text
    assert "PMARP 2% UPCROSS: SPY 1.5→2.4" in text
    assert 'class="alert"' in text


# ============================================================
# Task 4: 0b 成交集中度 — build_html_payload() -> compile_morning_html_report()
# 全链路 (build_html_payload 的单元测试见 tests/test_morning_report.py，
# 遵循既有归属惯例；本文件专注编译产出的最终 HTML)
# ============================================================

def test_compile_full_html_renders_volume_concentration_available(tmp_path):
    market_signals = {
        "volume_concentration": {
            "available": True,
            "as_of": "2026-07-17",
            "share_sm_pct": 47.8,
            "share_pctile_1y": 91.6335,   # non-trivial rounding -> 92
            "churn_sm_pct": 25.3,
            "churn_pctile_1y": 0.0,       # -> 0
            "spy_ret20_pct": 3.2,
            "regime": "高集中+上行（拥挤）",
        },
    }
    payload = build_html_payload(market_signals, None, as_of="2026-07-17")
    out = compile_morning_html_report(payload, "2026-07-17", out_dir=tmp_path)
    text = out.read_text(encoding="utf-8")

    assert "0b. 成交集中度" in text
    assert "Top50 成交额占比(20日平滑)" in text
    assert "Top50 名单20日换手率(平滑)" in text
    assert "<td>47.8%</td>" in text
    assert "<td>92</td>" in text
    assert "<td>25.3%</td>" in text
    assert "高集中+上行（拥挤）" in text

    i0b = text.index("0b. 成交集中度")
    i1 = text.index("1. PMARP 信号")
    assert i0b < i1


def test_compile_full_html_renders_volume_concentration_unavailable(tmp_path):
    market_signals = {
        "volume_concentration": {"available": False, "reason": "历史数据不足"},
    }
    payload = build_html_payload(market_signals, None, as_of="2026-07-17")
    out = compile_morning_html_report(payload, "2026-07-17", out_dir=tmp_path)
    text = out.read_text(encoding="utf-8")

    assert "0b. 成交集中度" in text
    assert "成交集中度: 数据不足（历史数据不足）" in text
