"""
绩效指标计算模块

计算回测结果的完整绩效指标集：
CAGR、Sharpe、Sortino、Calmar、MaxDD、年化波动率、
Alpha、Beta、信息比率、跟踪误差、年化换手率、总交易成本、胜率
"""

from dataclasses import dataclass
from typing import List, Optional, Tuple

import numpy as np


TRADING_DAYS_PER_YEAR = 252  # 美股
CALENDAR_DAYS_PER_YEAR = 365  # 币圈


@dataclass
class BacktestMetrics:
    """回测绩效指标"""
    # 收益
    total_return: float       # 总收益率
    cagr: float               # 年化收益率
    # 风险
    annual_volatility: float  # 年化波动率
    max_drawdown: float       # 最大回撤 (负数)
    max_dd_duration: int      # 最大回撤持续天数
    # 风险调整
    sharpe_ratio: float       # Sharpe (无风险利率=0)
    sortino_ratio: float      # Sortino
    calmar_ratio: float       # Calmar = CAGR / |MaxDD|
    # 相对基准
    alpha: float              # Jensen's alpha (年化)
    beta: float               # 相对基准的 beta
    information_ratio: float  # 信息比率
    tracking_error: float     # 跟踪误差
    # 交易
    annual_turnover: float    # 年化换手率
    total_costs: float        # 总交易成本
    win_rate: float           # 胜率 (正收益天数占比)
    # 元数据
    n_days: int               # 回测交易日天数
    n_trades: int             # 总交易笔数


def compute_metrics(
    nav_series: List[Tuple[str, float]],
    benchmark_nav: Optional[List[Tuple[str, float]]] = None,
    total_costs: float = 0.0,
    n_trades: int = 0,
    annual_turnover: float = 0.0,
    days_per_year: int = TRADING_DAYS_PER_YEAR,
) -> BacktestMetrics:
    """
    从 NAV 序列计算完整绩效指标

    Args:
        nav_series: [(date, nav), ...] 按日期排序
        benchmark_nav: [(date, nav), ...] 基准净值序列 (可选)
        total_costs: 总交易成本
        n_trades: 总交易笔数
        annual_turnover: 年化换手率
        days_per_year: 年化因子 (美股252, 币圈365)

    Returns:
        BacktestMetrics 数据类
    """
    navs = np.array([nav for _, nav in nav_series], dtype=np.float64)
    n_days = len(navs)

    if n_days < 2:
        return _empty_metrics(total_costs, n_trades, annual_turnover, n_days)

    # ── 日收益率 ────────────────────────────────────
    daily_returns = np.diff(navs) / navs[:-1]

    # ── 总收益 & CAGR ──────────────────────────────
    total_return = navs[-1] / navs[0] - 1
    years = n_days / days_per_year
    if years > 0 and navs[-1] > 0 and navs[0] > 0:
        cagr = (navs[-1] / navs[0]) ** (1 / years) - 1
    else:
        cagr = 0.0

    # ── 波动率 ─────────────────────────────────────
    annual_vol = float(np.std(daily_returns, ddof=1) * np.sqrt(days_per_year))

    # ── 最大回撤 ───────────────────────────────────
    max_dd, max_dd_duration = _max_drawdown(navs)

    # ── Sharpe (Rf=0) ──────────────────────────────
    sharpe = cagr / annual_vol if annual_vol > 1e-10 else 0.0

    # ── Sortino ────────────────────────────────────
    downside = daily_returns[daily_returns < 0]
    if len(downside) > 1:
        downside_vol = float(np.std(downside, ddof=1) * np.sqrt(days_per_year))
        sortino = cagr / downside_vol if downside_vol > 1e-10 else 0.0
    else:
        sortino = 0.0

    # ── Calmar ─────────────────────────────────────
    calmar = cagr / abs(max_dd) if abs(max_dd) > 1e-10 else 0.0

    # ── 胜率 ───────────────────────────────────────
    win_rate = float(np.mean(daily_returns > 0)) if len(daily_returns) > 0 else 0.0

    # ── Alpha / Beta / IR / TE (需要基准) ──────────
    alpha, beta, ir, te = 0.0, 0.0, 0.0, 0.0
    if benchmark_nav is not None and len(benchmark_nav) >= 2:
        alpha, beta, ir, te = _relative_metrics(
            daily_returns, benchmark_nav, days_per_year
        )

    return BacktestMetrics(
        total_return=round(total_return, 6),
        cagr=round(cagr, 6),
        annual_volatility=round(annual_vol, 6),
        max_drawdown=round(max_dd, 6),
        max_dd_duration=max_dd_duration,
        sharpe_ratio=round(sharpe, 4),
        sortino_ratio=round(sortino, 4),
        calmar_ratio=round(calmar, 4),
        alpha=round(alpha, 6),
        beta=round(beta, 4),
        information_ratio=round(ir, 4),
        tracking_error=round(te, 6),
        annual_turnover=round(annual_turnover, 4),
        total_costs=round(total_costs, 2),
        win_rate=round(win_rate, 4),
        n_days=n_days,
        n_trades=n_trades,
    )


