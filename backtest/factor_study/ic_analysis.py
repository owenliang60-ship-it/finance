"""
Track 1: IC 分析 — 连续因子预测力度量

IC = Spearman Rank Correlation(factor_score, forward_return)
不需要定义信号，直接衡量因子分数与未来收益的 rank 相关。

输出:
- ICResult: 单个 horizon 的 IC 统计
- ICDecayCurve: 跨 horizon 的 IC 衰减曲线
"""

from dataclasses import dataclass, field
from typing import Dict, List, Tuple

import numpy as np
import pandas as pd
from scipy.stats import spearmanr

from backtest.factor_study.protocol import FactorMeta


@dataclass
class ICResult:
    """单个 horizon 的 IC 统计"""
    factor_name: str
    horizon: int
    mean_ic: float
    std_ic: float
    ic_ir: float               # IC_IR = mean_ic / std_ic
    ic_hit_rate: float         # IC > 0 的比率
    quantile_returns: Dict[int, float]  # {1..5: mean_return}
    top_bottom_spread: float   # Q5 - Q1


@dataclass
class ICDecayCurve:
    """IC 衰减曲线 — 跨 horizon"""
    factor_name: str
    horizons: List[int]
    mean_ics: List[float]


def analyze_ic(
    factor_meta: FactorMeta,
    score_history: Dict[str, List[Tuple[str, float]]],
    return_matrices: Dict[int, pd.DataFrame],
    computation_dates: List[str],
    n_quantiles: int = 5,
) -> Tuple[List[ICResult], ICDecayCurve]:
    """
    计算因子的 IC 分析

    Args:
        factor_meta: 因子元信息
        score_history: {symbol: [(date, score), ...]}
        return_matrices: {horizon: DataFrame[date x symbol]}
        computation_dates: 计算日期列表
        n_quantiles: 分位数数量 (默认 5)

    Returns:
        (ic_results_per_horizon, ic_decay_curve)
    """
    # 构建因子分数矩阵: DataFrame[date x symbol]
    score_matrix = _build_score_matrix(score_history, computation_dates)

    ic_results: List[ICResult] = []
    decay_horizons: List[int] = []
    decay_ics: List[float] = []

    for horizon, ret_df in sorted(return_matrices.items()):
        result = _ic_for_horizon(
            factor_meta, score_matrix, ret_df,
            computation_dates, horizon, n_quantiles,
        )
        if result is not None:
            ic_results.append(result)
            decay_horizons.append(horizon)
            decay_ics.append(result.mean_ic)

    decay_curve = ICDecayCurve(
        factor_name=factor_meta.name,
        horizons=decay_horizons,
        mean_ics=decay_ics,
    )

    return ic_results, decay_curve


def _build_score_matrix(
    score_history: Dict[str, List[Tuple[str, float]]],
    computation_dates: List[str],
) -> pd.DataFrame:
    """将 score_history 转为 DataFrame[date x symbol]"""
    symbols = sorted(score_history.keys())
    data: Dict[str, Dict[str, float]] = {}

    for sym, history in score_history.items():
        date_score = {d: s for d, s in history}
        data[sym] = date_score

    df = pd.DataFrame(data, index=computation_dates)
    df.index.name = "date"
    return df


def _ic_for_horizon(
    factor_meta: FactorMeta,
    score_matrix: pd.DataFrame,
    ret_df: pd.DataFrame,
    computation_dates: List[str],
    horizon: int,
    n_quantiles: int,
) -> ICResult:
    """计算单个 horizon 的 IC"""
    common_dates = [d for d in computation_dates
                    if d in score_matrix.index and d in ret_df.index]
    common_symbols = [s for s in score_matrix.columns if s in ret_df.columns]

    if len(common_dates) < 5 or len(common_symbols) < 5:
        return None

    ic_series: List[float] = []

    for date in common_dates:
        scores = score_matrix.loc[date, common_symbols]
        returns = ret_df.loc[date, common_symbols]

        # 去掉 NaN
        mask = scores.notna() & returns.notna()
        s = scores[mask]
        r = returns[mask]

        if len(s) < 5:
            continue

        corr, _ = spearmanr(s.values, r.values)
        if not np.isnan(corr):
            ic_series.append(corr)

    if len(ic_series) < 3:
        return None

    ic_arr = np.array(ic_series)
    mean_ic = float(np.mean(ic_arr))
    std_ic = float(np.std(ic_arr, ddof=1))
    ic_ir = mean_ic / std_ic if std_ic > 1e-10 else 0.0
    ic_hit_rate = float(np.mean(ic_arr > 0))

    # 分位数收益
    quantile_returns = _quantile_returns(
        score_matrix, ret_df, common_dates, common_symbols,
        n_quantiles, factor_meta.higher_is_stronger,
    )

    # Top - Bottom spread
    if n_quantiles in quantile_returns and 1 in quantile_returns:
        spread = quantile_returns[n_quantiles] - quantile_returns[1]
    else:
        spread = 0.0

    return ICResult(
        factor_name=factor_meta.name,
        horizon=horizon,
        mean_ic=mean_ic,
        std_ic=std_ic,
        ic_ir=ic_ir,
        ic_hit_rate=ic_hit_rate,
        quantile_returns=quantile_returns,
        top_bottom_spread=spread,
    )


def _quantile_returns(
    score_matrix: pd.DataFrame,
    ret_df: pd.DataFrame,
    dates: List[str],
    symbols: List[str],
    n_quantiles: int,
    higher_is_stronger: bool,
) -> Dict[int, float]:
    """计算各分位数的平均收益"""
    quantile_sums: Dict[int, List[float]] = {q: [] for q in range(1, n_quantiles + 1)}

    for date in dates:
        scores = score_matrix.loc[date, symbols]
        returns = ret_df.loc[date, symbols]

        mask = scores.notna() & returns.notna()
        s = scores[mask]
        r = returns[mask]

        if len(s) < n_quantiles:
            continue

        # 按分数排名分组
        try:
            labels = pd.qcut(
                s.rank(method="first"),
                q=n_quantiles,
                labels=range(1, n_quantiles + 1),
            )
        except ValueError:
            continue

        for q in range(1, n_quantiles + 1):
            q_mask = labels == q
            if q_mask.any():
                quantile_sums[q].append(float(r[q_mask].mean()))

    result: Dict[int, float] = {}
    for q, vals in quantile_sums.items():
        if vals:
            result[q] = float(np.mean(vals))
        else:
            result[q] = 0.0

    return result
