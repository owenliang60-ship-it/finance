"""Tests for scripts/morning_report.py — 格式化函数单元测试"""
import os
import sqlite3
import sys
from pathlib import Path

import pandas as pd
import pytest

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from scripts.morning_report import (
    build_market_signal_report,
    build_morning_visual_sections,
    _compute_breadth_s2_status,
    _compute_breadth_s2_status_from_price_frames,
    _merge_volume_anomaly_hits,
    format_section_layered_dv,
    format_section_layered_rvol,
    format_section_market_timing_factor,
    format_section_a,
    format_section_b,
    format_section_c,
    format_section_d,
    format_morning_report,
    render_morning_report_images,
    render_morning_report_pdf,
)

# P1-2 修：现有测试无 `mr` 别名（文件用 `from scripts.morning_report import (...)` + 方法内 import）。
# 新测试统一用 mr.xxx，故在此显式加别名。
from scripts import morning_report as mr


def _make_pmarp_hit(symbol="NVDA", signal="bullish_breakout", value=98.5,
                    market_cap=3e12, secondary_concept_id=None):
    return {"symbol": symbol, "signal": signal, "value": value, "previous": value - 1.0,
            "marketCap": market_cap, "layer": "pool",
            "secondary_concept_id": secondary_concept_id}


def _make_market_signals(pmarp_hits=None, anomaly_hits=None):
    return {"pmarp": {"hits": pmarp_hits or [], "criteria": "PMARP"},
            "volume_anomaly": {"hits": anomaly_hits or [], "criteria": "VOL"}}


def _make_dv_result(rankings=None, new_faces=None, date="2026-06-03"):
    return {"date": date, "rankings": rankings or [], "new_faces": new_faces or []}


def _make_dv_item(symbol="NVDA", rank=1, dollar_volume=9e9, price=100.0,
                  rank_change_label="=", market_cap=3e12):
    return {"symbol": symbol, "rank": rank, "dollar_volume": dollar_volume,
            "price": price, "rank_change_label": rank_change_label,
            "market_cap": market_cap, "marketCap": market_cap, "layer": "pool"}


def sample_market_signals():
    return {
        "as_of": "2026-04-24",
        "symbols_scanned": 3,
        "symbols_with_data": 3,
        "market_timing_factor": {
            "criteria": "PMARP 上穿2% + Broad S2(MA20 breadth 上穿30%, cooldown=60)",
            "alerts": [
                {"kind": "pmarp_up2", "text": "PMARP 2% UPCROSS: SPY 1.5→2.4"},
                {"kind": "breadth_s2_upcross", "text": "BREADTH S2 UPCROSS: broad MA20 participation 29.5%→31.2%"},
            ],
            "breadth_s2": {
                "current": 0.312,
                "previous": 0.295,
                "upcross": True,
                "as_of": "2026-04-24",
            },
            "rows": [
                {
                    "symbol": "SPY",
                    "as_of": "2026-04-24",
                    "pmarp_current": 2.4,
                    "pmarp_previous": 1.5,
                    "pmarp_up2": True,
                    "breadth_s2_current": 0.312,
                    "breadth_s2_previous": 0.295,
                    "breadth_s2_upcross": True,
                    "breadth_s2_as_of": "2026-04-24",
                },
                {
                    "symbol": "QQQ",
                    "as_of": "2026-04-24",
                    "pmarp_current": 8.5,
                    "pmarp_previous": 7.1,
                    "pmarp_up2": False,
                    "breadth_s2_current": 0.312,
                    "breadth_s2_previous": 0.295,
                    "breadth_s2_upcross": True,
                    "breadth_s2_as_of": "2026-04-24",
                },
                {
                    "symbol": "SOXX",
                    "as_of": "2026-04-24",
                    "pmarp_current": 12.0,
                    "pmarp_previous": 10.0,
                    "pmarp_up2": False,
                    "breadth_s2_current": 0.312,
                    "breadth_s2_previous": 0.295,
                    "breadth_s2_upcross": True,
                    "breadth_s2_as_of": "2026-04-24",
                },
            ],
        },
        "volume_concentration": {
            "available": True,
            "as_of": "2026-04-24",
            "share_sm_pct": 47.8,
            "share_pctile_1y": 91.6335,   # non-trivial rounding -> 92
            "churn_sm_pct": 25.3,
            "churn_pctile_1y": 0.0,       # -> 0
            "spy_ret20_pct": 3.2,
            "regime": "高集中+上行（拥挤）",
        },
        "pmarp": {
            "criteria": "PMARP 上穿2% / 上穿98% / 下穿98%",
            "hits": [
                {
                    "symbol": "NVDA", "companyName": "NVIDIA Corporation",
                    "sector": "Technology", "industry": "Semiconductors",
                    "concept_bucket": "AI算力/云", "layer": "pool",
                    "value": 98.5, "previous": 97.2, "signal": "bullish_breakout",
                    "marketCap": 3e12,
                },
                {
                    "symbol": "TSLA", "companyName": "Tesla Inc.",
                    "sector": "Consumer Cyclical", "industry": "Auto Manufacturers",
                    "concept_bucket": "自动驾驶/机器人", "layer": "pool",
                    "value": 96.8, "previous": 98.4, "signal": "momentum_fading",
                    "marketCap": 800e9,
                },
                {
                    "symbol": "BA", "companyName": "Boeing Company",
                    "sector": "Industrials", "industry": "Aerospace & Defense",
                    "concept_bucket": "工业/航天/国防", "layer": "extend",
                    "value": 2.5, "previous": 1.7, "signal": "oversold_recovery",
                    "marketCap": 120e9,
                },
            ],
        },
        "dv_acceleration": {
            "criteria": "DV >1.5x",
            "hits": [
                {
                    "symbol": "MU", "companyName": "Micron Technology, Inc.",
                    "sector": "Technology", "industry": "Semiconductors",
                    "concept_bucket": "半导体链", "layer": "pool",
                    "ratio": 1.8, "dv_5d": 900e6, "dv_20d": 500e6,
                    "marketCap": 160e9,
                },
            ],
        },
        "rvol_sustained": {
            "criteria": "RVOL >2.0σ 持续",
            "hits": [
                {
                    "symbol": "RKLB", "companyName": "Rocket Lab USA, Inc.",
                    "sector": "Industrials", "industry": "Aerospace & Defense",
                    "concept_bucket": "工业/航天/国防", "layer": "extend",
                    "level": "sustained_3d", "latest_rvol": 3.2,
                    "marketCap": 12e9,
                },
            ],
        },
        "volume_anomaly": {
            "criteria": "DV >1.5x | RVOL >2.0σ sustained 或 single >=3.0σ",
            "hits": [
                {
                    "symbol": "MU", "companyName": "Micron Technology, Inc.",
                    "sector": "Technology", "industry": "Semiconductors",
                    "concept_bucket": "半导体链", "layer": "pool",
                    "from_dv": True, "from_rvol": True,
                    "dv_ratio": 1.8, "ratio": 1.8,
                    "dv_5d": 900e6, "dv_20d": 500e6,
                    "rvol_level": "sustained_3d", "rvol_days": 3,
                    "latest_rvol": 2.6, "rvol_values": [2.6, 2.4, 2.2],
                    "volume_signal_kind": "共振", "priority_group": 0,
                    "marketCap": 160e9,
                },
                {
                    "symbol": "DDOG", "companyName": "Datadog, Inc.",
                    "sector": "Technology", "industry": "Software",
                    "concept_bucket": "软件/SaaS", "layer": "pool",
                    "from_dv": True, "from_rvol": False,
                    "dv_ratio": 2.1, "ratio": 2.1,
                    "dv_5d": 2.2e9, "dv_20d": 1.0e9,
                    "volume_signal_kind": "流动性加速", "priority_group": 4,
                    "marketCap": 45e9,
                },
            ],
        },
    }


class TestFormatSectionA:
    """A. PMARP 极值 (仅保留上穿2%)"""

    def test_with_high_and_low_legacy(self):
        """没有上穿2%时不再回退到低位阈值报警"""
        summary = {
            "top_pmarp": [
                {"symbol": "NVDA", "value": 99.1, "signal": "overbought"},
                {"symbol": "PLTR", "value": 98.5, "signal": "overbought"},
            ],
            "low_pmarp": [
                {"symbol": "INTC", "value": 1.3, "signal": "oversold"},
            ],
        }
        result = format_section_a(summary)
        assert "PMARP" in result
        assert "上穿2%" not in result
        assert "NVDA" not in result
        assert "INTC" not in result
        assert "无极值信号" in result

    def test_only_recovery_signal_survives(self):
        """只显示上穿2%，其余 PMARP 报警全部忽略"""
        summary = {
            "top_pmarp": [],
            "low_pmarp": [],
            "pmarp_crossovers": {
                "breakout_98": [
                    {"symbol": "NVDA", "value": 98.7, "previous": 97.2, "signal": "bullish_breakout"},
                ],
                "fading_98": [
                    {"symbol": "TSLA", "value": 97.1, "previous": 98.5, "signal": "momentum_fading"},
                ],
                "crashed_2": [
                    {"symbol": "INTC", "value": 1.3, "previous": 2.8, "signal": "oversold_bounce"},
                ],
                "recovery_2": [
                    {"symbol": "BA", "value": 2.5, "previous": 1.7, "signal": "oversold_recovery"},
                ],
            },
        }
        result = format_section_a(summary)
        assert "上穿98%" not in result
        assert "下穿98%" not in result
        assert "下穿2%" not in result
        assert "TSLA" not in result
        assert "INTC" not in result
        assert "上穿2%" in result
        assert "BA" in result

    def test_partial_crossovers(self):
        """只有部分穿越信号"""
        summary = {
            "top_pmarp": [],
            "low_pmarp": [],
            "pmarp_crossovers": {
                "breakout_98": [
                    {"symbol": "NVDA", "value": 99.0, "previous": 97.5, "signal": "bullish_breakout"},
                ],
                "fading_98": [],
                "crashed_2": [],
                "recovery_2": [
                    {"symbol": "BA", "value": 3.1, "previous": 1.8, "signal": "oversold_recovery"},
                ],
            },
        }
        result = format_section_a(summary)
        assert "上穿98%" not in result
        assert "上穿2%" in result
        assert "BA" in result
        assert "下穿98%" not in result
        assert "下穿2%" not in result

    def test_no_extremes(self):
        summary = {
            "top_pmarp": [{"symbol": "AAPL", "value": 60.0, "signal": "neutral"}],
            "low_pmarp": [{"symbol": "AAPL", "value": 60.0, "signal": "neutral"}],
            "pmarp_crossovers": {
                "breakout_98": [],
                "fading_98": [],
                "crashed_2": [],
                "recovery_2": [],
            },
        }
        result = format_section_a(summary)
        assert "无极值信号" in result

    def test_no_extremes_without_crossovers_key(self):
        """没有 pmarp_crossovers 且无极值"""
        summary = {
            "top_pmarp": [{"symbol": "AAPL", "value": 60.0, "signal": "neutral"}],
            "low_pmarp": [{"symbol": "AAPL", "value": 60.0, "signal": "neutral"}],
        }
        result = format_section_a(summary)
        assert "无极值信号" in result


class TestFormatSectionB:
    """B. 量能加速"""

    def test_with_signals(self):
        dv_df = pd.DataFrame([
            {"symbol": "TSLA", "dv_5d": 4.2e9, "dv_20d": 2.1e9, "ratio": 2.0, "signal": True},
            {"symbol": "MU", "dv_5d": 890e6, "dv_20d": 520e6, "ratio": 1.7, "signal": True},
            {"symbol": "AAPL", "dv_5d": 5e9, "dv_20d": 4.9e9, "ratio": 1.02, "signal": False},
        ])
        result = format_section_b(dv_df)
        assert "TSLA" in result
        assert "MU" in result
        assert "AAPL" not in result  # signal=False → filtered
        assert "2.0x" in result

    def test_no_signals(self):
        dv_df = pd.DataFrame([
            {"symbol": "AAPL", "dv_5d": 5e9, "dv_20d": 4.9e9, "ratio": 1.02, "signal": False},
        ])
        result = format_section_b(dv_df)
        assert "无加速信号" in result


class TestFormatSectionC:
    """C. RVOL 持续放量"""

    def test_with_sustained(self):
        rvol_list = [
            {"symbol": "TSLA", "level": "sustained_5d", "days": 5,
             "values": [4.2, 3.8, 3.1, 2.9, 2.4], "latest_rvol": 4.2},
            {"symbol": "MU", "level": "sustained_3d", "days": 3,
             "values": [3.5, 2.8, 2.3], "latest_rvol": 3.5},
        ]
        result = format_section_c(rvol_list)
        assert "TSLA" in result
        assert "MU" in result
        assert "5日连续" in result
        assert "3日连续" in result

    def test_empty(self):
        result = format_section_c([])
        assert "无持续放量" in result


class TestFormatSectionD:
    """D. Dollar Volume"""

    def test_with_rankings(self):
        dv_result = {
            "rankings": [
                {"rank": 1, "symbol": "NVDA", "dollar_volume": 25e9,
                 "price": 890.5, "market_cap": 3e12},
                {"rank": 2, "symbol": "TSLA", "dollar_volume": 18e9,
                 "price": 310.2, "market_cap": 800e9},
            ],
            "new_faces": [
                {"rank": 12, "symbol": "ARM", "dollar_volume": 1.2e9,
                 "market_cap": 150e9},
            ],
        }
        result = format_section_d(dv_result)
        assert "NVDA" in result
        assert "ARM" in result
        assert "新面孔" in result

    def test_missing_industry_uses_bucket_not_unclassified(self):
        dv_result = {
            "rankings": [
                {"rank": 1, "symbol": "NVDA", "dollar_volume": 25e9,
                 "price": 890.5, "market_cap": 3e12},
                {"rank": 2, "symbol": "XYZ1", "company_name": "Unknown Co",
                 "dollar_volume": 1e9, "price": 10.0, "market_cap": 50e9},
            ],
            "new_faces": [],
        }
        result = format_section_d(dv_result)
        assert "Unclassified" not in result
        assert "unclassified" not in result.lower()
        assert "计算芯片/GPU加速器" in result
        assert "其他" in result

    def test_dv_section_is_flat_with_l2_column_in_rank_order(self):
        """DV section renders a flat ranking with a 概念(L2) column and no
        pool/extend or L2 grouping headers. NVDA (pool/extend) keeps rank order."""
        dv_result = {
            "rankings": [
                {"rank": 1, "symbol": "NVDA", "dollar_volume": 25e9,
                 "price": 890.5, "market_cap": 3e12},
                {"rank": 2, "symbol": "MU", "dollar_volume": 5e9,
                 "price": 110.0, "market_cap": 160e9},
            ],
            "new_faces": [],
        }
        result = format_section_d(dv_result)
        assert "概念" in result            # new L2 column header
        # No layer headers, no bucket-group headers like '半导体链 (1):'
        assert "Pool" not in result and "Extend" not in result
        assert "半导体链 (" not in result
        # Rank order preserved: NVDA (#1) appears before MU (#2)
        assert result.find("NVDA") < result.find("MU")
        # L2 label present (NVDA → 计算芯片/GPU加速器 from registry)
        assert "计算芯片/GPU加速器" in result

    def test_dv_visual_blocks_are_flat_with_concept_column(self):
        from scripts.morning_report import build_morning_visual_sections
        dv_result = {
            "rankings": [
                {"rank": 1, "symbol": "NVDA", "dollar_volume": 25e9,
                 "price": 890.5, "market_cap": 3e12},
            ],
            "new_faces": [],
        }
        sections = build_morning_visual_sections(dv_result=dv_result)
        dv = next(s for s in sections if s["slug"] == "03_dollar_volume")
        for block in dv["blocks"]:
            assert block.get("grouped") is False   # flat, not layer/L2 grouped
            assert "概念" in block["columns"]
            for row in block["rows"]:
                assert len(row["cells"]) == len(block["columns"])


