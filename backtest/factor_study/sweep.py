"""
参数网格 — 每个因子的默认阈值 + 信号定义组合

提供 get_default_sweep(factor_name) 返回 List[SignalDefinition]，
以及支持自定义阈值覆盖。
"""

from typing import Dict, List, Optional

from backtest.factor_study.signals import SignalDefinition, SignalType


# ══════════════════════════════════════════════════════════
# 默认参数网格
# ══════════════════════════════════════════════════════════

_DEFAULT_GRIDS: Dict[str, dict] = {
    "RS_Rating_B": {
        "threshold": [70, 80, 90],
        "cross_up": [70, 80, 90],
        "cross_down": [10, 20, 30],
        "sustained": [(70, 3), (70, 5), (80, 3), (80, 5), (90, 3), (90, 7)],
    },
    "RS_Rating_C": {
        "threshold": [70, 80, 90],
        "cross_up": [70, 80, 90],
        "cross_down": [10, 20, 30],
        "sustained": [(70, 3), (70, 5), (80, 3), (80, 5), (90, 3), (90, 7)],
    },
    "PMARP": {
        "threshold": [90, 95, 98],
        "cross_up": [90, 95, 98],
        "cross_down": [2, 5, 10],
        "sustained": [(90, 3), (90, 5), (95, 3), (95, 5), (95, 7)],
    },
    "RVOL": {
        "threshold": [2.0, 3.0, 4.0],
        "cross_up": [2.0, 3.0, 4.0],
        "cross_down": [],
        "sustained": [(2.0, 3), (2.0, 5), (3.0, 3), (3.0, 5), (3.0, 7)],
    },
    "DV_Acceleration": {
        "threshold": [1.3, 1.5, 2.0],
        "cross_up": [1.3, 1.5, 2.0],
        "cross_down": [],
        "sustained": [],
    },
    "RVOL_Sustained": {
        "threshold": [1, 3, 5],
        "cross_up": [],
        "cross_down": [],
        "sustained": [],
    },
    "Crypto_RS_B": {
        "threshold": [70, 80, 90],
        "cross_up": [70, 80, 90],
        "cross_down": [10, 20, 30],
        "sustained": [(70, 3), (70, 5), (80, 3), (80, 5), (90, 3)],
    },
    "Crypto_RS_C": {
        "threshold": [70, 80, 90],
        "cross_up": [70, 80, 90],
        "cross_down": [10, 20, 30],
        "sustained": [(70, 3), (70, 5), (80, 3), (80, 5), (90, 3)],
    },
    "Market_Momentum": {
        "threshold": [1.0, 1.5, 2.0],
        "cross_up": [1.0, 1.5, 2.0],
        "cross_down": [-1.0, -1.5, -2.0],
        "sustained": [(1.5, 3), (1.5, 5), (2.0, 3)],
    },
}


def get_default_sweep(factor_name: str) -> List[SignalDefinition]:
    """
    获取因子的默认参数扫描列表

    Args:
        factor_name: 因子名称

    Returns:
        SignalDefinition 列表
    """
    grid = _DEFAULT_GRIDS.get(factor_name)
    if grid is None:
        return []

    return _grid_to_signals(grid)


def build_custom_sweep(
    thresholds: List[float],
    signal_types: Optional[List[str]] = None,
    sustained_ns: Optional[List[int]] = None,
) -> List[SignalDefinition]:
    """
    构建自定义参数扫描

    Args:
        thresholds: 阈值列表
        signal_types: 信号类型列表 (默认 ["threshold", "cross_up"])
        sustained_ns: sustained 的 N 值列表

    Returns:
        SignalDefinition 列表
    """
    if signal_types is None:
        signal_types = ["threshold", "cross_up"]
    if sustained_ns is None:
        sustained_ns = [3, 5]

    signals: List[SignalDefinition] = []

    for t in thresholds:
        if "threshold" in signal_types:
            signals.append(SignalDefinition(
                signal_type=SignalType.THRESHOLD,
                threshold=t,
            ))
        if "cross_up" in signal_types:
            signals.append(SignalDefinition(
                signal_type=SignalType.CROSS_UP,
                threshold=t,
            ))
        if "cross_down" in signal_types:
            signals.append(SignalDefinition(
                signal_type=SignalType.CROSS_DOWN,
                threshold=t,
            ))
        if "sustained" in signal_types:
            for n in sustained_ns:
                signals.append(SignalDefinition(
                    signal_type=SignalType.SUSTAINED,
                    threshold=t,
                    sustained_n=n,
                ))

    return signals


def _grid_to_signals(grid: dict) -> List[SignalDefinition]:
    """将 grid dict 转为 SignalDefinition 列表"""
    signals: List[SignalDefinition] = []

    for t in grid.get("threshold", []):
        signals.append(SignalDefinition(
            signal_type=SignalType.THRESHOLD,
            threshold=t,
        ))

    for t in grid.get("cross_up", []):
        signals.append(SignalDefinition(
            signal_type=SignalType.CROSS_UP,
            threshold=t,
        ))

    for t in grid.get("cross_down", []):
        signals.append(SignalDefinition(
            signal_type=SignalType.CROSS_DOWN,
            threshold=t,
        ))

    for entry in grid.get("sustained", []):
        if isinstance(entry, tuple):
            t, n = entry
        else:
            t, n = entry, 3
        signals.append(SignalDefinition(
            signal_type=SignalType.SUSTAINED,
            threshold=t,
            sustained_n=n,
        ))

    return signals