# ── 内部函数 ─────────────────────────────────────────

def _max_drawdown(navs: np.ndarray) -> Tuple[float, int]:
    """
    计算最大回撤和持续天数

    Returns:
        (max_dd, duration) — max_dd 为负数
    """
    peak = navs[0]
    max_dd = 0.0
    dd_start = 0
    max_dd_duration = 0
    current_dd_start = 0

    for i in range(len(navs)):
        if navs[i] >= peak:
            peak = navs[i]
            current_dd_start = i
        else:
            dd = (navs[i] - peak) / peak
            if dd < max_dd:
                max_dd = dd
                max_dd_duration = i - current_dd_start

    return max_dd, max_dd_duration


def _relative_metrics(
    strategy_returns: np.ndarray,
    benchmark_nav: List[Tuple[str, float]],
    days_per_year: int,
) -> Tuple[float, float, float, float]:
    """
    计算相对基准的 Alpha, Beta, IR, TE

    Returns:
        (alpha, beta, information_ratio, tracking_error)
    """
    bm_navs = np.array([nav for _, nav in benchmark_nav], dtype=np.float64)

    if len(bm_navs) < 2:
        return 0.0, 0.0, 0.0, 0.0

    bm_returns = np.diff(bm_navs) / bm_navs[:-1]

    # 对齐长度 (取较短的)
    min_len = min(len(strategy_returns), len(bm_returns))
    sr = strategy_returns[:min_len]
    br = bm_returns[:min_len]

    if len(sr) < 2:
        return 0.0, 0.0, 0.0, 0.0

    # Beta = Cov(Rs, Rb) / Var(Rb)
    cov_matrix = np.cov(sr, br)
    var_bm = cov_matrix[1, 1]
    beta = float(cov_matrix[0, 1] / var_bm) if var_bm > 1e-10 else 0.0

    # Alpha = Rs_annual - Beta * Rb_annual
    rs_annual = float(np.mean(sr) * days_per_year)
    rb_annual = float(np.mean(br) * days_per_year)
    alpha = rs_annual - beta * rb_annual

    # Tracking Error & Information Ratio
    active = sr - br
    te = float(np.std(active, ddof=1) * np.sqrt(days_per_year))
    ir = float(np.mean(active) * days_per_year / te) if te > 1e-10 else 0.0

    return alpha, beta, ir, te


def _empty_metrics(
    total_costs: float, n_trades: int, annual_turnover: float, n_days: int
) -> BacktestMetrics:
    """数据不足时返回空指标"""
    return BacktestMetrics(
        total_return=0.0,
        cagr=0.0,
        annual_volatility=0.0,
        max_drawdown=0.0,
        max_dd_duration=0,
        sharpe_ratio=0.0,
        sortino_ratio=0.0,
        calmar_ratio=0.0,
        alpha=0.0,
        beta=0.0,
        information_ratio=0.0,
        tracking_error=0.0,
        annual_turnover=annual_turnover,
        total_costs=total_costs,
        win_rate=0.0,
        n_days=n_days,
        n_trades=n_trades,
    )