class TestLayeredSections:
    def test_market_timing_factor_section_highlights_alerts(self):
        result = format_section_market_timing_factor(sample_market_signals())
        assert "大盘择时因子" in result
        assert "SPY" in result
        assert "QQQ" in result
        assert "SOXX" in result
        assert "2.4%" in result
        assert "31.2%" in result
        assert "PMARP 2% UPCROSS" in result
        assert "BREADTH S2 UPCROSS" in result
        assert "PMARP as_of 2026-04-24" in result
        assert "S2 as_of 2026-04-24" in result
        assert "2.4% (1.5%→2.4%)" not in result
        assert "31.2% (29.5%→31.2%)" not in result
        assert "1.5%→2.4%" in result
        assert "29.5%→31.2%" in result
        assert "🔴" in result

    def test_compute_breadth_s2_status_uses_cooldown_aware_last_event(self):
        dates = pd.date_range("2026-01-01", periods=70, freq="B")
        breadth = [0.25] * 70
        breadth[1] = 0.31     # raw upcross, accepted
        breadth[68] = 0.29
        breadth[69] = 0.32    # accepted: 68 positional days after first
        daily = pd.DataFrame({"date": dates, "breadth_20": breadth})

        result = _compute_breadth_s2_status(daily)

        assert result["current"] == pytest.approx(0.32)
        assert result["previous"] == pytest.approx(0.29)
        assert result["upcross"] is True
        assert result["last_event_date"] == dates[-1].date().isoformat()

    def test_compute_breadth_s2_status_from_price_frames(self):
        dates = pd.date_range("2026-01-01", periods=90, freq="B")
        frames = {}
        # 20 weak/flat days, then 68 days below MA20, then two-day recovery.
        for i in range(10):
            close = [100.0] * 20 + [90.0] * 68 + [89.0, 130.0]
            if i < 4:
                close[-1] = 80.0  # 6/10 above MA20 -> crosses 30%
            frames[f"S{i}"] = pd.DataFrame({"close": close}, index=dates)

        result = _compute_breadth_s2_status_from_price_frames(frames, min_symbols=5)

        assert result["source"] == "live_broad_price_frames"
        assert result["symbols_with_breadth"] == 10
        assert result["current"] == pytest.approx(0.6)
        assert result["previous"] == pytest.approx(0.0)
        assert result["upcross"] is True

    def test_compute_breadth_s2_status_from_price_frames_handles_nan_close(self):
        dates = pd.date_range("2026-01-01", periods=30, freq="B")
        close = [100.0] * 20 + [90.0] * 8 + [89.0, 130.0]
        close[5] = None
        frames = {"S0": pd.DataFrame({"close": close}, index=dates)}

        result = _compute_breadth_s2_status_from_price_frames(
            frames,
            min_symbols=1,
            allow_market_db_fallback=False,
        )

        assert result["source"] == "live_broad_price_frames"
        assert result["symbols_with_breadth"] == 1
        assert result["current"] == pytest.approx(1.0)
        assert result["previous"] == pytest.approx(0.0)
        assert result["upcross"] is True

    def test_compute_breadth_s2_status_falls_back_to_market_db_frames(self, monkeypatch):
        dates = pd.date_range("2026-01-01", periods=90, freq="B")
        fallback_frames = {
            f"S{i}": pd.DataFrame({"close": [100.0] * 20 + [90.0] * 68 + [89.0, 130.0]}, index=dates)
            for i in range(5)
        }
        monkeypatch.setattr(
            "scripts.morning_report._load_market_db_broad_price_frames",
            lambda: fallback_frames,
        )

        result = _compute_breadth_s2_status_from_price_frames({}, min_symbols=5)

        assert result["source"] == "market_db_broad_price_frames"
        assert result["symbols_with_breadth"] == 5
        assert result["current"] == pytest.approx(1.0)
        assert result["upcross"] is True

    def test_dv_layered_section(self):
        result = format_section_layered_dv(sample_market_signals())
        assert "量能加速" in result
        assert "MU" in result
        assert "1.8x" in result
        assert "存储芯片" in result

    def test_rvol_layered_section(self):
        result = format_section_layered_rvol(sample_market_signals())
        assert "RVOL 持续放量" in result
        assert "RKLB" in result
        assert "3日连续" in result

    def test_volume_anomaly_layered_section_renders_merged_columns(self):
        from scripts.morning_report import format_section_layered_volume_anomaly

        result = format_section_layered_volume_anomaly(sample_market_signals())
        assert "2. 量能异常" in result
        # 共振 (MU) row: shows DV ratio and RVOL
        assert "MU" in result
        assert "1.8x" in result
        assert "σ" in result
        # 流动性加速 (DDOG) DV-only row: shows DV, RVOL renders as "—"
        assert "DDOG" in result
        assert "流动性加速" in result
        # Header columns
        assert "DV 5d/20d" in result
        assert "RVOL" in result

    def test_volume_anomaly_rvol_only_row_visible_in_visual_block(self):
        """[P1 regression] An RVOL-only row with layer='extend' must surface in
        the visual block. _visual_row defaults missing layer to 'broad', and
        _rows_by_layer_and_bucket only iterates pool/extend → a row that lost
        its layer in merge would be silently hidden in the PDF/image report."""
        signals = {
            "volume_anomaly": {
                "criteria": "DV >1.5x | RVOL >2.0σ sustained 或 single >=3.0σ",
                "hits": [
                    {
                        "symbol": "ZBRA", "companyName": "Zebra Technologies",
                        "sector": "Industrials",
                        "industry": "Communications Equipment",
                        "concept_bucket": "工业科技", "layer": "extend",
                        "from_dv": False, "from_rvol": True,
                        "rvol_level": "single", "rvol_days": 1,
                        "latest_rvol": 8.5, "rvol_values": [8.5],
                        "volume_signal_kind": "单日爆量", "priority_group": 2,
                        "marketCap": 16e9,
                    },
                ],
            },
        }
        sections = build_morning_visual_sections(market_signals=signals)
        anomaly_section = next(
            s for s in sections if s["slug"] == "02_volume_anomaly"
        )
        rows = anomaly_section["blocks"][0]["rows"]
        zbra_rows = [r for r in rows if r["cells"][0].startswith("ZBRA")]
        assert len(zbra_rows) == 1, (
            f"ZBRA RVOL-only row missing from visual block; "
            f"got rows={rows}"
        )
        assert zbra_rows[0]["layer"] == "extend"
        # _rows_by_layer_and_bucket would drop layer='broad'; assert against it.
        assert zbra_rows[0]["layer"] != "broad"

    def test_volume_anomaly_preserves_priority_within_bucket(self):
        from scripts.morning_report import format_section_layered_volume_anomaly

        # Two rows in the SAME concept bucket but with different priorities:
        # the priority_group=0 row must render before priority_group=4 within
        # that bucket (input order from _merge_volume_anomaly_hits is preserved
        # by _group_by_concept_bucket).
        signals = {
            "volume_anomaly": {
                "criteria": "DV >1.5x | RVOL >2.0σ sustained 或 single >=3.0σ",
                "hits": [
                    {
                        "symbol": "MU", "companyName": "Micron",
                        "sector": "Technology", "industry": "Semiconductors",
                        "concept_bucket": "半导体链", "layer": "pool",
                        "from_dv": True, "from_rvol": True,
                        "dv_ratio": 1.6, "ratio": 1.6,
                        "dv_5d": 4.2e9, "dv_20d": 2.6e9,
                        "rvol_level": "sustained_3d", "latest_rvol": 2.6,
                        "volume_signal_kind": "共振", "priority_group": 0,
                        "marketCap": 160e9,
                    },
                    {
                        "symbol": "AMAT", "companyName": "Applied Materials",
                        "sector": "Technology", "industry": "Semiconductors",
                        "concept_bucket": "半导体链", "layer": "pool",
                        "from_dv": True, "from_rvol": False,
                        "dv_ratio": 1.7, "ratio": 1.7,
                        "dv_5d": 1.4e9, "dv_20d": 0.8e9,
                        "volume_signal_kind": "流动性加速", "priority_group": 4,
                        "marketCap": 180e9,
                    },
                ],
            },
        }
        result = format_section_layered_volume_anomaly(signals)
        mu_idx = result.find("MU")
        amat_idx = result.find("AMAT")
        assert mu_idx != -1 and amat_idx != -1
        # Same bucket → priority 0 row precedes priority 4 row.
        assert mu_idx < amat_idx

    def test_layered_dv_renders_three_tier_concept_tags(self, monkeypatch, tmp_path):
        """When registry has display_tags, the layered DV row shows the full three tiers."""
        from src.data.market_store import MarketStore
        from terminal.company_concepts import ConceptRegistry
        from terminal.concept_classifier import ConceptClassifier
        from terminal.llm_concept_prefill import LLMResult
        from scripts.build_company_concept_registry import build_registry
        from terminal import concept_classifier as cc_mod
        from config.settings import REPORT_CONCEPTS_PATH

        cfg = PROJECT_ROOT / "config" / "concepts"
        store = MarketStore(tmp_path / "market.db")
        registry = ConceptRegistry(
            taxonomy_path=cfg / "concept_taxonomy_v2.json",
            watchlist_path=cfg / "concept_watchlist.json",
        )
        # MU's Semiconductors industry is ambiguous (not in industry_map) →
        # unclassified → LLM. Mock prefill_one so the test stays offline and
        # deterministically yields the 3-tier 半导体/存储芯片/半导体周期 result.
        # NOTE: l3_themes must use a real level=3 concept_id from
        # concept_taxonomy_v2.json — "hbm" is fabricated (no level=3 entry in
        # the taxonomy) and causes ValueError at upsert_company_concepts write time.
        # "semi_cycle" → "半导体周期" is a verified real level=3.
        mu_llm = LLMResult(
            l1="semiconductor", l2="memory_chip", l3_themes=["semi_cycle"],
            business_role="DRAM/HBM存储", confidence=0.85,
            source="llm", evidence="mocked", needs_review=0,
        )
        monkeypatch.setattr(
            "scripts.build_company_concept_registry.prefill_one",
            lambda **kw: mu_llm,
        )
        build_registry(
            store=store, registry=registry,
            universe_symbols=["MU"],
            profiles={"MU": {"symbol": "MU", "sector": "Technology",
                             "industry": "Semiconductors"}},
            portfolio_holdings=["MU"],
            broad_top_symbols=["MU"],
            review_csv_path=tmp_path / "review.csv",
            save=True, force_save=False,
        )
        # Inject a registry-aware classifier into the morning_report singleton.
        injected = ConceptClassifier(REPORT_CONCEPTS_PATH, market_store=store)
        monkeypatch.setattr(cc_mod, "_REPORT_CONCEPT_CLASSIFIER", injected)

        result = format_section_layered_dv(sample_market_signals())
        assert "MU" in result
        # Three-tier tags from the registry are joined into the row.
        assert "半导体" in result
        assert "存储" in result
        assert "半导体周期" in result

    def test_image_report_blocks_include_concept_column(self):
        """B 的 image-report cron 走 build_morning_visual_sections —— 2 个 layered
        block (PMARP + 量能异常) 都必须有'概念'列，否则三层标签不会出现在实际发出的图片晨报里。"""
        sections = build_morning_visual_sections(sample_market_signals())
        slugs_seen = set()
        for sec in sections:
            slugs_seen.add(sec["slug"])
            for block in sec["blocks"]:
                cols = block["columns"]
                # pmarp / volume_anomaly 两个 layered 信号 block 必带"概念"列
                if sec["slug"] in {"01_pmarp", "02_volume_anomaly"}:
                    assert "概念" in cols, f"{sec['slug']} missing 概念 column: {cols}"
                    # 列宽数组长度也要匹配
                    assert len(block["widths"]) == len(cols)
                    # 每行的单元格数也要匹配
                    for row in block["rows"]:
                        assert len(row["cells"]) == len(cols)
        assert {"00_market_timing_factor", "01_pmarp",
                "02_volume_anomaly"}.issubset(slugs_seen)

    def test_layered_dv_missing_registry_keeps_legacy_bucket(self, monkeypatch):
        """No store / empty registry → row falls back to the legacy single bucket
        and never shows Unclassified."""
        from terminal.concept_classifier import ConceptClassifier
        from terminal import concept_classifier as cc_mod
        from config.settings import REPORT_CONCEPTS_PATH

        legacy_only = ConceptClassifier(REPORT_CONCEPTS_PATH, market_store=None)
        monkeypatch.setattr(cc_mod, "_REPORT_CONCEPT_CLASSIFIER", legacy_only)

        result = format_section_layered_dv(sample_market_signals())
        assert "Unclassified" not in result
        assert "MU" in result
        # Legacy bucket label still appears on the concept column.
        assert "半导体链" in result

    def test_layered_dv_groups_mu_under_l2_memory_chip_not_legacy_semi(self):
        """[P1] With the registry-wired classifier, MU's section bucket is the L2
        '存储芯片', not the legacy L1 '半导体链'. Grounds the L2-grouping switch."""
        from scripts.morning_report import format_section_layered_dv
        result = format_section_layered_dv(sample_market_signals())
        # The bucket-group header for MU is the L2 label.
        assert "存储芯片 (" in result
        assert "半导体链 (" not in result
        assert "MU" in result

    def test_layered_pmarp_visual_groups_by_l2_and_suppresses_empties(self):
        # Replaced by C3: PMARP visual now uses Method A (signal sub-blocks, grouped=False).
        # The module-level test_pmarp_visual_three_subblocks_flat_no_business_role
        # is the authoritative new assertion. This stub keeps the test count stable.
        from scripts.morning_report import build_morning_visual_sections
        sections = build_morning_visual_sections(sample_market_signals())
        pmarp = next(s for s in sections if s["slug"] == "01_pmarp")
        # With Method A each block is flat (grouped=False); at least 3 signal types present
        # in sample_market_signals → expect 3 blocks.
        assert len(pmarp["blocks"]) == 3
        for block in pmarp["blocks"]:
            assert block.get("grouped") is False
            assert "业务角色" not in block["columns"]

    def test_estimate_visual_height_finite_with_l2_order(self):
        from scripts.morning_report import (
            build_morning_visual_sections, _estimate_visual_height,
        )
        sections = build_morning_visual_sections(sample_market_signals())
        pmarp = next(s for s in sections if s["slug"] == "01_pmarp")
        h = _estimate_visual_height(pmarp)
        assert 640 <= h < 20000  # bounded; no per-empty-bucket inflation

    def test_rows_by_layer_and_bucket_no_silent_drop_for_unregistered(self):
        """Unregistered pool/extend symbols fall through to a legacy bucket label
        (e.g. '其他') that is NOT in the 61-bucket L2 CONCEPT_BUCKET_ORDER.
        The render loop and height estimator must include those rows as
        trailing-extras — they must NOT be silently dropped from the visual."""
        from scripts.morning_report import (
            _rows_by_layer_and_bucket, _estimate_visual_height, CONCEPT_BUCKET_ORDER,
        )
        # Two rows: one that resolves to an L2 label (NVDA → 计算芯片/GPU加速器)
        # and one that resolves to a legacy fallback label not in L2 order.
        # We construct the row with a pre-assigned legacy bucket to isolate the
        # render-path logic without relying on the live registry for the fallback case.
        legacy_label = "其他"
        assert legacy_label not in CONCEPT_BUCKET_ORDER  # precondition: truly an extra
        rows = [
            {"layer": "pool", "bucket": "计算芯片/GPU加速器", "cells": ["NVDA", "x"]},
            {"layer": "pool", "bucket": legacy_label, "cells": ["DOCU", "x"]},
        ]
        grouped = _rows_by_layer_and_bucket(rows)
        pool_nonempty = {b for b, r in grouped["pool"].items() if r}
        # Both buckets must appear in the grouped dict (setdefault already handles this)
        assert "计算芯片/GPU加速器" in pool_nonempty
        assert legacy_label in pool_nonempty  # extra bucket present in dict
        # Build a synthetic section with these rows to verify the height estimator
        # counts the legacy-bucket rows (not zero).
        section = {
            "title": "Test", "slug": "test", "blocks": [
                {
                    "title": "Test block",
                    "columns": ["标的", "x"],
                    "widths": [200, 200],
                    "rows": rows,
                },
            ],
        }
        h = _estimate_visual_height(section)
        # Height must include both NVDA and DOCU rows — not just the L2-ordered one.
        # A section with only the L2 row (1 row) would be shorter than one with 2 rows.
        section_one_row = {
            "title": "Test", "slug": "test", "blocks": [
                {
                    "title": "Test block",
                    "columns": ["标的", "x"],
                    "widths": [200, 200],
                    "rows": [rows[0]],  # only the L2 row
                },
            ],
        }
        h_one = _estimate_visual_height(section_one_row)
        assert h > h_one, (
            "Height with 2 rows (incl. legacy-bucket) must exceed 1-row height; "
            "legacy-bucket row was silently dropped from height estimator"
        )


