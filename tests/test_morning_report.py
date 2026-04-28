"""Tests for scripts/morning_report.py — 格式化函数单元测试"""
import sys
from pathlib import Path

import pandas as pd
import pytest

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from scripts.morning_report import (
    format_section_broad_signal,
    format_section_layered_dv,
    format_section_layered_pmarp,
    format_section_layered_rvol,
    format_section_a,
    format_section_b,
    format_section_c,
    format_section_d,
    format_morning_report,
)


def sample_market_signals():
    return {
        "as_of": "2026-04-24",
        "symbols_scanned": 3,
        "symbols_with_data": 3,
        "broad_scan": {
            "criteria": "RVOL ≥3σ + 涨 ≥3%",
            "hits": [
                {
                    "symbol": "NVDA", "companyName": "NVIDIA Corporation",
                    "sector": "Technology", "industry": "Semiconductors",
                    "concept_bucket": "AI算力/云", "layer": "pool",
                    "rvol": 3.5, "return_pct": 4.2, "marketCap": 3e12,
                },
                {
                    "symbol": "APP", "companyName": "AppLovin Corporation",
                    "sector": "Technology", "industry": "Software - Application",
                    "concept_bucket": "软件/SaaS", "layer": "extend",
                    "rvol": 4.1, "return_pct": 5.5, "marketCap": 45e9,
                },
                {
                    "symbol": "OKLO", "companyName": "Oklo Inc.",
                    "sector": "Utilities", "industry": "Regulated Electric",
                    "concept_bucket": "数据中心电力", "layer": "broad",
                    "rvol": 5.2, "return_pct": 9.0, "marketCap": 6e9,
                },
            ],
            "triggered_total": 3,
        },
        "pmarp": {
            "criteria": "PMARP 上穿 2%",
            "hits": [
                {
                    "symbol": "BA", "companyName": "Boeing Company",
                    "sector": "Industrials", "industry": "Aerospace & Defense",
                    "concept_bucket": "工业/航天/国防", "layer": "extend",
                    "value": 2.5, "previous": 1.7, "marketCap": 12e9,
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
                    "concept_bucket": "工业/航天/国防", "layer": "broad",
                    "level": "sustained_3d", "latest_rvol": 3.2,
                    "marketCap": 8e9,
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
                {"rank": 1, "symbol": "NVDA", "dollar_volume": 25e9, "price": 890.5},
                {"rank": 2, "symbol": "TSLA", "dollar_volume": 18e9, "price": 310.2},
            ],
            "new_faces": [
                {"rank": 12, "symbol": "ARM", "dollar_volume": 1.2e9},
            ],
        }
        result = format_section_d(dv_result)
        assert "NVDA" in result
        assert "ARM" in result
        assert "新面孔" in result

    def test_missing_industry_uses_bucket_not_unclassified(self):
        dv_result = {
            "rankings": [
                {"rank": 1, "symbol": "NVDA", "dollar_volume": 25e9, "price": 890.5},
                {"rank": 2, "symbol": "XYZ1", "company_name": "Unknown Co",
                 "dollar_volume": 1e9, "price": 10.0},
            ],
            "new_faces": [],
        }
        result = format_section_d(dv_result)
        assert "Unclassified" not in result
        assert "unclassified" not in result.lower()
        assert "AI算力/云" in result
        assert "其他" in result


class TestLayeredSections:
    def test_broad_signal_groups_by_concept_bucket(self):
        result = format_section_broad_signal(sample_market_signals())
        assert "广扫标准" in result
        assert "AI算力/云" in result
        assert "软件/SaaS" in result
        assert "数据中心电力" in result
        assert "NVIDIA" in result
        assert "GPU/AI加速器" in result
        assert "Semiconductors" not in result
        assert "NVDA" in result

    def test_broad_signal_missing_industry_uses_concept_bucket(self):
        signals = sample_market_signals()
        signals["broad_scan"]["hits"] = [
            {
                "symbol": "TSLA",
                "companyName": "Tesla Inc.",
                "concept_bucket": "自动驾驶/机器人",
                "rvol": 3.2,
                "return_pct": 4.0,
                "marketCap": 800e9,
            }
        ]
        result = format_section_broad_signal(signals)
        assert "Unclassified" not in result
        assert "自动驾驶/机器人" in result
        assert "电动车/自动驾驶" in result

    def test_pmarp_layered_section(self):
        result = format_section_layered_pmarp(sample_market_signals())
        assert "PMARP 信号" in result
        assert "BA" in result
        assert "1.7→2.5" in result
        assert "商用飞机/军工" in result

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

    def test_bucketed_sections_do_not_truncate_with_more(self):
        data = sample_market_signals()
        data["broad_scan"]["hits"] = [
            {
                "symbol": f"S{i}", "companyName": f"SignalCo {i}",
                "sector": "Technology", "industry": "Semiconductors",
                "concept_bucket": "半导体链", "rvol": 3.0 + i / 10,
                "return_pct": 4.0 + i / 10, "marketCap": 1e9 + i,
            }
            for i in range(12)
        ]

        result = format_section_broad_signal(data)

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
            "rankings": [{"rank": 1, "symbol": "NVDA", "dollar_volume": 25e9, "price": 890.5}],
            "new_faces": [],
        }

        result = format_morning_report(
            market_signals=sample_market_signals(),
            dv_result=dv_result,
            elapsed=5,
        )

        assert "1. 广扫标准" in result
        assert "2. PMARP 信号" in result
        assert "3. 量能加速" in result
        assert "4. RVOL 持续放量" in result
        assert "*D. Dollar Volume*" in result
        assert "扫描: 3只" in result
