"""6 个月滚动 beta — 个股日收益率对基准（SPY）的回归系数。

晨报个股属性用（与市值并列展示，spec: docs/design/2026-06-12-morning-report-beta-attribute.md）。
纯计算模块：输入收盘价序列，不碰数据库（遵循 src/indicators/ 惯例）。
"""
from typing import Optional

import numpy as np
import pandas as pd

BETA_WINDOW = 126   # ≈6 个月交易日
BETA_MIN_OBS = 60   # 对齐后收益率样本下限，不足返回 None（如上市不足 6 个月）


def compute_beta(
    stock_closes: Optional[pd.Series],
    bench_closes: Optional[pd.Series],
    window: int = BETA_WINDOW,
    min_obs: int = BETA_MIN_OBS,
) -> Optional[float]:
    """beta = cov(r_i, r_m) / var(r_m)，日简单收益率。

    两条序列以日期为 index，按日期 inner join 对齐后取末尾 window 个收益率。
    停牌/缺日的日期经 inner join 移除；缺口后首日收益率为跨缺口复合收益率
    （基准同对齐到稀疏 index，对 beta 影响极小）。
    样本 < min_obs 或基准方差为 0 时返回 None。
    """
    if stock_closes is None or bench_closes is None:
        return None
    if len(stock_closes) < 2 or len(bench_closes) < 2:
        return None
    aligned = pd.concat(
        [stock_closes.rename("stock"), bench_closes.rename("bench")],
        axis=1, join="inner",
    ).sort_index().dropna()
    returns = aligned.pct_change().dropna().tail(window)
    if len(returns) < min_obs:
        return None
    var_bench = float(returns["bench"].var())
    if not np.isfinite(var_bench) or var_bench < 1e-12:
        return None
    cov = float(returns["stock"].cov(returns["bench"]))
    return cov / var_bench