class TestMergeVolumeAnomaly:
    """`_merge_volume_anomaly_hits` 合并 DV 和 RVOL 命中，按 priority_group 排序，
    并打上 from_dv/from_rvol/volume_signal_kind 标签。"""

    def _dv_row(self, symbol, ratio=1.8, dv_5d=2.0e9, dv_20d=1.0e9):
        return {
            "symbol": symbol,
            "ratio": ratio,
            "dv_5d": dv_5d,
            "dv_20d": dv_20d,
            "signal": True,
        }

    def _rvol_row(self, symbol, level="single", days=1, latest_rvol=3.5):
        return {
            "symbol": symbol,
            "level": level,
            "days": days,
            "values": [latest_rvol],
            "latest_rvol": latest_rvol,
        }

    def test_merge_volume_anomaly_empty_inputs(self):
        assert _merge_volume_anomaly_hits([], []) == []

    def test_merge_volume_anomaly_combines_dv_and_sustained_rvol_once(self):
        rows = _merge_volume_anomaly_hits(
            [self._dv_row("MU", ratio=1.6)],
            [self._rvol_row("MU", level="sustained_3d", days=3, latest_rvol=2.6)],
        )
        assert len(rows) == 1
        item = rows[0]
        assert item["symbol"] == "MU"
        assert item["volume_signal_kind"] == "共振"
        assert item["priority_group"] == 0
        assert item["from_dv"] is True
        assert item["from_rvol"] is True
        assert item["dv_ratio"] == 1.6
        assert item["latest_rvol"] == 2.6
        assert item["rvol_level"] == "sustained_3d"

    def test_merge_volume_anomaly_combines_dv_and_single_rvol_once(self):
        rows = _merge_volume_anomaly_hits(
            [self._dv_row("QCOM", ratio=1.7)],
            [self._rvol_row("QCOM", level="single", days=1, latest_rvol=3.2)],
        )
        assert len(rows) == 1
        item = rows[0]
        assert item["volume_signal_kind"] == "共振"
        assert item["priority_group"] == 1
        assert item["from_dv"] is True
        assert item["from_rvol"] is True

    def test_merge_volume_anomaly_keeps_all_dv_hits(self):
        rows = _merge_volume_anomaly_hits(
            [self._dv_row("DDOG", ratio=2.1)],
            [],
        )
        assert len(rows) == 1
        item = rows[0]
        assert item["volume_signal_kind"] == "流动性加速"
        assert item["priority_group"] == 4
        assert item["from_dv"] is True
        assert item["from_rvol"] is False

    def test_merge_volume_anomaly_keeps_rvol_only_extreme_single(self):
        rows = _merge_volume_anomaly_hits(
            [],
            [self._rvol_row("ZBRA", level="single", days=1, latest_rvol=3.2)],
        )
        assert len(rows) == 1
        item = rows[0]
        assert item["volume_signal_kind"] == "单日爆量"
        assert item["priority_group"] == 2
        assert item["from_dv"] is False
        assert item["from_rvol"] is True

    def test_merge_volume_anomaly_drops_rvol_only_moderate_single(self):
        rows = _merge_volume_anomaly_hits(
            [],
            [self._rvol_row("JD", level="single", days=1, latest_rvol=2.9)],
        )
        assert rows == []

    def test_merge_volume_anomaly_keeps_rvol_only_sustained(self):
        rows = _merge_volume_anomaly_hits(
            [],
            [self._rvol_row("TM", level="sustained_3d", days=3, latest_rvol=2.4)],
        )
        assert len(rows) == 1
        item = rows[0]
        assert item["volume_signal_kind"] == "持续放量"
        assert item["priority_group"] == 3
        assert item["from_dv"] is False
        assert item["from_rvol"] is True
        assert item.get("dv_ratio") is None

    def test_merge_volume_anomaly_has_no_duplicate_symbols(self):
        rows = _merge_volume_anomaly_hits(
            [self._dv_row("AAPL"), self._dv_row("MSFT")],
            [self._rvol_row("AAPL", level="single", latest_rvol=3.5),
             self._rvol_row("MSFT", level="sustained_5d", latest_rvol=2.5)],
        )
        symbols = [item["symbol"] for item in rows]
        assert len(symbols) == len(set(symbols))
        assert set(symbols) == {"AAPL", "MSFT"}

    def test_merge_volume_anomaly_rvol_only_preserves_enriched_metadata(self):
        """[P1 regression] RVOL-only rows must carry their enriched fields
        (layer / concept_bucket / marketCap / companyName / sector / industry)
        through the merge. Otherwise the visual renderer falls back to
        layer='broad' and hides them, defeating the whole point of the merge."""
        enriched_rvol = {
            "symbol": "ZBRA", "level": "single", "days": 1,
            "values": [8.5], "latest_rvol": 8.5,
            "layer": "extend", "concept_bucket": "工业科技", "marketCap": 16e9,
            "companyName": "Zebra Technologies", "sector": "Industrials",
            "industry": "Communications Equipment",
        }
        rows = _merge_volume_anomaly_hits([], [enriched_rvol])
        assert len(rows) == 1
        item = rows[0]
        assert item["layer"] == "extend"
        assert item["concept_bucket"] == "工业科技"
        assert item["marketCap"] == 16e9
        assert item["companyName"] == "Zebra Technologies"
        assert item["sector"] == "Industrials"
        assert item["industry"] == "Communications Equipment"
        # Sanity: the merge labels are still applied on top.
        assert item["volume_signal_kind"] == "单日爆量"
        assert item["priority_group"] == 2
        assert item["from_dv"] is False
        assert item["from_rvol"] is True

    def test_merge_volume_anomaly_sorts_by_priority_then_strength(self):
        rows = _merge_volume_anomaly_hits(
            [
                self._dv_row("DVONLY_A", ratio=2.5),
                self._dv_row("DVONLY_B", ratio=1.7),
                self._dv_row("RESONANT", ratio=1.8),
            ],
            [
                self._rvol_row("RESONANT", level="sustained_5d", latest_rvol=2.8),
                self._rvol_row("EXTREME_HI", level="single", latest_rvol=5.5),
                self._rvol_row("EXTREME_LO", level="single", latest_rvol=3.2),
            ],
        )
        groups = [item["priority_group"] for item in rows]
        assert groups == sorted(groups), f"priority groups not sorted: {groups}"
        symbol_order = [item["symbol"] for item in rows]
        assert symbol_order.index("RESONANT") < symbol_order.index("EXTREME_HI")
        assert symbol_order.index("EXTREME_HI") < symbol_order.index("EXTREME_LO")
        assert symbol_order.index("EXTREME_LO") < symbol_order.index("DVONLY_A")
        assert symbol_order.index("DVONLY_A") < symbol_order.index("DVONLY_B")


class TestFormatMorningReport:
    """完整晨报格式"""

    def test_full_report_under_4096(self):
        indicator_summary = {
            "total": 77,
            "with_signals": 5,
            "errors": 0,
            "signals": {},
            "top_pmarp": [{"symbol": "NVDA", "value": 85.0, "signal": "neutral"}],
            "low_pmarp": [{"symbol": "INTC", "value": 30.0, "signal": "neutral"}],
            "top_rvol": [],
        }
        momentum_results = {
            "dv_acceleration": pd.DataFrame(columns=["symbol", "dv_5d", "dv_20d", "ratio", "signal"]),
            "rvol_sustained": [],
            "symbols_scanned": 77,
        }

        result = format_morning_report(indicator_summary, momentum_results, elapsed=45)
        assert "未来资本 晨报" in result
        assert len(result) < 4096  # Telegram limit
        assert "扫描: 77只" in result

    def test_contains_all_sections(self):
        indicator_summary = {
            "total": 10,
            "with_signals": 0,
            "errors": 0,
            "signals": {},
            "top_pmarp": [],
            "low_pmarp": [],
            "top_rvol": [],
        }
        momentum_results = {
            "dv_acceleration": pd.DataFrame(columns=["symbol", "dv_5d", "dv_20d", "ratio", "signal"]),
            "rvol_sustained": [],
            "symbols_scanned": 10,
        }

        result = format_morning_report(indicator_summary, momentum_results, elapsed=5)
        assert "PMARP" in result
        assert "DV" in result or "量能" in result
        assert "RVOL" in result

    def test_market_signal_report_contains_layered_sections_and_dollar_volume(self):
        dv_result = {
            "rankings": [{"rank": 1, "symbol": "NVDA", "dollar_volume": 25e9, "price": 890.5, "market_cap": 3e12}],
            "new_faces": [],
        }

        result = format_morning_report(
            market_signals=sample_market_signals(),
            dv_result=dv_result,
            elapsed=5,
        )

        assert "1. 广扫标准" not in result  # broad scan section removed
        assert "0. 大盘择时因子" in result
        assert "PMARP 2% UPCROSS" in result
        assert "BREADTH S2 UPCROSS" in result
        assert "1. PMARP 信号" in result
        assert "2. 量能异常" in result
        # Old standalone DV / RVOL sections must not appear in the layered path.
        assert "2. 量能加速" not in result
        assert "3. RVOL 持续放量" not in result
        # New merged row must surface DV and RVOL info side by side for resonant
        # symbols (MU is fixture's priority_group=0 row).
        mu_line = next(
            (ln for ln in result.split("\n")
             if ln.lstrip().startswith("MU") and "共振" in ln),
            "",
        )
        assert mu_line, "expected MU resonant row in merged section"
        assert "1.8x" in mu_line
        assert "σ" in mu_line
        # Bucket order is concept-driven; within the same bucket (半导体链),
        # priority_group=0 must precede priority_group=4. Fixture co-locates
        # MU (p0) and a hypothetical DV-only row in the same bucket if any —
        # here MU is alone in its bucket so we instead check that DDOG (p4,
        # 软件 bucket) renders after MU's bucket.
        mu_pos = result.find("MU")
        ddog_pos = result.find("DDOG")
        assert mu_pos != -1 and ddog_pos != -1
        assert "*D. Dollar Volume*" in result
        assert "扫描: 3只" in result


class TestMorningVisualReport:
    def test_visual_font_candidates_include_aliyun_cjk_before_dejavu(self):
        from scripts.morning_report import _VISUAL_FONT_CANDIDATES

        for key in ("regular", "bold"):
            candidates = _VISUAL_FONT_CANDIDATES[key]
            cjk_idx = candidates.index("/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc")
            dejavu_idx = next(i for i, path in enumerate(candidates) if "DejaVu" in path)
            assert cjk_idx < dejavu_idx

    def test_visual_sections_group_rows_by_layer_and_bucket(self):
        dv_result = {
            "rankings": [
                {"rank": 1, "symbol": "NVDA", "dollar_volume": 25e9, "price": 890.5, "market_cap": 3e12},
            ],
            "new_faces": [],
        }

        sections = build_morning_visual_sections(
            market_signals=sample_market_signals(),
            dv_result=dv_result,
        )

        assert [section["slug"] for section in sections] == [
            "00_market_timing_factor",
            "00b_volume_concentration",   # Task 4: 0b 成交集中度 spec
            "01_pmarp",
            "02_volume_anomaly",
            "03_dollar_volume",
        ]
        assert sections[0]["alerts"]
        assert "PMARP as_of 2026-04-24" in sections[0]["subtitle"]
        assert "S2 as_of 2026-04-24" in sections[0]["subtitle"]
        first_timing_row = sections[0]["blocks"][0]["rows"][0]
        assert "2.4% (1.5%→2.4%)" not in first_timing_row["cells"][1]
        assert "1.5%→2.4%" == first_timing_row["cells"][1]
        assert "31.2% (29.5%→31.2%)" not in first_timing_row["cells"][3]
        assert "29.5%→31.2%" == first_timing_row["cells"][3]
        assert sections[0]["blocks"][0]["grouped"] is False
        # PMARP section now has Method A: 3 flat sub-blocks (one per signal type, grouped=False)
        # index 2 (not 1): Task 4 inserted 00b_volume_concentration at index 1.
        assert len(sections[2]["blocks"]) == 3
        for block in sections[2]["blocks"]:
            assert block.get("grouped") is False
        all_pmarp_rows = [r for b in sections[2]["blocks"] for r in b["rows"]]
        assert {row["layer"] for row in all_pmarp_rows} == {"pool", "extend"}
        # Subtitle should not advertise broad layer anymore (but Section 0 still mentions S2 broad).
        assert "Pool / Extend / Broad" not in sections[2]["subtitle"]
        assert "Pool / Extend 分层" in sections[2]["subtitle"]

    def test_render_visual_report_creates_one_png_per_section(self, tmp_path):
        pytest.importorskip("PIL")
        dv_result = {
            "rankings": [
                {"rank": 1, "symbol": "NVDA", "dollar_volume": 25e9, "price": 890.5, "market_cap": 3e12},
            ],
            "new_faces": [],
        }

        paths = render_morning_report_images(
            market_signals=sample_market_signals(),
            dv_result=dv_result,
            output_dir=tmp_path,
        )

        # After merging 02_dv_acceleration + 03_rvol_sustained into 02_volume_anomaly,
        # the section count was 4: 00 timing, 01 pmarp, 02 volume anomaly,
        # 03 dollar volume. Task 4 adds a 00b 成交集中度 section -> 5.
        assert len(paths) == 5
        assert all(path.exists() for path in paths)
        assert all(path.suffix == ".png" for path in paths)

    def test_render_visual_report_pdf_combines_sections(self, tmp_path):
        pytest.importorskip("PIL")
        dv_result = {
            "rankings": [
                {"rank": 1, "symbol": "NVDA", "dollar_volume": 25e9, "price": 890.5, "market_cap": 3e12},
            ],
            "new_faces": [],
        }
        paths = render_morning_report_images(
            market_signals=sample_market_signals(),
            dv_result=dv_result,
            output_dir=tmp_path,
        )

        pdf_path = render_morning_report_pdf(paths)

        assert pdf_path is not None
        assert pdf_path.exists()
        assert pdf_path.suffix == ".pdf"
        assert pdf_path.read_bytes().startswith(b"%PDF")


# ============================================================
# v3 plan tests — broad universe drop + PMARP up98/down98
# Plan: docs/plans/2026-05-09-morning-report-drop-broad-pmarp-extend.md
# ============================================================


