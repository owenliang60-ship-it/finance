"""
BacktestEngine — 核心回测循环（市场无关）

流程:
  for date in trading_dates:
      if date in rebalance_set:
          sliced = adapter.slice_to_date(date)    # ← 防前视
          rs_df = rs_func(sliced)
          action = rebalancer.compute(rs_df, holdings)
          execute_sells(action.to_sell)
          execute_buys(target_weights)
      portfolio.take_snapshot(date, prices)
"""

import logging
from typing import List, Optional, Tuple

from backtest.config import BacktestConfig, FREQ_DAYS
from backtest.metrics import BacktestMetrics, compute_metrics, TRADING_DAYS_PER_YEAR, CALENDAR_DAYS_PER_YEAR
from backtest.portfolio import PortfolioState
from backtest.rebalancer import Rebalancer

logger = logging.getLogger(__name__)


class BacktestEngine:
    """
    市场无关的回测引擎

    通过 adapter 抽象层支持美股和币安合约两个市场。
    """

    def __init__(self, config: BacktestConfig, adapter=None):
        """
        Args:
            config: BacktestConfig 回测配置
            adapter: USStocksAdapter 或 CryptoAdapter 实例
                     如果为 None，根据 config.market 自动创建
        """
        self.config = config

        if adapter is None:
            adapter = self._create_adapter()
        self.adapter = adapter

        self.portfolio = PortfolioState(
            initial_capital=config.initial_capital,
            cost_rate=config.cost_rate,
        )
        self.rebalancer = Rebalancer(
            top_n=config.top_n,
            sell_buffer=config.sell_buffer,
        )

        self._rs_func = adapter.get_rs_function(config.rs_method)
        self._rebalance_count = 0
        self._turnover_notional = 0.0  # 累计换手金额

    def run(self) -> BacktestMetrics:
        """
        执行回测

        Returns:
            BacktestMetrics — 完整绩效指标
        """
        # 加载数据
        self.adapter.load_all()
        trading_dates = self.adapter.get_trading_dates()

        if not trading_dates:
            logger.error("无交易日期数据")
            return compute_metrics([], n_trades=0)

        # 应用日期过滤
        if self.config.start_date:
            trading_dates = [d for d in trading_dates if d >= self.config.start_date]
        if self.config.end_date:
            trading_dates = [d for d in trading_dates if d <= self.config.end_date]

        if not trading_dates:
            logger.error("过滤后无交易日期")
            return compute_metrics([], n_trades=0)

        # 生成 rebalance 日期集合
        rebalance_set = self._build_rebalance_set(trading_dates)

        logger.info(
            f"回测开始: {trading_dates[0]} → {trading_dates[-1]}, "
            f"{len(trading_dates)} 个交易日, "
            f"{len(rebalance_set)} 次换仓"
        )

        # ── 主循环 ────────────────────────────────────
        for date in trading_dates:
            current_prices = self.adapter.get_prices_at(date)

            if not current_prices:
                continue

            if date in rebalance_set:
                self._rebalance(date, current_prices)

            self.portfolio.take_snapshot(date, current_prices)

        # ── 计算指标 ──────────────────────────────────
        nav_series = self.portfolio.nav_series()
        if not nav_series:
            return compute_metrics([], n_trades=0)

        # 基准
        benchmark_nav = None
        if self.config.benchmark_symbol:
            benchmark_nav = self.adapter.get_benchmark_nav(
                self.config.benchmark_symbol
            )
            if benchmark_nav:
                # 按回测日期范围过滤基准数据
                start = nav_series[0][0]
                end = nav_series[-1][0]
                benchmark_nav = [
                    (d, v) for d, v in benchmark_nav
                    if start <= d <= end
                ]

        # 年化换手率
        days_per_year = (
            CALENDAR_DAYS_PER_YEAR
            if self.config.market == "crypto"
            else TRADING_DAYS_PER_YEAR
        )
        n_days = len(nav_series)
        years = n_days / days_per_year if days_per_year > 0 else 1
        avg_nav = sum(v for _, v in nav_series) / len(nav_series) if nav_series else 1
        annual_turnover = (self._turnover_notional / avg_nav / years) if years > 0 and avg_nav > 0 else 0.0

        return compute_metrics(
            nav_series=nav_series,
            benchmark_nav=benchmark_nav,
            total_costs=self.portfolio.total_costs,
            n_trades=self.portfolio.total_trades,
            annual_turnover=annual_turnover,
            days_per_year=days_per_year,
        )

    # ── 换仓逻辑 ──────────────────────────────────────

    def _rebalance(self, date: str, current_prices: dict):
        """执行单次换仓"""
        self._rebalance_count += 1

        # 防前视: 只截取到当日
        sliced = self.adapter.slice_to_date(date)

        # 计算 RS 排名
        rs_df = self._rs_func(sliced)

        if rs_df.empty:
            logger.debug(f"{date}: RS 计算无结果, 跳过换仓")
            return

        # 计算换仓操作
        current_holdings = set(self.portfolio.holdings.keys())
        action = self.rebalancer.compute(rs_df, current_holdings)

        # 计算目标权重
        weights = self.rebalancer.compute_weights(
            action, rs_df, self.config.weighting
        )

        # 执行卖出
        for sym in action.to_sell:
            price = current_prices.get(sym)
            if price and price > 0:
                shares = self.portfolio.holdings.get(sym, 0)
                if shares > 0:
                    notional = shares * price
                    self._turnover_notional += notional
                    self.portfolio.sell_all(sym, price, date)

        # 计算当前 NAV 用于分配
        nav = self.portfolio.compute_nav(current_prices)

        # 执行买入 (目标权重分配)
        for sym in action.to_buy:
            price = current_prices.get(sym)
            if price and price > 0 and sym in weights:
                target_notional = nav * weights[sym]
                # 已持有的不重复买
                current_shares = self.portfolio.holdings.get(sym, 0)
                current_value = current_shares * price
                buy_amount = target_notional - current_value
                if buy_amount > 0:
                    self._turnover_notional += buy_amount
                    self.portfolio.buy(sym, buy_amount, price, date)

    # ── 辅助方法 ──────────────────────────────────────

    def _build_rebalance_set(self, trading_dates: List[str]) -> set:
        """
        从交易日期列表生成 rebalance 日期集合

        根据 config.rebalance_freq 间隔采样
        """
        freq_days = FREQ_DAYS.get(self.config.rebalance_freq, 21)
        rebalance_dates = set()

        for i in range(0, len(trading_dates), freq_days):
            rebalance_dates.add(trading_dates[i])

        return rebalance_dates

    def _create_adapter(self):
        """根据 market 自动创建适配器"""
        if self.config.market == "crypto":
            from backtest.adapters.crypto import CryptoAdapter
            return CryptoAdapter()
        else:
            from backtest.adapters.us_stocks import USStocksAdapter
            return USStocksAdapter()
