"""Tests for scripts/morning_report.py — 格式化函数单元测试"""
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
    format_section_layered_dv,
    format_section_layered_pmarp,
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
        assert "AI算力/云" in result
        assert "其他" in result


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

    def test_pmarp_section_renders_three_signal_kinds(self):
        result = format_section_layered_pmarp(sample_market_signals())
        assert "1. PMARP 信号" in result
        # Criteria string covers all 3 signal kinds
        assert "上穿2%" in result
        assert "上穿98%" in result
        assert "下穿98%" in result
        # All 3 sample symbols rendered with their signal labels
        assert "NVDA" in result
        assert "TSLA" in result
        assert "BA" in result
        # New "信号" column header
        assert "信号" in result
        # The 信号 column shows Chinese label per kind
        nvda_line = [ln for ln in result.split("\n") if "NVDA" in ln][0]
        assert "上穿98%" in nvda_line
        tsla_line = [ln for ln in result.split("\n") if "TSLA" in ln][0]
        assert "下穿98%" in tsla_line
        ba_line = [ln for ln in result.split("\n") if "BA " in ln or ln.endswith("BA")][0] \
            if any("BA" in ln for ln in result.split("\n")) else ""
        # Ensure 上穿2% appears tied to BA's row
        assert "1.7→2.5" in result

    def test_dv_layered_section(self):
        result = format_section_layered_dv(sample_market_signals())
        assert "量能加速" in result
        assert "MU" in result
        assert "1.8x" in result
        assert "DRAM/HBM存储" in result

    def test_rvol_layered_section(self):
        result = format_section_layered_rvol(sample_market_signals())
        assert "RVOL 持续放量" in result
        assert "RKLB" in result
        assert "3日连续" in result
        assert "小型火箭发射" in result

    def test_layered_dv_renders_three_tier_concept_tags(self, monkeypatch, tmp_path):
        """When registry has display_tags, the layered DV row shows the full three tiers."""
        from src.data.market_store import MarketStore
        from terminal.company_concepts import ConceptRegistry
        from terminal.concept_classifier import ConceptClassifier
        from scripts.build_company_concept_registry import build_registry
        from terminal import concept_classifier as cc_mod
        from config.settings import REPORT_CONCEPTS_PATH

        cfg = PROJECT_ROOT / "config" / "concepts"
        store = MarketStore(tmp_path / "market.db")
        registry = ConceptRegistry(
            taxonomy_path=cfg / "taxonomy.json",
            themes_path=cfg / "concept_themes.json",
            overrides_path=cfg / "company_concept_overrides.json",
            watchlist_path=cfg / "concept_watchlist.json",
        )
        build_registry(
            store=store, registry=registry,
            universe_symbols=["MU"],
            profiles={"MU": {"symbol": "MU", "industry": "Semiconductors"}},
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
        assert "HBM" in result
        # Business role still rendered for context.
        assert "DRAM/HBM存储" in result

    def test_image_report_blocks_include_concept_column(self):
        """B 的 image-report cron 走 build_morning_visual_sections —— 3 个 layered
        block 都必须有'概念'列，否则三层标签不会出现在实际发出的图片晨报里。"""
        sections = build_morning_visual_sections(sample_market_signals())
        slugs_seen = set()
        for sec in sections:
            slugs_seen.add(sec["slug"])
            for block in sec["blocks"]:
                cols = block["columns"]
                # pmarp/dv/rvol 3 个 layered 信号 block 必带"概念"列
                if sec["slug"] in {"01_pmarp",
                                   "02_dv_acceleration", "03_rvol_sustained"}:
                    assert "概念" in cols, f"{sec['slug']} missing 概念 column: {cols}"
                    # 列宽数组长度也要匹配
                    assert len(block["widths"]) == len(cols)
                    # 每行的单元格数也要匹配
                    for row in block["rows"]:
                        assert len(row["cells"]) == len(cols)
        assert {"00_market_timing_factor", "01_pmarp",
                "02_dv_acceleration", "03_rvol_sustained"}.issubset(slugs_seen)

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
        assert "DRAM/HBM存储" in result
        # Legacy bucket label still appears on the concept column.
        assert "半导体链" in result

    def test_bucketed_sections_do_not_truncate_with_more(self):
        """Layered sections with >10 rows must not truncate; bucket display covers all entries."""
        data = sample_market_signals()
        data["pmarp"]["hits"] = [
            {
                "symbol": f"S{i}", "companyName": f"SignalCo {i}",
                "sector": "Technology", "industry": "Semiconductors",
                "concept_bucket": "半导体链", "layer": "extend",
                "value": 95.0 + i / 10, "previous": 94.0 + i / 10,
                "signal": "bullish_breakout", "marketCap": 12e9 + i,
            }
            for i in range(12)
        ]

        result = format_section_layered_pmarp(data)

        assert "... +" not in result
        assert "more" not in result
        assert "S0 SignalCo 0" in result
        assert "S11 SignalCo 11" in result


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
        assert "2. 量能加速" in result
        assert "3. RVOL 持续放量" in result
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
            "01_pmarp",
            "02_dv_acceleration",
            "03_rvol_sustained",
            "04_dollar_volume",
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
        # PMARP block now contains 3 signal kinds across pool/extend layers
        pmarp_rows = sections[1]["blocks"][0]["rows"]
        assert {row["layer"] for row in pmarp_rows} == {"pool", "extend"}
        # Subtitle should not advertise broad layer anymore (but Section 0 still mentions S2 broad).
        assert "Pool / Extend / Broad" not in sections[1]["subtitle"]
        assert "Pool / Extend 分层" in sections[1]["subtitle"]

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
        Aligned with the selection-scan universe scope (pool ∪ extend)."""
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
                # mcap 8B → broad, dropped
                {"rank": 18, "symbol": "ARM", "dollar_volume": 1.0e9, "market_cap": 8e9},
            ],
        }
        result = format_section_d(dv_result)
        assert "NVDA" in result
        assert "OKLO" not in result
        assert "ARM" not in result

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
        dv_section = next(s for s in sections if s["slug"] == "04_dollar_volume")
        all_cells = [
            cell for block in dv_section["blocks"]
            for row in block["rows"] for cell in row["cells"]
        ]
        rendered = " ".join(all_cells)
        assert "NVDA" in rendered
        assert "OKLO" not in rendered