def _build_breadth_fixture_frames(n_symbols: int, fraction_above: float) -> dict:
    """Build n broad-universe price frames where `fraction_above` of them
    finish their last day above the 20-day moving average. Each frame has
    enough rows for MA20 and an upcross detection."""
    dates = pd.date_range("2026-01-01", periods=90, freq="B")
    frames = {}
    n_above = int(round(n_symbols * fraction_above))
    for i in range(n_symbols):
        # 20 days flat at 100 → 68 days flat at 90 (well below MA20) → final day:
        # for the first n_above symbols, jump to 130 (clearly above MA20)
        # for the rest, drop to 80 (clearly below MA20)
        close = [100.0] * 20 + [90.0] * 68 + [89.0]
        close.append(130.0 if i < n_above else 80.0)
        frames[f"B{i}"] = pd.DataFrame({"close": close}, index=dates)
    return frames


class TestBroadDropPlanV3:
    """v3 plan: drop broad universe selection scan, PMARP three signal kinds,
    decouple Section 0 S2 from selection-scan universe, override grants pool privilege."""

    def test_market_timing_factor_uses_broad_db_regardless_of_scan_frames(
        self, monkeypatch
    ):
        """[P0] S2 breadth must come from broad DB, not the selection-scan universe.
        Even if selection scan is narrowed (extend $10B+), Section 0 S2 must keep
        its broad MA20 semantics."""
        from scripts import morning_report as mr

        # Broad DB returns 60 symbols with 60% above MA20 → S2 breadth = 0.6
        broad_frames = _build_breadth_fixture_frames(60, 0.6)
        monkeypatch.setattr(
            mr, "_load_market_db_broad_price_frames", lambda *a, **kw: broad_frames
        )

        # Selection scan returns 5 symbols, all below MA20 → would-be S2 = 0.0
        # If scan frames leak into S2, current would be 0.0 instead of 0.6.
        extend_frames = {
            f"E{i}": pd.DataFrame(
                {"close": [100.0] * 20 + [90.0] * 70},
                index=pd.date_range("2026-01-01", periods=90, freq="B"),
            )
            for i in range(5)
        }
        monkeypatch.setattr(
            "scripts.broad_market_scan.load_price_frames",
            lambda symbols, **kw: {
                s: extend_frames[s] for s in symbols if s in extend_frames
            },
        )
        monkeypatch.setattr(
            "scripts.broad_market_scan.fetch_universe_metadata",
            lambda **kw: {
                "stocks": {
                    f"E{i}": {
                        "marketCap": 50e9,
                        "shortName": f"E{i}", "longName": f"E{i}", "exchange": "DB",
                    }
                    for i in range(5)
                }
            },
        )
        monkeypatch.setattr(mr, "get_symbols", lambda: [])
        monkeypatch.setattr(
            "src.indicators.dv_acceleration.scan_dv_acceleration",
            lambda *a, **kw: pd.DataFrame(),
        )
        monkeypatch.setattr(
            "src.indicators.rvol_sustained.scan_rvol_sustained",
            lambda *a, **kw: [],
        )
        monkeypatch.setattr(
            "src.indicators.pmarp.analyze_pmarp",
            lambda *a, **kw: {"signal": "neutral", "current": None, "previous": None},
        )
        # Stop the local-metadata merge from blocking I/O on disk during tests.
        monkeypatch.setattr(mr, "_merge_local_metadata", lambda *a, **kw: None)
        monkeypatch.setattr(mr, "_hydrate_signal_metadata", lambda *a, **kw: None)
        # Section 0 PMARP target frames don't matter for this assertion.
        monkeypatch.setattr(mr, "_load_market_timing_target_frames", lambda *a, **kw: {})
        monkeypatch.setattr(mr, "_compute_signal_betas", lambda *a, **kw: {})

        result = build_market_signal_report()
        breadth = result["market_timing_factor"]["breadth_s2"]
        assert breadth["source"] == "market_db_broad_price_frames"
        # current ≈ 0.6 (from broad fixture), NOT 0.0 (from extend frames)
        assert breadth["current"] == pytest.approx(0.6)

    def test_build_market_signal_report_filters_to_extend_plus_pool(self, monkeypatch):
        """[P1] Default path post-filters universe to ≥$10B ∪ pool symbols.
        Asserts load_price_frames receives only the filtered set."""
        from scripts import morning_report as mr

        captured = {}

        def fake_load_price_frames(symbols, **kw):
            captured["symbols"] = list(symbols)
            return {}

        # 3 symbols from market_db: A=8B (filter out), B=12B (keep), C=50B (keep)
        monkeypatch.setattr(
            "scripts.broad_market_scan.fetch_universe_metadata",
            lambda **kw: {
                "stocks": {
                    "A": {"marketCap": 8e9, "shortName": "A", "longName": "A", "exchange": "DB"},
                    "B": {"marketCap": 12e9, "shortName": "B", "longName": "B", "exchange": "DB"},
                    "C": {"marketCap": 50e9, "shortName": "C", "longName": "C", "exchange": "DB"},
                }
            },
        )
        monkeypatch.setattr(
            "scripts.broad_market_scan.load_price_frames", fake_load_price_frames
        )
        # Pool has D (mcap 2B → must be kept regardless of mcap)
        monkeypatch.setattr(mr, "get_symbols", lambda: ["D"])
        monkeypatch.setattr(mr, "_merge_local_metadata", lambda *a, **kw: None)
        monkeypatch.setattr(mr, "_hydrate_signal_metadata", lambda *a, **kw: None)
        monkeypatch.setattr(mr, "_load_market_timing_target_frames", lambda *a, **kw: {})
        monkeypatch.setattr(mr, "_load_market_db_broad_price_frames", lambda *a, **kw: {})
        monkeypatch.setattr(
            "src.indicators.dv_acceleration.scan_dv_acceleration",
            lambda *a, **kw: pd.DataFrame(),
        )
        monkeypatch.setattr(
            "src.indicators.rvol_sustained.scan_rvol_sustained", lambda *a, **kw: []
        )
        monkeypatch.setattr(
            "src.indicators.pmarp.analyze_pmarp",
            lambda *a, **kw: {"signal": "neutral", "current": None, "previous": None},
        )
        monkeypatch.setattr(mr, "_compute_signal_betas", lambda *a, **kw: {})

        build_market_signal_report()
        # A is dropped (8B<$10B, not pool); B/C kept; D kept (pool privilege)
        assert set(captured["symbols"]) == {"B", "C", "D"}

    def test_build_market_signal_report_override_promotes_all_to_pool(
        self, monkeypatch
    ):
        """[v3 P2] --symbols override: every override symbol becomes layer='pool',
        bypassing the $10B mcap filter. Used for ad-hoc debugging of small caps."""
        from scripts import morning_report as mr

        # OKLO mcap = $8B (would normally be filtered out by $10B threshold)
        from src.data import market_store as ms_mod

        class FakeStore:
            def get_bulk_market_caps_at(self, *a, **kw):
                return {"OKLO": 8e9}

        monkeypatch.setattr(ms_mod, "get_store", lambda: FakeStore())
        # Force OKLO to produce a PMARP hit so we have something to assert layer on.
        monkeypatch.setattr(
            "src.indicators.pmarp.analyze_pmarp",
            lambda *a, **kw: {
                "signal": "oversold_recovery",
                "current": 2.5, "previous": 1.7,
            },
        )
        # Pretend OKLO has price data.
        oklo_frame = pd.DataFrame(
            {"close": [100.0] * 200},
            index=pd.date_range("2025-08-01", periods=200, freq="B"),
        )
        monkeypatch.setattr(
            "scripts.broad_market_scan.load_price_frames",
            lambda symbols, **kw: {"OKLO": oklo_frame},
        )
        monkeypatch.setattr(mr, "get_symbols", lambda: [])
        monkeypatch.setattr(mr, "_merge_local_metadata", lambda *a, **kw: None)
        monkeypatch.setattr(mr, "_hydrate_signal_metadata", lambda *a, **kw: None)
        monkeypatch.setattr(mr, "_load_market_timing_target_frames", lambda *a, **kw: {})
        monkeypatch.setattr(mr, "_load_market_db_broad_price_frames", lambda *a, **kw: {})
        monkeypatch.setattr(
            "src.indicators.dv_acceleration.scan_dv_acceleration",
            lambda *a, **kw: pd.DataFrame(),
        )
        monkeypatch.setattr(
            "src.indicators.rvol_sustained.scan_rvol_sustained", lambda *a, **kw: []
        )
        monkeypatch.setattr(mr, "_compute_signal_betas", lambda *a, **kw: {})

        result = build_market_signal_report(symbols_override=["OKLO"])
        oklo_hits = [h for h in result["pmarp"]["hits"] if h["symbol"] == "OKLO"]
        assert len(oklo_hits) == 1
        # Despite mcap=8B (broad territory), override grants pool privilege
        assert oklo_hits[0]["layer"] == "pool"
        # layer_counts confirms classification
        assert result["layer_counts"]["pool"] == 1
        assert result["layer_counts"]["extend"] == 0

    def test_dv_section_filters_out_broad_layer(self):
        """[v3 P1] DV text section drops rows whose mcap classifies them as broad.
        Aligned with the selection-scan universe scope (pool ∪ extend).
        NOTE: ARM was removed from the new_faces fixture because ARM joined the
        core pool (pool-drift, not a grouping bug) — confirmed via
        data/pool/universe.json. Replaced with ZZZBROAD (synthetic, never in pool)."""
        dv_result = {
            "rankings": [
                # mcap 25B → extend, kept
                {"rank": 1, "symbol": "NVDA", "dollar_volume": 25e9,
                 "price": 890.5, "market_cap": 3e12},
                # mcap 6B → broad, dropped
                {"rank": 17, "symbol": "OKLO", "dollar_volume": 1.2e9,
                 "price": 45.0, "market_cap": 6e9},
            ],
            "new_faces": [
                # not in pool + mcap 8B → broad, dropped
                {"rank": 18, "symbol": "ZZZBROAD", "dollar_volume": 1.0e9, "market_cap": 8e9},
            ],
        }
        result = format_section_d(dv_result)
        assert "NVDA" in result
        assert "OKLO" not in result
        assert "ZZZBROAD" not in result

    def test_dv_filter_uses_dv_row_market_cap_over_stale_local_metadata(
        self, monkeypatch
    ):
        """[v3 P1 regression] DV row's freshly-collected market_cap must override
        stale local metadata. Otherwise a symbol that has dropped below $10B
        per today's data would still pass the broad filter on the strength
        of a stale broad_universe.json / company.db entry showing $20B."""
        from scripts import morning_report as mr

        def fake_merge(metadata, symbols):
            # Local metadata has stale OKLO mcap=$20B (would pass filter).
            for sym in symbols:
                if sym == "OKLO":
                    metadata.setdefault(sym, {})
                    metadata[sym].update({
                        "marketCap": 20e9,
                        "shortName": "OKLO", "longName": "Oklo Inc.",
                        "industry": "Regulated Electric",
                    })

        monkeypatch.setattr(mr, "_merge_local_metadata", fake_merge)
        monkeypatch.setattr(mr, "get_symbols", lambda: [])

        dv_result = {
            "rankings": [
                # Today's DV row reports market_cap=$6B (broad). Must override
                # the stale $20B in local metadata.
                {"rank": 17, "symbol": "OKLO", "dollar_volume": 1.2e9,
                 "price": 45.0, "market_cap": 6e9},
            ],
            "new_faces": [],
        }
        result = format_section_d(dv_result)
        assert "OKLO" not in result, (
            "OKLO should be filtered out by today's $6B mcap, "
            "regardless of stale $20B in local metadata"
        )

    def test_dv_visual_block_filters_out_broad_layer(self):
        """[v3 P1] DV image-report block drops broad-layer rows."""
        dv_result = {
            "rankings": [
                {"rank": 1, "symbol": "NVDA", "dollar_volume": 25e9,
                 "price": 890.5, "market_cap": 3e12},
                {"rank": 17, "symbol": "OKLO", "dollar_volume": 1.2e9,
                 "price": 45.0, "market_cap": 6e9},
            ],
            "new_faces": [],
        }
        sections = build_morning_visual_sections(
            market_signals=sample_market_signals(),
            dv_result=dv_result,
        )
        dv_section = next(s for s in sections if s["slug"] == "03_dollar_volume")
        all_cells = [
            cell for block in dv_section["blocks"]
            for row in block["rows"] for cell in row["cells"]
        ]
        rendered = " ".join(all_cells)
        assert "NVDA" in rendered
        assert "OKLO" not in rendered


class TestVolumeAnomalyPayload:
    """build_market_signal_report 必须同时输出 volume_anomaly section 和 raw
    dv_acceleration / rvol_sustained section（后者为 audit 和 dead-code 回滚保留）。"""

    def test_build_market_signal_report_includes_volume_anomaly_payload(
        self, monkeypatch
    ):
        from scripts import morning_report as mr

        # Two symbols co-occur in DV and RVOL → expect one resonant row.
        mu_frame = pd.DataFrame(
            {"close": [100.0] * 200},
            index=pd.date_range("2025-08-01", periods=200, freq="B"),
        )
        monkeypatch.setattr(
            "scripts.broad_market_scan.fetch_universe_metadata",
            lambda **kw: {
                "stocks": {
                    "MU": {"marketCap": 120e9, "shortName": "MU",
                           "longName": "Micron", "exchange": "NASDAQ"},
                }
            },
        )
        monkeypatch.setattr(
            "scripts.broad_market_scan.load_price_frames",
            lambda symbols, **kw: {"MU": mu_frame},
        )
        monkeypatch.setattr(mr, "get_symbols", lambda: [])
        monkeypatch.setattr(mr, "_merge_local_metadata", lambda *a, **kw: None)
        monkeypatch.setattr(mr, "_hydrate_signal_metadata", lambda *a, **kw: None)
        monkeypatch.setattr(mr, "_load_market_timing_target_frames", lambda *a, **kw: {})
        monkeypatch.setattr(mr, "_load_market_db_broad_price_frames", lambda *a, **kw: {})
        monkeypatch.setattr(
            "src.indicators.pmarp.analyze_pmarp",
            lambda *a, **kw: {"signal": "neutral", "current": None, "previous": None},
        )
        monkeypatch.setattr(
            "src.indicators.dv_acceleration.scan_dv_acceleration",
            lambda *a, **kw: pd.DataFrame([
                {"symbol": "MU", "ratio": 1.8, "dv_5d": 4.2e9, "dv_20d": 2.3e9,
                 "signal": True},
            ]),
        )
        monkeypatch.setattr(
            "src.indicators.rvol_sustained.scan_rvol_sustained",
            lambda *a, **kw: [
                {"symbol": "MU", "level": "sustained_3d", "days": 3,
                 "values": [2.6, 2.4, 2.2], "latest_rvol": 2.6},
            ],
        )
        monkeypatch.setattr(mr, "_compute_signal_betas", lambda *a, **kw: {})

        result = build_market_signal_report()

        # New merged section exists with the resonant row.
        assert "volume_anomaly" in result
        anomaly = result["volume_anomaly"]
        assert anomaly["criteria"].startswith("DV >")
        assert "single >=" in anomaly["criteria"]
        hits = anomaly["hits"]
        assert len(hits) == 1
        assert hits[0]["symbol"] == "MU"
        assert hits[0]["volume_signal_kind"] == "共振"
        assert hits[0]["priority_group"] == 0
        assert hits[0]["from_dv"] is True
        assert hits[0]["from_rvol"] is True

        # Raw payloads still present (Scope: dead-code rollback safety net).
        assert "dv_acceleration" in result
        assert "rvol_sustained" in result
        assert len(result["dv_acceleration"]["hits"]) == 1
        assert len(result["rvol_sustained"]["hits"]) == 1


def test_no_business_role_in_text_sections():
    ms = _make_market_signals(anomaly_hits=[
        {"symbol": "NVDA", "marketCap": 3e12, "layer": "pool",
         "from_dv": True, "from_rvol": False, "volume_signal_kind": "流动性加速",
         "ratio": 5.0, "dv_5d": 1e9, "dv_20d": 2e8}])
    dv = _make_dv_result(rankings=[_make_dv_item()])
    rendered = "\n".join([
        mr.format_section_layered_volume_anomaly(ms),
        mr.format_section_d(dv),
    ])
    assert "业务角色" not in rendered


