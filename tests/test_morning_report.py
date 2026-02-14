"""Tests for scripts/morning_report.py — 格式化函数单元测试"""
import sys
from pathlib import Path

import pandas as pd
import pytest

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from scripts.morning_report import (
    format_section_a,
    format_section_b,
    format_section_c,
    format_section_d,
    format_section_e,
    format_section_f,
    format_morning_report,
)


class TestFormatSectionA:
    """A. PMARP 极值 (四种穿越信号)"""

    def test_with_high_and_low_legacy(self):
        """向后兼容: 没有 pmarp_crossovers 时用 value 过滤"""
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
        assert "NVDA" in result
        assert "INTC" in result
        assert "98%" in result

    def test_four_crossover_signals(self):
        """四种穿越信号全显示"""
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
        assert "上穿98%" in result
        assert "NVDA" in result
        assert "下穿98%" in result
        assert "TSLA" in result
        assert "下穿2%" in result
        assert "INTC" in result
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
        assert "上穿98%" in result
        assert "NVDA" in result
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
    """B. RS 动量评级"""

    def test_basic_formatting(self):
        rs_b = pd.DataFrame([
            {"symbol": "NVDA", "rs_rank": 99, "z_3m": 2.31, "z_1m": 1.87, "z_1w": 1.54},
            {"symbol": "PLTR", "rs_rank": 97, "z_3m": 2.10, "z_1m": 1.92, "z_1w": 0.84},
        ])
        rs_c = pd.DataFrame([
            {"symbol": "PLTR", "rs_rank": 99, "clenow_63d": 0.94, "clenow_21d": 0.87, "clenow_10d": 0.72},
        ])
        result = format_section_b(rs_b, rs_c)
        assert "Method B" in result
        assert "Method C" in result
        assert "NVDA" in result
        assert "PLTR" in result

    def test_empty_dataframes(self):
        rs_b = pd.DataFrame()
        rs_c = pd.DataFrame()
        result = format_section_b(rs_b, rs_c)
        assert "RS" in result


class TestFormatSectionC:
    """C. 量能加速"""

    def test_with_signals(self):
        dv_df = pd.DataFrame([
            {"symbol": "TSLA", "dv_5d": 4.2e9, "dv_20d": 2.1e9, "ratio": 2.0, "signal": True},
            {"symbol": "MU", "dv_5d": 890e6, "dv_20d": 520e6, "ratio": 1.7, "signal": True},
            {"symbol": "AAPL", "dv_5d": 5e9, "dv_20d": 4.9e9, "ratio": 1.02, "signal": False},
        ])
        result = format_section_c(dv_df)
        assert "TSLA" in result
        assert "MU" in result
        assert "AAPL" not in result  # signal=False → filtered
        assert "2.0x" in result

    def test_no_signals(self):
        dv_df = pd.DataFrame([
            {"symbol": "AAPL", "dv_5d": 5e9, "dv_20d": 4.9e9, "ratio": 1.02, "signal": False},
        ])
        result = format_section_c(dv_df)
        assert "无加速信号" in result


class TestFormatSectionD:
    """D. RVOL 持续放量"""

    def test_with_sustained(self):
        rvol_list = [
            {"symbol": "TSLA", "level": "sustained_5d", "days": 5,
             "values": [4.2, 3.8, 3.1, 2.9, 2.4], "latest_rvol": 4.2},
            {"symbol": "MU", "level": "sustained_3d", "days": 3,
             "values": [3.5, 2.8, 2.3], "latest_rvol": 3.5},
        ]
        result = format_section_d(rvol_list)
        assert "TSLA" in result
        assert "MU" in result
        assert "5日连续" in result
        assert "3日连续" in result

    def test_empty(self):
        result = format_section_d([])
        assert "无持续放量" in result


class TestFormatSectionE:
    """E. Dollar Volume"""

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
        result = format_section_e(dv_result)
        assert "NVDA" in result
        assert "ARM" in result
        assert "新面孔" in result


class TestFormatSectionF:
    """F. 聚类"""

    def test_with_clusters(self):
        cluster_result = {
            "clusters": {
                "0": ["NVDA", "AMD", "AVGO"],
                "1": ["JPM", "GS"],
            },
            "comparison": {"jaccard": 0.2, "new_formation": True, "changes": []},
        }
        result = format_section_f(cluster_result)
        assert "NEW FORMATION" in result
        assert "Cluster 0" in result
        assert "NVDA" in result


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
            "rs_rating_b": pd.DataFrame([
                {"symbol": "NVDA", "rs_rank": 99, "z_3m": 2.0, "z_1m": 1.5, "z_1w": 1.0},
            ]),
            "rs_rating_c": pd.DataFrame([
                {"symbol": "NVDA", "rs_rank": 99, "clenow_63d": 0.9, "clenow_21d": 0.8, "clenow_10d": 0.7},
            ]),
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
            "rs_rating_b": pd.DataFrame(),
            "rs_rating_c": pd.DataFrame(),
            "dv_acceleration": pd.DataFrame(columns=["symbol", "dv_5d", "dv_20d", "ratio", "signal"]),
            "rvol_sustained": [],
            "symbols_scanned": 10,
        }

        result = format_morning_report(indicator_summary, momentum_results, elapsed=5)
        assert "PMARP" in result
        assert "RS" in result
        assert "DV" in result or "量能" in result
        assert "RVOL" in result
