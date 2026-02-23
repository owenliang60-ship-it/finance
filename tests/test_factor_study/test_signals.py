"""
信号检测测试 — 四种信号类型
"""

import pytest

from backtest.factor_study.signals import (
    SignalType,
    SignalDefinition,
    detect_signals,
)


# ── 测试数据 ─────────────────────────────────────────────

def _make_history(scores):
    """辅助: 生成 [(date, score)] 列表"""
    return [(f"2024-01-{i+1:02d}", s) for i, s in enumerate(scores)]


# ── SignalDefinition ─────────────────────────────────────

class TestSignalDefinition:
    def test_label_threshold(self):
        sd = SignalDefinition(SignalType.THRESHOLD, 90)
        assert sd.label() == "threshold_90"

    def test_label_cross_up(self):
        sd = SignalDefinition(SignalType.CROSS_UP, 80)
        assert sd.label() == "cross_up_80"

    def test_label_sustained(self):
        sd = SignalDefinition(SignalType.SUSTAINED, 70, sustained_n=5)
        assert sd.label() == "sustained_70x5"


# ── THRESHOLD 信号 ───────────────────────────────────────

class TestThresholdSignal:
    def test_basic(self):
        history = {"AAPL": _make_history([50, 60, 95, 80, 91])}
        sig = SignalDefinition(SignalType.THRESHOLD, 90)
        events = detect_signals(history, sig)
        assert "AAPL" in events
        assert len(events["AAPL"]) == 2  # 95 和 91

    def test_no_events(self):
        history = {"AAPL": _make_history([50, 60, 70])}
        sig = SignalDefinition(SignalType.THRESHOLD, 90)
        events = detect_signals(history, sig)
        assert "AAPL" not in events

    def test_empty_history(self):
        history = {"AAPL": []}
        sig = SignalDefinition(SignalType.THRESHOLD, 90)
        events = detect_signals(history, sig)
        assert not events


# ── CROSS_UP 信号 ────────────────────────────────────────

class TestCrossUpSignal:
    def test_basic(self):
        history = {"AAPL": _make_history([85, 91, 88, 92])}
        sig = SignalDefinition(SignalType.CROSS_UP, 90)
        events = detect_signals(history, sig)
        assert "AAPL" in events
        assert len(events["AAPL"]) == 2  # 85→91, 88→92

    def test_already_above_no_event(self):
        # 从 91 到 95: 没有穿越
        history = {"AAPL": _make_history([91, 95])}
        sig = SignalDefinition(SignalType.CROSS_UP, 90)
        events = detect_signals(history, sig)
        assert "AAPL" not in events

    def test_exact_threshold_then_above(self):
        # 90 → 91: prev <= 90, curr > 90 → 触发
        history = {"AAPL": _make_history([90, 91])}
        sig = SignalDefinition(SignalType.CROSS_UP, 90)
        events = detect_signals(history, sig)
        assert len(events.get("AAPL", [])) == 1


# ── CROSS_DOWN 信号 ──────────────────────────────────────

class TestCrossDownSignal:
    def test_basic(self):
        history = {"AAPL": _make_history([25, 18, 22, 15])}
        sig = SignalDefinition(SignalType.CROSS_DOWN, 20)
        events = detect_signals(history, sig)
        assert "AAPL" in events
        assert len(events["AAPL"]) == 2  # 25→18, 22→15

    def test_exact_threshold_then_below(self):
        # 20 → 19: prev >= 20, curr < 20 → 触发
        history = {"AAPL": _make_history([20, 19])}
        sig = SignalDefinition(SignalType.CROSS_DOWN, 20)
        events = detect_signals(history, sig)
        assert len(events.get("AAPL", [])) == 1


# ── SUSTAINED 信号 ───────────────────────────────────────

class TestSustainedSignal:
    def test_basic_3_periods(self):
        history = {"AAPL": _make_history([50, 85, 85, 85, 60])}
        sig = SignalDefinition(SignalType.SUSTAINED, 80, sustained_n=3)
        events = detect_signals(history, sig)
        assert "AAPL" in events
        assert len(events["AAPL"]) == 1  # 只在第 3 天达标时触发

    def test_dedup_continuous(self):
        # 连续 5 天 > 80，sustained_n=3，只应触发一次
        history = {"AAPL": _make_history([85, 85, 85, 85, 85])}
        sig = SignalDefinition(SignalType.SUSTAINED, 80, sustained_n=3)
        events = detect_signals(history, sig)
        assert len(events.get("AAPL", [])) == 1

    def test_reset_after_break(self):
        # 3天 > 80, 跌破, 再3天 > 80 → 触发两次
        history = {"AAPL": _make_history([85, 85, 85, 50, 85, 85, 85])}
        sig = SignalDefinition(SignalType.SUSTAINED, 80, sustained_n=3)
        events = detect_signals(history, sig)
        assert len(events.get("AAPL", [])) == 2

    def test_not_enough_periods(self):
        history = {"AAPL": _make_history([85, 85])}
        sig = SignalDefinition(SignalType.SUSTAINED, 80, sustained_n=3)
        events = detect_signals(history, sig)
        assert "AAPL" not in events


# ── 多股票测试 ───────────────────────────────────────────

class TestMultipleSymbols:
    def test_different_symbols_independent(self):
        history = {
            "AAPL": _make_history([50, 95]),
            "MSFT": _make_history([50, 60]),
        }
        sig = SignalDefinition(SignalType.THRESHOLD, 90)
        events = detect_signals(history, sig)
        assert "AAPL" in events
        assert "MSFT" not in events