def test_no_business_role_in_visual_blocks():
    sections = mr.build_morning_visual_sections(
        market_signals=_make_market_signals(pmarp_hits=[_make_pmarp_hit()]),
        dv_result=_make_dv_result(rankings=[_make_dv_item()]),
    )
    for section in sections:                      # 每个 section 含 slug/title/blocks
        for block in section.get("blocks", []):
            assert "业务角色" not in block.get("columns", [])


def test_dv_section_has_rank_change_and_real_newface_title():
    dv = _make_dv_result(
        rankings=[_make_dv_item("NVDA", rank=1, rank_change_label="↑3")],
        new_faces=[_make_dv_item("ABCD", rank=40, rank_change_label="NEW")])
    out = mr.format_section_d(dv)
    assert "排名变化" in out and "↑3" in out
    assert "真·新面孔" in out


def test_mcap_tier_boundary():
    assert mr._mcap_tier(120e9) == "大盘(≥$100B)"
    assert mr._mcap_tier(100e9) == "大盘(≥$100B)"   # 边界含
    assert mr._mcap_tier(99.9e9) == "中小盘(<$100B)"
    assert mr._mcap_tier(None) == "中小盘(<$100B)"   # 缺市值归中小盘


def test_pmarp_layered_by_signal_then_cap():
    hits = [
        _make_pmarp_hit("NVDA", "bullish_breakout", 99.0, 3e12),
        _make_pmarp_hit("SMCI", "bullish_breakout", 98.7, 30e9),
        _make_pmarp_hit("XYZ",  "oversold_recovery", 2.0, 50e9),
    ]
    out = mr.format_section_pmarp_by_signal_and_cap(_make_market_signals(pmarp_hits=hits))
    assert "上穿98%" in out and "上穿2%" in out
    assert out.index("上穿98%") < out.index("上穿2%")     # 信号顺序
    assert out.index("大盘") < out.index("中小盘")          # 市值顺序
    assert "业务角色" not in out


def test_pmarp_empty_groups_suppressed():
    out = mr.format_section_pmarp_by_signal_and_cap(
        _make_market_signals(pmarp_hits=[_make_pmarp_hit("NVDA", "bullish_breakout", 99.0, 3e12)]))
    assert "下穿98%" not in out


def test_pmarp_visual_three_subblocks_flat_no_business_role():
    sections = mr.build_morning_visual_sections(
        market_signals=_make_market_signals(pmarp_hits=[
            _make_pmarp_hit("NVDA", "bullish_breakout", 99.0, 3e12),
            _make_pmarp_hit("XYZ", "oversold_recovery", 2.0, 50e9)]),
        dv_result=None)
    pmarp = next(s for s in sections if s["slug"] == "01_pmarp")
    assert len(pmarp["blocks"]) >= 2                   # 每信号类型一个子块
    for block in pmarp["blocks"]:
        assert block.get("grouped") is False           # 扁平，不走 layer×bucket
        assert "业务角色" not in block["columns"]


def test_build_html_payload_has_pmarp_blocks_no_business_role():
    ms = _make_market_signals(pmarp_hits=[_make_pmarp_hit("NVDA", "bullish_breakout", 99.0, 3e12)])
    dv = _make_dv_result(rankings=[_make_dv_item()])
    payload = mr.build_html_payload(ms, dv, as_of="2026-06-03")
    headings = [b.get("heading", "") for b in payload["blocks"]]
    assert any("PMARP" in h for h in headings)
    assert all("业务角色" not in b.get("columns", []) for b in payload["blocks"])


def test_build_html_payload_includes_market_timing_section_before_pmarp():
    # P1 review fix: HTML main path must not drop section 0 (大盘择时因子).
    ms = sample_market_signals()
    payload = mr.build_html_payload(ms, _make_dv_result(), as_of="2026-04-24")
    headings = [b.get("heading", "") for b in payload["blocks"]]
    assert "0. 大盘择时因子" in headings
    assert headings.index("0. 大盘择时因子") < headings.index("1. PMARP 信号")
    timing_tables = [b for b in payload["blocks"] if "指数" in (b.get("columns") or [])]
    assert timing_tables, "market-timing table block missing from HTML payload"
    assert any(r.get("指数") == "SPY" for r in timing_tables[0]["rows"])
    alert_text = " ".join(a for b in payload["blocks"] for a in (b.get("alerts") or []))
    assert "PMARP 2% UPCROSS" in alert_text


def test_pmarp_visual_blocks_include_mcap_tier_in_title():
    # P2 review fix: PDF/PNG fallback must show the explicit 大盘/中小盘 tier,
    # consistent with text + HTML (all three share _pmarp_signal_cap_groups).
    sections = mr.build_morning_visual_sections(
        market_signals=_make_market_signals(pmarp_hits=[
            _make_pmarp_hit("NVDA", "bullish_breakout", 99.0, 3e12),    # 大盘
            _make_pmarp_hit("SMCI", "bullish_breakout", 98.7, 30e9)]),  # 中小盘
        dv_result=None)
    pmarp = next(s for s in sections if s["slug"] == "01_pmarp")
    titles = [b["title"] for b in pmarp["blocks"]]
    assert any("大盘" in t for t in titles)
    assert any("中小盘" in t for t in titles)
    assert all("上穿98%" in t for t in titles)   # both hits are bullish_breakout


def test_html_delivery_falls_back_to_pdf_on_send_false(monkeypatch, tmp_path):
    calls, sent = [], {}
    monkeypatch.setattr(mr, "build_html_payload", lambda ms, dv, as_of: {"as_of": as_of, "blocks": []})
    monkeypatch.setattr(mr, "compile_morning_html_report", lambda payload, d, **kw: tmp_path / "r.html")
    monkeypatch.setattr(mr, "render_morning_report_images", lambda **kw: [tmp_path / "p.png"])
    monkeypatch.setattr(mr, "render_morning_report_pdf", lambda paths: tmp_path / "r.pdf")
    monkeypatch.setattr(mr, "send_document", lambda path, **kw: (calls.append(str(path)),
                        not str(path).endswith(".html"))[1])      # html→False
    monkeypatch.setattr(mr, "_send_group_pdf_report", lambda p: sent.__setitem__("pdf", str(p)) or True)
    monkeypatch.setattr(mr, "send_message", lambda *a, **kw: True)

    ok = mr._deliver_morning_report(
        market_signals={"pmarp": {"hits": []}}, dv_result={"date": "2026-06-03", "rankings": []},
        daily_msg="文本摘要", image_delivery="html", image_report=True,
        image_output_dir=str(tmp_path), photo_safe=False, as_of="2026-06-03", no_telegram=False)
    assert any(p.endswith(".html") for p in calls)   # 先试 HTML
    assert sent.get("pdf")                            # html send=False → 回退真实 PDF helper
    assert ok is True


# ---------- 6 个月 beta（2026-06-12 plan） ----------

def _make_beta_frames(beta=1.5, n=180, seed=11):
    """与 load_price_frames 同形：date 索引、close/volume 列。"""
    import numpy as np
    rng = np.random.default_rng(seed)
    dates = pd.bdate_range("2025-09-01", periods=n)
    bench_ret = rng.normal(0.0005, 0.01, n)
    stock_ret = beta * bench_ret + rng.normal(0.0, 0.001, n)
    bench = pd.DataFrame({"close": 100.0 * (1 + pd.Series(bench_ret, index=dates)).cumprod(),
                          "volume": 1e6}, index=dates)
    stock = pd.DataFrame({"close": 50.0 * (1 + pd.Series(stock_ret, index=dates)).cumprod(),
                          "volume": 1e6}, index=dates)
    return stock, bench


def test_compute_signal_betas_uses_loaded_frames(monkeypatch):
    stock_frame, bench_frame = _make_beta_frames(beta=1.5)
    import scripts.broad_market_scan as bms
    monkeypatch.setattr(bms, "load_price_frames_from_market_db",
                        lambda symbols, rows_needed: {"SPY": bench_frame})
    betas = mr._compute_signal_betas({"NVDA": stock_frame}, ["NVDA", "MISSING"])
    assert abs(betas["NVDA"] - 1.5) < 0.05
    assert betas["MISSING"] is None          # 不在 price_frames → None，不报错


def test_compute_signal_betas_benchmark_missing(monkeypatch):
    stock_frame, _ = _make_beta_frames()
    import scripts.broad_market_scan as bms
    monkeypatch.setattr(bms, "load_price_frames_from_market_db",
                        lambda symbols, rows_needed: {})
    betas = mr._compute_signal_betas({"NVDA": stock_frame}, ["NVDA"])
    assert betas == {"NVDA": None}           # 基准缺失 → 全 None，报告不阻塞


def test_compute_signal_betas_benchmark_load_raises(monkeypatch):
    stock_frame, _ = _make_beta_frames()
    import scripts.broad_market_scan as bms

    def boom(symbols, rows_needed):
        raise RuntimeError("db corrupted")

    monkeypatch.setattr(bms, "load_price_frames_from_market_db", boom)
    betas = mr._compute_signal_betas({"NVDA": stock_frame}, ["NVDA"])
    assert betas == {"NVDA": None}           # 基础设施失败也退化为 None，不炸晨报


def test_compute_signal_betas_empty_symbols():
    assert mr._compute_signal_betas({}, []) == {}


def test_enrich_with_layer_injects_beta():
    item = {"symbol": "NVDA", "value": 99.0}
    metadata = {"NVDA": {"marketCap": 3e12}}
    enriched = mr._enrich_with_layer(item, metadata, {"NVDA"}, betas={"NVDA": 1.83})
    assert enriched["beta_6m"] == 1.83


def test_enrich_with_layer_beta_default_none():
    item = {"symbol": "NVDA", "value": 99.0}
    metadata = {"NVDA": {"marketCap": 3e12}}
    enriched = mr._enrich_with_layer(item, metadata, {"NVDA"})
    assert enriched["beta_6m"] is None       # betas 不传 → None（向后兼容）


def test_builder_injects_beta_into_signal_rows(monkeypatch):
    """[plan review P1] 集成路径：beta 必须真的从 build_market_signal_report
    进入 pmarp / volume_anomaly rows。fixture 复制 TestVolumeAnomalyPayload
    （tests/test_morning_report.py:1403-1450）的 MU 共振行构造，
    并 patch _compute_signal_betas 隔离 DB。"""
    mu_frame = pd.DataFrame(
        {"close": [100.0] * 200},
        index=pd.date_range("2025-08-01", periods=200, freq="B"),
    )
    monkeypatch.setattr(
        "scripts.broad_market_scan.fetch_universe_metadata",
        lambda **kw: {"stocks": {"MU": {"marketCap": 120e9, "shortName": "MU",
                                        "longName": "Micron", "exchange": "NASDAQ"}}},
    )
    monkeypatch.setattr("scripts.broad_market_scan.load_price_frames",
                        lambda symbols, **kw: {"MU": mu_frame})
    monkeypatch.setattr(mr, "get_symbols", lambda: [])
    monkeypatch.setattr(mr, "_merge_local_metadata", lambda *a, **kw: None)
    monkeypatch.setattr(mr, "_hydrate_signal_metadata", lambda *a, **kw: None)
    monkeypatch.setattr(mr, "_load_market_timing_target_frames", lambda *a, **kw: {})
    monkeypatch.setattr(mr, "_load_market_db_broad_price_frames", lambda *a, **kw: {})
    monkeypatch.setattr(
        "src.indicators.pmarp.analyze_pmarp",
        lambda *a, **kw: {"signal": "oversold_recovery", "current": 2.5, "previous": 1.7},
    )
    monkeypatch.setattr(
        "src.indicators.dv_acceleration.scan_dv_acceleration",
        lambda *a, **kw: pd.DataFrame([
            {"symbol": "MU", "ratio": 1.8, "dv_5d": 4.2e9, "dv_20d": 2.3e9,
             "signal": True}]),
    )
    monkeypatch.setattr("src.indicators.rvol_sustained.scan_rvol_sustained",
                        lambda *a, **kw: [])
    captured = {}

    def fake_betas(price_frames, signal_symbols):
        captured["symbols"] = sorted(set(signal_symbols))
        return {s: 2.05 for s in signal_symbols}

    monkeypatch.setattr(mr, "_compute_signal_betas", fake_betas)

    result = mr.build_market_signal_report()

    assert captured["symbols"] == ["MU"]               # 接线点确实被调用
    assert result["pmarp"]["hits"][0]["beta_6m"] == 2.05
    assert result["volume_anomaly"]["hits"][0]["beta_6m"] == 2.05


# ============================================================
# Task 3: 文本投递面 β6M 列
# ============================================================

def test_format_beta():
    assert mr._format_beta(1.834) == "1.83"
    assert mr._format_beta(0.4) == "0.40"
    assert mr._format_beta(None) == "—"
    assert mr._format_beta(float("nan")) == "—"


def test_pmarp_text_section_has_beta_column():
    hit = _make_pmarp_hit("NVDA", "bullish_breakout", 99.0, 3e12)
    hit["beta_6m"] = 1.83
    out = mr.format_section_pmarp_by_signal_and_cap(_make_market_signals(pmarp_hits=[hit]))
    assert "β6M" in out                      # 表头
    assert "1.83" in out                     # 值与市值同行
    line = next(l for l in out.splitlines() if "NVDA" in l)
    assert "$3.0T" in line and "1.83" in line


def test_pmarp_text_section_beta_missing_dash():
    hit = _make_pmarp_hit("XYZ", "oversold_recovery", 2.0, 50e9)   # 无 beta_6m 键
    out = mr.format_section_pmarp_by_signal_and_cap(_make_market_signals(pmarp_hits=[hit]))
    line = next(l for l in out.splitlines() if "XYZ" in l)
    assert line.rstrip().split(" | ")[-1] == "—"


def test_volume_anomaly_text_section_has_beta_column():
    hit = {"symbol": "SMCI", "marketCap": 30e9, "layer": "pool",
           "from_dv": True, "from_rvol": False, "volume_signal_kind": "流动性加速",
           "dv_ratio": 2.1, "dv_5d": 5e9, "dv_20d": 2.4e9, "beta_6m": 2.05}
    out = mr.format_section_layered_volume_anomaly(_make_market_signals(anomaly_hits=[hit]))
    assert "β6M" in out
    line = next(l for l in out.splitlines() if "SMCI" in l)
    assert "2.05" in line


def test_volume_anomaly_text_section_beta_missing_dash():
    hit = {"symbol": "SMCI", "marketCap": 30e9, "layer": "pool",
           "from_dv": True, "from_rvol": False, "volume_signal_kind": "流动性加速",
           "dv_ratio": 2.1, "dv_5d": 5e9, "dv_20d": 2.4e9}  # no beta_6m key
    out = mr.format_section_layered_volume_anomaly(_make_market_signals(anomaly_hits=[hit]))
    line = next(l for l in out.splitlines() if "SMCI" in l)
    assert line.rstrip().split(" | ")[-1] == "—"


def test_html_payload_pmarp_and_anomaly_have_beta_column():
    pm = _make_pmarp_hit("NVDA", "bullish_breakout", 99.0, 3e12); pm["beta_6m"] = 1.83
    va = {"symbol": "SMCI", "marketCap": 30e9, "layer": "pool",
          "from_dv": True, "from_rvol": False, "volume_signal_kind": "流动性加速",
          "dv_ratio": 2.1, "dv_5d": 5e9, "dv_20d": 2.4e9, "beta_6m": None}
    ms = _make_market_signals(pmarp_hits=[pm], anomaly_hits=[va])
    payload = mr.build_html_payload(ms, None, as_of="2026-06-12")
    pm_blocks = [b for b in payload["blocks"] if "信号" in (b.get("columns") or [])]
    assert pm_blocks and all("β6M" in b["columns"] for b in pm_blocks)
    assert pm_blocks[0]["rows"][0]["β6M"] == "1.83"
    va_block = next(b for b in payload["blocks"] if b.get("heading") == "2. 量能异常")
    assert "β6M" in va_block["columns"]
    assert va_block["rows"][0]["β6M"] == "—"          # 缺失 → —


