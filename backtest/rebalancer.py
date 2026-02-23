"""
Rebalancer — 换仓逻辑 + Hysteresis Buffer

核心规则:
1. Top N 按 rs_rank 降序选入
2. 已持有的股票只有排名跌出 Top(N + sell_buffer) 才卖
3. 空出的 slots 从 Top N 中未持有的填入
4. 不在当日 RS 结果中的 → 强制卖出
"""

from dataclasses import dataclass
from typing import Dict, List, Set, Tuple

import pandas as pd


@dataclass
class RebalanceAction:
    """一次换仓的操作清单"""
    to_sell: List[str]       # 需要卖出的 symbols
    to_buy: List[str]        # 需要买入的 symbols
    to_hold: List[str]       # 继续持有的 symbols
    target_count: int        # 目标持仓数


class Rebalancer:
    """
    带 Hysteresis Buffer 的换仓决策器

    Args:
        top_n: 目标持仓只数
        sell_buffer: 卖出缓冲 (排名跌出 top_n + sell_buffer 才卖)
    """

    def __init__(self, top_n: int = 10, sell_buffer: int = 5):
        self.top_n = top_n
        self.sell_buffer = sell_buffer

    def compute(
        self,
        rs_df: pd.DataFrame,
        current_holdings: Set[str],
    ) -> RebalanceAction:
        """
        根据 RS 排名和当前持仓计算换仓操作

        Args:
            rs_df: RS 排名 DataFrame，必须包含 [symbol, rs_rank] 列
            current_holdings: 当前持仓的 symbol 集合

        Returns:
            RebalanceAction — 买卖清单
        """
        if rs_df.empty:
            # RS 无结果 → 清仓所有持仓
            return RebalanceAction(
                to_sell=sorted(current_holdings),
                to_buy=[],
                to_hold=[],
                target_count=0,
            )

        # 按 rs_rank 降序排列
        ranked = rs_df.sort_values("rs_rank", ascending=False).reset_index(drop=True)
        all_symbols = ranked["symbol"].tolist()

        # Top N 集合 (强买入区)
        top_n_symbols = set(all_symbols[:self.top_n])

        # 安全区 = Top(N + buffer)，在此范围内的现有持仓不卖
        safe_zone_size = min(self.top_n + self.sell_buffer, len(all_symbols))
        safe_zone = set(all_symbols[:safe_zone_size])

        # RS 中有数据的全部 symbols
        rs_universe = set(all_symbols)

        # ── 决定卖出 ──
        to_sell = []
        for sym in current_holdings:
            if sym not in rs_universe:
                # 不在 RS 结果中 → 强制卖出 (退市/无数据)
                to_sell.append(sym)
            elif sym not in safe_zone:
                # 排名跌出安全区 → 卖出
                to_sell.append(sym)

        # ── 卖出后剩余持仓 ──
        remaining = current_holdings - set(to_sell)

        # ── 计算需要新买入多少 ──
        slots_available = self.top_n - len(remaining)

        # ── 从 Top N 中选择新买入 ──
        to_buy = []
        if slots_available > 0:
            for sym in all_symbols[:self.top_n]:
                if sym not in remaining and sym not in to_sell:
                    to_buy.append(sym)
                    if len(to_buy) >= slots_available:
                        break

        to_hold = sorted(remaining)

        return RebalanceAction(
            to_sell=sorted(to_sell),
            to_buy=to_buy,
            to_hold=to_hold,
            target_count=len(to_hold) + len(to_buy),
        )

    def compute_weights(
        self,
        action: RebalanceAction,
        rs_df: pd.DataFrame,
        weighting: str = "equal",
    ) -> Dict[str, float]:
        """
        计算目标权重

        Args:
            action: RebalanceAction
            rs_df: RS 排名数据
            weighting: "equal" 或 "rs_weighted"

        Returns:
            {symbol: target_weight} — 权重和为 1.0
        """
        target_symbols = action.to_hold + action.to_buy
        if not target_symbols:
            return {}

        if weighting == "equal":
            w = 1.0 / len(target_symbols)
            return {sym: w for sym in target_symbols}

        # RS 加权: 用 rs_rank 作为权重
        rs_map = dict(zip(rs_df["symbol"], rs_df["rs_rank"]))
        raw_weights = {sym: max(rs_map.get(sym, 0), 1) for sym in target_symbols}
        total = sum(raw_weights.values())
        if total <= 0:
            w = 1.0 / len(target_symbols)
            return {sym: w for sym in target_symbols}

        return {sym: w / total for sym, w in raw_weights.items()}
