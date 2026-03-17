"""
择时回测引擎

给定价格序列和信号列表，模拟全仓/空仓切换，
与 buy-and-hold 对比计算 excess metrics。
"""

from dataclasses import dataclass
from typing import List, Tuple

from backtest.metrics import BacktestMetrics, compute_metrics


@dataclass
class TimingResult:
    """单只股票的择时回测结果"""
    symbol: str
    signal_name: str
    strategy_nav: List[Tuple[str, float]]   # 策略 NAV 序列
    buyhold_nav: List[Tuple[str, float]]    # buy-and-hold NAV 序列
    strategy_metrics: BacktestMetrics
    buyhold_metrics: BacktestMetrics
    excess_cagr: float          # strategy CAGR - buyhold CAGR
    sharpe_diff: float          # strategy Sharpe - buyhold Sharpe
    mdd_diff: float             # strategy MDD - buyhold MDD (正值=策略更好)
    n_trades: int
    time_in_market: float       # 持仓天数 / 总天数


def run_timing_backtest(
    symbol: str,
    signal_name: str,
    price_df,
    signals: List[Tuple[str, str]],
    initial_capital: float = 100.0,
) -> TimingResult:
    """
    单资产择时回测

    Args:
        symbol: 标的代码
        signal_name: 信号名称
        price_df: DataFrame with date, close columns (ascending)
        signals: [(date_str, "BUY"/"SELL"), ...]
        initial_capital: 初始资金

    Returns:
        TimingResult
    """
    dates = price_df["date"].astype(str).tolist()
    closes = price_df["close"].astype(float).tolist()

    if len(dates) < 2:
        empty_metrics = compute_metrics([(dates[0], initial_capital)] if dates else [])
        return TimingResult(
            symbol=symbol,
            signal_name=signal_name,
            strategy_nav=[],
            buyhold_nav=[],
            strategy_metrics=empty_metrics,
            buyhold_metrics=empty_metrics,
            excess_cagr=0.0,
            sharpe_diff=0.0,
            mdd_diff=0.0,
            n_trades=0,
            time_in_market=0.0,
        )

    # 构建信号查找表 (date -> action)
    # 如果同一天有多个信号，以最后一个为准
    signal_map = {}
    for date_str, action in signals:
        signal_map[date_str] = action

    # 模拟：全仓 / 空仓
    in_market = False
    entry_price = 0.0
    strategy_nav_list = []
    buyhold_nav_list = []
    n_trades = 0
    days_in_market = 0
    nav = initial_capital
    bh_start_price = closes[0]

    for i, (date, close) in enumerate(zip(dates, closes)):
        # 更新 buy-and-hold NAV
        bh_nav = initial_capital * (close / bh_start_price)
        buyhold_nav_list.append((date, round(bh_nav, 6)))

        # 检查信号
        action = signal_map.get(date)

        if action == "BUY" and not in_market:
            in_market = True
            entry_price = close
            n_trades += 1
        elif action == "SELL" and in_market:
            # 结算持仓收益到 NAV
            nav = nav * (close / entry_price)
            in_market = False

        # 计算策略 NAV
        if in_market:
            current_nav = nav * (close / entry_price)
            days_in_market += 1
        else:
            current_nav = nav

        strategy_nav_list.append((date, round(current_nav, 6)))

    # 如果收盘时还在场内，用最后价格结算
    if in_market:
        nav = nav * (closes[-1] / entry_price)

    total_days = len(dates)
    time_in_market = days_in_market / total_days if total_days > 0 else 0.0

    # 计算绩效指标
    strategy_metrics = compute_metrics(
        strategy_nav_list,
        benchmark_nav=buyhold_nav_list,
        n_trades=n_trades,
    )
    buyhold_metrics = compute_metrics(
        buyhold_nav_list,
        n_trades=0,
    )

    excess_cagr = strategy_metrics.cagr - buyhold_metrics.cagr
    sharpe_diff = strategy_metrics.sharpe_ratio - buyhold_metrics.sharpe_ratio
    # MDD 是负数，策略 MDD 更大（更接近 0）= 更好
    mdd_diff = strategy_metrics.max_drawdown - buyhold_metrics.max_drawdown

    return TimingResult(
        symbol=symbol,
        signal_name=signal_name,
        strategy_nav=strategy_nav_list,
        buyhold_nav=buyhold_nav_list,
        strategy_metrics=strategy_metrics,
        buyhold_metrics=buyhold_metrics,
        excess_cagr=round(excess_cagr, 6),
        sharpe_diff=round(sharpe_diff, 4),
        mdd_diff=round(mdd_diff, 6),
        n_trades=n_trades,
        time_in_market=round(time_in_market, 4),
    )