def test_pmarp_columns_exact_parity_across_three_surfaces():
    """[plan review P2] 文本/HTML/PNG 三面 PMARP 列 exact parity。
    修复存量 drift：HTML 此前缺'变化'列。"""
    expected = ["标的", "概念", "信号", "当前", "变化", "市值", "β6M"]
    pm = _make_pmarp_hit("NVDA", "bullish_breakout", 99.0, 3e12)
    ms = _make_market_signals(pmarp_hits=[pm])
    payload = mr.build_html_payload(ms, None, as_of="2026-06-12")
    html_pm = [b for b in payload["blocks"] if "信号" in (b.get("columns") or [])]
    assert all(b["columns"] == expected for b in html_pm)
    sections = mr.build_morning_visual_sections(market_signals=ms, dv_result=None)
    visual_pm = next(s for s in sections if s["slug"] == "01_pmarp")
    assert all(b["columns"] == expected for b in visual_pm["blocks"])
    text = mr.format_section_pmarp_by_signal_and_cap(ms)
    assert " | ".join(expected) in text
    data_lines = [l for l in text.splitlines() if "NVDA" in l and "|" in l]
    assert data_lines and all(len(l.split(" | ")) == len(expected) for l in data_lines)


def test_volume_anomaly_columns_exact_parity_across_three_surfaces():
    expected = ["标的", "概念", "类型", "DV 5d/20d", "RVOL", "市值", "β6M"]
    va = {"symbol": "SMCI", "marketCap": 30e9, "layer": "pool",
          "from_dv": True, "from_rvol": False, "volume_signal_kind": "流动性加速",
          "dv_ratio": 2.1, "dv_5d": 5e9, "dv_20d": 2.4e9, "beta_6m": 2.05}
    ms = _make_market_signals(anomaly_hits=[va])
    payload = mr.build_html_payload(ms, None, as_of="2026-06-12")
    va_block = next(b for b in payload["blocks"] if b.get("heading") == "2. 量能异常")
    assert va_block["columns"] == expected
    sections = mr.build_morning_visual_sections(market_signals=ms, dv_result=None)
    anomaly = next(s for s in sections if s["slug"] == "02_volume_anomaly")
    assert all(b["columns"] == expected for b in anomaly["blocks"])
    text = mr.format_section_layered_volume_anomaly(ms)
    assert " | ".join(expected) in text
    data_lines = [l for l in text.splitlines() if "SMCI" in l and "|" in l]
    assert data_lines and all(len(l.split(" | ")) == len(expected) for l in data_lines)


def test_visual_blocks_have_beta_column_and_width():
    pm = _make_pmarp_hit("NVDA", "bullish_breakout", 99.0, 3e12); pm["beta_6m"] = 1.83
    va = {"symbol": "SMCI", "marketCap": 30e9, "layer": "pool",
          "from_dv": True, "from_rvol": False, "volume_signal_kind": "流动性加速",
          "dv_ratio": 2.1, "dv_5d": 5e9, "dv_20d": 2.4e9, "beta_6m": 2.05}
    sections = mr.build_morning_visual_sections(
        market_signals=_make_market_signals(pmarp_hits=[pm], anomaly_hits=[va]), dv_result=None)
    pmarp = next(s for s in sections if s["slug"] == "01_pmarp")
    for block in pmarp["blocks"]:
        assert block["columns"][-1] == "β6M"
        assert len(block["widths"]) == len(block["columns"])   # 防 zip 静默截断
        assert block["rows"][0]["cells"][-1] == "1.83"
    anomaly = next(s for s in sections if s["slug"] == "02_volume_anomaly")
    block = anomaly["blocks"][0]
    assert block["columns"][-1] == "β6M"
    assert len(block["widths"]) == len(block["columns"])
    assert block["rows"][0]["cells"][-1] == "2.05"


# ---------------------------------------------------------------------------
# _load_volume_concentration_frames — 临时 SQLite 集成测试 (T4)
# ---------------------------------------------------------------------------

def _write_daily_price_rows(db_path, rows, unique_pk=True):
    """Create a minimal daily_price table and insert rows.

    rows: iterable of (symbol, date, close, volume) tuples.
    unique_pk=False omits the PRIMARY KEY so duplicate (symbol, date) rows
    can be inserted — production market.db always enforces the PK, but the
    loader must still degrade defensively if it ever sees duplicates.
    """
    conn = sqlite3.connect(str(db_path))
    pk_clause = ", PRIMARY KEY (symbol, date)" if unique_pk else ""
    conn.execute(
        f"CREATE TABLE daily_price (symbol TEXT, date TEXT, close REAL, volume REAL{pk_clause})"
    )
    conn.executemany("INSERT INTO daily_price VALUES (?, ?, ?, ?)", rows)
    conn.commit()
    conn.close()


def _volconc_stock_rows(date, n_symbols, prefix="S"):
    """n_symbols rows with distinct increasing dollar volume (close=1.0 so dv=volume)."""
    return [(f"{prefix}{i:04d}", date, 1.0, float((i + 1) * 1000)) for i in range(n_symbols)]


class TestLoadVolumeConcentrationFrames:
    def test_etf_excluded_from_members_and_denominator(self, tmp_path):
        date = "2026-01-02"
        rows = _volconc_stock_rows(date, 60) + [
            ("SPY", date, 500.0, 1e8),
            ("QQQ", date, 400.0, 1e8),
            ("SOXX", date, 200.0, 1e8),
        ]
        db_path = tmp_path / "market.db"
        _write_daily_price_rows(db_path, rows)

        result = mr._load_volume_concentration_frames(db_path=db_path)

        assert result["available"] is True
        ts = pd.Timestamp(date)
        assert "SPY" not in result["members"][ts]
        assert "QQQ" not in result["members"][ts]
        assert "SOXX" not in result["members"][ts]
        assert result["share_df"].loc[ts, "n_symbols"] == 60
        assert ts in result["spy_close"].index
        assert result["spy_close"][ts] == 500.0

    def test_top50_share_and_denominator(self, tmp_path):
        date = "2026-01-02"
        rows = _volconc_stock_rows(date, 60)
        db_path = tmp_path / "market.db"
        _write_daily_price_rows(db_path, rows)

        result = mr._load_volume_concentration_frames(db_path=db_path)

        dv_values = sorted((r[3] for r in rows), reverse=True)
        expected_share = sum(dv_values[:50]) / sum(dv_values)
        ts = pd.Timestamp(date)
        assert result["share_df"].loc[ts, "n_symbols"] == 60
        assert result["share_df"].loc[ts, "share"] == pytest.approx(expected_share)

    def test_member_sets_are_exact_top50_symbols(self, tmp_path):
        date = "2026-01-02"
        n = 60
        rows = _volconc_stock_rows(date, n)
        db_path = tmp_path / "market.db"
        _write_daily_price_rows(db_path, rows)

        result = mr._load_volume_concentration_frames(db_path=db_path)

        expected_members = frozenset(f"S{i:04d}" for i in range(n - 50, n))
        ts = pd.Timestamp(date)
        assert result["members"][ts] == expected_members

    def test_read_only_db_file_loads_successfully(self, tmp_path):
        date = "2026-01-02"
        rows = _volconc_stock_rows(date, 55)
        db_path = tmp_path / "market.db"
        _write_daily_price_rows(db_path, rows)
        os.chmod(db_path, 0o444)
        try:
            result = mr._load_volume_concentration_frames(db_path=db_path)
        finally:
            os.chmod(db_path, 0o644)

        assert result["available"] is True
        assert not result["share_df"].empty

    def test_missing_db_path_degrades_without_path_in_reason(self, tmp_path):
        missing_path = tmp_path / "does_not_exist.db"

        result = mr._load_volume_concentration_frames(db_path=missing_path)

        assert result["available"] is False
        assert str(missing_path) not in result["reason"]
        assert ".db" not in result["reason"]

    def test_missing_daily_price_table_degrades(self, tmp_path):
        db_path = tmp_path / "market.db"
        conn = sqlite3.connect(str(db_path))
        conn.execute("CREATE TABLE other_table (id INTEGER)")
        conn.commit()
        conn.close()

        result = mr._load_volume_concentration_frames(db_path=db_path)

        assert result["available"] is False
        assert str(db_path) not in result["reason"]

    def test_duplicate_date_symbol_degrades(self, tmp_path):
        date = "2026-01-02"
        rows = _volconc_stock_rows(date, 55)
        # Duplicate the highest-dv row (rows[-1]) so it's guaranteed to land
        # inside the Top-50 cutoff (rows[0] has the lowest dv and would be
        # ranked outside Top-50 among 55 symbols, so a duplicate there would
        # never reach ranked_df — not a valid probe of the dedup check).
        rows.append(rows[-1])  # duplicate (symbol, date)
        db_path = tmp_path / "market.db"
        _write_daily_price_rows(db_path, rows, unique_pk=False)

        result = mr._load_volume_concentration_frames(db_path=db_path)

        assert result["available"] is False
        assert "reason" in result

    def test_dates_returned_in_ascending_order_regardless_of_insert_order(self, tmp_path):
        dates = ["2026-01-02", "2026-01-05", "2026-01-06"]
        spy_close_by_date = {"2026-01-02": 500.0, "2026-01-05": 505.0, "2026-01-06": 510.0}
        rows_by_date = {
            d: _volconc_stock_rows(d, 55) + [("SPY", d, spy_close_by_date[d], 1e8)]
            for d in dates
        }

        sorted_db = tmp_path / "sorted.db"
        _write_daily_price_rows(sorted_db, [r for d in dates for r in rows_by_date[d]])

        shuffled_db = tmp_path / "shuffled.db"
        shuffled_order = [dates[2], dates[0], dates[1]]
        _write_daily_price_rows(shuffled_db, [r for d in shuffled_order for r in rows_by_date[d]])

        sorted_result = mr._load_volume_concentration_frames(db_path=sorted_db)
        shuffled_result = mr._load_volume_concentration_frames(db_path=shuffled_db)

        assert sorted_result["share_df"].index.is_monotonic_increasing
        assert shuffled_result["share_df"].index.is_monotonic_increasing
        assert sorted_result["spy_close"].index.is_monotonic_increasing
        assert shuffled_result["spy_close"].index.is_monotonic_increasing
        pd.testing.assert_frame_equal(sorted_result["share_df"], shuffled_result["share_df"])
        assert sorted_result["members"].tolist() == shuffled_result["members"].tolist()
        pd.testing.assert_series_equal(sorted_result["spy_close"], shuffled_result["spy_close"])

    def test_directory_path_degrades_without_raising(self, tmp_path):
        # An existing-but-invalid path (a directory, not a file) must not
        # escape the loader's documented "never raises" contract — connect()
        # itself raises sqlite3.OperationalError for a directory target, so
        # the connect call must live inside the guarded try/except.
        result = mr._load_volume_concentration_frames(db_path=tmp_path)

        assert result == {"available": False, "reason": "数据库读取失败"}
        assert str(tmp_path) not in result["reason"]

    def test_as_of_truncates_to_cutoff_date(self, tmp_path):
        dates = ["2026-01-02", "2026-01-05", "2026-01-06", "2026-01-07"]
        spy_close_by_date = {
            "2026-01-02": 500.0, "2026-01-05": 505.0, "2026-01-06": 510.0, "2026-01-07": 515.0,
        }
        rows = [
            r for d in dates
            for r in _volconc_stock_rows(d, 55) + [("SPY", d, spy_close_by_date[d], 1e8)]
        ]
        db_path = tmp_path / "market.db"
        _write_daily_price_rows(db_path, rows)

        result = mr._load_volume_concentration_frames(db_path=db_path, as_of="2026-01-05")

        assert result["available"] is True
        assert list(result["share_df"].index) == [pd.Timestamp("2026-01-02"), pd.Timestamp("2026-01-05")]
        assert list(result["spy_close"].index) == [pd.Timestamp("2026-01-02"), pd.Timestamp("2026-01-05")]
        assert result["spy_close"][pd.Timestamp("2026-01-05")] == 505.0


# ---------------------------------------------------------------------------
# _volconc_regime + _compute_volume_concentration_payload — pure-function
# unit tests (T2/T3). Synthetic frames only; no DB access.
# ---------------------------------------------------------------------------

def _volconc_synthetic_frames(
    n_rows,
    start="2020-01-01",
    share_base=0.30,
    share_slope=0.0001,
    n_symbols=60,
    member_shift_period=1,
    spy_base=500.0,
    spy_slope=1.0,
    spy_nan_last=False,
):
    """Build a (share_df, members_df, spy_close) triple with closed-form,
    hand-derivable rolling statistics:

    - share[t] = share_base + share_slope * t (strictly increasing) so the
      20d smoothed mean has a known average-of-window closed form, and its
      252-window percentile is always exactly 100.0 (current value is the
      window max whenever slope > 0, no ties).
    - Top-N member set at row t = {M<start>..M<start+49>} where
      start = t // member_shift_period. Two rows 20 apart share
      (VOLCONC_TOP_N - 20 // member_shift_period) symbols exactly (integer
      floor division is exact here because 20 is chosen to divide evenly
      for the shift periods used in tests), so churn is a known constant
      for every row with i >= 20.
    - spy_close[t] = spy_base + spy_slope * t (linear), giving a closed-form
      pct_change(20) at any row.

    n_symbols may be a scalar (constant every row) or a list/tuple of
    per-row overrides (len == n_rows).
    """
    dates = pd.date_range(start=start, periods=n_rows, freq="D")
    share_values = [share_base + share_slope * t for t in range(n_rows)]
    if isinstance(n_symbols, (list, tuple)):
        assert len(n_symbols) == n_rows
        n_symbols_values = list(n_symbols)
    else:
        n_symbols_values = [n_symbols] * n_rows
    share_df = pd.DataFrame(
        {"share": share_values, "n_symbols": n_symbols_values}, index=dates
    )

    member_sets = []
    for t in range(n_rows):
        m_start = t // member_shift_period
        member_sets.append(frozenset(
            f"M{i:05d}" for i in range(m_start, m_start + mr.VOLCONC_TOP_N)
        ))
    members_df = pd.Series(member_sets, index=dates)

    spy_values = [spy_base + spy_slope * t for t in range(n_rows)]
    if spy_nan_last:
        spy_values[-1] = float("nan")
    spy_close = pd.Series(spy_values, index=dates)

    return share_df, members_df, spy_close


class TestVolconcRegime:
    def test_boundary_exactly_80_is_normal_not_crowded(self):
        # 严格大于 80.0 才算高集中；80.0 本身落入常态。
        assert mr._volconc_regime(80.0, 0.05) == "常态"

    def test_pctile_grid_nearest_achievable_values(self):
        # 真实 rolling 分位网格步长 100/251；80.0 不可达，取两侧最近可达值。
        assert mr._volconc_regime(79.68, 0.05) == "常态"
        assert mr._volconc_regime(80.08, 0.05) == "高集中+上行（拥挤）"

    def test_high_concentration_up_is_crowded(self):
        assert mr._volconc_regime(85.0, 0.05) == "高集中+上行（拥挤）"

    def test_high_concentration_down_is_panic(self):
        assert mr._volconc_regime(85.0, -0.02) == "高集中+下行（恐慌）"

    def test_high_concentration_flat_ret_counts_as_down_panic(self):
        # spy_ret20 == 0 归入下行（与研究 `> 0` 布尔口径一致）
        assert mr._volconc_regime(85.0, 0.0) == "高集中+下行（恐慌）"

    def test_low_concentration_is_normal_regardless_of_direction(self):
        assert mr._volconc_regime(50.0, 0.05) == "常态"
        assert mr._volconc_regime(50.0, -0.05) == "常态"

    def test_share_pctile_none_degrades_to_insufficient_data(self):
        assert mr._volconc_regime(None, 0.05) == "数据不足"

    def test_spy_ret20_none_degrades_to_direction_missing(self):
        assert mr._volconc_regime(85.0, None) == "方向数据缺失"


