"""
Track 2: 事件研究 — 离散信号有效性检验

定义信号规则 → 检测事件 → 衡量信号后 T 天收益 → 统计检验

输出:
- EventStudyResult: 含 n_events, mean_return, hit_rate, t_stat, p_value
"""

from collections import defaultdict
from dataclasses import dataclass
from typing import Dict, List

import numpy as np
import pandas as pd
from scipy.stats import ttest_1samp

from backtest.factor_study.signals import SignalDefinition


@dataclass
class EventStudyResult:
    """单个信号 × 单个 horizon 的事件研究结果"""
    factor_name: str
    signal_label: str
    horizon: int
    n_events: int           # 原始事件数 (stock × date)
    n_effective: int = 0    # 聚类后有效观测数 (独立日期数)
    mean_return: float = 0.0
    median_return: float = 0.0
    hit_rate: float = 0.0   # 正收益事件占比
    t_stat: float = 0.0     # H0: mean_return = 0
    p_value: float = 1.0


def run_event_study(
    factor_name: str,
    signal_def: SignalDefinition,
    events: Dict[str, List[str]],
    return_matrices: Dict[int, pd.DataFrame],
) -> List[EventStudyResult]:
    """
    运行事件研究

    Args:
        factor_name: 因子名称
        signal_def: 信号定义
        events: {symbol: [event_date, ...]} — detect_signals() 的输出
        return_matrices: {horizon: DataFrame[date x symbol]}

    Returns:
        每个 horizon 一个 EventStudyResult
    """
    signal_label = signal_def.label()
    results: List[EventStudyResult] = []

    for horizon, ret_df in sorted(return_matrices.items()):
        result = _study_for_horizon(
            factor_name, signal_label, horizon,
            events, ret_df,
        )
        results.append(result)

    return results


def _study_for_horizon(
    factor_name: str,
    signal_label: str,
    horizon: int,
    events: Dict[str, List[str]],
    ret_df: pd.DataFrame,
) -> EventStudyResult:
    """单个 horizon 的事件研究 (日期聚类版)

    按日期聚类: 同一天触发的多个事件取均值作为一个独立观测，
    然后在聚类均值上做 t-test。这消除了重叠窗口导致的样本膨胀。
    """
    # 1. 按日期桶收集事件收益
    date_bucket: Dict[str, List[float]] = defaultdict(list)
    n_raw = 0

    for symbol, event_dates in events.items():
        if symbol not in ret_df.columns:
            continue

        for date in event_dates:
            if date not in ret_df.index:
                continue

            fwd_ret = ret_df.loc[date, symbol]
            if not np.isnan(fwd_ret):
                date_bucket[date].append(float(fwd_ret))
                n_raw += 1

    if n_raw == 0:
        return EventStudyResult(
            factor_name=factor_name,
            signal_label=signal_label,
            horizon=horizon,
            n_events=0,
            n_effective=0,
        )

    # 2. 每个日期取均值 → 一个独立观测
    cluster_means = np.array([np.mean(rets) for rets in date_bucket.values()])
    n_effective = len(cluster_means)

    mean_ret = float(np.mean(cluster_means))
    median_ret = float(np.median(cluster_means))
    hit_rate = float(np.mean(cluster_means > 0))

    # 3. t-test on cluster means (正确的有效 N)
    if n_effective >= 2:
        t_stat, p_value = ttest_1samp(cluster_means, 0.0)
        t_stat = float(t_stat)
        p_value = float(p_value)
    else:
        t_stat = 0.0
        p_value = 1.0

    return EventStudyResult(
        factor_name=factor_name,
        signal_label=signal_label,
        horizon=horizon,
        n_events=n_raw,
        n_effective=n_effective,
        mean_return=mean_ret,
        median_return=median_ret,
        hit_rate=hit_rate,
        t_stat=t_stat,
        p_value=p_value,
    )
