from __future__ import annotations

from dataclasses import asdict
from typing import Dict, List

import pandas as pd

from backtest.pipeline.primitives.pit_data import PitData
from backtest.pipeline.spec import ExecutionSpec
from backtest.pipeline.types import BacktestRunResult
from backtest.portfolio import PortfolioState


class ExecutionEngine:
    def __init__(
        self,
        pit_data: PitData,
        initial_capital: float = 100_000.0,
    ):
        self.pit_data = pit_data
        self.initial_capital = initial_capital

    def run(
        self,
        target_weights: pd.DataFrame,
        benchmark_symbol: str,
        execution: ExecutionSpec,
        start_date: str,
        end_date: str,
    ) -> BacktestRunResult:
        """Run a standalone backtest window from fresh capital.

        The caller is responsible for whether IS/OOS windows should inherit
        positions. The V3 runner intentionally starts IS and OOS as separate
        capital paths.
        """
        calendar = self.pit_data.trading_calendar(start_date, end_date)
        if not calendar:
            empty = pd.DataFrame(columns=["date", "nav"])
            return BacktestRunResult(
                nav=empty,
                trades=pd.DataFrame(),
                positions_daily=pd.DataFrame(),
                benchmark_nav=empty,
                total_costs=0.0,
                annual_turnover=0.0,
                n_trades=0,
            )

        target_weights = target_weights.sort_index()
        symbols = sorted(set(target_weights.columns.astype(str)))
        price_panel = self.pit_data.price_panel(
            symbols=symbols + [benchmark_symbol],
            start_date=start_date,
            end_date=end_date,
        )
        prices_by_date = self._prices_by_date(price_panel)
        execution_schedule = self._execution_schedule(target_weights, calendar)

        portfolio = PortfolioState(
            initial_capital=self.initial_capital,
            cost_rate=(execution.transaction_cost_bps + execution.spread_bps) / 10_000.0,
        )
        positions_daily_rows: List[dict[str, float | str]] = []
        last_known_close: Dict[str, float] = {}

        for trade_date in calendar:
            day_prices = prices_by_date.get(trade_date, {})
            open_prices = {
                symbol: values["open"]
                for symbol, values in day_prices.items()
                if values.get("open") is not None and values["open"] > 0
            }
            close_prices = {
                symbol: values["close"]
                for symbol, values in day_prices.items()
                if values.get("close") is not None and values["close"] > 0
            }
            last_known_close.update(close_prices)
            valuation_prices = last_known_close.copy()
            for symbol, price in open_prices.items():
                valuation_prices.setdefault(symbol, price)

            if trade_date in execution_schedule:
                self._rebalance(
                    portfolio=portfolio,
                    open_prices=open_prices,
                    target_weights=execution_schedule[trade_date],
                    trade_date=trade_date,
                )

            snapshot = portfolio.take_snapshot(trade_date, valuation_prices)
            nav = snapshot.nav
            if nav > 0:
                for symbol, shares in sorted(portfolio.holdings.items()):
                    close_price = valuation_prices.get(symbol)
                    if close_price is None or close_price <= 0:
                        continue
                    market_value = shares * close_price
                    positions_daily_rows.append(
                        {
                            "date": trade_date,
                            "symbol": symbol,
                            "shares": shares,
                            "close": close_price,
                            "market_value": market_value,
                            "weight": market_value / nav,
                        }
                    )

        nav_df = pd.DataFrame(
            [{"date": snap.date, "nav": snap.nav} for snap in portfolio.snapshots]
        )
        trades_df = pd.DataFrame([asdict(trade) for trade in portfolio.trades])
        positions_daily_df = pd.DataFrame(positions_daily_rows)
        benchmark_nav = self._build_benchmark_nav(
            benchmark_symbol=benchmark_symbol,
            start_date=start_date,
            end_date=end_date,
        )

        annual_turnover = self._annual_turnover(trades_df, nav_df)
        return BacktestRunResult(
            nav=nav_df,
            trades=trades_df,
            positions_daily=positions_daily_df,
            benchmark_nav=benchmark_nav,
            total_costs=portfolio.total_costs,
            annual_turnover=annual_turnover,
            n_trades=portfolio.total_trades,
        )

    def _execution_schedule(
        self,
        target_weights: pd.DataFrame,
        calendar: List[str],
    ) -> Dict[str, Dict[str, float]]:
        calendar_index = {date: idx for idx, date in enumerate(calendar)}
        schedule: Dict[str, Dict[str, float]] = {}

        for signal_date, row in target_weights.iterrows():
            date_str = str(signal_date)
            if date_str not in calendar_index:
                continue
            execution_idx = calendar_index[date_str] + 1
            if execution_idx >= len(calendar):
                continue
            execution_date = calendar[execution_idx]
            weights = {
                str(symbol): float(weight)
                for symbol, weight in row.dropna().items()
                if float(weight) > 0
            }
            schedule[execution_date] = weights

        return schedule

    def _prices_by_date(
        self,
        panel: pd.DataFrame,
    ) -> Dict[str, Dict[str, Dict[str, float]]]:
        mapping: Dict[str, Dict[str, Dict[str, float]]] = {}
        for row in panel.to_dict("records"):
            mapping.setdefault(str(row["date"]), {})[str(row["symbol"])] = {
                "open": float(row["open"]) if row["open"] is not None else None,
                "close": float(row["close"]) if row["close"] is not None else None,
            }
        return mapping

    def _rebalance(
        self,
        portfolio: PortfolioState,
        open_prices: Dict[str, float],
        target_weights: Dict[str, float],
        trade_date: str,
    ) -> None:
        if not open_prices:
            return

        nav = portfolio.compute_nav(open_prices)

        # Sell names removed from target.
        for symbol in list(portfolio.holdings):
            if symbol in target_weights:
                continue
            price = open_prices.get(symbol)
            if price is not None and price > 0:
                portfolio.sell_all(symbol, price, trade_date)

        # Trim overweight positions first.
        for symbol, target_weight in target_weights.items():
            price = open_prices.get(symbol)
            if price is None or price <= 0:
                continue
            current_shares = portfolio.holdings.get(symbol, 0.0)
            current_notional = current_shares * price
            target_notional = nav * target_weight
            if current_notional > target_notional + 1e-9:
                shares_to_sell = (current_notional - target_notional) / price
                portfolio.sell(symbol, shares_to_sell, price, trade_date)

        # Add underweight positions second.
        for symbol, target_weight in target_weights.items():
            price = open_prices.get(symbol)
            if price is None or price <= 0:
                continue
            current_shares = portfolio.holdings.get(symbol, 0.0)
            current_notional = current_shares * price
            target_notional = nav * target_weight
            if current_notional + 1e-9 < target_notional:
                portfolio.buy(symbol, target_notional - current_notional, price, trade_date)

    def _build_benchmark_nav(
        self,
        benchmark_symbol: str,
        start_date: str,
        end_date: str,
    ) -> pd.DataFrame:
        frame = self.pit_data.benchmark_prices(benchmark_symbol, start_date, end_date)
        if frame.empty:
            return pd.DataFrame(columns=["date", "nav"])
        base = float(frame.iloc[0]["close"])
        frame = frame[["date", "close"]].copy()
        frame["nav"] = frame["close"].astype(float) / base * self.initial_capital
        return frame[["date", "nav"]]

    def _annual_turnover(self, trades: pd.DataFrame, nav: pd.DataFrame) -> float:
        if trades.empty or nav.empty or len(nav) < 2:
            return 0.0
        years = len(nav) / 252.0
        if years <= 0:
            return 0.0
        avg_nav = float(nav["nav"].mean())
        if avg_nav <= 1e-9:
            return 0.0
        gross_turnover = float(trades["notional"].abs().sum()) / 2.0
        return gross_turnover / avg_nav / years