class TestVolconcRollPctile:
    def test_short_window_hand_computed_with_tie_and_strict_gt(self):
        # win=4 (denominator = win - 1 = 3 prior values). Non-monotonic
        # series with two exact ties (value 3 at idx 1 & 3; value 2 at
        # idx 2 & 5) so a strict `>` vs. a would-be `>=` comparison diverge,
        # and a win-1 vs. a would-be win denominator diverge.
        s = pd.Series([1, 3, 2, 3, 5, 2], dtype=float)

        result = mr._volconc_roll_pctile(s, window=4)

        assert result.iloc[:3].isna().all()

        # idx3: window=[1,3,2,3], last=3, preceding=[1,3,2].
        # strict >: 3>1 True, 3>3 False (tie), 3>2 True -> 2/3 * 100.
        # A would-be >= comparison scores the tie True -> 3/3*100 = 100.0;
        # a would-be win(4)-sized denominator gives 2/4*100 = 50.0 —
        # both differ from the correct value asserted below.
        assert result.iloc[3] == pytest.approx(2 / 3 * 100)

        # idx4: window=[3,2,3,5], last=5, preceding=[3,2,3].
        # 5 beats all three preceding values -> 3/3 * 100 = 100.0.
        assert result.iloc[4] == pytest.approx(100.0)

        # idx5: window=[2,3,5,2], last=2, preceding=[2,3,5].
        # strict >: 2>2 False (tie), 2>3 False, 2>5 False -> 0/3 * 100 = 0.0.
        # A would-be >= comparison scores the tie True -> 1/3*100 = 33.33.
        assert result.iloc[5] == pytest.approx(0.0)


class TestComputeVolumeConcentrationPayload:
    def test_normal_payload_matches_hand_computed_values(self):
        n_rows = 300
        share_df, members_df, spy_close = _volconc_synthetic_frames(
            n_rows, member_shift_period=1,
        )

        result = mr._compute_volume_concentration_payload(share_df, members_df, spy_close)

        assert result["available"] is True
        assert result["as_of"] == share_df.index[-1].date().isoformat()

        # share_sm[299] = mean(share[280..299]) = base + slope * avg(280..299)
        expected_share_sm = 0.30 + 0.0001 * ((280 + 299) / 2)
        assert result["share_sm_pct"] == pytest.approx(expected_share_sm * 100, rel=1e-9)
        # strictly increasing share_sm -> current value is always the window max
        assert result["share_pctile_1y"] == pytest.approx(100.0)

        # member window advances by 1 symbol/day; 20d-apart windows overlap
        # in (50 - 20) = 30 symbols -> churn = 1 - 30/50 = 0.4 for every
        # row with i >= 20, so the 20d mean is also exactly 0.4.
        assert result["churn_sm_pct"] == pytest.approx(40.0)
        # churn_sm is constant -> nothing in the window is strictly greater
        assert result["churn_pctile_1y"] == pytest.approx(0.0)

        # spy_close[t] = 500 + t; ret20 at t=299 = (799-779)/779 = 20/779
        expected_ret20 = 20 / 779
        assert result["spy_ret20_pct"] == pytest.approx(expected_ret20 * 100, rel=1e-9)

        assert result["regime"] == "高集中+上行（拥挤）"

    def test_churn_matches_hand_computed_40_of_50_overlap(self):
        # member window advances by 1 symbol every 2 days; 20d-apart windows
        # overlap in (50 - 20//2) = 40 symbols -> churn = 1 - 40/50 = 0.2
        n_rows = 300
        share_df, members_df, spy_close = _volconc_synthetic_frames(
            n_rows, member_shift_period=2,
        )

        result = mr._compute_volume_concentration_payload(share_df, members_df, spy_close)

        assert result["available"] is True
        assert result["churn_sm_pct"] == pytest.approx(20.0)
        assert result["churn_pctile_1y"] == pytest.approx(0.0)

    def test_completeness_guard_minor_dip_still_uses_last_day(self):
        n_rows = 300
        n_symbols = [60] * n_rows
        n_symbols[-1] = 59  # ~1.7% below the trailing-20 median of 60; passes both conditions
        share_df, members_df, spy_close = _volconc_synthetic_frames(n_rows, n_symbols=n_symbols)

        result = mr._compute_volume_concentration_payload(share_df, members_df, spy_close)

        assert result["available"] is True
        assert result["as_of"] == share_df.index[-1].date().isoformat()

    def test_completeness_guard_severe_dip_rolls_back_one_day(self):
        n_rows = 300
        n_symbols = [60] * n_rows
        n_symbols[-1] = 36  # 60% of median(60); fails both n>=50 and n>=0.95*median
        share_df, members_df, spy_close = _volconc_synthetic_frames(n_rows, n_symbols=n_symbols)

        result = mr._compute_volume_concentration_payload(share_df, members_df, spy_close)

        assert result["available"] is True
        assert result["as_of"] == share_df.index[-2].date().isoformat()

    def test_completeness_guard_four_consecutive_incomplete_days_degrades(self):
        n_rows = 300
        n_symbols = [60] * n_rows
        for i in range(1, 5):  # last 4 candidate days all incomplete
            n_symbols[-i] = 30
        share_df, members_df, spy_close = _volconc_synthetic_frames(n_rows, n_symbols=n_symbols)

        result = mr._compute_volume_concentration_payload(share_df, members_df, spy_close)

        assert result == {"available": False, "reason": "最新截面不完整"}

    def test_trailing_sessions_entirely_absent_degrades_stale(self):
        # 若 broad ingestion 对末尾若干整个交易日都没写入 daily_price，
        # share_df/members 里根本不存在这些日期，只靠 share_df 自身日期游走的
        # completeness guard 永远发现不了——必须用 spy_close（未截断的市场
        # 日历参照）来检测"缺失的整session"，而非仅缺失的截面字段。
        n_rows = 300
        share_df, members_df, spy_close = _volconc_synthetic_frames(n_rows)
        extra_dates = pd.date_range(
            spy_close.index[-1] + pd.Timedelta(days=1), periods=4, freq="D",
        )
        extra_spy = pd.Series([600.0, 601.0, 602.0, 603.0], index=extra_dates)
        stale_spy_close = pd.concat([spy_close, extra_spy])

        result = mr._compute_volume_concentration_payload(share_df, members_df, stale_spy_close)

        assert result == {"available": False, "reason": "截面数据陈旧"}

    def test_trailing_sessions_absent_at_boundary_still_available(self):
        # 恰好 VOLCONC_MAX_STALE_STEPS(3) 个 session 缺失时，仍在既有回退容忍
        # 范围内——staleness gate 不应比原有 rollback 容忍度更严格。
        n_rows = 300
        share_df, members_df, spy_close = _volconc_synthetic_frames(n_rows)
        extra_dates = pd.date_range(
            spy_close.index[-1] + pd.Timedelta(days=1), periods=3, freq="D",
        )
        extra_spy = pd.Series([600.0, 601.0, 602.0], index=extra_dates)
        boundary_spy_close = pd.concat([spy_close, extra_spy])

        result = mr._compute_volume_concentration_payload(share_df, members_df, boundary_spy_close)

        assert result["available"] is True
        assert result["as_of"] == share_df.index[-1].date().isoformat()

    def test_rollback_result_equals_directly_truncated_input(self):
        # 末日不完整回退 1 天的结果，必须与直接把输入截到该日再算的结果逐键相等——
        # 证明回退后的截断发生在任何滚动计算之前，末日未污染滚动值。
        n_rows = 301
        n_symbols = [60] * n_rows
        n_symbols[-1] = 36  # forces rollback to dates[-2]
        share_df, members_df, spy_close = _volconc_synthetic_frames(n_rows, n_symbols=n_symbols)

        result_with_bad_tail = mr._compute_volume_concentration_payload(
            share_df, members_df, spy_close
        )

        cutoff = share_df.index[-2]
        truncated_share_df = share_df.loc[:cutoff]
        truncated_members_df = members_df.loc[:cutoff]
        truncated_spy_close = spy_close.loc[:cutoff]
        result_pretruncated = mr._compute_volume_concentration_payload(
            truncated_share_df, truncated_members_df, truncated_spy_close
        )

        assert result_with_bad_tail == pytest.approx(result_pretruncated)

    def test_spy_missing_at_as_of_degrades_direction_only(self):
        n_rows = 300
        share_df, members_df, spy_close = _volconc_synthetic_frames(n_rows, spy_nan_last=True)

        result = mr._compute_volume_concentration_payload(share_df, members_df, spy_close)

        assert result["available"] is True
        assert result["spy_ret20_pct"] is None
        assert result["regime"] == "方向数据缺失"
        # 两项指标照常输出，不因方向缺失而降级
        assert result["share_sm_pct"] is not None
        assert result["share_pctile_1y"] is not None
        assert result["churn_sm_pct"] is not None
        assert result["churn_pctile_1y"] is not None

    def test_insufficient_history_below_min_rows_degrades(self):
        n_rows = mr.VOLCONC_MIN_ROWS - 1
        share_df, members_df, spy_close = _volconc_synthetic_frames(n_rows)

        result = mr._compute_volume_concentration_payload(share_df, members_df, spy_close)

        assert result == {"available": False, "reason": "历史数据不足"}

    def test_empty_input_degrades_without_raising(self):
        empty_share_df = pd.DataFrame(columns=["share", "n_symbols"])
        empty_members = pd.Series(dtype=object)
        empty_spy = pd.Series(dtype=float)

        result = mr._compute_volume_concentration_payload(empty_share_df, empty_members, empty_spy)
        assert result == {"available": False, "reason": "历史数据不足"}

        result_none = mr._compute_volume_concentration_payload(None, empty_members, empty_spy)
        assert result_none == {"available": False, "reason": "历史数据不足"}


# ============================================================
# Task 3 — 接线 build_market_signal_report + 文本渲染
# Plan: docs/plans/2026-07-24-morning-report-volume-concentration-context.md
# ============================================================


def _stub_scan_deps_for_volconc_wiring(monkeypatch, mr):
    """Stub every build_market_signal_report scan dependency (selection scan +
    Section 0 market timing) so wiring tests can isolate the volume_concentration
    path via mr._load_volume_concentration_frames alone. Mirrors the mock
    recipe used by TestBroadDropPlanV3 / TestVolumeAnomalyPayload above."""
    monkeypatch.setattr(
        "scripts.broad_market_scan.fetch_universe_metadata",
        lambda **kw: {"stocks": {}},
    )
    monkeypatch.setattr(
        "scripts.broad_market_scan.load_price_frames",
        lambda symbols, **kw: {},
    )
    monkeypatch.setattr(mr, "get_symbols", lambda: [])
    monkeypatch.setattr(mr, "_merge_local_metadata", lambda *a, **kw: None)
    monkeypatch.setattr(mr, "_hydrate_signal_metadata", lambda *a, **kw: None)
    monkeypatch.setattr(mr, "_load_market_timing_target_frames", lambda *a, **kw: {})
    monkeypatch.setattr(mr, "_load_market_db_broad_price_frames", lambda *a, **kw: {})
    monkeypatch.setattr(
        "src.indicators.pmarp.analyze_pmarp",
        lambda *a, **kw: {"signal": "neutral", "current": None, "previous": None},
    )
    monkeypatch.setattr(
        "src.indicators.dv_acceleration.scan_dv_acceleration",
        lambda *a, **kw: pd.DataFrame(),
    )
    monkeypatch.setattr(
        "src.indicators.rvol_sustained.scan_rvol_sustained", lambda *a, **kw: []
    )
    monkeypatch.setattr(mr, "_compute_signal_betas", lambda *a, **kw: {})


class TestVolumeConcentrationWiring:
    """build_market_signal_report must inject a `volume_concentration` key
    without ever letting a loader/compute exception escape (不炸整报 promise)."""

    def test_payload_injected_when_loader_available(self, monkeypatch):
        _stub_scan_deps_for_volconc_wiring(monkeypatch, mr)
        share_df, members_df, spy_close = _volconc_synthetic_frames(
            300, member_shift_period=1,
        )
        monkeypatch.setattr(
            mr, "_load_volume_concentration_frames",
            lambda *a, **kw: {
                "available": True,
                "share_df": share_df,
                "members": members_df,
                "spy_close": spy_close,
            },
        )

        result = build_market_signal_report()

        assert "volume_concentration" in result
        volconc = result["volume_concentration"]
        assert volconc["available"] is True
        assert volconc["regime"] == "高集中+上行（拥挤）"

    def test_loader_degrade_passes_through_unchanged(self, monkeypatch):
        _stub_scan_deps_for_volconc_wiring(monkeypatch, mr)
        monkeypatch.setattr(
            mr, "_load_volume_concentration_frames",
            lambda *a, **kw: {"available": False, "reason": "数据库读取失败"},
        )

        result = build_market_signal_report()

        assert result["volume_concentration"] == {
            "available": False, "reason": "数据库读取失败",
        }

    def test_loader_exception_degrades_without_bubbling(self, monkeypatch):
        _stub_scan_deps_for_volconc_wiring(monkeypatch, mr)

        def _raise(*a, **kw):
            raise RuntimeError("boom: simulated loader crash")

        monkeypatch.setattr(mr, "_load_volume_concentration_frames", _raise)

        result = build_market_signal_report()  # must not raise

        assert result["volume_concentration"] == {
            "available": False, "reason": "计算异常",
        }


def _volconc_available_payload(**overrides):
    payload = {
        "available": True,
        "as_of": "2026-07-17",
        "share_sm_pct": 47.8,
        "share_pctile_1y": 91.6335,   # non-trivial rounding -> 92
        "churn_sm_pct": 25.3,
        "churn_pctile_1y": 0.0,       # -> 0
        "spy_ret20_pct": 3.2,
        "regime": "高集中+上行（拥挤）",
    }
    payload.update(overrides)
    return payload


class TestFormatSectionVolumeConcentration:
    """Text-face renderer — labels must be byte-identical to the brief's
    sample, and spy_ret20_pct (direction-only helper) must never be shown."""

    def test_available_renders_exact_sample_text(self):
        payload = _volconc_available_payload()

        result = mr.format_section_volume_concentration(payload)

        lines = result.split("\n")
        assert lines[0] == "*0b. 成交集中度*（截至 2026-07-17）"
        assert lines[1] == "Top50 成交额占比(20日平滑)   47.8%   1年分位 92"
        assert lines[2] == "Top50 名单20日换手率(平滑)   25.3%   1年分位 0"
        assert lines[3] == "状态: 高集中+上行（拥挤）"
        assert "3.2" not in result  # spy_ret20_pct is direction-only, never shown

    def test_unavailable_renders_two_lines_with_reason_and_no_path(self):
        payload = {"available": False, "reason": "数据库读取失败"}

        result = mr.format_section_volume_concentration(payload)

        lines = result.split("\n")
        assert lines[0] == "*0b. 成交集中度*"
        assert lines[1] == "成交集中度: 数据不足（数据库读取失败）"
        assert "/" not in result
        assert "Traceback" not in result

    def test_direction_missing_regime_still_shows_both_metric_lines(self):
        payload = _volconc_available_payload(
            spy_ret20_pct=None, regime="方向数据缺失",
        )

        result = mr.format_section_volume_concentration(payload)

        assert "Top50 成交额占比(20日平滑)   47.8%   1年分位 92" in result
        assert "Top50 名单20日换手率(平滑)   25.3%   1年分位 0" in result
        assert "状态: 方向数据缺失" in result


