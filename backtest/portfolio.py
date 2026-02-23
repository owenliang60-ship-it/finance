"""
PortfolioState — 持仓、现金、NAV 跟踪

支持 fractional shares，简化等权分配计算。
"""

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple


@dataclass
class Trade:
    """单笔交易记录"""
    date: str
    symbol: str
    side: str        # "BUY" or "SELL"
    shares: float
    price: float
    cost: float      # 交易成本 (绝对金额)
    notional: float  # 名义金额


@dataclass
class Snapshot:
    """单日快照"""
    date: str
    nav: float
    cash: float
    n_holdings: int


class PortfolioState:
    """
    组合状态跟踪器

    - 支持 fractional shares
    - 每笔交易自动扣除交易成本
    - 维护每日 NAV 快照序列
    """

    def __init__(self, initial_capital: float, cost_rate: float = 0.0005):
        self.initial_capital = initial_capital
        self.cash = initial_capital
        self.cost_rate = cost_rate  # 单边成本比率

        # {symbol: shares} — 正数表示多头
        self.holdings: Dict[str, float] = {}

        # 交易记录和快照
        self.trades: List[Trade] = []
        self.snapshots: List[Snapshot] = []

    # ── 交易操作 ───────────────────────────────────────

    def buy(self, symbol: str, notional: float, price: float, date: str) -> float:
        """
        买入指定金额的股票

        Args:
            symbol: 股票代码
            notional: 目标名义金额 (扣成本前)
            price: 买入价格
            date: 交易日期

        Returns:
            实际买入的股数
        """
        if price <= 0 or notional <= 0:
            return 0.0

        cost = notional * self.cost_rate
        net_amount = notional - cost
        shares = net_amount / price

        if net_amount > self.cash:
            # 资金不足，按可用资金买
            available = self.cash - (self.cash * self.cost_rate / (1 + self.cost_rate))
            if available <= 0:
                return 0.0
            cost = self.cash - available
            shares = available / price
            net_amount = available
            notional = self.cash

        self.cash -= (net_amount + cost)
        self.holdings[symbol] = self.holdings.get(symbol, 0.0) + shares

        self.trades.append(Trade(
            date=date,
            symbol=symbol,
            side="BUY",
            shares=shares,
            price=price,
            cost=cost,
            notional=notional,
        ))

        return shares

    def sell(self, symbol: str, shares: float, price: float, date: str) -> float:
        """
        卖出指定股数

        Args:
            symbol: 股票代码
            shares: 卖出股数 (如果超过持仓，卖出全部)
            price: 卖出价格
            date: 交易日期

        Returns:
            实际卖出的名义金额 (扣成本后)
        """
        if price <= 0 or shares <= 0:
            return 0.0

        current = self.holdings.get(symbol, 0.0)
        if current <= 0:
            return 0.0

        actual_shares = min(shares, current)
        gross = actual_shares * price
        cost = gross * self.cost_rate
        net = gross - cost

        self.cash += net
        self.holdings[symbol] = current - actual_shares

        # 清理零持仓
        if self.holdings[symbol] < 1e-10:
            del self.holdings[symbol]

        self.trades.append(Trade(
            date=date,
            symbol=symbol,
            side="SELL",
            shares=actual_shares,
            price=price,
            cost=cost,
            notional=gross,
        ))

        return net

    def sell_all(self, symbol: str, price: float, date: str) -> float:
        """卖出某只股票的全部持仓"""
        shares = self.holdings.get(symbol, 0.0)
        if shares <= 0:
            return 0.0
        return self.sell(symbol, shares, price, date)

    # ── NAV 计算 ──────────────────────────────────────

    def compute_nav(self, prices: Dict[str, float]) -> float:
        """
        计算当前净值

        Args:
            prices: {symbol: current_price}

        Returns:
            总净值 = 现金 + 持仓市值
        """
        market_value = sum(
            shares * prices.get(sym, 0.0)
            for sym, shares in self.holdings.items()
        )
        return self.cash + market_value

    def take_snapshot(self, date: str, prices: Dict[str, float]) -> Snapshot:
        """记录每日快照"""
        nav = self.compute_nav(prices)
        snap = Snapshot(
            date=date,
            nav=nav,
            cash=self.cash,
            n_holdings=len(self.holdings),
        )
        self.snapshots.append(snap)
        return snap

    # ── 统计 ──────────────────────────────────────────

    @property
    def total_trades(self) -> int:
        return len(self.trades)

    @property
    def total_costs(self) -> float:
        return sum(t.cost for t in self.trades)

    @property
    def holding_symbols(self) -> List[str]:
        return sorted(self.holdings.keys())

    def nav_series(self) -> List[Tuple[str, float]]:
        """返回 (date, nav) 序列"""
        return [(s.date, s.nav) for s in self.snapshots]
