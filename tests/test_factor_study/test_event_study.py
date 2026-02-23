"""
事件研究测试 — Track 2
"""

import numpy as np
import pandas as pd
import pytest

from backtest.factor_study.event_study import run_event_study, EventStudyResult
from backtest.factor_study.signals import SignalDefinition, SignalType


# ── 合成数据 ─────────────────────────────────────────────

def _make_return_matrices():
    """3 只股票 × 10 个日期, 两个 horizon"""
    dates = [f"2024-01-{d:02d}" for d in range(1, 11)]
    symbols = ["AAPL", "MSFT", "GOOG"]

    # 固定收益方便验证
    data_5 = {
        "AAPL": [0.05, 0.03, 0.02, -0.01, 0.04, 0.01, 0.02, 0.03, np.nan, np.nan],
        "MSFT": [0.02, 0.01, -0.01, 0.03, 0.02, 0.01, -0.02, 0.01, np.nan, np.nan],
        "GOOG": [-0.01, 0.02, 0.01, 0.04, -0.02, 0.03, 0.01, 0.02, np.nan, np.nan],
    }
    data_10 = {
        "AAPL": [0.10, 0.08, 0.05, -0.02, 0.06, np.nan, np.nan, np.nan, np.nan, np.nan],
        "MSFT": [0.04, 0.03, -0.02, 0.05, 0.03, np.nan, np.nan, np.nan, np.nan, np.nan],
        "GOOG": [-0.02, 0.05, 0.03, 0.06, -0.03, np.nan, np.nan, np.nan, np.nan, np.nan],
    }

    return {
        5: pd.DataFrame(data_5, index=dates),
        10: pd.DataFrame(data_10, index=dates),
    }


# ── 测试 ─────────────────────────────────────────────────

class TestRunEventStudy:
    def test_basic(self):
        events = {
            "AAPL": ["2024-01-01", "2024-01-05"],
            "MSFT": ["2024-01-01"],
        }
        ret_matrices = _make_return_matrices()
        sig = SignalDefinition(SignalType.THRESHOLD, 90)

        results = run_event_study("RS_B", sig, events, ret_matrices)

        assert len(results) == 2  # 2 horizons
        for r in results:
            assert r.factor_name == "RS_B"
            assert r.n_events >= 2

    def test_horizon_5_specific(self):
        events = {"AAPL": ["2024-01-01"]}
        ret_matrices = _make_return_matrices()
        sig = SignalDefinition(SignalType.THRESHOLD, 90)

        results = run_event_study("Test", sig, events, ret_matrices)
        r5 = [r for r in results if r.horizon == 5][0]

        assert r5.n_events == 1
        assert abs(r5.mean_return - 0.05) < 1e-10
        assert r5.hit_rate == 1.0  # 0.05 > 0

    def test_no_events(self):
        events = {}
        ret_matrices = _make_return_matrices()
        sig = SignalDefinition(SignalType.THRESHOLD, 90)

        results = run_event_study("Test", sig, events, ret_matrices)
        for r in results:
            assert r.n_events == 0
            assert r.mean_return == 0.0
            assert r.p_value == 1.0

    def test_missing_symbol_ignored(self):
        events = {"UNKNOWN": ["2024-01-01"]}
        ret_matrices = _make_return_matrices()
        sig = SignalDefinition(SignalType.THRESHOLD, 90)

        results = run_event_study("Test", sig, events, ret_matrices)
        for r in results:
            assert r.n_events == 0

    def test_missing_date_ignored(self):
        events = {"AAPL": ["2099-01-01"]}
        ret_matrices = _make_return_matrices()
        sig = SignalDefinition(SignalType.THRESHOLD, 90)

        results = run_event_study("Test", sig, events, ret_matrices)
        for r in results:
            assert r.n_events == 0

    def test_hit_rate_calculation(self):
        # 两个事件: 一个正收益, 一个负收益
        events = {"AAPL": ["2024-01-01", "2024-01-04"]}
        ret_matrices = _make_return_matrices()
        sig = SignalDefinition(SignalType.THRESHOLD, 90)

        results = run_event_study("Test", sig, events, ret_matrices)
        r5 = [r for r in results if r.horizon == 5][0]

        # 2024-01-01 → 0.05 (pos), 2024-01-04 → -0.01 (neg)
        assert r5.n_events == 2
        assert r5.hit_rate == 0.5

    def test_t_stat_significant(self):
        # 多个正收益事件 → t_stat 应显著
        events = {
            "AAPL": ["2024-01-01", "2024-01-02", "2024-01-05"],
            "GOOG": ["2024-01-02", "2024-01-04"],
        }
        ret_matrices = _make_return_matrices()
        sig = SignalDefinition(SignalType.THRESHOLD, 90)

        results = run_event_study("Test", sig, events, ret_matrices)
        r5 = [r for r in results if r.horizon == 5][0]

        assert r5.n_events == 5
        assert r5.mean_return > 0


class TestEventStudyResult:
    def test_dataclass_fields(self):
        r = EventStudyResult(
            factor_name="RS_B",
            signal_label="threshold_90",
            horizon=5,
            n_events=10,
            mean_return=0.03,
            median_return=0.025,
            hit_rate=0.7,
            t_stat=2.5,
            p_value=0.02,
        )
        assert r.factor_name == "RS_B"
        assert r.p_value < 0.05