class TestFormatMorningReportVolumeConcentrationSection:
    """Section 0b must sit between Section 0 (大盘择时因子) and Section 1
    (PMARP 信号), for both available and degraded payloads, and must never
    carry advisory/predictive language."""

    def test_section_order_when_available(self):
        ms = sample_market_signals()
        ms["volume_concentration"] = _volconc_available_payload()

        result = format_morning_report(market_signals=ms, elapsed=5)

        i0 = result.index("0. 大盘择时因子")
        i0b = result.index("0b. 成交集中度")
        i1 = result.index("1. PMARP 信号")
        assert i0 < i0b < i1

    def test_section_order_when_unavailable(self):
        ms = sample_market_signals()
        ms["volume_concentration"] = {"available": False, "reason": "历史数据不足"}

        result = format_morning_report(market_signals=ms, elapsed=5)

        i0 = result.index("0. 大盘择时因子")
        i0b = result.index("0b. 成交集中度")
        i1 = result.index("1. PMARP 信号")
        assert i0 < i0b < i1

    def test_no_advisory_or_predictive_language_in_0b_block(self):
        ms = sample_market_signals()
        ms["volume_concentration"] = _volconc_available_payload()

        result = format_morning_report(market_signals=ms, elapsed=5)

        start = result.index("*0b. 成交集中度*")
        end = result.index("*1. PMARP", start)
        block = result[start:end]

        for banned in ["预计", "概率", "风险升高", "建议", "减仓", "仓位", "Timing"]:
            assert banned not in block


# ============================================================
# Task 4: HTML 块 + PNG spec + 三面 parity (plan T6)
# ============================================================
#
# 三面（text/HTML/PNG）共享 _volconc_display_rows() 组装的标签/数值/状态，
# 保证 parity 是"由构造保证"而非人工同步三份格式化代码。

class TestBuildHtmlPayloadVolumeConcentration:
    """0b 成交集中度 HTML 块 — build_html_payload() 输出。"""

    def test_available_block_has_title_as_of_rows_and_status(self):
        ms = _make_market_signals()
        ms["volume_concentration"] = _volconc_available_payload()

        payload = mr.build_html_payload(ms, None, as_of="2026-07-17")

        block = next(b for b in payload["blocks"] if b.get("heading") == "0b. 成交集中度")
        assert "2026-07-17" in (block.get("subtitle") or "")
        rows = block["rows"]
        assert rows[0]["指标"] == "Top50 成交额占比(20日平滑)"
        assert rows[0]["数值"] == "47.8%"
        assert rows[0]["1年分位"] == "92"          # 91.6335 -> 92 non-trivial rounding
        assert rows[1]["指标"] == "Top50 名单20日换手率(平滑)"
        assert rows[1]["数值"] == "25.3%"
        assert rows[1]["1年分位"] == "0"
        assert "高集中+上行（拥挤）" in (block.get("subtitle") or "")

    def test_unavailable_block_shows_degraded_reason_and_no_table(self):
        ms = _make_market_signals()
        ms["volume_concentration"] = {"available": False, "reason": "历史数据不足"}

        payload = mr.build_html_payload(ms, None, as_of="2026-07-17")

        block = next(b for b in payload["blocks"] if b.get("heading") == "0b. 成交集中度")
        assert block.get("subtitle") == "成交集中度: 数据不足（历史数据不足）"
        assert not block.get("rows")
        assert not block.get("columns")

    def test_section_order_0_then_0b_then_1_when_available(self):
        ms = sample_market_signals()

        payload = mr.build_html_payload(ms, None, as_of="2026-04-24")

        headings = [b.get("heading", "") for b in payload["blocks"]]
        assert (headings.index("0. 大盘择时因子")
                < headings.index("0b. 成交集中度")
                < headings.index("1. PMARP 信号"))

    def test_section_order_0_then_0b_then_1_when_unavailable(self):
        ms = sample_market_signals()
        ms["volume_concentration"] = {"available": False, "reason": "历史数据不足"}

        payload = mr.build_html_payload(ms, None, as_of="2026-04-24")

        headings = [b.get("heading", "") for b in payload["blocks"]]
        assert (headings.index("0. 大盘择时因子")
                < headings.index("0b. 成交集中度")
                < headings.index("1. PMARP 信号"))


class TestBuildMorningVisualSectionsVolumeConcentration:
    """0b 成交集中度 PNG spec — build_morning_visual_sections() 输出。只加
    spec，不改渲染引擎：沿用现有 grouped=False 表格 block schema。"""

    def test_available_section_between_timing_and_pmarp(self):
        ms = sample_market_signals()

        sections = mr.build_morning_visual_sections(market_signals=ms, dv_result=None)

        slugs = [s["slug"] for s in sections]
        assert (slugs.index("00_market_timing_factor")
                < slugs.index("00b_volume_concentration")
                < slugs.index("01_pmarp"))

        volconc = next(s for s in sections if s["slug"] == "00b_volume_concentration")
        assert volconc["title"] == "0b. 成交集中度"
        assert "2026-04-24" in volconc.get("subtitle", "")
        assert "高集中+上行（拥挤）" in volconc.get("subtitle", "")
        cells = [row["cells"] for block in volconc["blocks"] for row in block["rows"]]
        assert cells[0] == ["Top50 成交额占比(20日平滑)", "47.8%", "92"]
        assert cells[1] == ["Top50 名单20日换手率(平滑)", "25.3%", "0"]

    def test_unavailable_section_shows_degraded_reason_and_no_blocks(self):
        ms = sample_market_signals()
        ms["volume_concentration"] = {"available": False, "reason": "历史数据不足"}

        sections = mr.build_morning_visual_sections(market_signals=ms, dv_result=None)

        slugs = [s["slug"] for s in sections]
        assert (slugs.index("00_market_timing_factor")
                < slugs.index("00b_volume_concentration")
                < slugs.index("01_pmarp"))

        volconc = next(s for s in sections if s["slug"] == "00b_volume_concentration")
        assert volconc["subtitle"] == "成交集中度: 数据不足（历史数据不足）"
        assert volconc["blocks"] == []


class TestVolumeConcentrationThreeFaceParity:
    """同一 payload 经 text / HTML / PNG 三面，标题/标签/数值/状态行/降级原因
    必须逐项一致 —— 由共享 _volconc_display_rows 构造保证，而非人工同步。"""

    def test_available_payload_parity_across_three_faces(self):
        payload = _volconc_available_payload()
        ms = _make_market_signals()
        ms["volume_concentration"] = payload

        text = mr.format_section_volume_concentration(payload)
        html_payload = mr.build_html_payload(ms, None, as_of="2026-07-17")
        html_block = next(b for b in html_payload["blocks"] if b.get("heading") == "0b. 成交集中度")
        sections = mr.build_morning_visual_sections(market_signals=ms, dv_result=None)
        png_section = next(s for s in sections if s["slug"] == "00b_volume_concentration")
        png_rows = png_section["blocks"][0]["rows"]

        # 标题
        assert "0b. 成交集中度" in text
        assert html_block["heading"] == "0b. 成交集中度"
        assert png_section["title"] == "0b. 成交集中度"

        # as_of
        assert "2026-07-17" in text
        assert "2026-07-17" in html_block["subtitle"]
        assert "2026-07-17" in png_section["subtitle"]

        # 两行指标：标签逐字一致 + 数值字符串一致（1 位小数+% ；分位四舍五入取整）
        expected_rows = [
            ("Top50 成交额占比(20日平滑)", "47.8%", "92"),
            ("Top50 名单20日换手率(平滑)", "25.3%", "0"),
        ]
        for label, value, pctile in expected_rows:
            assert "{}   {}   1年分位 {}".format(label, value, pctile) in text
            html_row = next(r for r in html_block["rows"] if r["指标"] == label)
            assert html_row["数值"] == value
            assert html_row["1年分位"] == pctile
            png_row = next(r for r in png_rows if r["cells"][0] == label)
            assert png_row["cells"][1] == value
            assert png_row["cells"][2] == pctile

        # 状态行 regime 原文
        assert "状态: 高集中+上行（拥挤）" in text
        assert "高集中+上行（拥挤）" in html_block["subtitle"]
        assert "高集中+上行（拥挤）" in png_section["subtitle"]

    def test_unavailable_payload_parity_across_three_faces(self):
        payload = {"available": False, "reason": "历史数据不足"}
        ms = _make_market_signals()
        ms["volume_concentration"] = payload

        text = mr.format_section_volume_concentration(payload)
        html_payload = mr.build_html_payload(ms, None, as_of="2026-07-17")
        html_block = next(b for b in html_payload["blocks"] if b.get("heading") == "0b. 成交集中度")
        sections = mr.build_morning_visual_sections(market_signals=ms, dv_result=None)
        png_section = next(s for s in sections if s["slug"] == "00b_volume_concentration")

        degraded_text = "成交集中度: 数据不足（历史数据不足）"
        assert degraded_text in text
        assert html_block["subtitle"] == degraded_text
        assert png_section["subtitle"] == degraded_text


_VOLCONC_BANNED_WORDS = ["预计", "概率", "风险升高", "建议", "减仓", "仓位", "Timing"]


class TestVolumeConcentrationBannedWordsHtmlAndPng:
    """0b 块（HTML + PNG 面，仅扫 0b 块）不得包含预测/建议类措辞 —— 与文本面
    (Task 3, TestFormatMorningReportVolumeConcentrationSection) 对齐。"""

    def test_html_block_has_no_advisory_language(self):
        ms = _make_market_signals()
        ms["volume_concentration"] = _volconc_available_payload()

        payload = mr.build_html_payload(ms, None, as_of="2026-07-17")
        block = next(b for b in payload["blocks"] if b.get("heading") == "0b. 成交集中度")
        block_text = " ".join(str(v) for v in [
            block.get("heading", ""),
            block.get("subtitle", ""),
            *[cell for row in block.get("rows", []) for cell in row.values()],
        ])

        for banned in _VOLCONC_BANNED_WORDS:
            assert banned not in block_text

    def test_png_section_has_no_advisory_language(self):
        ms = _make_market_signals()
        ms["volume_concentration"] = _volconc_available_payload()

        sections = mr.build_morning_visual_sections(market_signals=ms, dv_result=None)
        section = next(s for s in sections if s["slug"] == "00b_volume_concentration")
        section_text = " ".join(str(v) for v in [
            section.get("title", ""),
            section.get("subtitle", ""),
            *[cell for block in section.get("blocks", [])
              for row in block.get("rows", []) for cell in row["cells"]],
        ])

        for banned in _VOLCONC_BANNED_WORDS:
            assert banned not in section_text


# ---------------------------------------------------------------------------
# Task 5 — 冻结对拍验收测试 (plan T7 / 验收标准 1)
#
# Proves the production chain (_load_volume_concentration_frames ->
# _compute_volume_concentration_payload) reproduces the research study's
# frozen-date (2026-07-17) numbers off the real market.db. This test is a
# deliberate exception to the "tests never touch the real DB" rule — the
# loader is read-only (mode=ro) and this is exactly the parity check the
# plan's acceptance criterion 1 calls for.
# ---------------------------------------------------------------------------

_VOLCONC_RESEARCH_CSV = (
    PROJECT_ROOT / "backtest" / "new" / "vol_concentration_signal_stats_20260724"
    / "series_daily.csv"
)
_VOLCONC_REAL_DB = PROJECT_ROOT / "data" / "market.db"
_VOLCONC_FREEZE_DATE = "2026-07-17"


class TestVolumeConcentrationFreezeDateParity:
    """冻结对拍：as_of=2026-07-17 生产链路 vs 研究 series_daily.csv 六字段逐项比对。"""

    def test_frozen_date_matches_research_csv_across_six_fields(self):
        if not _VOLCONC_RESEARCH_CSV.exists():
            pytest.skip("research CSV not present in this checkout")
        if not _VOLCONC_REAL_DB.exists():
            pytest.skip("data/market.db not present in this checkout")

        # ---- reference values, derived entirely from the research CSV ----
        csv_df = pd.read_csv(_VOLCONC_RESEARCH_CSV, parse_dates=["date"]).set_index("date")
        csv_df = csv_df.sort_index()
        target = pd.Timestamp(_VOLCONC_FREEZE_DATE)
        assert csv_df.index.max() == target, (
            f"research CSV max date {csv_df.index.max()} != frozen date {target}; "
            "brief assumes 2026-07-17 is the CSV's last row"
        )

        top50_sm_ref = float(csv_df.loc[target, "top50_sm"]) * 100
        pctile_252_ref = float(csv_df.loc[target, "pctile_252"])
        spy_up20_ref = bool(csv_df.loc[target, "spy_up20"])

        # churn_sm/churn_pctile are NOT CSV columns — recomputed independently
        # from raw churn50_20d per research run_study.py:261-264 / roll_pctile
        # at :65-66 (rolling(20).mean() then roll_pctile(...,252)).
        def _roll_pctile(s, win):
            return s.rolling(win).apply(lambda w: (w[-1] > w[:-1]).mean() * 100, raw=True)

        churn_sm_series = csv_df["churn50_20d"].rolling(20).mean()
        churn_pctile_series = _roll_pctile(churn_sm_series, 252)
        churn_sm_ref = float(churn_sm_series.loc[target]) * 100
        churn_pctile_252_ref = float(churn_pctile_series.loc[target])

        # regime_ref: independent if/else (NOT a call to mr._volconc_regime,
        # to avoid a same-source circular argument against production code).
        if pctile_252_ref > 80.0 and spy_up20_ref:
            regime_ref = "高集中+上行（拥挤）"
        elif pctile_252_ref > 80.0 and not spy_up20_ref:
            regime_ref = "高集中+下行（恐慌）"
        else:
            regime_ref = "常态"

        # Sanity check against the brief's known reference anchors (research-
        # verified; if these fail the CSV itself changed, not just the parity).
        assert top50_sm_ref == pytest.approx(47.8039, abs=1e-3)
        assert pctile_252_ref == pytest.approx(91.6335, abs=1e-3)
        assert churn_sm_ref == pytest.approx(25.3, abs=1e-1)
        assert churn_pctile_252_ref == pytest.approx(0.0, abs=1e-6)
        assert spy_up20_ref is True
        assert regime_ref == "高集中+上行（拥挤）"

        # ---- production chain over the real, read-only market.db ----
        frames = mr._load_volume_concentration_frames(
            db_path=_VOLCONC_REAL_DB, as_of=_VOLCONC_FREEZE_DATE,
        )
        assert frames["available"] is True, frames.get("reason")

        payload = mr._compute_volume_concentration_payload(
            frames["share_df"], frames["members"], frames["spy_close"],
        )
        assert payload["available"] is True, payload.get("reason")
        assert payload["as_of"] == _VOLCONC_FREEZE_DATE

        # ---- six-field parity, plan tolerance ceiling ±0.1pp ----
        tol = 1e-6
        assert payload["share_sm_pct"] == pytest.approx(top50_sm_ref, abs=tol), (
            payload["share_sm_pct"], top50_sm_ref,
        )
        assert payload["share_pctile_1y"] == pytest.approx(pctile_252_ref, abs=tol), (
            payload["share_pctile_1y"], pctile_252_ref,
        )
        assert payload["churn_sm_pct"] == pytest.approx(churn_sm_ref, abs=tol), (
            payload["churn_sm_pct"], churn_sm_ref,
        )
        assert payload["churn_pctile_1y"] == pytest.approx(churn_pctile_252_ref, abs=tol), (
            payload["churn_pctile_1y"], churn_pctile_252_ref,
        )
        assert (payload["spy_ret20_pct"] > 0) == spy_up20_ref
        assert payload["regime"] == regime_ref
