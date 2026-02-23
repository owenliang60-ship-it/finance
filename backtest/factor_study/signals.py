"""
信号检测 — 四种信号类型 (threshold / cross_up / cross_down / sustained)

detect_signals() 接收因子分数历史，按 SignalDefinition 规则检测事件日期。
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Tuple


class SignalType(Enum):
    """信号类型"""
    THRESHOLD = "threshold"       # score > X
    CROSS_UP = "cross_up"         # 前期 ≤ X, 本期 > X
    CROSS_DOWN = "cross_down"     # 前期 ≥ X, 本期 < X
    SUSTAINED = "sustained"       # 连续 N 期 score > X


@dataclass
class SignalDefinition:
    """信号定义"""
    signal_type: SignalType
    threshold: float
    sustained_n: int = 1          # SUSTAINED 专用: 连续 N 期

    def label(self) -> str:
        """可读标签"""
        if self.signal_type == SignalType.SUSTAINED:
            return f"{self.signal_type.value}_{self.threshold}x{self.sustained_n}"
        return f"{self.signal_type.value}_{self.threshold}"


def detect_signals(
    score_history: Dict[str, List[Tuple[str, float]]],
    signal_def: SignalDefinition,
) -> Dict[str, List[str]]:
    """
    检测信号事件

    Args:
        score_history: {symbol: [(date, score), ...]}
            每只股票的因子分数时间序列，按日期正序
        signal_def: 信号定义

    Returns:
        {symbol: [event_date, ...]} — 触发信号的日期列表
    """
    events: Dict[str, List[str]] = {}

    for symbol, history in score_history.items():
        if not history:
            continue

        symbol_events = _detect_for_symbol(history, signal_def)
        if symbol_events:
            events[symbol] = symbol_events

    return events


def _detect_for_symbol(
    history: List[Tuple[str, float]],
    signal_def: SignalDefinition,
) -> List[str]:
    """单只股票的信号检测"""
    st = signal_def.signal_type
    threshold = signal_def.threshold
    events: List[str] = []

    if st == SignalType.THRESHOLD:
        for date, score in history:
            if score > threshold:
                events.append(date)

    elif st == SignalType.CROSS_UP:
        for i in range(1, len(history)):
            prev_score = history[i - 1][1]
            curr_score = history[i][1]
            curr_date = history[i][0]
            if prev_score <= threshold < curr_score:
                events.append(curr_date)

    elif st == SignalType.CROSS_DOWN:
        for i in range(1, len(history)):
            prev_score = history[i - 1][1]
            curr_score = history[i][1]
            curr_date = history[i][0]
            if prev_score >= threshold > curr_score:
                events.append(curr_date)

    elif st == SignalType.SUSTAINED:
        n = signal_def.sustained_n
        if n < 1:
            return events

        # 滑动窗口: 连续 N 期 > threshold
        # 只在第一天达标时触发，避免重复
        consecutive = 0
        triggered = False

        for date, score in history:
            if score > threshold:
                consecutive += 1
                if consecutive >= n and not triggered:
                    events.append(date)
                    triggered = True
            else:
                consecutive = 0
                triggered = False

    return events
